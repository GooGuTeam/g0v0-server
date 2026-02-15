"""Message router module for chat message operations.

This module provides endpoints for sending, receiving, and managing
chat messages within channels.
"""

from typing import Annotated

from app.database import ChatChannelModel
from app.database.chat import (
    ChannelType,
    ChatChannel,
    ChatMessage,
    ChatMessageModel,
    MessageType,
    SilenceUser,
    UserSilenceResp,
)
from app.database.user import User
from app.dependencies.database import Database, Redis, redis_message_client
from app.dependencies.param import BodyOrForm
from app.dependencies.user import get_current_user
from app.helpers import api_doc
from app.log import log
from app.models.error import ErrorType, RequestError
from app.models.notification import ChannelMessage, ChannelMessageTeam
from app.router.v2 import api_v2_router as router
from app.service.redis_message_system import redis_message_system

from .banchobot import bot
from .server import server

from fastapi import Depends, Path, Query, Security
from pydantic import BaseModel, Field
from sqlmodel import col, select


class KeepAliveResp(BaseModel):
    """Response model for keep-alive endpoint.

    Attributes:
        silences: List of recent user silences.
    """

    silences: list[UserSilenceResp] = Field(default_factory=list)


logger = log("Chat")


@router.post(
    "/chat/ack",
    name="Keep Alive",
    response_model=KeepAliveResp,
    description="Keep the connection to public channels alive. Also returns recent silence records.",
    tags=["Chat"],
)
async def keep_alive(
    session: Database,
    current_user: Annotated[User, Security(get_current_user, scopes=["chat.read"])],
    history_since: Annotated[int | None, Query(description="Get silence records after this silence ID")] = None,
    since: Annotated[int | None, Query(description="Get silence records after this message ID")] = None,
):
    """Keep chat connection alive and fetch recent silences.

    Args:
        session: Database session.
        current_user: The authenticated user.
        history_since: Get silence records after this silence ID.
        since: Get silence records after this message ID.

    Returns:
        KeepAliveResp with recent silence records.
    """
    resp = KeepAliveResp()
    if history_since:
        silences = (await session.exec(select(SilenceUser).where(col(SilenceUser.id) > history_since))).all()
        resp.silences.extend([UserSilenceResp.from_db(silence) for silence in silences])
    elif since:
        msg = await session.get(ChatMessage, since)
        if msg:
            silences = (await session.exec(select(SilenceUser).where(col(SilenceUser.banned_at) > msg.timestamp))).all()
            resp.silences.extend([UserSilenceResp.from_db(silence) for silence in silences])

    return resp


class MessageReq(BaseModel):
    """Request model for sending a message.

    Attributes:
        message: The message content.
        is_action: Whether the message is an action (/me).
        uuid: Optional client-generated UUID for deduplication.
    """

    message: str
    is_action: bool = False
    uuid: str | None = None


