"""Channel router module for chat channel operations.

This module provides endpoints for managing chat channels, including
joining/leaving channels, listing channels, and creating new channels.
"""

from typing import Annotated, Literal, Self

from app.database.chat import (
    ChannelType,
    ChatChannel,
    ChatChannelModel,
    ChatMessage,
    SilenceUser,
    UserSilenceResp,
)
from app.database.user import User, UserModel
from app.dependencies.database import Database, Redis
from app.dependencies.param import BodyOrForm
from app.dependencies.user import get_current_user
from app.models.error import ErrorType, RequestError
from app.router.v2 import api_v2_router as router
from app.utils import api_doc

from .server import server

from fastapi import Depends, Path, Query, Security
from pydantic import BaseModel, model_validator
from sqlmodel import col, select


@router.get(
    "/chat/updates",
    name="Get Updates",
    description="Get the latest silence records for channels the current user has joined.",
    tags=["Chat"],
    responses={
        200: api_doc(
            "Update response.",
            {"presence": list[ChatChannelModel], "silences": list[UserSilenceResp]},
            ChatChannel.LISTING_INCLUDES,
            name="UpdateResponse",
        )
    },
)
async def get_update(
    session: Database,
    current_user: Annotated[User, Security(get_current_user, scopes=["chat.read"])],
    redis: Redis,
    history_since: Annotated[int | None, Query(description="Get silence records after this silence ID")] = None,
    since: Annotated[int | None, Query(description="Get silence records after this message ID")] = None,
    includes: Annotated[
        list[str],
        Query(alias="includes[]", description="Types of updates to include"),
    ] = ["presence", "silences"],
):
    """Get channel presence and silence updates.

    Args:
        session: Database session.
        current_user: The authenticated user.
        redis: Redis client.
        history_since: Get silence records after this silence ID.
        since: Get silence records after this message ID.
        includes: Types of updates to include.

    Returns:
        Dict containing presence and silence information.
    """
    resp = {
        "presence": [],
        "silences": [],
    }
    if "presence" in includes:
        channel_ids = server.get_user_joined_channel(current_user.id)
        for channel_id in channel_ids:
            # Use explicit query to avoid lazy loading
            db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_id == channel_id))).first()
            if db_channel:
                resp["presence"].append(
                    await ChatChannelModel.transform(
                        db_channel,
                        user=current_user,
                        server=server,
                        includes=ChatChannel.LISTING_INCLUDES,
                    )
                )
    if "silences" in includes:
        if history_since:
            silences = (await session.exec(select(SilenceUser).where(col(SilenceUser.id) > history_since))).all()
            resp["silences"].extend([UserSilenceResp.from_db(silence) for silence in silences])
        elif since:
            msg = await session.get(ChatMessage, since)
            if msg:
                silences = (
                    await session.exec(select(SilenceUser).where(col(SilenceUser.banned_at) > msg.timestamp))
                ).all()
                resp["silences"].extend([UserSilenceResp.from_db(silence) for silence in silences])
    return resp


@router.put(
    "/chat/channels/{channel}/users/{user}",
    name="Join Channel",
    description="Join a specified public/room channel.",
    tags=["Chat"],
    responses={200: api_doc("The joined channel", ChatChannelModel, ChatChannel.LISTING_INCLUDES)},
)
async def join_channel(
    session: Database,
    channel: Annotated[str, Path(..., description="Channel ID/name")],
    user: Annotated[str, Path(..., description="User ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["chat.write_manage"])],
):
    """Join a chat channel.

    Args:
        session: Database session.
        channel: Channel ID or name.
        user: User ID.
        current_user: The authenticated user.

    Returns:
        The joined channel information.

    Raises:
        RequestError: If user is restricted or channel not found.
    """
    if await current_user.is_restricted(session):
        raise RequestError(ErrorType.MESSAGING_RESTRICTED)

    # Use explicit query to avoid lazy loading
    if channel.isdigit():
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_id == int(channel)))).first()
    else:
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_name == channel))).first()

    if db_channel is None:
        raise RequestError(ErrorType.CHANNEL_NOT_FOUND)
    return await server.join_channel(current_user, db_channel)


