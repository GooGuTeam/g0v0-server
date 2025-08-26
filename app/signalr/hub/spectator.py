from __future__ import annotations

import asyncio
import json
import lzma
import struct
import time
from typing import override

from app.calculator import clamp
from app.config import settings
from app.database import Beatmap, User
from app.database.failtime import FailTime, FailTimeResp
from app.database.score import Score
from app.database.score_token import ScoreToken
from app.database.statistics import UserStatistics
from app.dependencies.database import get_redis, with_db
from app.dependencies.fetcher import get_fetcher
from app.dependencies.storage import get_storage_service
from app.exception import InvokeException
from app.log import logger
from app.models.mods import APIMod, mods_to_int
from app.models.score import GameMode, LegacyReplaySoloScoreInfo, ScoreStatistics
from app.models.spectator_hub import (
    APIUser,
    FrameDataBundle,
    LegacyReplayFrame,
    ScoreInfo,
    SpectatedUserState,
    SpectatorState,
    StoreClientState,
    StoreScore,
)
from app.utils import unix_timestamp_to_windows

from .hub import Client, Hub

from httpx import HTTPError
from sqlalchemy.orm import joinedload
from sqlmodel import select

READ_SCORE_TIMEOUT = 30
REPLAY_LATEST_VER = 30000016


def encode_uleb128(num: int) -> bytes | bytearray:
    if num == 0:
        return b"\x00"

    ret = bytearray()

    while num != 0:
        ret.append(num & 0x7F)
        num >>= 7
        if num != 0:
            ret[-1] |= 0x80

    return ret


def encode_string(s: str) -> bytes:
    """Write `s` into bytes (ULEB128 & string)."""
    if s:
        encoded = s.encode()
        ret = b"\x0b" + encode_uleb128(len(encoded)) + encoded
    else:
        ret = b"\x00"

    return ret


async def save_replay(
    ruleset_id: int,
    md5: str,
    username: str,
    score: Score,
    statistics: ScoreStatistics,
    maximum_statistics: ScoreStatistics,
    frames: list[LegacyReplayFrame],
) -> None:
    data = bytearray()
    data.extend(struct.pack("<bi", ruleset_id, REPLAY_LATEST_VER))
    data.extend(encode_string(md5))
    data.extend(encode_string(username))
    data.extend(encode_string(f"lazer-{username}-{score.started_at.isoformat()}"))
    data.extend(
        struct.pack(
            "<hhhhhhihbi",
            score.n300,
            score.n100,
            score.n50,
            score.ngeki,
            score.nkatu,
            score.nmiss,
            score.total_score,
            score.max_combo,
            score.is_perfect_combo,
            mods_to_int(score.mods),
        )
    )
    data.extend(encode_string(""))  # hp graph
    data.extend(
        struct.pack(
            "<q",
            unix_timestamp_to_windows(round(score.started_at.timestamp())),
        )
    )

    # write frames
    frame_strs = []
    last_time = 0
    for frame in frames:
        time = round(frame.time)
        frame_strs.append(f"{time - last_time}|{frame.mouse_x or 0.0}|{frame.mouse_y or 0.0}|{frame.button_state}")
        last_time = time
    frame_strs.append("-12345|0|0|0")

    compressed = lzma.compress(",".join(frame_strs).encode("ascii"), format=lzma.FORMAT_ALONE)
    data.extend(struct.pack("<i", len(compressed)))
    data.extend(compressed)
    data.extend(struct.pack("<q", score.id))
    score_info = LegacyReplaySoloScoreInfo(
        online_id=score.id,
        mods=score.mods,
        statistics=statistics,
        maximum_statistics=maximum_statistics,
        client_version="",
        rank=score.rank,
        user_id=score.user_id,
        total_score_without_mods=score.total_score_without_mods,
    )
    compressed = lzma.compress(json.dumps(score_info).encode(), format=lzma.FORMAT_ALONE)
    data.extend(struct.pack("<i", len(compressed)))
    data.extend(compressed)

    storage_service = get_storage_service()
    replay_path = f"replays/{score.id}_{score.beatmap_id}_{score.user_id}_lazer_replay.osr"
    await storage_service.write_file(
        replay_path,
        bytes(data),
    )


