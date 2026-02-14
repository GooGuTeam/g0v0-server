"""Multiplayer room database models.

This module provides models for multiplayer rooms including playlists,
participants, and room configuration.
"""

from datetime import datetime
from typing import ClassVar, NotRequired, TypedDict

from app.models.room import (
    MatchType,
    QueueMode,
    RoomCategory,
    RoomDifficultyRange,
    RoomPlaylistItemStats,
    RoomStatus,
)
from app.utils import utcnow

from ._base import DatabaseModel, included, ondemand
from .item_attempts_count import ItemAttemptsCount, ItemAttemptsCountDict, ItemAttemptsCountModel
from .playlists import Playlist, PlaylistDict, PlaylistModel
from .room_participated_user import RoomParticipatedUser
from .user import User, UserDict, UserModel

from pydantic import field_validator
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlmodel import BigInteger, Column, DateTime, Field, ForeignKey, Relationship, SQLModel, col, select
from sqlmodel.ext.asyncio.session import AsyncSession


class RoomDict(TypedDict):
    """TypedDict representation of a multiplayer room."""

    id: int
    name: str
    category: RoomCategory
    status: RoomStatus
    type: MatchType
    duration: int | None
    starts_at: datetime | None
    ends_at: datetime | None
    max_attempts: int | None
    participant_count: int
    channel_id: int
    queue_mode: QueueMode
    auto_skip: bool
    auto_start_duration: int
    has_password: NotRequired[bool]
    current_playlist_item: NotRequired["PlaylistDict | None"]
    playlist: NotRequired[list["PlaylistDict"]]
    playlist_item_stats: NotRequired[RoomPlaylistItemStats]
    difficulty_range: NotRequired[RoomDifficultyRange]
    host: NotRequired[UserDict]
    recent_participants: NotRequired[list[UserDict]]
    current_user_score: NotRequired["ItemAttemptsCountDict | None"]


