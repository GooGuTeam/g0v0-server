import asyncio
from typing import Annotated, overload

from app.database import ChatMessageDict
from app.database.chat import ChannelType, ChatChannel, ChatChannelDict, ChatChannelModel
from app.database.notification import UserNotification, insert_notification
from app.database.user import User
from app.dependencies.database import (
    DBFactory,
    Redis,
    get_db_factory,
    redis_message_client,
    with_db,
)
from app.dependencies.user import get_current_user_and_token
from app.log import log
from app.models.chat import ChatEvent
from app.models.notification import NotificationDetail
from app.service.subscribers.chat import ChatSubscriber
from app.utils import bg_tasks, safe_json_dumps

from fastapi import APIRouter, Depends, Header, Query, WebSocket, WebSocketDisconnect
from fastapi.security import SecurityScopes
from fastapi.websockets import WebSocketState
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = log("NotificationServer")


class ChatServer:
    def __init__(self):
        self.connect_client: dict[int, WebSocket] = {}
        self.channels: dict[int, list[int]] = {}
        self.redis: Redis = redis_message_client

        self.tasks: set[asyncio.Task] = set()
        self.ChatSubscriber = ChatSubscriber()
        self.ChatSubscriber.chat_server = self
        self._subscribed = False

    def connect(self, user_id: int, client: WebSocket):
        self.connect_client[user_id] = client

    def get_user_joined_channel(self, user_id: int) -> list[int]:
        return [channel_id for channel_id, users in self.channels.items() if user_id in users]

    async def disconnect(self, user: User, session: AsyncSession):
        user_id = user.id
        if user_id in self.connect_client:
            del self.connect_client[user_id]

        # 创建频道ID列表的副本以避免在迭代过程中修改字典
        channel_ids_to_process = []
        for channel_id, channel in self.channels.items():
            if user_id in channel:
                channel_ids_to_process.append(channel_id)

        # 现在安全地处理每个频道
        for channel_id in channel_ids_to_process:
            # 再次检查用户是否仍在频道中（防止并发修改）
            if channel_id in self.channels and user_id in self.channels[channel_id]:
                self.channels[channel_id].remove(user_id)
                # 使用明确的查询避免延迟加载
                db_channel = (
                    await session.exec(select(ChatChannel).where(ChatChannel.channel_id == channel_id))
                ).first()
                if db_channel:
                    await self.leave_channel(user, db_channel)

    @overload
    async def send_event(self, client: int, event: ChatEvent): ...

    @overload
    async def send_event(self, client: WebSocket, event: ChatEvent): ...

    async def send_event(self, client: WebSocket | int, event: ChatEvent):
        if isinstance(client, int):
            client_ = self.connect_client.get(client)
            if client_ is None:
                return
            client = client_
        if client.client_state == WebSocketState.CONNECTED:
            await client.send_text(safe_json_dumps(event))

    async def broadcast(self, channel_id: int, event: ChatEvent):
        users_in_channel = self.channels.get(channel_id, [])
        logger.info(f"Broadcasting to channel {channel_id}, users: {users_in_channel}")

        # 如果频道中没有用户，检查是否是多人游戏频道
        if not users_in_channel:
            try:
                async with with_db() as session:
                    channel = await session.get(ChatChannel, channel_id)
                    if channel and channel.type == ChannelType.MULTIPLAYER:
                        logger.warning(
                            f"No users in multiplayer channel {channel_id}, message will not be delivered to anyone"
                        )
                        # 对于多人游戏房间，这可能是正常的（用户都离开了房间）
                        # 但我们仍然记录这个情况以便调试
            except Exception as e:
                logger.error(f"Failed to check channel type for {channel_id}: {e}")

        for user_id in users_in_channel:
            await self.send_event(user_id, event)
            logger.debug(f"Sent event to user {user_id} in channel {channel_id}")

    async def mark_as_read(self, channel_id: int, user_id: int, message_id: int):
        await self.redis.set(f"chat:{channel_id}:last_read:{user_id}", message_id)

    async def send_message_to_channel(self, message: ChatMessageDict, is_bot_command: bool = False):
        logger.info(
            f"Sending message to channel {message['channel_id']}, message_id: "
            f"{message['message_id']}, is_bot_command: {is_bot_command}"
        )

        event = ChatEvent(
            event="chat.message.new",
            data={"messages": [message], "users": [message["sender"]]},  # pyright: ignore[reportTypedDictNotRequiredAccess]
        )
        if is_bot_command:
            logger.info(f"Sending bot command to user {message['sender_id']}")
            bg_tasks.add_task(self.send_event, message["sender_id"], event)
        else:
            # 总是广播消息，无论是临时ID还是真实ID
            logger.info(f"Broadcasting message to all users in channel {message['channel_id']}")
            bg_tasks.add_task(
                self.broadcast,
                message["channel_id"],
                event,
            )

        # 只有真实消息 ID（正数且非零）才进行标记已读和设置最后消息
        # Redis 消息系统生成的ID都是正数，所以这里应该都能正常处理
        if message["message_id"] and message["message_id"] > 0:
            await self.mark_as_read(message["channel_id"], message["sender_id"], message["message_id"])
            await self.redis.set(f"chat:{message['channel_id']}:last_msg", message["message_id"])
            logger.info(f"Updated last message ID for channel {message['channel_id']} to {message['message_id']}")
        else:
            logger.debug(f"Skipping last message update for message ID: {message['message_id']}")

    async def batch_join_channel(self, users: list[User], channel: ChatChannel):
        channel_id = channel.channel_id

        not_joined = []

        if channel_id not in self.channels:
            self.channels[channel_id] = []
        for user in users:
            if user.id not in self.channels[channel_id]:
                self.channels[channel_id].append(user.id)
                not_joined.append(user)

        for user in not_joined:
            channel_resp = await ChatChannelModel.transform(
                channel, user=user, server=server, includes=ChatChannel.LISTING_INCLUDES
            )
            await self.send_event(
                user.id,
                ChatEvent(
                    event="chat.channel.join",
                    data=channel_resp,  # pyright: ignore[reportArgumentType]
                ),
            )

    async def join_channel(self, user: User, channel: ChatChannel) -> ChatChannelDict:
        user_id = user.id
        channel_id = channel.channel_id

        if channel_id not in self.channels:
            self.channels[channel_id] = []
        if user_id not in self.channels[channel_id]:
            self.channels[channel_id].append(user_id)

        channel_resp: ChatChannelDict = await ChatChannelModel.transform(
            channel, user=user, server=server, includes=ChatChannel.LISTING_INCLUDES
        )

        await self.send_event(
            user_id,
            ChatEvent(
                event="chat.channel.join",
                data=channel_resp,  # pyright: ignore[reportArgumentType]
            ),
        )

        return channel_resp

    async def leave_channel(self, user: User, channel: ChatChannel) -> None:
        user_id = user.id
        channel_id = channel.channel_id

        if channel_id in self.channels and user_id in self.channels[channel_id]:
            self.channels[channel_id].remove(user_id)

        if (c := self.channels.get(channel_id)) is not None and not c:
            del self.channels[channel_id]

        channel_resp = await ChatChannelModel.transform(
            channel, user=user, server=server, includes=ChatChannel.LISTING_INCLUDES
        )
        await self.send_event(
            user_id,
            ChatEvent(
                event="chat.channel.part",
                data=channel_resp,  # pyright: ignore[reportArgumentType]
            ),
        )

    async def join_room_channel(self, channel_id: int, user_id: int):
        async with with_db() as session:
            # 使用明确的查询避免延迟加载
            db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_id == channel_id))).first()
            if db_channel is None:
                logger.warning(f"Attempted to join non-existent channel {channel_id} by user {user_id}")
                return

            user = await session.get(User, user_id)
            if user is None:
                logger.warning(f"Attempted to join channel {channel_id} by non-existent user {user_id}")
                return

            logger.info(f"User {user_id} joining channel {channel_id} (type: {db_channel.type.value})")
            await self.join_channel(user, db_channel)

    async def leave_room_channel(self, channel_id: int, user_id: int):
        async with with_db() as session:
            # 使用明确的查询避免延迟加载
            db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_id == channel_id))).first()
            if db_channel is None:
                logger.warning(f"Attempted to leave non-existent channel {channel_id} by user {user_id}")
                return

            user = await session.get(User, user_id)
            if user is None:
                logger.warning(f"Attempted to leave channel {channel_id} by non-existent user {user_id}")
                return

            logger.info(f"User {user_id} leaving channel {channel_id} (type: {db_channel.type.value})")
            await self.leave_channel(user, db_channel)

    async def new_private_notification(self, detail: NotificationDetail):
        async with with_db() as session:
            id = await insert_notification(session, detail)
            users = (await session.exec(select(UserNotification).where(UserNotification.notification_id == id))).all()
            for user_notification in users:
                data = user_notification.notification.model_dump()
                data["is_read"] = user_notification.is_read
                data["details"] = user_notification.notification.details
                await server.send_event(
                    user_notification.user_id,
                    ChatEvent(
                        event="new",
                        data=data,
                    ),
                )


