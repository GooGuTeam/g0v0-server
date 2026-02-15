"""WebSocket notification server for real-time chat.

This module implements a WebSocket-based notification server that handles
real-time chat messaging, channel management, and user presence.
"""

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
from app.helpers import bg_tasks, safe_json_dumps
from app.log import log
from app.models.chat import ChatEvent
from app.models.events.chat import JoinChannelEvent, LeaveChannelEvent, MessageSentEvent
from app.models.notification import NotificationDetail
from app.plugins import event_hub
from app.service.subscribers.chat import ChatSubscriber

from fastapi import APIRouter, Depends, Header, Query, WebSocket, WebSocketDisconnect
from fastapi.security import SecurityScopes
from fastapi.websockets import WebSocketState
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = log("NotificationServer")


class ChatServer:
    """WebSocket chat server managing real-time connections.

    Handles WebSocket connections, channel subscriptions, message broadcasting,
    and user presence tracking.

    Attributes:
        connect_client: Dict mapping user IDs to their WebSocket connections.
        channels: Dict mapping channel IDs to lists of user IDs.
        redis: Redis client for message persistence.
    """

    def __init__(self):
        """Initialize the chat server."""
        self.connect_client: dict[int, WebSocket] = {}
        self.channels: dict[int, list[int]] = {}
        self.redis: Redis = redis_message_client

        self.tasks: set[asyncio.Task] = set()
        self.ChatSubscriber = ChatSubscriber()
        self.ChatSubscriber.chat_server = self
        self._subscribed = False

    def connect(self, user_id: int, client: WebSocket):
        """Register a WebSocket connection for a user.

        Args:
            user_id: The user's ID.
            client: The WebSocket connection.
        """
        self.connect_client[user_id] = client

    def get_user_joined_channel(self, user_id: int) -> list[int]:
        """Get list of channel IDs a user has joined.

        Args:
            user_id: The user's ID.

        Returns:
            List of channel IDs the user is in.
        """
        return [channel_id for channel_id, users in self.channels.items() if user_id in users]

    async def disconnect(self, user: User, session: AsyncSession):
        """Handle user disconnection and cleanup.

        Args:
            user: The disconnecting user.
            session: Database session.
        """
        user_id = user.id
        if user_id in self.connect_client:
            del self.connect_client[user_id]

        # Create a copy of channel ID list to avoid modifying dict during iteration
        channel_ids_to_process = []
        for channel_id, channel in self.channels.items():
            if user_id in channel:
                channel_ids_to_process.append(channel_id)

        # Now safely process each channel
        for channel_id in channel_ids_to_process:
            # Check again if user is still in channel (prevent concurrent modification)
            if channel_id in self.channels and user_id in self.channels[channel_id]:
                self.channels[channel_id].remove(user_id)
                # Use explicit query to avoid lazy loading
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
        """Send an event to a specific client.

        Args:
            client: WebSocket connection or user ID.
            event: Chat event to send.
        """
        if isinstance(client, int):
            client_ = self.connect_client.get(client)
            if client_ is None:
                return
            client = client_
        if client.client_state == WebSocketState.CONNECTED:
            await client.send_text(safe_json_dumps(event))

    async def broadcast(self, channel_id: int, event: ChatEvent):
        """Broadcast an event to all users in a channel.

        Args:
            channel_id: The channel ID to broadcast to.
            event: Chat event to broadcast.
        """
        users_in_channel = self.channels.get(channel_id, [])
        logger.info(f"Broadcasting to channel {channel_id}, users: {users_in_channel}")

        # If no users in channel, check if it's a multiplayer channel
        if not users_in_channel:
            try:
                async with with_db() as session:
                    channel = await session.get(ChatChannel, channel_id)
                    if channel and channel.type == ChannelType.MULTIPLAYER:
                        logger.warning(
                            f"No users in multiplayer channel {channel_id}, message will not be delivered to anyone"
                        )
                        # For multiplayer rooms, this may be normal (all users left the room)
                        # But we still log this for debugging
            except Exception as e:
                logger.error(f"Failed to check channel type for {channel_id}: {e}")

        for user_id in users_in_channel:
            await self.send_event(user_id, event)
            logger.debug(f"Sent event to user {user_id} in channel {channel_id}")

    async def mark_as_read(self, channel_id: int, user_id: int, message_id: int):
        """Mark a message as read for a user.

        Args:
            channel_id: The channel ID.
            user_id: The user's ID.
            message_id: The message ID to mark as read.
        """
        await self.redis.set(f"chat:{channel_id}:last_read:{user_id}", message_id)

    async def send_message_to_channel(self, message: ChatMessageDict, is_bot_command: bool = False):
        """Send a message to a channel and update read state.

        Args:
            message: The message data to send.
            is_bot_command: Whether this is a bot command (sent only to sender).
        """
        logger.info(
            f"Sending message to channel {message['channel_id']}, message_id: "
            f"{message['message_id']}, is_bot_command: {is_bot_command}"
        )

        event_hub.emit(
            MessageSentEvent(
                sender_id=message["sender_id"],
                channel_id=message["channel_id"],
                message_content=message["content"],
                timestamp=message["timestamp"],
                type=message["type"],
                is_bot_command=is_bot_command,
            )
        )

        event = ChatEvent(
            event="chat.message.new",
            data={"messages": [message], "users": [message["sender"]]},  # pyright: ignore[reportTypedDictNotRequiredAccess]
        )
        if is_bot_command:
            logger.info(f"Sending bot command to user {message['sender_id']}")
            bg_tasks.add_task(self.send_event, message["sender_id"], event)
        else:
            # Always broadcast message, regardless of temporary or real ID
            logger.info(f"Broadcasting message to all users in channel {message['channel_id']}")
            bg_tasks.add_task(
                self.broadcast,
                message["channel_id"],
                event,
            )

        # Only update last message for real message IDs (positive and non-zero)
        # Redis message system generates positive IDs, so this should work normally
        if message["message_id"] and message["message_id"] > 0:
            await self.mark_as_read(message["channel_id"], message["sender_id"], message["message_id"])
            await self.redis.set(f"chat:{message['channel_id']}:last_msg", message["message_id"])
            logger.info(f"Updated last message ID for channel {message['channel_id']} to {message['message_id']}")
        else:
            logger.debug(f"Skipping last message update for message ID: {message['message_id']}")

    async def batch_join_channel(self, users: list[User], channel: ChatChannel):
        """Add multiple users to a channel at once.

        Args:
            users: List of users to add.
            channel: The channel to join.
        """
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
        """Add a user to a channel and notify them.

        Args:
            user: The user joining.
            channel: The channel to join.

        Returns:
            The channel data sent to the user.
        """
        user_id = user.id
        channel_id = channel.channel_id

        event_hub.emit(
            JoinChannelEvent(
                user_id=user_id,
                channel_id=channel_id,
            )
        )

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
        """Remove a user from a channel and notify them.

        Args:
            user: The user leaving.
            channel: The channel to leave.
        """
        user_id = user.id
        channel_id = channel.channel_id

        event_hub.emit(LeaveChannelEvent(user_id=user_id, channel_id=channel_id))

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
        """Join a room channel (called from external multiplayer system).

        Args:
            channel_id: The channel ID to join.
            user_id: The user ID joining.
        """
        async with with_db() as session:
            # Use explicit query to avoid lazy loading
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
        """Leave a room channel (called from external multiplayer system).

        Args:
            channel_id: The channel ID to leave.
            user_id: The user ID leaving.
        """
        async with with_db() as session:
            # Use explicit query to avoid lazy loading
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
        """Create and send a new private notification.

        Args:
            detail: The notification details to send.
        """
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
    """Listen for WebSocket disconnect or stop events.

    Args:
        ws: The WebSocket connection.
        user_id: The connected user's ID.
        factory: Database session factory.
    """
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
    token: Annotated[str | None, Query(description="Auth token, supports passing via URL parameter")] = None,
    access_token: Annotated[str | None, Query(description="Access token, supports passing via URL parameter")] = None,
    authorization: Annotated[str | None, Header(description="Bearer auth header")] = None,
):
    """WebSocket endpoint for real-time chat notifications.

    Args:
        websocket: The WebSocket connection.
        factory: Database session factory.
        token: Optional auth token from query parameter.
        access_token: Optional access token from query parameter.
        authorization: Optional Bearer auth header.
    """
    if not server._subscribed:
        server._subscribed = True
        await server.ChatSubscriber.start_subscribe()

    async for session in factory():
        # Prioritize token from query parameters, supports both token and access_token parameter names
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
        # Use explicit query to avoid lazy loading
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_id == 1))).first()
        if db_channel is not None:
            await server.join_channel(user, db_channel)

        await _listen_stop(websocket, user_id, factory)
