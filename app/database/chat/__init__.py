from datetime import datetime
from enum import Enum
from typing import Self

from app.database.user.lazer_user import RANKING_INCLUDES, User, UserResp
from app.models.model import UTCBaseModel
from app.utils import utcnow

from pydantic import BaseModel
from redis.asyncio import Redis
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

# ChatChannel


class ChatUserAttributes(BaseModel):
    can_message: bool
    can_message_error: str | None = None
    last_read_id: int


class ChannelType(str, Enum):
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


class ChatChannelBase(SQLModel):
    name: str = Field(sa_column=Column(VARCHAR(50), index=True))
    description: str = Field(sa_column=Column(VARCHAR(255), index=True))
    icon: str | None = Field(default=None)
    type: ChannelType = Field(index=True)


class ChatChannel(ChatChannelBase, table=True):
    __tablename__: str = "chat_channels"
    channel_id: int = Field(primary_key=True, index=True, default=None)

    @classmethod
    async def get(cls, channel: str | int, session: AsyncSession) -> "ChatChannel | None":
        if isinstance(channel, int) or channel.isdigit():
            # 使用查询而不是 get() 来确保对象完全加载
            result = await session.exec(select(ChatChannel).where(ChatChannel.channel_id == int(channel)))
            channel_ = result.first()
            if channel_ is not None:
                return channel_
        result = await session.exec(select(ChatChannel).where(ChatChannel.name == channel))
        return result.first()

    @classmethod
    async def get_pm_channel(cls, user1: int, user2: int, session: AsyncSession) -> "ChatChannel | None":
        channel = await cls.get(f"pm_{user1}_{user2}", session)
        if channel is None:
            channel = await cls.get(f"pm_{user2}_{user1}", session)
        return channel


class ChatChannelResp(ChatChannelBase):
    channel_id: int
    moderated: bool = False
    uuid: str | None = None
    current_user_attributes: ChatUserAttributes | None = None
    last_read_id: int | None = None
    last_message_id: int | None = None
    recent_messages: list["ChatMessageResp"] = Field(default_factory=list)
    users: list[int] = Field(default_factory=list)
    message_length_limit: int = 1000

    @classmethod
    async def from_db(
        cls,
        channel: ChatChannel,
        session: AsyncSession,
        user: User,
        redis: Redis,
        users: list[int] | None = None,
        include_recent_messages: bool = False,
    ) -> Self:
        c = cls.model_validate(channel)
        silence = (
            await session.exec(
                select(SilenceUser).where(
                    SilenceUser.channel_id == channel.channel_id,
                    SilenceUser.user_id == user.id,
                )
            )
        ).first()

        last_msg = await redis.get(f"chat:{channel.channel_id}:last_msg")
        if last_msg and last_msg.isdigit():
            last_msg = int(last_msg)
        else:
            last_msg = None

        last_read_id = await redis.get(f"chat:{channel.channel_id}:last_read:{user.id}")
        if last_read_id and last_read_id.isdigit():
            last_read_id = int(last_read_id)
        else:
            last_read_id = last_msg

        if silence is not None:
            attribute = ChatUserAttributes(
                can_message=False,
                can_message_error=silence.reason or "You are muted in this channel.",
                last_read_id=last_read_id or 0,
            )
            c.moderated = True
        else:
            attribute = ChatUserAttributes(
                can_message=True,
                last_read_id=last_read_id or 0,
            )
            c.moderated = False

        c.current_user_attributes = attribute
        if c.type != ChannelType.PUBLIC and users is not None:
            c.users = users
        c.last_message_id = last_msg
        c.last_read_id = last_read_id

        if include_recent_messages:
            messages = (
                await session.exec(
                    select(ChatMessage)
                    .where(ChatMessage.channel_id == channel.channel_id)
                    .order_by(col(ChatMessage.timestamp).desc())
                    .limit(10)
                )
            ).all()
            c.recent_messages = [await ChatMessageResp.from_db(msg, session, user) for msg in messages]
            c.recent_messages.reverse()

        if c.type == ChannelType.PM and users and len(users) == 2:
            target_user_id = next(u for u in users if u != user.id)
            target_name = await session.exec(select(User.username).where(User.id == target_user_id))
            c.name = target_name.one()
            c.users = [target_user_id, user.id]
        return c


# ChatMessage


class MessageType(str, Enum):
    ACTION = "action"
    MARKDOWN = "markdown"
    PLAIN = "plain"


class ChatMessageBase(UTCBaseModel, SQLModel):
    channel_id: int = Field(index=True, foreign_key="chat_channels.channel_id")
    content: str = Field(sa_column=Column(VARCHAR(1000)))
    message_id: int = Field(index=True, primary_key=True, default=None)
    sender_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    timestamp: datetime = Field(sa_column=Column(DateTime, index=True), default_factory=utcnow)
    type: MessageType = Field(default=MessageType.PLAIN, index=True, exclude=True)
    uuid: str | None = Field(default=None)


class ChatMessage(ChatMessageBase, table=True):
    __tablename__: str = "chat_messages"
    user: User = Relationship(sa_relationship_kwargs={"lazy": "joined"})
    channel: ChatChannel = Relationship()


class ChatMessageResp(ChatMessageBase):
    sender: UserResp | None = None
    is_action: bool = False

    @classmethod
    async def from_db(
        cls, db_message: ChatMessage, session: AsyncSession, user: User | None = None
    ) -> "ChatMessageResp":
        m = cls.model_validate(db_message.model_dump())
        m.is_action = db_message.type == MessageType.ACTION
        if user:
            m.sender = await UserResp.from_db(user, session, RANKING_INCLUDES)
        else:
            m.sender = await UserResp.from_db(db_message.user, session, RANKING_INCLUDES)
        return m


# SilenceUser


class SilenceUser(UTCBaseModel, SQLModel, table=True):
    __tablename__: str = "chat_silence_users"
    id: int = Field(primary_key=True, default=None, index=True)
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    channel_id: int = Field(foreign_key="chat_channels.channel_id", index=True)
    until: datetime | None = Field(sa_column=Column(DateTime, index=True), default=None)
    reason: str | None = Field(default=None, sa_column=Column(VARCHAR(255), index=True))
    banned_at: datetime = Field(sa_column=Column(DateTime, index=True), default_factory=utcnow)


class UserSilenceResp(SQLModel):
    id: int
    user_id: int

    @classmethod
    def from_db(cls, db_silence: SilenceUser) -> "UserSilenceResp":
        return cls(
            id=db_silence.id,
            user_id=db_silence.user_id,
        )
