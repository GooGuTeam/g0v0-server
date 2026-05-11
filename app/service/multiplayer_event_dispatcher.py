"""
The realtime state management and event broadcasting service for multiplayer rooms.
This acts as a compatibility layer for osu-server-spectator using Redis instead of SignalR.
"""

import asyncio
from contextlib import suppress
from datetime import datetime
from typing import Any

from app.database import ChatChannel, ChatMessage, Room, User
from app.dependencies.database import get_redis, with_db
from app.log import logger
from app.utils import safe_json_dumps

import redis.asyncio as redis


class MultiplayerEventDispatcher:
    """
    通过 osu-channel 向 osu-server-spectator 广播实时事件
    兼容官方 SignalR Hub 的事件协议
    """

    def __init__(self, redis_client: redis.Redis | None = None):
        self.redis = redis_client or get_redis()
        self.channel_prefix = "osu-channel:"
        self.event_channel = "multiplayer:events"
        self.pubsub = None
        self.handlers: dict[str, ...] = {}
        self._running = False
        self._listen_task: asyncio.Task | None = None

    def register_handler(self, event_type: str, handler) -> None:
        """注册事件处理器"""
        self.handlers[event_type] = handler
        logger.info(f"Registered handler for event type: {event_type}")

    async def start_subscribe(self) -> None:
        """启动 Redis 订阅"""
        if self._running:
            return

        try:
            self.pubsub = self.redis.pubsub()
            await self.pubsub.psubscribe("osu-channel:room:*")
            self._running = True
            logger.info("Started subscribing to multiplayer Redis events from spectator")

            self._listen_task = asyncio.create_task(self._listen())
        except Exception as e:
            logger.error(f"Error starting multiplayer redis subscriber: {e}")
            raise

    async def stop_subscribe(self) -> None:
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
        """监听 Redis 消息"""
        if not self.pubsub:
            return

        async for message in self.pubsub.listen():
            if not self._running:
                break

            if message["type"] == "pmessage":
                try:
                    import json
                    data = json.loads(message["data"])
                    event_type = data.get("type")

                    if event_type in self.handlers:
                        handler = self.handlers[event_type]
                        await handler(data)
                    else:
                        logger.debug(f"No handler for event type: {event_type}")
                except Exception as e:
                    logger.warning(f"Error processing Redis message: {e}")

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

    async def post_host_changed(self, room_id: int, new_host_user_id: int) -> None:
        """
        通知客户端主机变更
        对应 SignalR 的 IMultiplayerClient.HostChanged
        """
        await self._publish(
            room_id,
            {
                "type": "HostChanged",
                "userId": new_host_user_id,
            },
        )

    async def post_match_room_state_changed(self, room_id: int, locked: bool) -> None:
        """
        通知客户端房间全局状态变更（TeamVersus 锁状态）
        对应 SignalR 的 IMultiplayerClient.MatchRoomStateChanged
        """
        await self._publish(
            room_id,
            {
                "type": "MatchRoomStateChanged",
                "state": {
                    "locked": locked,
                },
            },
        )

    async def post_set_lock_state(self, room_id: int, locked: bool, by_user_id: int) -> None:
        """
        请求 spectator 按 RefereeHub 语义执行 SetLockState。
        对应 osu.Game.Online.Multiplayer.SetLockStateRequest。
        """
        await self._publish(
            room_id,
            {
                "type": "SetLockState",
                "locked": locked,
                "byUserId": by_user_id,
            },
        )

    async def post_match_user_state_changed(
        self,
        room_id: int,
        user_id: int,
        team_id: int,
    ) -> None:
        """
        通知客户端用户队伍状态变更
        对应 SignalR 的 IMultiplayerClient.MatchUserStateChanged
        team_id: 0 = 红队, 1 = 蓝队
        """
        await self._publish(
            room_id,
            {
                "type": "MatchUserStateChanged",
                "userId": user_id,
                "matchState": {
                    "teamID": team_id,
                },
            },
        )

    async def post_kick_player(self, room_id: int, user_id: int, by_user_id: int) -> None:
        """请求 spectator 按 RefereeHub 语义执行 KickPlayer。"""
        await self._publish(
            room_id,
            {
                "type": "KickPlayer",
                "userId": user_id,
                "byUserId": by_user_id,
            },
        )

    async def post_ban_user(self, room_id: int, banned_user_id: int, by_user_id: int) -> None:
        """请求 spectator 按 RefereeHub 语义执行 BanUser。"""
        await self._publish(
            room_id,
            {
                "type": "BanUser",
                "bannedUserId": banned_user_id,
                "byUserId": by_user_id,
            },
        )

    async def post_add_referee(self, room_id: int, target_user_id: int, by_user_id: int) -> None:
        """请求 spectator 按 RefereeHub 语义执行 AddReferee。"""
        await self._publish(
            room_id,
            {
                "type": "AddReferee",
                "targetUserId": target_user_id,
                "byUserId": by_user_id,
            },
        )

    async def post_remove_referee(self, room_id: int, target_user_id: int, by_user_id: int) -> None:
        """请求 spectator 按 RefereeHub 语义执行 RemoveReferee。"""
        await self._publish(
            room_id,
            {
                "type": "RemoveReferee",
                "targetUserId": target_user_id,
                "byUserId": by_user_id,
            },
        )

    async def post_start_match(self, room_id: int, countdown_seconds: int | None, by_user_id: int) -> None:
        """请求 spectator 启动比赛倒计时（自动启动比赛，互斥于 reminder）。countdown_seconds=None 表示立即开始匹配。"""
        payload = {
            "type": "StartMatch",
            "byUserId": by_user_id,
        }
        if countdown_seconds is not None:
            payload["countdownSeconds"] = int(countdown_seconds)

        await self._publish(room_id, payload)

    async def post_start_reminder_timer(self, room_id: int, countdown_seconds: int, by_user_id: int) -> None:
        """请求 spectator 启动纯提醒计时（不启动比赛，仅提醒，互斥于比赛倒计时）。"""
        await self._publish(
            room_id,
            {
                "type": "StartReminderTimer",
                "countdownSeconds": int(countdown_seconds),
                "byUserId": by_user_id,
            },
        )

    async def post_stop_all_countdowns(self, room_id: int, by_user_id: int) -> None:
        """请求 spectator 停止所有倒计时（包括 MatchStart 和 ReminderCountdown）。"""
        await self._publish(
            room_id,
            {
                "type": "StopAllCountdowns",
                "byUserId": by_user_id,
            },
        )

    async def post_abort_match(self, room_id: int, by_user_id: int) -> None:
        """请求 spectator 中止正在进行的比赛。"""
        await self._publish(
            room_id,
            {
                "type": "AbortMatch",
                "byUserId": by_user_id,
            },
        )


multiplayer_event_dispatcher = MultiplayerEventDispatcher()
