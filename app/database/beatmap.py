"""Beatmap database models and related utilities.

This module provides models for individual beatmaps (difficulty levels),
including transformation, fetching, caching, and difficulty attribute
calculations.
"""

from datetime import datetime
import hashlib
from typing import TYPE_CHECKING, ClassVar, NotRequired, TypedDict

from app.calculator import get_calculator
from app.config import settings
from app.models.beatmap import BeatmapRankStatus
from app.models.mods import APIMod
from app.models.performance import DifficultyAttributesUnion
from app.models.score import GameMode

from ._base import DatabaseModel, OnDemand, included, ondemand
from .beatmap_playcounts import BeatmapPlaycounts
from .beatmap_tags import BeatmapTagVote
from .beatmapset import Beatmapset, BeatmapsetDict, BeatmapsetModel
from .failtime import FailTime, FailTimeResp
from .user import User, UserDict, UserModel

from pydantic import BaseModel, TypeAdapter
from redis.asyncio import Redis
from sqlalchemy import Column, DateTime
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlmodel import VARCHAR, Field, Relationship, SQLModel, col, exists, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from app.fetcher import Fetcher


class BeatmapOwner(SQLModel):
    """Represents a beatmap owner (mapper)."""

    id: int
    username: str


class BeatmapDict(TypedDict):
    """TypedDict representation of a beatmap for API responses."""

    beatmapset_id: int
    difficulty_rating: float
    id: int
    mode: GameMode
    total_length: int
    user_id: int
    version: str
    url: str

    checksum: NotRequired[str]
    max_combo: NotRequired[int | None]
    ar: NotRequired[float]
    cs: NotRequired[float]
    drain: NotRequired[float]
    accuracy: NotRequired[float]
    bpm: NotRequired[float]
    count_circles: NotRequired[int]
    count_sliders: NotRequired[int]
    count_spinners: NotRequired[int]
    deleted_at: NotRequired[datetime | None]
    hit_length: NotRequired[int]
    last_updated: NotRequired[datetime]

    status: NotRequired[str]
    beatmapset: NotRequired[BeatmapsetDict]
    current_user_playcount: NotRequired[int]
    current_user_tag_ids: NotRequired[list[int]]
    failtimes: NotRequired[FailTimeResp]
    top_tag_ids: NotRequired[list[dict[str, int]]]
    user: NotRequired[UserDict]
    convert: NotRequired[bool]
    is_scoreable: NotRequired[bool]
    mode_int: NotRequired[int]
    ranked: NotRequired[int]
    playcount: NotRequired[int]
    passcount: NotRequired[int]