@router.post(
    "/chat/channels/{channel}/messages",
    responses={200: api_doc("Sent message", ChatMessageModel, ["sender", "is_action"])},
    name="Send Message",
    description="Send a message to a specified channel.",
    tags=["Chat"],
)
async def send_message(
    session: Database,
    channel: Annotated[str, Path(..., description="Channel ID/name")],
    req: Annotated[MessageReq, Depends(BodyOrForm(MessageReq))],
    current_user: Annotated[User, Security(get_current_user, scopes=["chat.write"])],
):
    """Send a message to a chat channel.

    Args:
        session: Database session.
        channel: Channel ID or name.
        req: Message request data.
        current_user: The authenticated user.

    Returns:
        The sent message data.

    Raises:
        RequestError: If user is restricted or channel not found.
    """
    if await current_user.is_restricted(session):
        raise RequestError(ErrorType.MESSAGING_RESTRICTED)

    # Use explicit query to get channel, avoid lazy loading
    if channel.isdigit():
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_id == int(channel)))).first()
    else:
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_name == channel))).first()

    if db_channel is None:
        raise RequestError(ErrorType.CHANNEL_NOT_FOUND)

    # Extract all needed attributes immediately to avoid lazy loading later
    channel_id = db_channel.channel_id
    channel_type = db_channel.type
    channel_name = db_channel.channel_name
    user_id = current_user.id

    # For multiplayer rooms, check Redis key before sending message
    if channel_type == ChannelType.MULTIPLAYER:
        try:
            redis = redis_message_client
            key = f"channel:{channel_id}:messages"
            key_type = await redis.type(key)
            if key_type not in ["none", "zset"]:
                logger.warning(f"Fixing Redis key {key} with wrong type: {key_type}")
                await redis.delete(key)
        except Exception as e:
            logger.warning(f"Failed to check/fix Redis key for channel {channel_id}: {e}")

    # Use Redis message system to send message - return immediately
    resp = await redis_message_system.send_message(
        channel_id=channel_id,
        user=current_user,
        content=req.message,
        is_action=req.is_action,
        user_uuid=req.uuid,
    )

    # Immediately broadcast message to all clients
    is_bot_command = req.message.startswith("!")
    await server.send_message_to_channel(resp, is_bot_command and channel_type == ChannelType.PUBLIC)

    # 处理机器人命令
    if is_bot_command:
        await bot.try_handle(current_user, db_channel, req.message, session)

    await session.refresh(current_user)
    # Create temporary ChatMessage object for notification system (only for PM and team channels)
    if channel_type in [ChannelType.PM, ChannelType.TEAM]:
        temp_msg = ChatMessage(
            message_id=resp["message_id"],  # Use ID generated by Redis system
            channel_id=channel_id,
            content=req.message,
            sender_id=user_id,
            type=MessageType.ACTION if req.is_action else MessageType.PLAIN,
            uuid=req.uuid,
        )

        if channel_type == ChannelType.PM:
            user_ids = channel_name.split("_")[1:]
            await server.new_private_notification(
                ChannelMessage.init(temp_msg, current_user, [int(u) for u in user_ids], channel_type)
            )
        elif channel_type == ChannelType.TEAM:
            await server.new_private_notification(ChannelMessageTeam.init(temp_msg, current_user))

    return resp


@router.get(
    "/chat/channels/{channel}/messages",
    responses={200: api_doc("Retrieved messages", list[ChatMessageModel], ["sender"])},
    name="Get Messages",
    description="Get message list for a specified channel (returned in chronological order).",
    tags=["Chat"],
)
async def get_message(
    session: Database,
    channel: str,
    current_user: Annotated[User, Security(get_current_user, scopes=["chat.read"])],
    limit: Annotated[int, Query(ge=1, le=50, description="Number of messages to retrieve")] = 50,
    since: Annotated[int, Query(ge=0, description="Get messages after this message ID (load newer messages)")] = 0,
    until: Annotated[int | None, Query(description="Get messages before this message ID (load older history)")] = None,
):
    """Get messages from a chat channel.

    Args:
        session: Database session.
        channel: Channel ID or name.
        current_user: The authenticated user.
        limit: Maximum number of messages to return.
        since: Get messages with ID greater than this.
        until: Get messages with ID less than this.

    Returns:
        List of chat messages in chronological order.

    Raises:
        RequestError: If channel not found.
    """
    # Query channel
    if channel.isdigit():
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_id == int(channel)))).first()
    else:
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_name == channel))).first()

    if db_channel is None:
        raise RequestError(ErrorType.CHANNEL_NOT_FOUND)

    channel_id = db_channel.channel_id

    try:
        messages = await redis_message_system.get_messages(channel_id, limit, since)
        if len(messages) >= 2 and messages[0]["message_id"] > messages[-1]["message_id"]:
            messages.reverse()
        return messages
    except Exception as e:
        logger.warning(f"Failed to get messages from Redis system: {e}")

    base = select(ChatMessage).where(ChatMessage.channel_id == channel_id)

    if since > 0 and until is None:
        # Load newer messages forward -> use ASC directly
        query = base.where(col(ChatMessage.message_id) > since).order_by(col(ChatMessage.message_id).asc()).limit(limit)
        rows = (await session.exec(query)).all()
        resp = await ChatMessageModel.transform_many(rows, includes=["sender"])
        # Already ASC, no need to reverse
        return resp

    # until branch (load older history)
    if until is not None:
        # Use DESC to get recent older messages, then reverse to ASC
        query = (
            base.where(col(ChatMessage.message_id) < until).order_by(col(ChatMessage.message_id).desc()).limit(limit)
        )
        rows = (await session.exec(query)).all()
        rows = list(rows)
        rows.reverse()  # Reverse to ASC
        resp = await ChatMessageModel.transform_many(rows, includes=["sender"])
        return resp

    query = base.order_by(col(ChatMessage.message_id).desc()).limit(limit)
    rows = (await session.exec(query)).all()
    rows = list(rows)
    rows.reverse()  # Reverse to ASC
    resp = await ChatMessageModel.transform_many(rows, includes=["sender"])
    return resp


