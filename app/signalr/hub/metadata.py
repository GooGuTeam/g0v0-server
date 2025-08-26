from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Coroutine
import math
from typing import override

from app.calculator import clamp
from app.database import Relationship, RelationshipType, User
from app.database.playlist_best_score import PlaylistBestScore
from app.database.playlists import Playlist
from app.database.room import Room
from app.database.score import Score
from app.dependencies.database import with_db
from app.log import logger
from app.models.metadata_hub import (
    TOTAL_SCORE_DISTRIBUTION_BINS,
    DailyChallengeInfo,
    MetadataClientState,
    MultiplayerPlaylistItemStats,
    MultiplayerRoomScoreSetEvent,
    MultiplayerRoomStats,
    OnlineStatus,
    UserActivity,
)
from app.models.room import RoomCategory
from app.service.subscribers.score_processed import ScoreSubscriber
from app.utils import utcnow

from .hub import Client, Hub

from sqlmodel import col, select

ONLINE_PRESENCE_WATCHERS_GROUP = "metadata:online-presence-watchers"


class MetadataHub(Hub[MetadataClientState]):
    def __init__(self) -> None:
        super().__init__()
        self.subscriber = ScoreSubscriber()
        self.subscriber.metadata_hub = self
        self._daily_challenge_stats: MultiplayerRoomStats | None = None
        self._today = utcnow().date()
        self._lock = asyncio.Lock()

    def get_daily_challenge_stats(self, daily_challenge_room: int) -> MultiplayerRoomStats:
        if self._daily_challenge_stats is None or self._today != utcnow().date():
            self._daily_challenge_stats = MultiplayerRoomStats(
                room_id=daily_challenge_room,
                playlist_item_stats={},
            )
        return self._daily_challenge_stats

    @staticmethod
    def online_presence_watchers_group() -> str:
        return ONLINE_PRESENCE_WATCHERS_GROUP

    @staticmethod
    def room_watcher_group(room_id: int) -> str:
        return f"metadata:multiplayer-room-watchers:{room_id}"

    def broadcast_tasks(self, user_id: int, store: MetadataClientState | None) -> set[Coroutine]:
        if store is not None and not store.pushable:
            return set()
        data = store.for_push if store else None
        return {
            self.broadcast_group_call(
                self.online_presence_watchers_group(),
                "UserPresenceUpdated",
                user_id,
                data,
            ),
            self.broadcast_group_call(
                self.friend_presence_watchers_group(user_id),
                "FriendPresenceUpdated",
                user_id,
                data,
            ),
        }

    @staticmethod
    def friend_presence_watchers_group(user_id: int):
        return f"metadata:friend-presence-watchers:{user_id}"

    @override
    async def _clean_state(self, state: MetadataClientState) -> None:
        user_id = int(state.connection_id)

        if state.pushable:
            await asyncio.gather(*self.broadcast_tasks(user_id, None))

        async with with_db() as session:
            async with session.begin():
                user = (await session.exec(select(User).where(User.id == int(state.connection_id)))).one()
                user.last_visit = utcnow()
                await session.commit()

    @override
    def create_state(self, client: Client) -> MetadataClientState:
        return MetadataClientState(
            connection_id=client.connection_id,
            connection_token=client.connection_token,
        )

    async def on_client_connect(self, client: Client) -> None:
        user_id = int(client.connection_id)
        store = self.get_or_create_state(client)

        # CRITICAL FIX: Set online status IMMEDIATELY upon connection
        # This matches the C# official implementation behavior
        store.status = OnlineStatus.ONLINE
        logger.info(f"[MetadataHub] Set user {user_id} status to ONLINE upon connection")

        async with with_db() as session:
            async with session.begin():
                friends = (
                    await session.exec(
                        select(Relationship.target_id).where(
                            Relationship.user_id == user_id,
                            Relationship.type == RelationshipType.FOLLOW,
                        )
                    )
                ).all()
                tasks = []
                for friend_id in friends:
                    self.groups.setdefault(self.friend_presence_watchers_group(friend_id), set()).add(client)
                    if (friend_state := self.state.get(friend_id)) and friend_state.pushable:
                        tasks.append(
                            self.broadcast_group_call(
                                self.friend_presence_watchers_group(friend_id),
                                "FriendPresenceUpdated",
                                friend_id,
                                friend_state.for_push if friend_state.pushable else None,
                            )
                        )
                await asyncio.gather(*tasks)

                daily_challenge_room = (
                    await session.exec(
                        select(Room).where(
                            col(Room.ends_at) > utcnow(),
                            Room.category == RoomCategory.DAILY_CHALLENGE,
                        )
                    )
                ).first()
                if daily_challenge_room:
                    await self.call_noblock(
                        client,
                        "DailyChallengeUpdated",
                        DailyChallengeInfo(
                            room_id=daily_challenge_room.id,
                        ),
                    )

        # CRITICAL FIX: Immediately broadcast the user's online status to all watchers
        # This ensures the user appears as "currently online" right after connection
        # Similar to the C# implementation's immediate broadcast logic
        online_presence_tasks = self.broadcast_tasks(user_id, store)
        if online_presence_tasks:
            await asyncio.gather(*online_presence_tasks)
            logger.info(f"[MetadataHub] Broadcasted online status for user {user_id} to watchers")

        # Also send the user's own presence update to confirm online status
        await self.call_noblock(
            client,
            "UserPresenceUpdated",
            user_id,
            store.for_push,
        )
        logger.info(f"[MetadataHub] User {user_id} is now ONLINE and visible to other clients")

    async def UpdateStatus(self, client: Client, status: int) -> None:
        status_ = OnlineStatus(status)
        user_id = int(client.connection_id)
        store = self.get_or_create_state(client)
        if store.status is not None and store.status == status_:
            return
        store.status = OnlineStatus(status_)
        tasks = self.broadcast_tasks(user_id, store)
        tasks.add(
            self.call_noblock(
                client,
                "UserPresenceUpdated",
                user_id,
                store.for_push,
            )
        )
        await asyncio.gather(*tasks)

    async def UpdateActivity(self, client: Client, activity: UserActivity | None) -> None:
        user_id = int(client.connection_id)
        store = self.get_or_create_state(client)
        store.activity = activity
        tasks = self.broadcast_tasks(user_id, store)
        tasks.add(
            self.call_noblock(
                client,
                "UserPresenceUpdated",
                user_id,
                store.for_push,
            )
        )
        await asyncio.gather(*tasks)

    async def BeginWatchingUserPresence(self, client: Client) -> None:
        # Critical fix: Send all currently online users to the new watcher
        # Must use for_push to get the correct UserPresence format
        await asyncio.gather(
            *[
                self.call_noblock(
                    client,
                    "UserPresenceUpdated",
                    user_id,
                    store.for_push,  # Fixed: use for_push instead of store
                )
                for user_id, store in self.state.items()
                if store.pushable
            ]
        )
        self.add_to_group(client, self.online_presence_watchers_group())
        logger.info(
            f"[MetadataHub] Client {client.connection_id} now watching user presence, "
            f"sent {len([s for s in self.state.values() if s.pushable])} online users"
        )

    async def EndWatchingUserPresence(self, client: Client) -> None:
        self.remove_from_group(client, self.online_presence_watchers_group())

    async def notify_room_score_processed(self, event: MultiplayerRoomScoreSetEvent):
        await self.broadcast_group_call(self.room_watcher_group(event.room_id), "MultiplayerRoomScoreSet", event)

    async def BeginWatchingMultiplayerRoom(self, client: Client, room_id: int):
        self.add_to_group(client, self.room_watcher_group(room_id))
        await self.subscriber.subscribe_room_score(room_id, client.user_id)
        stats = self.get_daily_challenge_stats(room_id)
        await self.update_daily_challenge_stats(stats)
        return list(stats.playlist_item_stats.values())

    async def update_daily_challenge_stats(self, stats: MultiplayerRoomStats) -> None:
        async with with_db() as session:
            playlist_ids = (
                await session.exec(
                    select(Playlist.id).where(
                        Playlist.room_id == stats.room_id,
                    )
                )
            ).all()
            for playlist_id in playlist_ids:
                item = stats.playlist_item_stats.get(playlist_id, None)
                if item is None:
                    item = MultiplayerPlaylistItemStats(
                        playlist_item_id=playlist_id,
                        total_score_distribution=[0] * TOTAL_SCORE_DISTRIBUTION_BINS,
                        cumulative_score=0,
                        last_processed_score_id=0,
                    )
                stats.playlist_item_stats[playlist_id] = item
                last_processed_score_id = item.last_processed_score_id
                scores = (
                    await session.exec(
                        select(PlaylistBestScore).where(
                            PlaylistBestScore.room_id == stats.room_id,
                            PlaylistBestScore.playlist_id == playlist_id,
                            PlaylistBestScore.score_id > last_processed_score_id,
                            col(PlaylistBestScore.score).has(col(Score.passed).is_(True)),
                        )
                    )
                ).all()
                if len(scores) == 0:
                    continue

                async with self._lock:
                    if item.last_processed_score_id == last_processed_score_id:
                        totals = defaultdict(int)
                        for score in scores:
                            bin_index = int(
                                clamp(
                                    math.floor(score.total_score / 100000),
                                    0,
                                    TOTAL_SCORE_DISTRIBUTION_BINS - 1,
                                )
                            )
                            totals[bin_index] += 1

                        item.cumulative_score += sum(score.total_score for score in scores)

                        for j in range(TOTAL_SCORE_DISTRIBUTION_BINS):
                            item.total_score_distribution[j] += totals.get(j, 0)

                        if scores:
                            item.last_processed_score_id = max(score.score_id for score in scores)

    async def EndWatchingMultiplayerRoom(self, client: Client, room_id: int):
        self.remove_from_group(client, self.room_watcher_group(room_id))
        await self.subscriber.unsubscribe_room_score(room_id, client.user_id)