server = ChatServer()

chat_router = APIRouter(include_in_schema=False)


async def _listen_stop(ws: WebSocket, user_id: int, factory: DBFactory):
    try:
        while True:
            packets = await ws.receive_json()
            if packets.get("event") == "chat.end":
                async for session in factory():
                    user = await session.get(User, user_id)
                    if user is None:
                        break
                    await server.disconnect(user, session)
                await ws.close(code=1000)
                break
    except WebSocketDisconnect as e:
        logger.info(f"Client {user_id} disconnected: {e.code}, {e.reason}")
    except RuntimeError as e:
        if "disconnect message" in str(e):
            logger.info(f"Client {user_id} closed the connection.")
        else:
            logger.exception(f"RuntimeError in client {user_id}: {e}")
    except Exception:
        logger.exception(f"Error in client {user_id}")


@chat_router.websocket("/notification-server")
async def chat_websocket(
    websocket: WebSocket,
    factory: Annotated[DBFactory, Depends(get_db_factory)],
    token: Annotated[str | None, Query(description="认证令牌，支持通过URL参数传递")] = None,
    access_token: Annotated[str | None, Query(description="访问令牌，支持通过URL参数传递")] = None,
    authorization: Annotated[str | None, Header(description="Bearer认证头")] = None,
):
    if not server._subscribed:
        server._subscribed = True
        await server.ChatSubscriber.start_subscribe()

    async for session in factory():
        # 优先使用查询参数中的token，支持token或access_token参数名
        auth_token = token or access_token
        if not auth_token and authorization:
            auth_token = authorization.removeprefix("Bearer ")

        if not auth_token:
            await websocket.close(code=1008, reason="Missing authentication token")
            return

        if (
            user_and_token := await get_current_user_and_token(
                session, SecurityScopes(scopes=["chat.read"]), token_pw=auth_token
            )
        ) is None:
            await websocket.close(code=1008, reason="Invalid or expired token")
            return

        await websocket.accept()
        login = await websocket.receive_json()
        if login.get("event") != "chat.start":
            await websocket.close(code=1008)
            return
        user = user_and_token[0]
        user_id = user.id
        server.connect(user_id, websocket)
        # 使用明确的查询避免延迟加载
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_id == 1))).first()
        if db_channel is not None:
            await server.join_channel(user, db_channel)

        await _listen_stop(websocket, user_id, factory)