class SpectatorHub(Hub[StoreClientState]):
    @staticmethod
    def group_id(user_id: int) -> str:
        return f"watch:{user_id}"

    @override
    def create_state(self, client: Client) -> StoreClientState:
        return StoreClientState(
            connection_id=client.connection_id,
            connection_token=client.connection_token,
        )

    @override
    async def _clean_state(self, state: StoreClientState) -> None:
        """
        Enhanced cleanup based on official osu-server-spectator implementation.
        Properly notifies watched users when spectator disconnects.
        """
        user_id = int(state.connection_id)
        if state.state:
            await self._end_session(user_id, state.state, state)

        # Critical fix: Notify all watched users that this spectator has disconnected
        # This matches the official CleanUpState implementation
        for watched_user_id in state.watched_user:
            if (target_client := self.get_client_by_id(str(watched_user_id))) is not None:
                await self.call_noblock(target_client, "UserEndedWatching", user_id)
                logger.debug(f"[SpectatorHub] Notified {watched_user_id} that {user_id} stopped watching")

    async def on_client_connect(self, client: Client) -> None:
        """
        Enhanced connection handling based on official implementation.
        Send all active player states to newly connected clients.
        """
        logger.info(f"[SpectatorHub] Client {client.user_id} connected")

        # Send all current player states to the new client
        # This matches the official OnConnectedAsync behavior
        active_states = []
        for user_id, store in self.state.items():
            if store.state is not None:
                active_states.append((user_id, store.state))

        if active_states:
            logger.debug(f"[SpectatorHub] Sending {len(active_states)} active player states to {client.user_id}")
            # Send states sequentially to avoid overwhelming the client
            for user_id, state in active_states:
                try:
                    await self.call_noblock(client, "UserBeganPlaying", user_id, state)
                except Exception as e:
                    logger.debug(f"[SpectatorHub] Failed to send state for user {user_id}: {e}")

        # Also sync with MultiplayerHub for cross-hub spectating
        await self._sync_with_multiplayer_hub(client)

    async def _sync_with_multiplayer_hub(self, client: Client) -> None:
        """
        Sync with MultiplayerHub to get active multiplayer game states.
        This ensures spectators can see multiplayer games from other pages.
        """
        try:
            # Import here to avoid circular imports
            from app.signalr.hub import MultiplayerHubs

            # Check all active multiplayer rooms for playing users
            for room_id, server_room in MultiplayerHubs.rooms.items():
                for room_user in server_room.room.users:
                    # Send state for users who are playing or in results
                    if room_user.state.is_playing and room_user.user_id not in self.state:
                        # Create a synthetic SpectatorState for multiplayer players
                        # This helps with cross-hub spectating
                        try:
                            synthetic_state = SpectatorState(
                                beatmap_id=server_room.queue.current_item.beatmap_id,
                                ruleset_id=room_user.ruleset_id or 0,  # Default to osu!
                                mods=room_user.mods,
                                state=SpectatedUserState.Playing,
                                maximum_statistics={},
                            )

                            await self.call_noblock(
                                client,
                                "UserBeganPlaying",
                                room_user.user_id,
                                synthetic_state,
                            )
                            logger.debug(
                                f"[SpectatorHub] Sent synthetic multiplayer state for user {room_user.user_id}"
                            )
                        except Exception as e:
                            logger.debug(f"[SpectatorHub] Failed to create synthetic state: {e}")

                    # Critical addition: Notify about finished players in multiplayer games
                    elif (
                        hasattr(room_user.state, "name")
                        and room_user.state.name == "RESULTS"
                        and room_user.user_id not in self.state
                    ):
                        try:
                            # Create a synthetic finished state
                            finished_state = SpectatorState(
                                beatmap_id=server_room.queue.current_item.beatmap_id,
                                ruleset_id=room_user.ruleset_id or 0,
                                mods=room_user.mods,
                                state=SpectatedUserState.Passed,  # Assume passed for results
                                maximum_statistics={},
                            )

                            await self.call_noblock(
                                client,
                                "UserFinishedPlaying",
                                room_user.user_id,
                                finished_state,
                            )
                            logger.debug(f"[SpectatorHub] Sent synthetic finished state for user {room_user.user_id}")
                        except Exception as e:
                            logger.debug(f"[SpectatorHub] Failed to create synthetic finished state: {e}")

        except Exception as e:
            logger.debug(f"[SpectatorHub] Failed to sync with MultiplayerHub: {e}")
            # This is not critical, so we don't raise the exception

    async def BeginPlaySession(self, client: Client, score_token: int, state: SpectatorState) -> None:
        user_id = int(client.connection_id)
        store = self.get_or_create_state(client)
        if store.state is not None:
            logger.warning(f"[SpectatorHub] User {user_id} began new session without ending previous one; cleaning up")
            try:
                await self._end_session(user_id, store.state, store)
            finally:
                store.state = None
                store.beatmap_status = None
                store.checksum = None
                store.ruleset_id = None
                store.score_token = None
                store.score = None
        if state.beatmap_id is None or state.ruleset_id is None:
            return

        fetcher = await get_fetcher()
        async with with_db() as session:
            async with session.begin():
                try:
                    beatmap = await Beatmap.get_or_fetch(session, fetcher, bid=state.beatmap_id)
                except HTTPError:
                    raise InvokeException(f"Beatmap {state.beatmap_id} not found.")
                user = (await session.exec(select(User).where(User.id == user_id))).first()
                if not user:
                    return
                name = user.username
                store.state = state
                store.beatmap_status = beatmap.beatmap_status
                store.checksum = beatmap.checksum
                store.ruleset_id = state.ruleset_id
                store.score_token = score_token
                store.score = StoreScore(
                    score_info=ScoreInfo(
                        mods=state.mods,
                        user=APIUser(id=user_id, name=name),
                        ruleset=state.ruleset_id,
                        maximum_statistics=state.maximum_statistics,
                    )
                )
        logger.info(f"[SpectatorHub] {client.user_id} began playing {state.beatmap_id}")

        await self.broadcast_group_call(
            self.group_id(user_id),
            "UserBeganPlaying",
            user_id,
            state,
        )

    async def SendFrameData(self, client: Client, frame_data: FrameDataBundle) -> None:
        user_id = int(client.connection_id)
        store = self.get_or_create_state(client)
        if store.state is None or store.score is None:
            return

        header = frame_data.header
        score_info = store.score.score_info
        score_info.accuracy = header.accuracy
        score_info.combo = header.combo
        score_info.max_combo = header.max_combo
        score_info.statistics = header.statistics
        store.score.replay_frames.extend(frame_data.frames)

        await self.broadcast_group_call(self.group_id(user_id), "UserSentFrames", user_id, frame_data)

    async def EndPlaySession(self, client: Client, state: SpectatorState) -> None:
        user_id = int(client.connection_id)
        store = self.get_or_create_state(client)
        score = store.score

        # Early return if no active session
        if (
            score is None
            or store.score_token is None
            or store.beatmap_status is None
            or store.state is None
            or store.score is None
        ):
            return

        try:
            # Process score if conditions are met
            if (settings.enable_all_beatmap_leaderboard and store.beatmap_status.has_leaderboard()) and any(
                k.is_hit() and v > 0 for k, v in score.score_info.statistics.items()
            ):
                await self._process_score(store, client)

            # End the play session and notify watchers
            await self._end_session(user_id, state, store)

        finally:
            # CRITICAL FIX: Always clear state in finally block to ensure cleanup
            # This matches the official C# implementation pattern
            store.state = None
            store.beatmap_status = None
            store.checksum = None
            store.ruleset_id = None
            store.score_token = None
            store.score = None
            logger.info(f"[SpectatorHub] Cleared all session state for user {user_id}")

    async def _process_score(self, store: StoreClientState, client: Client) -> None:
        user_id = int(client.connection_id)
        assert store.state is not None
        assert store.score_token is not None
        assert store.checksum is not None
        assert store.ruleset_id is not None
        assert store.score is not None
        async with with_db() as session:
            async with session:
                start_time = time.time()
                score_record = None
                while time.time() - start_time < READ_SCORE_TIMEOUT:
                    sub_query = select(ScoreToken.score_id).where(
                        ScoreToken.id == store.score_token,
                    )
                    result = await session.exec(
                        select(Score)
                        .options(joinedload(Score.beatmap))
                        .where(
                            Score.id == sub_query.scalar_subquery(),
                            Score.user_id == user_id,
                        )
                    )
                    score_record = result.first()
                    if score_record:
                        break
                if not score_record:
                    return
                if not score_record.passed:
                    return
                await self.call_noblock(
                    client,
                    "UserScoreProcessed",
                    user_id,
                    score_record.id,
                )
                # save replay
                score_record.has_replay = True
                await session.commit()
                await session.refresh(score_record)
                await save_replay(
                    ruleset_id=store.ruleset_id,
                    md5=store.checksum,
                    username=store.score.score_info.user.name,
                    score=score_record,
                    statistics=store.score.score_info.statistics,
                    maximum_statistics=store.score.score_info.maximum_statistics,
                    frames=store.score.replay_frames,
                )

    async def _end_session(self, user_id: int, state: SpectatorState, store: StoreClientState) -> None:
        async def _add_failtime():
            async with with_db() as session:
                failtime = await session.get(FailTime, state.beatmap_id)
                total_length = (
                    await session.exec(select(Beatmap.total_length).where(Beatmap.id == state.beatmap_id))
                ).one()
                index = clamp(round((exit_time / total_length) * 100), 0, 99)
                if failtime is not None:
                    resp = FailTimeResp.from_db(failtime)
                else:
                    resp = FailTimeResp()
                if state.state == SpectatedUserState.Failed:
                    resp.fail[index] += 1
                elif state.state == SpectatedUserState.Quit:
                    resp.exit[index] += 1

                assert state.beatmap_id
                new_failtime = FailTime.from_resp(state.beatmap_id, resp)
                if failtime is not None:
                    await session.merge(new_failtime)
                else:
                    session.add(new_failtime)
                await session.commit()

        async def _edit_playtime(token: int, ruleset_id: int, mods: list[APIMod]):
            redis = get_redis()
            key = f"score:existed_time:{token}"
            messages = await redis.xrange(key, min="-", max="+", count=1)
            if not messages:
                return
            before_time = int(messages[0][1]["time"])
            await redis.delete(key)
            async with with_db() as session:
                gamemode = GameMode.from_int(ruleset_id).to_special_mode(mods)
                statistics = (
                    await session.exec(
                        select(UserStatistics).where(
                            UserStatistics.user_id == user_id,
                            UserStatistics.mode == gamemode,
                        )
                    )
                ).first()
                if statistics is None:
                    return
                statistics.play_time -= before_time
                statistics.play_time += round(min(before_time, exit_time))

        if state.state == SpectatedUserState.Playing:
            state.state = SpectatedUserState.Quit
            logger.debug(f"[SpectatorHub] Changed state from Playing to Quit for user {user_id}")

        # Calculate exit time safely
        exit_time = 0
        if store.score and store.score.replay_frames:
            exit_time = max(frame.time for frame in store.score.replay_frames) // 1000

        # Background task for playtime editing - only if we have valid data
        if store.score_token and store.ruleset_id and store.score:
            task = asyncio.create_task(
                _edit_playtime(
                    store.score_token,
                    store.ruleset_id,
                    store.score.score_info.mods,
                )
            )
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

        # Background task for failtime tracking - only for failed/quit states with valid data
        if (
            state.beatmap_id is not None
            and exit_time > 0
            and state.state in (SpectatedUserState.Failed, SpectatedUserState.Quit)
        ):
            task = asyncio.create_task(_add_failtime())
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

        logger.info(f"[SpectatorHub] {user_id} finished playing {state.beatmap_id} with {state.state}")
        await self.broadcast_group_call(
            self.group_id(user_id),
            "UserFinishedPlaying",
            user_id,
            state,
        )

    async def StartWatchingUser(self, client: Client, target_id: int) -> None:
        """
        Enhanced StartWatchingUser based on official osu-server-spectator implementation.
        Properly handles state synchronization and watcher notifications.
        """
        user_id = int(client.connection_id)

        logger.info(f"[SpectatorHub] {user_id} started watching {target_id}")

        try:
            # Get target user's current state if it exists
            target_store = self.state.get(target_id)
            if not target_store or not target_store.state:
                logger.info(f"[SpectatorHub] Rejecting watch request for {target_id}: user not playing")
                raise InvokeException("Target user is not currently playing")

            if target_store.state.state != SpectatedUserState.Playing:
                logger.info(
                    f"[SpectatorHub] Rejecting watch request for {target_id}: state is {target_store.state.state}"
                )
                raise InvokeException("Target user is not currently playing")

            logger.debug(f"[SpectatorHub] {target_id} is currently playing, sending state")
            # Send current state to the watcher immediately
            await self.call_noblock(
                client,
                "UserBeganPlaying",
                target_id,
                target_store.state,
            )
        except InvokeException:
            # Re-raise to inform caller without adding to group
            raise
        except Exception as e:
            # User isn't tracked or error occurred - this is not critical
            logger.debug(f"[SpectatorHub] Could not get state for {target_id}: {e}")
            raise InvokeException("Target user is not currently playing") from e

        # Add watcher to our tracked users only after validation
        store = self.get_or_create_state(client)
        store.watched_user.add(target_id)

        # Add to SignalR group for this target user
        self.add_to_group(client, self.group_id(target_id))

        # Get watcher's username and notify the target user
        try:
            async with with_db() as session:
                username = (await session.exec(select(User.username).where(User.id == user_id))).first()
                if not username:
                    logger.warning(f"[SpectatorHub] Could not find username for user {user_id}")
                    return

            # Notify target user that someone started watching
            if (target_client := self.get_client_by_id(str(target_id))) is not None:
                # Create watcher info array (matches official format)
                watcher_info = [[user_id, username]]
                await self.call_noblock(target_client, "UserStartedWatching", watcher_info)
                logger.debug(f"[SpectatorHub] Notified {target_id} that {username} started watching")
        except Exception as e:
            logger.error(f"[SpectatorHub] Error notifying target user {target_id}: {e}")

    async def EndWatchingUser(self, client: Client, target_id: int) -> None:
        """
        Enhanced EndWatchingUser based on official osu-server-spectator implementation.
        Properly cleans up watcher state and notifies target user.
        """
        user_id = int(client.connection_id)

        logger.info(f"[SpectatorHub] {user_id} ended watching {target_id}")

        # Remove from SignalR group
        self.remove_from_group(client, self.group_id(target_id))

        # Remove from our tracked watched users
        store = self.get_or_create_state(client)
        store.watched_user.discard(target_id)

        # Notify target user that watcher stopped watching
        if (target_client := self.get_client_by_id(str(target_id))) is not None:
            await self.call_noblock(target_client, "UserEndedWatching", user_id)
            logger.debug(f"[SpectatorHub] Notified {target_id} that {user_id} stopped watching")
        else:
            logger.debug(f"[SpectatorHub] Target user {target_id} not found for end watching notification")
