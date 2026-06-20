"""
The realtime state management and event broadcasting service for multiplayer rooms.
This acts as a compatibility layer for osu-server-spectator using Redis instead of SignalR.
"""

import asyncio
from contextlib import suppress
from datetime import datetime
import json
from typing import Any
import uuid

from app.database import ChatChannel, ChatMessage, Room, User
from app.dependencies.database import get_redis, with_db
from app.log import log
from app.models.mp_messages import MultiplayerCallbackDetails, MultiplayerCallbackMessage
from app.utils import safe_json_dumps

import redis.asyncio as redis

logger = log("MultiplayerTask")


class MultiplayerTaskAwaiter:
    """Manages the communication process with the spectator server using tasks."""

    def __init__(self, default_timeout: float = 10.0):
        self._futures: dict[str, asyncio.Future[MultiplayerCallbackMessage]] = {}
        self._default_timeout = default_timeout
        self._lock = asyncio.Lock()

    async def create_task(self, task_id: str | None = None) -> str:
        """Creates a new async task.

        Args:
            task_id (str | None): Optional task ID to use. Defaults to UUID4 if not given.

        Returns:
            str: The ID of the new task.
        """
        tid = task_id or str(uuid.uuid4())
        async with self._lock:
            # 清理已存在的同名任务（防御性）
            if tid in self._futures and not self._futures[tid].done():
                self._futures[tid].cancel()

            self._futures[tid] = asyncio.get_event_loop().create_future()
        return tid

    async def wait_for_result(
        self,
        task_id: str,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> MultiplayerCallbackMessage:
        """Wait for the result of a specific task.

        Returns:
            MultiplayerCallbackMessage: The callback event message from the spectator server.
            If the task runs out of time, return a placeholder message with the timeout prompt.
        """
        future = self._futures.get(task_id)
        if not future:
            raise ValueError(f"Task {task_id} not found")

        try:
            result = await asyncio.wait_for(future, timeout=timeout or self._default_timeout)
            return result
        except TimeoutError:
            logger.warning(f"Task {task_id} timed out")
            return MultiplayerCallbackMessage(
                id="0",
                type="TaskResult",
                success=False,
                message="Server-side request timeout. Please try again later.",
                details=MultiplayerCallbackDetails(),
            )
        finally:
            # 清理
            async with self._lock:
                self._futures.pop(task_id, None)

    def resolve_task(self, task_id: str, result: MultiplayerCallbackMessage) -> bool:
        """Sets a specified task as resolved with given callback message.

        Args:
            task_id (str): The task ID.
            result (MultiplayerCallbackMessage): The callback message as a result.

        Returns:
            bool: `True` if the task was resolved, `False` otherwise.
        """
        future = self._futures.get(task_id)
        if not future or future.done():
            return False

        future.set_result(result)
        return True


# 全局实例
task_awaiter = MultiplayerTaskAwaiter(default_timeout=10.0)


class MultiplayerEventDispatcher:
    """Send multiplayer events to the spectator server and wait for callbacks."""

    def __init__(self, redis_client: redis.Redis | None = None):
        self.redis = redis_client or get_redis()
        self.channel_prefix = "osu-channel:"
        self.pubsub = None
        self.handlers: dict[str, Any] = {}
        self._running = False
        self._listen_task: asyncio.Task | None = None

    def register_handler(self, event_type: str, handler) -> None:
        """Set to handle a message of specific event type with the given handler."""
        self.handlers[event_type] = handler
        logger.info(f"Registered handler for event type: {event_type}")

    async def start(self) -> None:
        """启动 Redis 订阅"""
        if self._running:
            return

        try:
            self.pubsub = self.redis.pubsub()
            await self.pubsub.psubscribe("osu-channel:room:*")
            await self.pubsub.psubscribe("osu-channel:callback:*")
            self._running = True
            logger.info("Started subscribing to multiplayer Redis events from spectator")

            self._listen_task = asyncio.create_task(self._listen())
        except Exception as e:
            logger.error(f"Error starting multiplayer redis subscriber: {e}")
            raise

    async def stop(self) -> None:
        """停止 Redis 订阅"""
        self._running = False
        if self.pubsub:
            await self.pubsub.unsubscribe()
            self.pubsub = None
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._listen_task
            self._listen_task = None
        logger.info("Stopped multiplayer redis subscription")

    async def _listen(self) -> None:
        """The main event listening and handling routine."""
        if not self.pubsub:
            return

        async for message in self.pubsub.listen():
            if not self._running:
                break

            if message["type"] != "pmessage":
                continue

            channel = message["channel"].decode()
            try:
                raw_data = message.get("data")
                if isinstance(raw_data, bytes):
                    raw_data = raw_data.decode()

                if not isinstance(raw_data, str):
                    continue

                data = json.loads(raw_data)

                # Handle callback events
                if channel.startswith("osu-channel:callback:"):
                    callback = MultiplayerCallbackMessage.model_validate(data)

                    if callback.id and callback.type == "TaskResult":
                        # Record error in log
                        if not callback.success:
                            logger.warning(
                                f"Multiplayer event task {callback.id} returned with error: {callback.message}"
                            )

                        task_awaiter.resolve_task(callback.id, callback)
                    continue

                # Other events if possible
                event_type = data["type"]
                if event_type in self.handlers:
                    await self.handlers[event_type](data)

            except Exception as e:
                logger.warning(f"Error processing Redis message: {e}")

    async def _publish_with_callback(
        self,
        room_id: int,
        message: dict,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> MultiplayerCallbackMessage:
        """Publish an event message and except a callback from the spectator side.

        Examples:
            ```
                result = await subscriber.publish_with_callback(
                    "osu-channel:room:12345",
                    {"type": "StartReminderTimer", "seconds": 30, ...}
                )
            ```
        """
        task_id = await task_awaiter.create_task()

        message["id"] = task_id

        await self.redis.publish(f"{self.channel_prefix}room:{room_id}", json.dumps(message))
        return await task_awaiter.wait_for_result(task_id, timeout)

    async def handle_countdown_tick(self, room_id: int, _countdown_id: int, seconds: float) -> None:
        """处理倒计时事件，在特定秒数发送 BanchoBot 消息"""
        chat_threshold_seconds = [60, 30, 10, 5, 4, 3, 2, 1, 0]

        if int(seconds) not in chat_threshold_seconds:
            return

        try:
            async with with_db() as session:
                room = await session.get(Room, room_id)
                if not room:
                    logger.debug(f"Room {room_id} not found for countdown tick")
                    return

                if room.channel_id <= 0:
                    logger.debug(f"Room {room_id} has no valid channel_id")
                    return

                chat_channel = await session.get(ChatChannel, room.channel_id)
                if not chat_channel:
                    logger.debug(f"Chat channel {room.channel_id} not found for room {room_id}")
                    return

                bancho_bot = await session.get(User, 1)
                if not bancho_bot:
                    logger.warning("BanchoBot (user_id=1) not found in database")
                    return

                message = "Match is starting!" if seconds == 0 else f"{int(seconds)} seconds remaining."

                new_chat = ChatMessage(
                    channel_id=room.channel_id,
                    sender_id=1,
                    content=message,
                    timestamp=datetime.utcnow(),
                )

                session.add(new_chat)
                await session.commit()

                logger.info(f"Sent countdown message to room {room_id}: {message}")

                from app.service.redis_message_system import redis_message_system

                await redis_message_system._initialize_message_counter()

        except Exception as e:
            logger.error(f"Error handling countdown tick for room {room_id}: {e}")

    async def _publish(self, room_id: int, event_data: dict[str, Any]) -> None:
        """发布通用事件"""
        try:
            msg = safe_json_dumps(event_data)
            channel = f"{self.channel_prefix}room:{room_id}"
            await self.redis.publish(channel, msg)
            logger.debug(f"Published multiplayer event to {channel}: {event_data.get('type')}")
        except Exception as e:
            logger.warning(f"Failed to publish multiplayer event for room {room_id}: {e}")

    async def post_get_referees(self, room_id: int, by_user_id: int):
        """从 spec 端请求裁判 ID 列表"""
        return await self._publish_with_callback(
            room_id,
            {
                "type": "ListReferees",
                "by": by_user_id,
            },
        )

    async def post_transfer_host(self, room_id: int, new_host_user_id: int, by_user_id: int):
        """通知客户端房主变更"""
        return await self._publish_with_callback(
            room_id,
            {
                "type": "TransferHost",
                "target": new_host_user_id,
                "by": by_user_id,
            },
        )

    async def post_set_lock_state(self, room_id: int, locked: bool, by_user_id: int):
        """
        请求 spectator 按 RefereeHub 语义执行 SetLockState。
        对应 osu.Game.Online.Multiplayer.SetLockStateRequest。
        """
        return await self._publish_with_callback(
            room_id,
            {
                "type": "SetLockState",
                "room_state": {
                    "locked": locked,
                },
                "by": by_user_id,
            },
        )

    async def post_change_room_settings(
        self,
        room_id: int,
        by_user_id: int,
        *,
        name: str | None = None,
        password: str | None = None,
        match_type: int | None = None,
        max_participants: int | None = None,
    ):
        """请求 spectator 按 RefereeHub 语义执行 ChangeRoomSettings（!mp name / !mp password / !mp set）。"""

        payload: dict[str, Any] = {
            "type": "ChangeRoomSettings",
            "by": by_user_id,
            "room_settings": {},
        }

        room_settings = payload["room_settings"]

        if name is not None:
            room_settings["name"] = name

        if password is not None:
            room_settings["password"] = password

        if match_type is not None:
            room_settings["type"] = match_type

        if max_participants is not None:
            room_settings["max_participants"] = max_participants

        return await self._publish_with_callback(room_id, payload)

    async def post_set_slot(self, room_id: int, user_id: int, by_user_id: int, slot_id: int):
        return await self._publish_with_callback(
            room_id,
            {
                "type": "SetUserSlot",
                "target": user_id,
                "by": by_user_id,
                "user_state": {
                    "slot_id": slot_id,
                },
            },
        )

    async def post_change_team(
        self,
        room_id: int,
        user_id: int,
        by_user_id: int,
        team_id: int,
    ):
        """通知客户端用户队伍状态变更"""
        return await self._publish_with_callback(
            room_id,
            {
                "type": "ChangeTeam",
                "target": user_id,
                "by": by_user_id,
                "user_state": {
                    "team_id": team_id,
                },
            },
        )

    async def post_kick_player(self, room_id: int, user_id: int, by_user_id: int):
        """请求 spectator 按 RefereeHub 语义执行 KickPlayer。"""
        return await self._publish_with_callback(
            room_id,
            {
                "type": "KickPlayer",
                "target": user_id,
                "by": by_user_id,
            },
        )

    async def post_ban_user(self, room_id: int, banned_user_id: int, by_user_id: int):
        """请求 spectator 按 RefereeHub 语义执行 BanUser。"""
        return await self._publish_with_callback(
            room_id,
            {
                "type": "BanUser",
                "target": banned_user_id,
                "by": by_user_id,
            },
        )

    async def post_add_referee(self, room_id: int, target_user_id: int, by_user_id: int):
        """请求 spectator 按 RefereeHub 语义执行 AddReferee。"""
        return await self._publish_with_callback(
            room_id,
            {
                "type": "AddReferee",
                "target": target_user_id,
                "by": by_user_id,
            },
        )

    async def post_remove_referee(self, room_id: int, target_user_id: int, by_user_id: int):
        """请求 spectator 按 RefereeHub 语义执行 RemoveReferee。"""
        return await self._publish_with_callback(
            room_id,
            {
                "type": "RemoveReferee",
                "target": target_user_id,
                "by": by_user_id,
            },
        )

    async def post_start_match(self, room_id: int, countdown_seconds: int | None, by_user_id: int):
        """请求 spectator 启动比赛倒计时（自动启动比赛，互斥于 reminder）。countdown_seconds=None 表示立即开始匹配。"""
        payload: dict[str, Any] = {
            "type": "StartMatch",
            "by": by_user_id,
        }
        if countdown_seconds is not None:
            payload["countdown"] = {"seconds": int(countdown_seconds)}

        return await self._publish_with_callback(room_id, payload)

    async def post_start_reminder_timer(self, room_id: int, countdown_seconds: int, by_user_id: int):
        """请求 spectator 启动纯提醒计时（不启动比赛，仅提醒，互斥于比赛倒计时）。"""
        return await self._publish_with_callback(
            room_id,
            {
                "type": "StartReminderTimer",
                "by": by_user_id,
                "countdown": {"seconds": int(countdown_seconds)},
            },
        )

    async def post_stop_all_countdowns(self, room_id: int, by_user_id: int):
        """请求 spectator 停止所有倒计时（包括 MatchStart 和 ReminderCountdown）。"""
        return await self._publish_with_callback(
            room_id,
            {
                "type": "StopAllCountdowns",
                "by": by_user_id,
            },
        )

    async def post_abort_match(self, room_id: int, by_user_id: int):
        """请求 spectator 中止正在进行的比赛。"""
        return await self._publish_with_callback(
            room_id,
            {
                "type": "AbortMatch",
                "by": by_user_id,
            },
        )

    async def post_close_room(self, room_id: int, by_user_id: int):
        return await self._publish_with_callback(
            room_id,
            {
                "type": "CloseRoom",
                "by": by_user_id,
            },
        )

    async def post_invite_user(self, room_id: int, target_user_id: int, by_user_id: int):
        return await self._publish_with_callback(
            room_id,
            {
                "type": "InviteUser",
                "target": target_user_id,
                "by": by_user_id,
            },
        )

    async def post_change_beatmap(
        self,
        room_id: int,
        by_user_id: int,
        beatmap_id: int,
        ruleset_id: int = 0,
        mod_acronyms: list[str] | None = None,
    ):
        """请求 spectator 修改房间当前 playlist item"""
        payload: dict[str, Any] = {
            "type": "ChangeBeatmap",
            "by": by_user_id,
            "map_settings": {
                "beatmap_id": beatmap_id,
                "ruleset_id": ruleset_id,
            },
        }
        if mod_acronyms:
            payload["map_settings"]["mods"] = mod_acronyms

        return await self._publish_with_callback(room_id, payload)


multiplayer_event_dispatcher = MultiplayerEventDispatcher()