class BeatmapModel(DatabaseModel[BeatmapDict]):
    """Base model for beatmap data with transformation support."""

    BEATMAP_TRANSFORMER_INCLUDES: ClassVar[list[str]] = [
        "checksum",
        "accuracy",
        "ar",
        "bpm",
        "convert",
        "count_circles",
        "count_sliders",
        "count_spinners",
        "cs",
        "deleted_at",
        "drain",
        "hit_length",
        "is_scoreable",
        "last_updated",
        "mode_int",
        "passcount",
        "playcount",
        "ranked",
        "url",
    ]
    DEFAULT_API_INCLUDES: ClassVar[list[str]] = [
        "beatmapset.ratings",
        "current_user_playcount",
        "failtimes",
        "max_combo",
        "owners",
    ]
    TRANSFORMER_INCLUDES: ClassVar[list[str]] = [*DEFAULT_API_INCLUDES, *BEATMAP_TRANSFORMER_INCLUDES]

    # Beatmap
    beatmapset_id: int = Field(foreign_key="beatmapsets.id", index=True)
    difficulty_rating: float = Field(default=0.0, index=True)
    id: int = Field(primary_key=True, index=True)
    mode: GameMode
    total_length: int
    user_id: int = Field(index=True)
    version: str = Field(index=True)

    url: OnDemand[str]
    # optional
    checksum: OnDemand[str] = Field(sa_column=Column(VARCHAR(32), index=True))
    max_combo: OnDemand[int | None] = Field(default=0)
    # TODO: owners

    # BeatmapExtended
    ar: OnDemand[float] = Field(default=0.0)
    cs: OnDemand[float] = Field(default=0.0)
    drain: OnDemand[float] = Field(default=0.0)  # hp
    accuracy: OnDemand[float] = Field(default=0.0)  # od
    bpm: OnDemand[float] = Field(default=0.0)
    count_circles: OnDemand[int] = Field(default=0)
    count_sliders: OnDemand[int] = Field(default=0)
    count_spinners: OnDemand[int] = Field(default=0)
    deleted_at: OnDemand[datetime | None] = Field(default=None, sa_column=Column(DateTime))
    hit_length: OnDemand[int] = Field(default=0)
    last_updated: OnDemand[datetime] = Field(sa_column=Column(DateTime, index=True))

    @included
    @staticmethod
    async def status(_session: AsyncSession, beatmap: "Beatmap") -> str:
        if settings.enable_all_beatmap_leaderboard and not beatmap.beatmap_status.has_leaderboard():
            return BeatmapRankStatus.APPROVED.name.lower()
        return beatmap.beatmap_status.name.lower()

    @ondemand
    @staticmethod
    async def beatmapset(
        _session: AsyncSession,
        beatmap: "Beatmap",
        includes: list[str] | None = None,
    ) -> BeatmapsetDict | None:
        if beatmap.beatmapset is not None:
            return await BeatmapsetModel.transform(
                beatmap.beatmapset, includes=(includes or []) + Beatmapset.BEATMAPSET_TRANSFORMER_INCLUDES
            )

    @ondemand
    @staticmethod
    async def current_user_playcount(_session: AsyncSession, beatmap: "Beatmap", user: "User") -> int:
        playcount = (
            await _session.exec(
                select(BeatmapPlaycounts.playcount).where(
                    BeatmapPlaycounts.beatmap_id == beatmap.id, BeatmapPlaycounts.user_id == user.id
                )
            )
        ).first()
        return int(playcount or 0)

    @ondemand
    @staticmethod
    async def current_user_tag_ids(_session: AsyncSession, beatmap: "Beatmap", user: "User | None" = None) -> list[int]:
        if user is None:
            return []
        tag_ids = (
            await _session.exec(
                select(BeatmapTagVote.tag_id).where(
                    BeatmapTagVote.beatmap_id == beatmap.id,
                    BeatmapTagVote.user_id == user.id,
                )
            )
        ).all()
        return list(tag_ids)

    @ondemand
    @staticmethod
    async def failtimes(_session: AsyncSession, beatmap: "Beatmap") -> FailTimeResp:
        if beatmap.failtimes is not None:
            return FailTimeResp.from_db(beatmap.failtimes)
        return FailTimeResp()

    @ondemand
    @staticmethod
    async def top_tag_ids(_session: AsyncSession, beatmap: "Beatmap") -> list[dict[str, int]]:
        all_votes = (
            await _session.exec(
                select(BeatmapTagVote.tag_id, func.count().label("vote_count"))
                .where(BeatmapTagVote.beatmap_id == beatmap.id)
                .group_by(col(BeatmapTagVote.tag_id))
                .having(func.count() > settings.beatmap_tag_top_count)
            )
        ).all()
        top_tag_ids: list[dict[str, int]] = []
        for id, votes in all_votes:
            top_tag_ids.append({"tag_id": id, "count": votes})
        top_tag_ids.sort(key=lambda x: x["count"], reverse=True)
        return top_tag_ids

    @ondemand
    @staticmethod
    async def user(
        _session: AsyncSession,
        beatmap: "Beatmap",
        includes: list[str] | None = None,
    ) -> UserDict | None:
        from .user import User

        user = await _session.get(User, beatmap.user_id)
        if user is None:
            return None
        return await UserModel.transform(user, includes=includes)

    @ondemand
    @staticmethod
    async def convert(_session: AsyncSession, _beatmap: "Beatmap") -> bool:
        return False

    @ondemand
    @staticmethod
    async def is_scoreable(_session: AsyncSession, beatmap: "Beatmap") -> bool:
        beatmap_status = beatmap.beatmap_status
        if settings.enable_all_beatmap_leaderboard:
            return True
        return beatmap_status.has_leaderboard()

    @ondemand
    @staticmethod
    async def mode_int(_session: AsyncSession, beatmap: "Beatmap") -> int:
        return int(beatmap.mode)

    @ondemand
    @staticmethod
    async def ranked(_session: AsyncSession, beatmap: "Beatmap") -> int:
        beatmap_status = beatmap.beatmap_status
        if settings.enable_all_beatmap_leaderboard and not beatmap_status.has_leaderboard():
            return BeatmapRankStatus.APPROVED.value
        return beatmap_status.value

    @ondemand
    @staticmethod
    async def playcount(_session: AsyncSession, beatmap: "Beatmap") -> int:
        result = (
            await _session.exec(
                select(func.sum(BeatmapPlaycounts.playcount)).where(BeatmapPlaycounts.beatmap_id == beatmap.id)
            )
        ).first()
        return int(result or 0)

    @ondemand
    @staticmethod
    async def passcount(_session: AsyncSession, beatmap: "Beatmap") -> int:
        from .score import Score

        return (
            await _session.exec(
                select(func.count())
                .select_from(Score)
                .where(
                    Score.beatmap_id == beatmap.id,
                    col(Score.passed).is_(True),
                )
            )
        ).one()


