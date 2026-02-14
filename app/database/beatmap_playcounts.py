"""Beatmap play count tracking database models.

This module tracks how many times each user has played each beatmap,
and generates events for playcount milestones.
"""

from typing import TYPE_CHECKING, NotRequired, TypedDict

from app.config import settings
from app.utils import utcnow

from ._base import DatabaseModel, included
from .events import Event, EventType

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlmodel import (
    BigInteger,
    Column,
    Field,
    ForeignKey,
    Index,
    Relationship,
    select,
)
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from .beatmap import Beatmap, BeatmapDict
    from .beatmapset import BeatmapsetDict
    from .user import User


class BeatmapPlaycountsDict(TypedDict):
    """TypedDict representation of beatmap play counts."""

    user_id: int
    beatmap_id: int
    count: NotRequired[int]
    beatmap: NotRequired["BeatmapDict"]
    beatmapset: NotRequired["BeatmapsetDict"]


class BeatmapPlaycountsModel(AsyncAttrs, DatabaseModel[BeatmapPlaycountsDict]):
    """Base model for beatmap play counts with transformation support."""

    id: int = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True), exclude=True)
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    beatmap_id: int = Field(foreign_key="beatmaps.id", index=True)
    playcount: int = Field(default=0, exclude=True)

    @included
    @staticmethod
    async def count(_session: AsyncSession, obj: "BeatmapPlaycounts") -> int:
        return obj.playcount

    @included
    @staticmethod
    async def beatmap(
        _session: AsyncSession, obj: "BeatmapPlaycounts", includes: list[str] | None = None
    ) -> "BeatmapDict":
        from .beatmap import BeatmapModel

        await obj.awaitable_attrs.beatmap
        return await BeatmapModel.transform(obj.beatmap, includes=includes)

    @included
    @staticmethod
    async def beatmapset(
        _session: AsyncSession, obj: "BeatmapPlaycounts", includes: list[str] | None = None
    ) -> "BeatmapsetDict":
        from .beatmap import BeatmapsetModel

        await obj.awaitable_attrs.beatmap
        return await BeatmapsetModel.transform(obj.beatmap.beatmapset, includes=includes)


class BeatmapPlaycounts(BeatmapPlaycountsModel, table=True):
    """Database table for beatmap play counts."""

    __tablename__: str = "beatmap_playcounts"
    __table_args__ = (Index("idx_beatmap_playcounts_playcount_id", "playcount", "id"),)

    user: "User" = Relationship()
    beatmap: "Beatmap" = Relationship()


async def process_beatmap_playcount(session: AsyncSession, user_id: int, beatmap_id: int) -> None:
    """Increment playcount for a user on a beatmap.

    Creates milestone events every 100 plays.

    Args:
        session: Database session.
        user_id: The user ID.
        beatmap_id: The beatmap ID.
    """
    existing_playcount = (
        await session.exec(
            select(BeatmapPlaycounts).where(
                BeatmapPlaycounts.user_id == user_id,
                BeatmapPlaycounts.beatmap_id == beatmap_id,
            )
        )
    ).first()

    if existing_playcount:
        existing_playcount.playcount += 1
        if existing_playcount.playcount % 100 == 0:
            playcount_event = Event(
                created_at=utcnow(),
                type=EventType.BEATMAP_PLAYCOUNT,
                user_id=user_id,
            )
            await existing_playcount.awaitable_attrs.beatmap
            playcount_event.event_payload = {
                "count": existing_playcount.playcount,
                "beatmap": {
                    "title": existing_playcount.beatmap.version,
                    "url": existing_playcount.beatmap.url.replace("https://osu.ppy.sh/", settings.web_url),
                },
            }
            session.add(playcount_event)
    else:
        new_playcount = BeatmapPlaycounts(user_id=user_id, beatmap_id=beatmap_id, playcount=1)
        session.add(new_playcount)
