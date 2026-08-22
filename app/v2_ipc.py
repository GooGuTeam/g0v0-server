"""This module implements g0v0 v2 IPC (Inter-Process Communication) for communication between the g0v0 server v2 series.

g0v0 v2 IPC specification:
- Communication is done via Redis pub/sub channels.
- Messages are JSON strings containing the following fields:
  - type:
    - notice: a one-way message that does not expect a response.
    - request: a message that expects a response.
    - response: a message that is sent in reply to a request.
    - error: a message that indicates an error occurred while processing a request.
  - name: a string identifier for the message type, used to route messages to the appropriate handlers.
  - uuid: a unique identifier for the message, used to correlate requests, responses, and errors.
  - source_server: the identifier of the server that sent the message.
  - data: an object containing message-specific data.
- Redis Pub/Sub channel format: g0v0:ipc:<server_identifier>. for this server, <server_identifier> is "lazer".
"""

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, NoReturn
import uuid

from app.database import ChannelType, ChatChannel, Room, User
from app.dependencies.database import get_redis_pubsub, with_db
from app.helpers.background_task import bg_tasks
from app.log import log
from app.models.score import RULESETS_VERSION_HASH
from app.router.notification.server import server
from app.service.user_cache_service import get_user_cache_service

from pydantic import BaseModel, Field
from redis.asyncio import Redis

CHANNEL_NAME = "g0v0:ipc:{server}"
SERVER_IDENTIFIER = "lazer"

logger = log("V2 IPC")


class IPCMessageType(StrEnum):
    """Enumeration of IPC message types."""

    NOTICE = "notice"
    REQUEST = "request"
    RESPONSE = "response"
    ERROR = "error"


class IPCMessage(BaseModel):
    """Data model for IPC messages."""

    type: IPCMessageType
    name: str
    uuid: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_server: str
    data: dict[str, Any] | None = None


class IPCErrorBody(BaseModel):
    """Data model for IPC errors."""

    code: int
    message: str


class IPCError(Exception):
    """Custom exception for IPC errors."""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"IPC Error {code}: {message}")


