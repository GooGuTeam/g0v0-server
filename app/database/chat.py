"""Chat system database models.

This module provides models for chat channels, messages, and user silencing
for the real-time chat system.
"""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, NotRequired, TypedDict

from app.helpers import utcnow
from app.models.model import UTCBaseModel

from ._base import DatabaseModel, included, ondemand
from .user import User, UserDict, UserModel

from pydantic import BaseModel
from sqlmodel import (
    VARCHAR,
    BigInteger,
    Column,
    DateTime,
    Field,
    ForeignKey,
    Relationship,
    SQLModel,
    col,
    select,
)
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from app.router.notification.server import ChatServer


# ChatChannel


class ChatUserAttributes(BaseModel):
    """User-specific attributes for a chat channel."""

    can_message: bool
    can_message_error: str | None = None
    last_read_id: int


class ChannelType(StrEnum):
    """Types of chat channels."""

    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"
    MULTIPLAYER = "MULTIPLAYER"
    SPECTATOR = "SPECTATOR"
    TEMPORARY = "TEMPORARY"
    PM = "PM"
    GROUP = "GROUP"
    SYSTEM = "SYSTEM"
    ANNOUNCE = "ANNOUNCE"
    TEAM = "TEAM"


class MessageType(StrEnum):
    """Types of chat messages."""

    ACTION = "action"
    MARKDOWN = "markdown"
    PLAIN = "plain"


class ChatChannelDict(TypedDict):
    """TypedDict representation of a chat channel."""

    channel_id: int
    description: str
    name: str
    icon: str | None
    type: ChannelType
    uuid: NotRequired[str | None]
    message_length_limit: NotRequired[int]
    moderated: NotRequired[bool]
    current_user_attributes: NotRequired[ChatUserAttributes]
    last_read_id: NotRequired[int | None]
    last_message_id: NotRequired[int | None]
    recent_messages: NotRequired[list["ChatMessageDict"]]
    users: NotRequired[list[int]]


class ChatChannelModel(DatabaseModel[ChatChannelDict]):
    """Base model for chat channels with transformation support."""

    CONVERSATION_INCLUDES: ClassVar[list[str]] = [
        "last_message_id",
        "users",
    ]
    LISTING_INCLUDES: ClassVar[list[str]] = [
        *CONVERSATION_INCLUDES,
        "current_user_attributes",
        "last_read_id",
    ]

    channel_id: int = Field(primary_key=True, index=True, default=None)
    description: str = Field(sa_column=Column(VARCHAR(255), index=True))
    icon: str | None = Field(default=None)
    type: ChannelType = Field(index=True)

    @included
    @staticmethod
    async def name(session: AsyncSession, channel: "ChatChannel", user: User, server: "ChatServer") -> str:
        users = server.channels.get(channel.channel_id, [])
        if channel.type == ChannelType.PM and users and len(users) == 2:
            target_user_id = next(u for u in users if u != user.id)
            target_name = await session.exec(select(User.username).where(User.id == target_user_id))
            return target_name.one()
        return channel.channel_name

    @included
    @staticmethod
    async def moderated(session: AsyncSession, channel: "ChatChannel", user: User) -> bool:
        silence = (
            await session.exec(
                select(SilenceUser).where(
                    SilenceUser.channel_id == channel.channel_id,
                    SilenceUser.user_id == user.id,
                )
            )
        ).first()

        return silence is not None

    @ondemand
    @staticmethod
    async def current_user_attributes(
        session: AsyncSession,
        channel: "ChatChannel",
        user: User,
    ) -> ChatUserAttributes:
        from app.dependencies.database import get_redis

        silence = (
            await session.exec(
                select(SilenceUser).where(
                    SilenceUser.channel_id == channel.channel_id,
                    SilenceUser.user_id == user.id,
                )
            )
        ).first()
        can_message = silence is None
        can_message_error = "You are silenced in this channel" if not can_message else None

        redis = get_redis()
        last_read_id_raw = await redis.get(f"chat:{channel.channel_id}:last_read:{user.id}")
        last_msg_raw = await redis.get(f"chat:{channel.channel_id}:last_msg")
        last_msg = int(last_msg_raw) if last_msg_raw and last_msg_raw.isdigit() else None
        last_read_id = int(last_read_id_raw) if last_read_id_raw and last_read_id_raw.isdigit() else (last_msg or 0)

        return ChatUserAttributes(
            can_message=can_message,
            can_message_error=can_message_error,
            last_read_id=last_read_id,
        )

    @ondemand
    @staticmethod
    async def last_read_id(_session: AsyncSession, channel: "ChatChannel", user: User) -> int | None:
        from app.dependencies.database import get_redis

        redis = get_redis()
        last_read_id_raw = await redis.get(f"chat:{channel.channel_id}:last_read:{user.id}")
        last_msg_raw = await redis.get(f"chat:{channel.channel_id}:last_msg")
        last_msg = int(last_msg_raw) if last_msg_raw and last_msg_raw.isdigit() else None
        return int(last_read_id_raw) if last_read_id_raw and last_read_id_raw.isdigit() else last_msg

    @ondemand
    @staticmethod
    async def last_message_id(_session: AsyncSession, channel: "ChatChannel") -> int | None:
        from app.dependencies.database import get_redis

        redis = get_redis()
        last_msg_raw = await redis.get(f"chat:{channel.channel_id}:last_msg")
        return int(last_msg_raw) if last_msg_raw and last_msg_raw.isdigit() else None

    @ondemand
    @staticmethod
    async def recent_messages(
        session: AsyncSession,
        channel: "ChatChannel",
    ) -> list["ChatMessageDict"]:
        messages = (
            await session.exec(
                select(ChatMessage)
                .where(ChatMessage.channel_id == channel.channel_id)
                .order_by(col(ChatMessage.message_id).desc())
                .limit(50)
            )
        ).all()
        result = [
            await ChatMessageModel.transform(
                msg,
            )
            for msg in reversed(messages)
        ]
        return result

    @ondemand
    @staticmethod
    async def users(
        _session: AsyncSession,
        channel: "ChatChannel",
        server: "ChatServer",
        user: User,
    ) -> list[int]:
        if channel.type == ChannelType.PUBLIC:
            return []
        users = server.channels.get(channel.channel_id, []).copy()
        if channel.type == ChannelType.PM and users and len(users) == 2:
            target_user_id = next(u for u in users if u != user.id)
            users = [target_user_id, user.id]
        return users

    @included
    @staticmethod
    async def message_length_limit(_session: AsyncSession, _channel: "ChatChannel") -> int:
        return 1000


