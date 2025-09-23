from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.database.room.participated_user import RoomParticipatedUser
from app.database.room.playlist_attempts import PlaylistAggregateScore
from app.models.model import UTCBaseModel
from app.models.room import (
    MatchType,
    QueueMode,
    RoomCategory,
    RoomDifficultyRange,
    RoomPlaylistItemStats,
    RoomStatus,
)
from app.utils import utcnow

from .playlists import Playlist, PlaylistResp

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlmodel import (
    BigInteger,
    Column,
    DateTime,
    Field,
    ForeignKey,
    Relationship,
    SQLModel,
    col,
    func,
    select,
)
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from app.database.user.lazer_user import User, UserResp
    from app.models.signalr.multiplayer_hub import ServerMultiplayerRoom
else:
    User = UserResp = Any  # type: ignore[assignment]


class RoomBase(SQLModel, UTCBaseModel):
    name: str = Field(index=True)
    category: RoomCategory = Field(default=RoomCategory.NORMAL, index=True)
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
    participant_count: int = Field(default=0)
    max_attempts: int | None = Field(default=None)  # playlists
    type: MatchType
    queue_mode: QueueMode
    auto_skip: bool
    auto_start_duration: int
    status: RoomStatus
    channel_id: int | None = None


class Room(AsyncAttrs, RoomBase, table=True):
    __tablename__: str = "rooms"
    id: int = Field(default=None, primary_key=True, index=True)
    host_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    password: str | None = Field(default=None)

    host: "User" = Relationship()
    playlist: list[Playlist] = Relationship(
        sa_relationship_kwargs={
            "lazy": "selectin",
            "cascade": "all, delete-orphan",
            "overlaps": "room",
        }
    )


class RoomResp(RoomBase):
    id: int
    has_password: bool = False
    host: "UserResp | None" = None
    playlist: list[PlaylistResp] = []
    playlist_item_stats: RoomPlaylistItemStats | None = None
    difficulty_range: RoomDifficultyRange | None = None
    current_playlist_item: PlaylistResp | None = None
    current_user_score: PlaylistAggregateScore | None = None
    recent_participants: list["UserResp"] = Field(default_factory=list)
    channel_id: int = 0

    @classmethod
    async def from_db(
        cls,
        room: Room,
        session: AsyncSession,
        include: list[str] = [],
        user: "User | None" = None,
    ) -> "RoomResp":
        d = room.model_dump()
        d["channel_id"] = d.get("channel_id", 0) or 0
        d["has_password"] = bool(room.password)
        resp = cls.model_validate(d)

        stats = RoomPlaylistItemStats(count_active=0, count_total=0)
        difficulty_range = RoomDifficultyRange(
            min=0,
            max=0,
        )
        rulesets = set()
        for playlist in room.playlist:
            stats.count_total += 1
            if not playlist.expired:
                stats.count_active += 1
            rulesets.add(playlist.ruleset_id)
            difficulty_range.min = min(difficulty_range.min, playlist.beatmap.difficulty_rating)
            difficulty_range.max = max(difficulty_range.max, playlist.beatmap.difficulty_rating)
            resp.playlist.append(await PlaylistResp.from_db(playlist, ["beatmap"]))
        stats.ruleset_ids = list(rulesets)
        resp.playlist_item_stats = stats
        resp.difficulty_range = difficulty_range
        resp.current_playlist_item = resp.playlist[-1] if resp.playlist else None
        resp.recent_participants = []
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
            resp.participant_count = (
                await session.exec(
                    select(func.count())
                    .select_from(RoomParticipatedUser)
                    .where(
                        RoomParticipatedUser.room_id == room.id,
                    )
                )
            ).first() or 0
        from app.database.user.lazer_user import UserResp

        for recent_participant in await session.exec(query):
            resp.recent_participants.append(
                await UserResp.from_db(
                    await recent_participant.awaitable_attrs.user,
                    session,
                    include=["statistics"],
                )
            )
        resp.host = await UserResp.from_db(
            await room.awaitable_attrs.host,
            session,
            include=["statistics"],
        )
        if "current_user_score" in include and user:
            resp.current_user_score = await PlaylistAggregateScore.from_db(room.id, user.id, session)
        return resp

    @classmethod
    async def from_hub(cls, server_room: "ServerMultiplayerRoom") -> "RoomResp":
        room = server_room.room
        resp = cls(
            id=room.room_id,
            name=room.settings.name,
            type=room.settings.match_type,
            queue_mode=room.settings.queue_mode,
            auto_skip=room.settings.auto_skip,
            auto_start_duration=int(room.settings.auto_start_duration.total_seconds()),
            status=server_room.status,
            category=server_room.category,
            # duration = room.settings.duration,
            starts_at=server_room.start_at,
            participant_count=len(room.users),
            channel_id=server_room.room.channel_id or 0,
        )
        return resp


class APIUploadedRoom(RoomBase):
    def to_room(self) -> Room:
        """
        将 APIUploadedRoom 转换为 Room 对象，playlist 字段需单独处理。
        """
        room_dict = self.model_dump()
        room_dict.pop("playlist", None)
        # host_id 已在字段中
        return Room(**room_dict)

    id: int | None
    host_id: int | None = None
    playlist: list[Playlist] = Field(default_factory=list)