class IPCClient:
    """Client for sending IPC messages to g0v0 server v2 series."""

    def __init__(self, redis_client: Redis):
        self.redis_client = redis_client
        self.pubsub = get_redis_pubsub()

        self.request_handlers: dict[str, Callable[[IPCMessage], Awaitable[Any]]] = {}
        self.notice_handlers: dict[str, Callable[[IPCMessage], Awaitable[Any]]] = {}

        self._futures: dict[str, asyncio.Future] = {}

    async def subscribe_requests(self, name: str, handler: Callable[[IPCMessage], Awaitable[Any]]):
        """Subscribe to incoming IPC requests with a specific name.

        Args:
            name: The name of the request type to handle.
            handler: An async function that takes an IPCMessage and returns a response.
        """
        self.request_handlers[name] = handler

    async def subscribe_notices(self, name: str, handler: Callable[[IPCMessage], Awaitable[Any]]):
        """Subscribe to incoming IPC notices with a specific name.

        Args:
            name: The name of the notice type to handle.
            handler: An async function that takes an IPCMessage and returns None.
        """
        self.notice_handlers[name] = handler

    def handle_request(self, name: str):
        """A decorator for subscribing to IPC requests with a specific name.

        Args:
            name: The name of the request type to handle.
        """

        def decorator(func: Callable[[IPCMessage], Awaitable[Any]]):
            self.request_handlers[name] = func
            return func

        return decorator

    def handle_notice(self, name: str):
        """A decorator for subscribing to IPC notices with a specific name.

        Args:
            name: The name of the notice type to handle.
        """

        def decorator(func: Callable[[IPCMessage], Awaitable[Any]]):
            self.notice_handlers[name] = func
            return func

        return decorator

    async def init(self):
        await self.pubsub.subscribe(CHANNEL_NAME.format(server=SERVER_IDENTIFIER))
        bg_tasks.add_task(self._receive_messages)

    async def send_notice(
        self,
        server: str,
        name: str,
        data: dict[str, Any],
    ) -> None:
        """Send an IPC message to the appropriate Redis channel.

        Args:
            server: The target server identifier (e.g., "lazer").
            name: A string identifier for the message type.
            data: A dictionary containing message-specific data.
        """
        channel = CHANNEL_NAME.format(server=server)
        message = IPCMessage(type=IPCMessageType.NOTICE, name=name, data=data, source_server=SERVER_IDENTIFIER)
        await self.redis_client.publish(channel, message.model_dump_json())

    async def send_request(
        self,
        server: str,
        name: str,
        data: dict[str, Any],
        *,
        timeout: int = 30,  # noqa: ASYNC109
    ) -> IPCMessage:
        """Send a request IPC message and return the UUID for correlation."""
        channel = CHANNEL_NAME.format(server=server)
        message = IPCMessage(type=IPCMessageType.REQUEST, name=name, data=data, source_server=SERVER_IDENTIFIER)
        await self.redis_client.publish(channel, message.model_dump_json())
        result = await self._wait_for_response(message.uuid, timeout)
        if isinstance(result, IPCErrorBody):
            raise IPCError(code=result.code, message=result.message)
        return result

    async def _send_response(
        self,
        server: str,
        name: str,
        uuid: str,
        data: dict[str, Any],
    ) -> None:
        """Send a response IPC message.

        Args:
            server: The target server identifier (e.g., "lazer").
            name: A string identifier for the message type.
            uuid: The UUID of the request message being responded to.
            data: A dictionary containing message-specific data.
        """
        channel = CHANNEL_NAME.format(server=server)
        message = IPCMessage(
            type=IPCMessageType.RESPONSE, name=name, uuid=uuid, data=data, source_server=SERVER_IDENTIFIER
        )
        await self.redis_client.publish(channel, message.model_dump_json())

    def _add_result(self, uuid: str, result: IPCErrorBody | Any) -> None:
        if future := self._futures.get(uuid):
            future.set_result(result)

    async def _wait_for_response(
        self,
        uuid: str,
        timeout: float | None,  # noqa: ASYNC109
    ) -> IPCErrorBody | Any:
        future = asyncio.get_event_loop().create_future()
        self._futures[uuid] = future
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            del self._futures[uuid]

    async def _receive_messages(self) -> NoReturn:
        """Continuously listen for incoming IPC messages and handle them."""
        while True:
            try:
                message = await self.pubsub.get_message(ignore_subscribe_messages=True, timeout=None)
                if message is not None and message["type"] == "message":
                    try:
                        body = IPCMessage.model_validate_json(message["data"])
                    except Exception:
                        logger.warning("Received invalid IPC message: {}", message["data"])
                        continue
                    bg_tasks.add_task(self._handle_message, body)
            except Exception:
                logger.exception("Error while receiving IPC messages")
                await asyncio.sleep(1)

    async def _handle_message(self, message: IPCMessage):
        """Handle an incoming IPC message by dispatching to the appropriate handler."""
        try:
            if message.type == IPCMessageType.REQUEST and message.name in self.request_handlers:
                handler = self.request_handlers[message.name]
                response_data = await handler(message)
                await self._send_response(
                    server=message.source_server,
                    name=message.name,
                    uuid=message.uuid,
                    data=response_data,
                )
            elif message.type == IPCMessageType.NOTICE and message.name in self.notice_handlers:
                handler = self.notice_handlers[message.name]
                await handler(message)
            elif message.type == IPCMessageType.RESPONSE:
                self._add_result(message.uuid, message.data)
            elif message.type == IPCMessageType.ERROR:
                error_body = IPCErrorBody.model_validate(message.data)
                self._add_result(message.uuid, error_body)
        except Exception:
            logger.exception("Error while handling IPC message: {}", message)


ipc_client: IPCClient | None = None