@router.delete(
    "/chat/channels/{channel}/users/{user}",
    status_code=204,
    name="Leave Channel",
    description="Remove user from a specified public/room channel.",
    tags=["Chat"],
)
async def leave_channel(
    session: Database,
    channel: Annotated[str, Path(..., description="Channel ID/name")],
    user: Annotated[str, Path(..., description="User ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["chat.write_manage"])],
):
    """Leave a chat channel.

    Args:
        session: Database session.
        channel: Channel ID or name.
        user: User ID.
        current_user: The authenticated user.

    Raises:
        RequestError: If user is restricted or channel not found.
    """
    if await current_user.is_restricted(session):
        raise RequestError(ErrorType.MESSAGING_RESTRICTED)

    # Use explicit query to avoid lazy loading
    if channel.isdigit():
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_id == int(channel)))).first()
    else:
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_name == channel))).first()

    if db_channel is None:
        raise RequestError(ErrorType.CHANNEL_NOT_FOUND)
    await server.leave_channel(current_user, db_channel)
    return


@router.get(
    "/chat/channels",
    responses={200: api_doc("Joined channels", list[ChatChannelModel])},
    name="Get Channel List",
    description="Get all public channels.",
    tags=["Chat"],
)
async def get_channel_list(
    session: Database,
    current_user: Annotated[User, Security(get_current_user, scopes=["chat.read"])],
):
    """Get list of all public channels.

    Args:
        session: Database session.
        current_user: The authenticated user.

    Returns:
        List of public chat channels.
    """
    channels = (await session.exec(select(ChatChannel).where(ChatChannel.type == ChannelType.PUBLIC))).all()
    results = await ChatChannelModel.transform_many(
        channels,
        user=current_user,
        server=server,
    )

    return results


@router.get(
    "/chat/channels/{channel}",
    responses={
        200: api_doc(
            "Channel details",
            {
                "channel": ChatChannelModel,
                "users": list[UserModel],
            },
            ChatChannel.LISTING_INCLUDES + User.CARD_INCLUDES,
            name="GetChannelResponse",
        )
    },
    name="Get Channel Info",
    description="Get information for a specified channel.",
    tags=["Chat"],
)
async def get_channel(
    session: Database,
    channel: Annotated[str, Path(..., description="Channel ID/name")],
    current_user: Annotated[User, Security(get_current_user, scopes=["chat.read"])],
    redis: Redis,
):
    """Get detailed information for a channel.

    Args:
        session: Database session.
        channel: Channel ID or name.
        current_user: The authenticated user.
        redis: Redis client.

    Returns:
        Dict containing channel info and user list.

    Raises:
        RequestError: If channel or target user not found.
    """
    # Use explicit query to avoid lazy loading
    if channel.isdigit():
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_id == int(channel)))).first()
    else:
        db_channel = (await session.exec(select(ChatChannel).where(ChatChannel.channel_name == channel))).first()

    if db_channel is None:
        raise RequestError(ErrorType.CHANNEL_NOT_FOUND)

    # Extract needed attributes immediately
    channel_type = db_channel.type
    channel_name = db_channel.channel_name

    users = []
    if channel_type == ChannelType.PM:
        user_ids = channel_name.split("_")[1:]
        if len(user_ids) != 2:
            raise RequestError(ErrorType.TARGET_USER_NOT_FOUND)
        for id_ in user_ids:
            if int(id_) == current_user.id:
                continue
            target_user = await session.get(User, int(id_))
            if target_user is None or await target_user.is_restricted(session):
                raise RequestError(ErrorType.TARGET_USER_NOT_FOUND)
            users.extend([target_user, current_user])
            break

    return {
        "channel": await ChatChannelModel.transform(
            db_channel,
            user=current_user,
            server=server,
            includes=ChatChannel.LISTING_INCLUDES,
        ),
        "users": await UserModel.transform_many(users, includes=User.CARD_INCLUDES),
    }