@router.put(
    "/chat/channels/{channel}/mark-as-read/{message}",
    status_code=204,
    name="Mark Message as Read",
    description="Mark a specified message as read.",
    tags=["Chat"],
)
async def mark_as_read(
    session: Database,
    channel: Annotated[str, Path(..., description="Channel ID/name")],
    message: Annotated[int, Path(..., description="Message ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["chat.read"])],
):
    """Mark a message as read in a channel.

    Args:
        session: Database session.
        channel: Channel ID or name.
        message: Message ID to mark as read.
        current_user: The authenticated user.

    Raises:
        RequestError: If channel not found.
    """
    # Use explicit query to get channel, avoid lazy loading
    if channel.isdigit():
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_id == int(channel)))).first()
    else:
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_name == channel))).first()

    if db_channel is None:
        raise RequestError(ErrorType.CHANNEL_NOT_FOUND)

    # Extract needed attribute immediately
    channel_id = db_channel.channel_id
    await server.mark_as_read(channel_id, current_user.id, message)


class PMReq(BaseModel):
    """Request model for creating a new PM channel.

    Attributes:
        target_id: Target user ID to message.
        message: Initial message content.
        is_action: Whether the message is an action (/me).
        uuid: Optional client-generated UUID for deduplication.
    """

    target_id: int
    message: str
    is_action: bool = False
    uuid: str | None = None


@router.post(
    "/chat/new",
    name="Create PM Channel",
    description="Create a new private message channel.",
    tags=["Chat"],
    responses={
        200: api_doc(
            "Create PM channel response",
            {
                "channel": ChatChannelModel,
                "message": ChatMessageModel,
                "new_channel_id": int,
            },
            ["recent_messages.sender", "sender"],
            name="NewPMResponse",
        )
    },
)
async def create_new_pm(
    session: Database,
    req: Annotated[PMReq, Depends(BodyOrForm(PMReq))],
    current_user: Annotated[User, Security(get_current_user, scopes=["chat.write"])],
    redis: Redis,
):
    """Create a new private message channel and send initial message.

    Args:
        session: Database session.
        req: PM creation request.
        current_user: The authenticated user.
        redis: Redis client.

    Returns:
        Dict containing channel, message, and new channel ID.

    Raises:
        RequestError: If user is restricted or target not found.
    """
    if await current_user.is_restricted(session):
        raise RequestError(ErrorType.MESSAGING_RESTRICTED)

    user_id = current_user.id
    target = await session.get(User, req.target_id)
    if target is None or await target.is_restricted(session):
        raise RequestError(ErrorType.TARGET_USER_NOT_FOUND)
    is_can_pm, block = await target.is_user_can_pm(current_user, session)
    if not is_can_pm:
        raise RequestError(ErrorType.MESSAGING_RESTRICTED, {"reason": block})

    channel = await ChatChannel.get_pm_channel(user_id, req.target_id, session)
    if channel is None:
        channel = ChatChannel(
            name=f"pm_{user_id}_{req.target_id}",
            description="Private message channel",
            type=ChannelType.PM,
        )
        session.add(channel)
        await session.commit()
        await session.refresh(channel)
        await session.refresh(target)
        await session.refresh(current_user)

    await server.batch_join_channel([target, current_user], channel)
    channel_resp = await ChatChannelModel.transform(
        channel, user=current_user, server=server, includes=["recent_messages.sender"]
    )
    msg = ChatMessage(
        channel_id=channel.channel_id,
        content=req.message,
        sender_id=user_id,
        type=MessageType.ACTION if req.is_action else MessageType.PLAIN,
        uuid=req.uuid,
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    await session.refresh(current_user)
    await session.refresh(channel)
    message_resp = await ChatMessageModel.transform(msg, user=current_user, includes=["sender"])
    await server.send_message_to_channel(message_resp)
    return {
        "channel": channel_resp,
        "message": message_resp,
        "new_channel_id": channel_resp["channel_id"],
    }