class Beatmap(AsyncAttrs, BeatmapModel, table=True):
    """Database table model for beatmaps (individual difficulties)."""

    __tablename__: str = "beatmaps"

    beatmap_status: BeatmapRankStatus = Field(index=True)
    # optional
    beatmapset: "Beatmapset" = Relationship(back_populates="beatmaps", sa_relationship_kwargs={"lazy": "joined"})
    failtimes: FailTime | None = Relationship(back_populates="beatmap", sa_relationship_kwargs={"lazy": "joined"})

    @classmethod
    async def from_resp_no_save(cls, _session: AsyncSession, resp: BeatmapDict) -> "Beatmap":
        d = {k: v for k, v in resp.items() if k != "beatmapset"}
        beatmapset_id = resp.get("beatmapset_id")
        bid = resp.get("id")
        ranked = resp.get("ranked")
        if beatmapset_id is None or bid is None or ranked is None:
            raise ValueError("beatmapset_id, id and ranked are required")
        beatmap = cls.model_validate(
            {
                **d,
                "beatmapset_id": beatmapset_id,
                "id": bid,
                "beatmap_status": BeatmapRankStatus(ranked),
            }
        )
        return beatmap

    @classmethod
    async def from_resp(cls, session: AsyncSession, resp: BeatmapDict) -> "Beatmap":
        beatmap = await cls.from_resp_no_save(session, resp)
        resp_id = resp.get("id")
        if resp_id is None:
            raise ValueError("id is required")
        if not (await session.exec(select(exists()).where(Beatmap.id == resp_id))).first():
            session.add(beatmap)
            await session.commit()
        return (await session.exec(select(Beatmap).where(Beatmap.id == resp_id))).one()

    @classmethod
    async def from_resp_batch(cls, session: AsyncSession, inp: list[BeatmapDict], from_: int = 0) -> list["Beatmap"]:
        beatmaps = []
        for resp_dict in inp:
            bid = resp_dict.get("id")
            if bid == from_ or bid is None:
                continue

            beatmapset_id = resp_dict.get("beatmapset_id")
            ranked = resp_dict.get("ranked")
            if beatmapset_id is None or ranked is None:
                continue

            # Create beatmap dict, removing beatmapset
            d = {k: v for k, v in resp_dict.items() if k != "beatmapset"}

            beatmap = Beatmap.model_validate(
                {
                    **d,
                    "beatmapset_id": beatmapset_id,
                    "id": bid,
                    "beatmap_status": BeatmapRankStatus(ranked),
                }
            )
            if not (await session.exec(select(exists()).where(Beatmap.id == bid))).first():
                session.add(beatmap)
            beatmaps.append(beatmap)
        await session.commit()
        for beatmap in beatmaps:
            await session.refresh(beatmap)
        return beatmaps

    @classmethod
    async def get_or_fetch(
        cls,
        session: AsyncSession,
        fetcher: "Fetcher",
        bid: int | None = None,
        md5: str | None = None,
    ) -> "Beatmap":
        stmt = select(Beatmap)
        if bid is not None:
            stmt = stmt.where(Beatmap.id == bid)
        elif md5 is not None:
            stmt = stmt.where(Beatmap.checksum == md5)
        else:
            raise ValueError("Either bid or md5 must be provided")
        beatmap = (await session.exec(stmt)).first()
        if not beatmap:
            resp = await fetcher.get_beatmap(bid, md5)
            beatmapset_id = resp.get("beatmapset_id")
            if beatmapset_id is None:
                raise ValueError("beatmapset_id is required")
            r = await session.exec(select(Beatmapset.id).where(Beatmapset.id == beatmapset_id))
            if not r.first():
                set_resp = await fetcher.get_beatmapset(beatmapset_id)
                resp_id = resp.get("id")
                await Beatmapset.from_resp(session, set_resp, from_=resp_id or 0)
            return await Beatmap.from_resp(session, resp)
        return beatmap


class APIBeatmapTag(BaseModel):
    """Beatmap tag with vote count."""

    tag_id: int
    count: int


class BannedBeatmaps(SQLModel, table=True):
    """Table of banned beatmaps that are excluded from leaderboards."""

    __tablename__: str = "banned_beatmaps"
    id: int | None = Field(primary_key=True, index=True, default=None)
    beatmap_id: int = Field(index=True)


async def calculate_beatmap_attributes(
    beatmap_id: int,
    ruleset: GameMode,
    mods_: list[APIMod],
    redis: Redis,
    fetcher: "Fetcher",
) -> DifficultyAttributesUnion:
    key = f"beatmap:{beatmap_id}:{ruleset}:{hashlib.sha256(str(mods_).encode()).hexdigest()}:attributes"
    if await redis.exists(key):
        return TypeAdapter(DifficultyAttributesUnion).validate_json(await redis.get(key))
    resp = await fetcher.get_or_fetch_beatmap_raw(redis, beatmap_id)

    attr = await get_calculator().calculate_difficulty(resp, mods_, ruleset)
    await redis.set(key, attr.model_dump_json())
    return attr


async def clear_cached_beatmap_raws(redis: Redis, beatmaps: list[int] = []):
    """Clear cached beatmap raw data using non-blocking operations.

    Args:
        redis: Redis client instance.
        beatmaps: List of beatmap IDs to clear. If empty, clears all.
    """
    if beatmaps:
        # Delete in batches to avoid blocking with too many keys
        batch_size = 50
        for i in range(0, len(beatmaps), batch_size):
            batch = beatmaps[i : i + batch_size]
            keys = [f"beatmap:{bid}:raw" for bid in batch]
            # Use unlink instead of delete (non-blocking, faster)
            try:
                await redis.unlink(*keys)
            except Exception:
                # Fallback to delete if unlink is not supported
                await redis.delete(*keys)
        return

    await redis.delete("beatmap:*:raw")