class ChatChannel(ChatChannelModel, table=True):
    """Database table model for chat channels."""

    __tablename__: str = "chat_channels"

    channel_name: str = Field(sa_column=Column(name="name", type_=VARCHAR(50), index=True))

    @classmethod
    async def get(cls, channel: str | int, session: AsyncSession) -> "ChatChannel | None":
        """Get a chat channel by ID or name.

        Args:
            channel: Channel ID (int) or name (str) to lookup.
            session: Database session.

        Returns:
            The chat channel if found, None otherwise.
        """
        if isinstance(channel, int) or channel.isdigit():
            # Use query instead of get() to ensure object is fully loaded
            result = await session.exec(select(ChatChannel).where(ChatChannel.channel_id == int(channel)))
            channel_ = result.first()
            if channel_ is not None:
                return channel_
        result = await session.exec(select(ChatChannel).where(ChatChannel.channel_name == channel))
        return result.first()

    @classmethod
    async def get_pm_channel(cls, user1: int, user2: int, session: AsyncSession) -> "ChatChannel | None":
        channel = await cls.get(f"pm_{user1}_{user2}", session)
        if channel is None:
            channel = await cls.get(f"pm_{user2}_{user1}", session)
        return channel


# ChatMessage
class ChatMessageDict(TypedDict):
    """TypedDict representation of a chat message."""

    channel_id: int
    content: str
    message_id: int
    sender_id: int
    timestamp: datetime
    type: MessageType
    uuid: str | None
    is_action: NotRequired[bool]
    sender: NotRequired[UserDict]


class ChatMessageModel(DatabaseModel[ChatMessageDict]):
    """Base model for chat messages with transformation support."""

    channel_id: int = Field(index=True, foreign_key="chat_channels.channel_id")
    content: str = Field(sa_column=Column(VARCHAR(1000)))
    message_id: int = Field(index=True, primary_key=True, default=None)
    sender_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    timestamp: datetime = Field(sa_column=Column(DateTime, index=True), default_factory=utcnow)
    type: MessageType = Field(default=MessageType.PLAIN, index=True, exclude=True)
    uuid: str | None = Field(default=None)

    @included
    @staticmethod
    async def is_action(_session: AsyncSession, db_message: "ChatMessage") -> bool:
        return db_message.type == MessageType.ACTION

    @ondemand
    @staticmethod
    async def sender(_session: AsyncSession, db_message: "ChatMessage") -> UserDict:
        return await UserModel.transform(db_message.user)


class ChatMessage(ChatMessageModel, table=True):
    """Database table model for chat messages."""

    __tablename__: str = "chat_messages"
    user: User = Relationship(sa_relationship_kwargs={"lazy": "joined"})
    channel: "ChatChannel" = Relationship()


class SilenceUser(UTCBaseModel, SQLModel, table=True):
    """Records user silences (mutes) in specific channels."""

    __tablename__: str = "chat_silence_users"
    id: int = Field(primary_key=True, default=None, index=True)
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    channel_id: int = Field(foreign_key="chat_channels.channel_id", index=True)
    until: datetime | None = Field(sa_column=Column(DateTime, index=True), default=None)
    reason: str | None = Field(default=None, sa_column=Column(VARCHAR(255), index=True))
    banned_at: datetime = Field(sa_column=Column(DateTime, index=True), default_factory=utcnow)


class UserSilenceResp(SQLModel):
    """Response model for user silence information."""

    id: int
    user_id: int

    @classmethod
    def from_db(cls, db_silence: SilenceUser) -> "UserSilenceResp":
        return cls(
            id=db_silence.id,
            user_id=db_silence.user_id,
        )