async def init_ipc(redis_client: Redis):
    """Initialize the IPC client."""
    global ipc_client
    ipc_client = IPCClient(redis_client)
    await ipc_client.init()

    # handlers
    @ipc_client.handle_notice("user_online_status_changed")
    async def handle_user_online_status_changed(message: IPCMessage):
        if message.source_server != "realtime":
            logger.warning(
                "Received user_online_status_changed notice isn't from 'realtime': {}", message.source_server
            )
            return
        if message.data is None:
            logger.warning("Received user_online_status_changed notice without data")
            return

        user_id = message.data.get("user_id")
        if user_id is None:
            logger.warning("Received user_online_status_changed notice without user_id")
            return
        logger.info(f"Received user online status update for user_id: {user_id}")
        await get_user_cache_service(redis_client).invalidate_user_cache(user_id)

    @ipc_client.handle_request("get_ruleset_hashes")
    async def handle_get_ruleset_hashes(message: IPCMessage):
        logger.info(f"Received get_ruleset_hashes request from {message.source_server}")
        return RULESETS_VERSION_HASH

    @ipc_client.handle_request("create_mp_channel")
    async def handle_create_mp_channel(message: IPCMessage):
        # TODO: When realtime server implements notification server, this method could be removed.
        logger.info(f"Received create_mp_channel request from {message.source_server}")
        if message.data is None:
            logger.warning("Received create_mp_channel request without data")
            raise IPCError(code=400, message="Missing data in create_mp_channel request")
        room_id = message.data.get("room_id")
        if room_id is None:
            logger.warning("Received create_mp_channel request without room_id")
            raise IPCError(code=400, message="Missing room_id in create_mp_channel request")
        host_id = message.data.get("host_id")

        async with with_db() as db:
            room = await db.get(Room, room_id)
            if room is None:
                logger.warning("Received create_mp_channel request for non-existent room_id: {}", room_id)
                raise IPCError(code=404, message=f"Room with id {room_id} not found")
            if room.channel_id == 0:
                ch = ChatChannel(
                    channel_name=f"mp_{room_id}",
                    description=f"Multiplayer room {room_id} chat",
                    type=ChannelType.MULTIPLAYER,
                )
                db.add(ch)
                await db.commit()
                await db.refresh(ch)
                await db.refresh(room)
                room.channel_id = ch.channel_id
                channel_id = ch.channel_id
                logger.info(f"Created new chat channel {channel_id} for room {room_id}")
                await db.commit()
                await db.refresh(ch)
            else:
                ch = await db.get(ChatChannel, room.channel_id)
                if ch is None:
                    logger.warning(
                        "Received create_mp_channel request for room_id {} with non-existent channel_id: {}",
                        room_id,
                        room.channel_id,
                    )
                    raise IPCError(
                        code=404, message=f"Chat channel with id {room.channel_id} not found for room {room_id}"
                    )

            if host_id is not None:
                host = await db.get(User, host_id)
                if host is not None:
                    await server.join_channel(host, ch)
            return {"channel_id": ch.channel_id}

    @ipc_client.handle_request("make_user_join_mp_channel")
    async def handle_make_user_join_mp_channel(message: IPCMessage):
        logger.info(f"Received make_user_join_mp_channel request from {message.source_server}")
        if message.data is None:
            logger.warning("Received make_user_join_mp_channel request without data")
            raise IPCError(code=400, message="Missing data in make_user_join_mp_channel request")
        user_id = message.data.get("user_id")
        channel_id = message.data.get("channel_id")
        if user_id is None or channel_id is None:
            logger.warning("Received make_user_join_mp_channel request without user_id or channel_id")
            raise IPCError(code=400, message="Missing user_id or channel_id in make_user_join_mp_channel request")

        async with with_db() as db:
            ch = await db.get(ChatChannel, channel_id)
            if ch is None:
                logger.warning("Received make_user_join_mp_channel request for non-existent channel_id: {}", channel_id)
                raise IPCError(code=404, message=f"Chat channel with id {channel_id} not found")
            user = await db.get(User, user_id)
            if user is None:
                logger.warning("Received make_user_join_mp_channel request for non-existent user_id: {}", user_id)
                raise IPCError(code=404, message=f"User with id {user_id} not found")

            await server.join_channel(user, ch)
            return {}

    @ipc_client.handle_request("make_user_leave_mp_channel")
    async def handle_make_user_leave_mp_channel(message: IPCMessage):
        logger.info(f"Received make_user_leave_mp_channel request from {message.source_server}")
        if message.data is None:
            logger.warning("Received make_user_leave_mp_channel request without data")
            raise IPCError(code=400, message="Missing data in make_user_leave_mp_channel request")
        user_id = message.data.get("user_id")
        channel_id = message.data.get("channel_id")
        if user_id is None or channel_id is None:
            logger.warning("Received make_user_leave_mp_channel request without user_id or channel_id")
            raise IPCError(code=400, message="Missing user_id or channel_id in make_user_leave_mp_channel request")

        async with with_db() as db:
            ch = await db.get(ChatChannel, channel_id)
            if ch is None:
                logger.warning(
                    "Received make_user_leave_mp_channel request for non-existent channel_id: {}", channel_id
                )
                raise IPCError(code=404, message=f"Chat channel with id {channel_id} not found")
            user = await db.get(User, user_id)
            if user is None:
                logger.warning("Received make_user_leave_mp_channel request for non-existent user_id: {}", user_id)
                raise IPCError(code=404, message=f"User with id {user_id} not found")

            await server.leave_channel(user, ch)
            return {}


def get_ipc_client() -> IPCClient:
    if ipc_client is None:
        raise RuntimeError("IPC client is not initialized")
    return ipc_client