class RoomModel(DatabaseModel[RoomDict]):
    """Base model for multiplayer rooms with transformation support."""

    SHOW_RESPONSE_INCLUDES: ClassVar[list[str]] = [
        "current_user_score.playlist_item_attempts",
        "host.country",
        "playlist.beatmap.beatmapset",
        "playlist.beatmap.checksum",
        "playlist.beatmap.max_combo",
        "recent_participants",
    ]

    id: int = Field(default=None, primary_key=True, index=True)
    name: str = Field(index=True)
    category: RoomCategory = Field(default=RoomCategory.NORMAL, index=True)
    status: RoomStatus
    type: MatchType
    duration: int | None = Field(default=None)  # minutes
    starts_at: datetime | None = Field(
        sa_column=Column(
            DateTime(timezone=True),
        ),
        default_factory=utcnow,
    )
    ends_at: datetime | None = Field(
        sa_column=Column(
            DateTime(timezone=True),
        ),
        default=None,
    )
    max_attempts: int | None = Field(default=None)  # playlists
    participant_count: int = Field(default=0)
    channel_id: int = 0
    queue_mode: QueueMode
    auto_skip: bool

    auto_start_duration: int

    @field_validator("channel_id", mode="before")
    @classmethod
    def validate_channel_id(cls, v):
        """Convert None to 0 for channel_id."""
        if v is None:
            return 0
        return v

    @included
    @staticmethod
    async def has_password(_session: AsyncSession, room: "Room") -> bool:
        return bool(room.password)

    @ondemand
    @staticmethod
    async def current_playlist_item(
        _session: AsyncSession, room: "Room", includes: list[str] | None = None
    ) -> "PlaylistDict | None":
        playlists = await room.awaitable_attrs.playlist
        if not playlists:
            return None
        return await PlaylistModel.transform(playlists[-1], includes=includes)

    @ondemand
    @staticmethod
    async def playlist(_session: AsyncSession, room: "Room", includes: list[str] | None = None) -> list["PlaylistDict"]:
        playlists = await room.awaitable_attrs.playlist
        result: list[PlaylistDict] = []
        for playlist_item in playlists:
            result.append(await PlaylistModel.transform(playlist_item, includes=includes))
        return result

    @ondemand
    @staticmethod
    async def playlist_item_stats(_session: AsyncSession, room: "Room") -> RoomPlaylistItemStats:
        playlists = await room.awaitable_attrs.playlist
        stats = RoomPlaylistItemStats(count_active=0, count_total=0, ruleset_ids=[])
        rulesets: set[int] = set()
        for playlist in playlists:
            stats.count_total += 1
            if not playlist.expired:
                stats.count_active += 1
            rulesets.add(playlist.ruleset_id)
        stats.ruleset_ids = list(rulesets)
        return stats

    @ondemand
    @staticmethod
    async def difficulty_range(_session: AsyncSession, room: "Room") -> RoomDifficultyRange:
        playlists = await room.awaitable_attrs.playlist
        if not playlists:
            return RoomDifficultyRange(min=0.0, max=0.0)
        min_diff = float("inf")
        max_diff = float("-inf")
        for playlist in playlists:
            rating = playlist.beatmap.difficulty_rating
            min_diff = min(min_diff, rating)
            max_diff = max(max_diff, rating)
        if min_diff == float("inf"):
            min_diff = 0.0
        if max_diff == float("-inf"):
            max_diff = 0.0
        return RoomDifficultyRange(min=min_diff, max=max_diff)

    @ondemand
    @staticmethod
    async def host(_session: AsyncSession, room: "Room", includes: list[str] | None = None) -> UserDict:
        host_user = await room.awaitable_attrs.host
        return await UserModel.transform(host_user, includes=includes)

    @ondemand
    @staticmethod
    async def recent_participants(session: AsyncSession, room: "Room") -> list[UserDict]:
        participants: list[UserDict] = []
        if room.category == RoomCategory.REALTIME:
            query = (
                select(RoomParticipatedUser)
                .where(
                    RoomParticipatedUser.room_id == room.id,
                    col(RoomParticipatedUser.left_at).is_(None),
                )
                .limit(8)
                .order_by(col(RoomParticipatedUser.joined_at).desc())
            )
        else:
            query = (
                select(RoomParticipatedUser)
                .where(
                    RoomParticipatedUser.room_id == room.id,
                )
                .limit(8)
                .order_by(col(RoomParticipatedUser.joined_at).desc())
            )
        for recent_participant in await session.exec(query):
            user_instance = await recent_participant.awaitable_attrs.user
            participants.append(await UserModel.transform(user_instance))
        return participants

    @ondemand
    @staticmethod
    async def current_user_score(
        session: AsyncSession, room: "Room", includes: list[str] | None = None
    ) -> "ItemAttemptsCountDict | None":
        item_attempt = (
            await session.exec(
                select(ItemAttemptsCount).where(
                    ItemAttemptsCount.room_id == room.id,
                )
            )
        ).first()
        if item_attempt is None:
            return None

        return await ItemAttemptsCountModel.transform(item_attempt, includes=includes)


class Room(AsyncAttrs, RoomModel, table=True):
    """Database table for multiplayer rooms."""

    __tablename__: str = "rooms"

    host_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    password: str | None = Field(default=None)

    host: User = Relationship()
    playlist: list[Playlist] = Relationship(
        sa_relationship_kwargs={
            "lazy": "selectin",
            "cascade": "all, delete-orphan",
            "overlaps": "room",
        }
    )


class APIUploadedRoom(SQLModel):
    """Model for room data uploaded via API."""

    name: str = Field(index=True)
    category: RoomCategory = Field(default=RoomCategory.NORMAL, index=True)
    status: RoomStatus
    type: MatchType
    duration: int | None = Field(default=None)  # minutes
    starts_at: datetime | None = Field(
        sa_column=Column(
            DateTime(timezone=True),
        ),
        default_factory=utcnow,
    )
    ends_at: datetime | None = Field(
        sa_column=Column(
            DateTime(timezone=True),
        ),
        default=None,
    )
    max_attempts: int | None = Field(default=None)  # playlists
    participant_count: int = Field(default=0)
    channel_id: int = 0
    queue_mode: QueueMode
    auto_skip: bool
    auto_start_duration: int
    playlist: list[Playlist] = Field(default_factory=list)