class CreateChannelReq(BaseModel):
    """Request model for creating a new channel.

    Attributes:
        message: Initial message for ANNOUNCE channels.
        type: Channel type (ANNOUNCE or PM).
        target_id: Target user ID for PM channels.
        target_ids: Target user IDs for ANNOUNCE channels.
        channel: Channel configuration for ANNOUNCE channels.
    """

    class AnnounceChannel(BaseModel):
        """Configuration for announcement channels."""

        name: str
        description: str

    message: str | None = None
    type: Literal["ANNOUNCE", "PM"] = "PM"
    target_id: int | None = None
    target_ids: list[int] | None = None
    channel: AnnounceChannel | None = None

    @model_validator(mode="after")
    def check(self) -> Self:
        if self.type == "PM":
            if self.target_id is None:
                raise ValueError("target_id must be set for PM channels")
        else:
            if self.target_ids is None or self.channel is None or self.message is None:
                raise ValueError("target_ids, channel, and message must be set for ANNOUNCE channels")
        return self


@router.post(
    "/chat/channels",
    responses={200: api_doc("Created channel", ChatChannelModel, ["recent_messages.sender"])},
    name="Create Channel",
    description="Create a new PM/announcement channel. Rejoins existing PM channel if one exists.",
    tags=["Chat"],
)
async def create_channel(
    session: Database,
    req: Annotated[CreateChannelReq, Depends(BodyOrForm(CreateChannelReq))],
    current_user: Annotated[User, Security(get_current_user, scopes=["chat.write_manage"])],
    redis: Redis,
):
    """Create a new chat channel.

    Args:
        session: Database session.
        req: Channel creation request.
        current_user: The authenticated user.
        redis: Redis client.

    Returns:
        The created or existing channel.

    Raises:
        RequestError: If user is restricted or target not found.
    """
    if await current_user.is_restricted(session):
        raise RequestError(ErrorType.MESSAGING_RESTRICTED)

    if req.type == "PM":
        target = await session.get(User, req.target_id)
        if not target or await target.is_restricted(session):
            raise RequestError(ErrorType.TARGET_USER_NOT_FOUND)
        is_can_pm, block = await target.is_user_can_pm(current_user, session)
        if not is_can_pm:
            raise RequestError(ErrorType.MESSAGING_RESTRICTED, {"reason": block})

        channel = await ChatChannel.get_pm_channel(
            current_user.id,
            req.target_id,  # pyright: ignore[reportArgumentType]
            session,
        )
        channel_name = f"pm_{current_user.id}_{req.target_id}"
    else:
        channel_name = req.channel.name if req.channel else "Unnamed Channel"
        result = await session.exec(select(ChatChannel).where(ChatChannel.channel_name == channel_name))
        channel = result.first()

    if channel is None:
        channel = ChatChannel(
            name=channel_name,
            description=req.channel.description if req.channel else "Private message channel",
            type=ChannelType.PM if req.type == "PM" else ChannelType.ANNOUNCE,
        )
        session.add(channel)
        await session.commit()
        await session.refresh(channel)
        await session.refresh(current_user)
    if req.type == "PM":
        await session.refresh(target)  # pyright: ignore[reportPossiblyUnboundVariable]
        await server.batch_join_channel([target, current_user], channel)  # pyright: ignore[reportPossiblyUnboundVariable]
    else:
        target_users = await session.exec(select(User).where(col(User.id).in_(req.target_ids or [])))
        await server.batch_join_channel([*target_users, current_user], channel)

    await server.join_channel(current_user, channel)

    return await ChatChannelModel.transform(
        channel, user=current_user, server=server, includes=["recent_messages.sender"]
    )
