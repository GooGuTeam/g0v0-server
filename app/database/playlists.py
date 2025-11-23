from datetime import datetime
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

from app.models.mods import APIMod
from app.models.playlist import PlaylistItem

from ._base import DatabaseModel, ondemand
from .beatmap import Beatmap, BeatmapDict, BeatmapModel

from sqlmodel import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Field,
    ForeignKey,
    Relationship,
    func,
    select,
)
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from .room import Room
    from .score import ScoreDict


class PlaylistDict(TypedDict):
    id: int
    room_id: int
    beatmap_id: int
    created_at: datetime | None
    ruleset_id: int
    allowed_mods: list[APIMod]
    required_mods: list[APIMod]
    freestyle: bool
    expired: bool
    owner_id: int
    playlist_order: int
    played_at: datetime | None
    beatmap: NotRequired["BeatmapDict"]
    scores: NotRequired[list[dict[str, Any]]]


class PlaylistModel(DatabaseModel[PlaylistDict]):
    id: int = Field(index=True)
    room_id: int = Field(foreign_key="rooms.id")
    beatmap_id: int = Field(
        foreign_key="beatmaps.id",
    )
    created_at: datetime | None = Field(default=None, sa_column_kwargs={"server_default": func.now()})
    ruleset_id: int
    allowed_mods: list[APIMod] = Field(
        default_factory=list,
        sa_column=Column(JSON),
    )
    required_mods: list[APIMod] = Field(
        default_factory=list,
        sa_column=Column(JSON),
    )
    freestyle: bool = Field(default=False)
    expired: bool = Field(default=False)
    owner_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id")))
    playlist_order: int = Field(default=0)
    played_at: datetime | None = Field(
        sa_column=Column(DateTime(timezone=True)),
        default=None,
    )

    @ondemand
    @staticmethod
    async def beatmap(_session: AsyncSession, playlist: "Playlist", includes: list[str] | None = None) -> BeatmapDict:
        return await BeatmapModel.transform(playlist.beatmap, includes=includes)

    @ondemand
    @staticmethod
    async def scores(session: AsyncSession, playlist: "Playlist") -> list["ScoreDict"]:
        from .score import Score, ScoreModel

        scores = (
            await session.exec(
                select(Score).where(
                    Score.playlist_item_id == playlist.id,
                    Score.room_id == playlist.room_id,
                )
            )
        ).all()
        result: list[ScoreDict] = []
        for score in scores:
            result.append(
                await ScoreModel.transform(
                    score,
                )
            )
        return result


class Playlist(PlaylistModel, table=True):
    __tablename__: str = "room_playlists"
    db_id: int = Field(default=None, primary_key=True, index=True, exclude=True)

    beatmap: Beatmap = Relationship(
        sa_relationship_kwargs={
            "lazy": "joined",
        }
    )
    room: "Room" = Relationship()
    updated_at: datetime | None = Field(
        default=None, sa_column_kwargs={"server_default": func.now(), "onupdate": func.now()}
    )

    @classmethod
    async def get_next_id_for_room(cls, room_id: int, session: AsyncSession) -> int:
        stmt = select(func.coalesce(func.max(cls.id), -1) + 1).where(cls.room_id == room_id)
        result = await session.exec(stmt)
        return result.one()

    @classmethod
    async def from_model(cls, playlist: PlaylistItem, room_id: int, session: AsyncSession) -> "Playlist":
        next_id = await cls.get_next_id_for_room(room_id, session=session)
        return cls(
            id=next_id,
            owner_id=playlist.owner_id,
            ruleset_id=playlist.ruleset_id,
            beatmap_id=playlist.beatmap_id,
            required_mods=playlist.required_mods,
            allowed_mods=playlist.allowed_mods,
            expired=playlist.expired,
            playlist_order=playlist.playlist_order,
            played_at=playlist.played_at,
            freestyle=playlist.freestyle,
            room_id=room_id,
        )

    @classmethod
    async def update(cls, playlist: PlaylistItem, room_id: int, session: AsyncSession):
        db_playlist = await session.exec(select(cls).where(cls.id == playlist.id, cls.room_id == room_id))
        db_playlist = db_playlist.first()
        if db_playlist is None:
            raise ValueError("Playlist item not found")
        db_playlist.owner_id = playlist.owner_id
        db_playlist.ruleset_id = playlist.ruleset_id
        db_playlist.beatmap_id = playlist.beatmap_id
        db_playlist.required_mods = playlist.required_mods
        db_playlist.allowed_mods = playlist.allowed_mods
        db_playlist.expired = playlist.expired
        db_playlist.playlist_order = playlist.playlist_order
        db_playlist.played_at = playlist.played_at
        db_playlist.freestyle = playlist.freestyle
        await session.commit()

    @classmethod
    async def add_to_db(cls, playlist: PlaylistItem, room_id: int, session: AsyncSession):
        db_playlist = await cls.from_model(playlist, room_id, session)
        session.add(db_playlist)
        await session.commit()
        await session.refresh(db_playlist)
        playlist.id = db_playlist.id

    @classmethod
    async def delete_item(cls, item_id: int, room_id: int, session: AsyncSession):
        db_playlist = await session.exec(select(cls).where(cls.id == item_id, cls.room_id == room_id))
        db_playlist = db_playlist.first()
        if db_playlist is None:
            raise ValueError("Playlist item not found")
        await session.delete(db_playlist)
        await session.commit()
