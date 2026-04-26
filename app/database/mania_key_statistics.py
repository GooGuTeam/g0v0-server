"""Mania key-specific user statistics database models.

This module provides models for tracking user statistics broken down
by mania key count (e.g. 4K, 7K, 10K), derived from the beatmap's
Circle Size (CS) value rather than mods.
"""

import math
from typing import ClassVar, NotRequired, TypedDict

from app.const import MANIA_MAX_KEY_COUNT, MANIA_MIN_KEY_COUNT
from app.helpers import utcnow
from app.models.score import GameMode

from ._base import DatabaseModel, included, ondemand
from .rank_history import RankHistory
from .user import User, UserDict, UserModel

from pydantic import field_validator
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import joinedload
from sqlmodel import (
    BigInteger,
    Column,
    Field,
    ForeignKey,
    Index,
    Relationship,
    col,
    func,
    select,
)
from sqlmodel.ext.asyncio.session import AsyncSession


class ManiaKeyStatisticsDict(TypedDict):
    """TypedDict representation of mania key statistics."""

    key_count: int
    count_100: int
    count_300: int
    count_50: int
    count_miss: int
    pp: float
    ranked_score: int
    hit_accuracy: float
    total_score: int
    total_hits: int
    maximum_combo: int
    play_count: int
    play_time: int
    is_ranked: bool
    level: NotRequired[dict[str, int]]
    global_rank: NotRequired[int | None]
    grade_counts: NotRequired[dict[str, int]]
    country_rank: NotRequired[int | None]
    user: NotRequired["UserDict"]


class ManiaKeyStatisticsModel(DatabaseModel[ManiaKeyStatisticsDict]):
    """Base model for mania key statistics with transformation support."""

    RANKING_INCLUDES: ClassVar[list[str]] = [
        "user.country",
        "user.cover",
        "user.team",
    ]

    key_count: int = Field(index=True)
    count_100: int = Field(default=0, sa_column=Column(BigInteger))
    count_300: int = Field(default=0, sa_column=Column(BigInteger))
    count_50: int = Field(default=0, sa_column=Column(BigInteger))
    count_miss: int = Field(default=0, sa_column=Column(BigInteger))

    pp: float = Field(default=0.0, index=True)
    ranked_score: int = Field(default=0, sa_column=Column(BigInteger))
    hit_accuracy: float = Field(default=0.00)
    total_score: int = Field(default=0, sa_column=Column(BigInteger))
    total_hits: int = Field(default=0, sa_column=Column(BigInteger))
    maximum_combo: int = Field(default=0)

    play_count: int = Field(default=0)
    play_time: int = Field(default=0, sa_column=Column(BigInteger))
    is_ranked: bool = Field(default=True)

    @field_validator("key_count", mode="before")
    @classmethod
    def validate_key_count(cls, v):
        """Ensure key_count is a positive integer within valid range."""
        if isinstance(v, float):
            v = int(v)
        if isinstance(v, int) and (v < MANIA_MIN_KEY_COUNT or v > MANIA_MAX_KEY_COUNT):
            raise ValueError(f"key_count must be between {MANIA_MIN_KEY_COUNT} and {MANIA_MAX_KEY_COUNT}")
        return v

    @included
    @staticmethod
    async def level(_session: AsyncSession, statistics: "ManiaKeyStatistics") -> dict[str, int]:
        return {
            "current": int(statistics.level_current),
            "progress": int(math.fmod(statistics.level_current, 1) * 100),
        }

    @included
    @staticmethod
    async def global_rank(session: AsyncSession, statistics: "ManiaKeyStatistics") -> int | None:
        return await get_mania_key_rank(session, statistics)

    @included
    @staticmethod
    async def grade_counts(_session: AsyncSession, statistics: "ManiaKeyStatistics") -> dict[str, int]:
        return {
            "ss": statistics.grade_ss,
            "ssh": statistics.grade_ssh,
            "s": statistics.grade_s,
            "sh": statistics.grade_sh,
            "a": statistics.grade_a,
        }

    @ondemand
    @staticmethod
    async def country_rank(
        session: AsyncSession, statistics: "ManiaKeyStatistics", user_country: str | None = None
    ) -> int | None:
        return await get_mania_key_rank(session, statistics, user_country)

    @ondemand
    @staticmethod
    async def user(_session: AsyncSession, statistics: "ManiaKeyStatistics") -> "UserDict":
        user_instance = await statistics.awaitable_attrs.user
        return await UserModel.transform(user_instance)


class ManiaKeyStatistics(AsyncAttrs, ManiaKeyStatisticsModel, table=True):
    """Database table for user statistics per mania key count."""

    __tablename__: str = "lazer_user_mania_key_statistics"
    __table_args__ = (Index("ix_mania_key_stats_user_key", "user_id", "key_count", unique=True),)

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("lazer_users.id"),
            index=True,
        ),
    )
    grade_ss: int = Field(default=0)
    grade_ssh: int = Field(default=0)
    grade_s: int = Field(default=0)
    grade_sh: int = Field(default=0)
    grade_a: int = Field(default=0)

    level_current: float = Field(default=1)

    user: User = Relationship(back_populates="mania_key_statistics")


async def recalculate_mania_key_statistics(
    session: AsyncSession,
    user_id: int,
    key_count: int,
    gamemode: GameMode,
) -> None:
    """Recalculate a user's mania key statistics from scratch.

    This is used when a score is deleted or when data needs to be corrected.
    It queries all mania scores for this user/key_count combination and
    rebuilds the statistics record.

    Args:
        session: Database session.
        user_id: The user whose statistics to recalculate.
        key_count: The mania key count (e.g. 4, 7).
        gamemode: The game mode (should be GameMode.MANIA or a variant).
    """
    from app.calculating import calculate_score_to_level
    from app.config import settings as app_settings
    from app.database.beatmap import Beatmap
    from app.database.score import Score, calculate_playtime, calculate_user_pp
    from app.models.score import Rank

    # Find all mania scores for this user on beatmaps with matching key_count
    scores = (
        await session.exec(
            select(Score)
            .where(
                Score.user_id == user_id,
                Score.gamemode == gamemode,
            )
            .join(Beatmap, Score.beatmap_id == Beatmap.id)
            .where(func.floor(Beatmap.cs) == key_count)
            .options(joinedload(Score.beatmap))
        )
    ).all()

    # Get or create the key statistics record
    key_stats = (
        await session.exec(
            select(ManiaKeyStatistics).where(
                ManiaKeyStatistics.user_id == user_id,
                ManiaKeyStatistics.key_count == key_count,
            )
        )
    ).first()

    if key_stats is None:
        key_stats = ManiaKeyStatistics(
            user_id=user_id,
            key_count=key_count,
        )
        session.add(key_stats)
        await session.flush()

    # Reset all counters
    key_stats.play_count = 0
    key_stats.total_score = 0
    key_stats.ranked_score = 0
    key_stats.maximum_combo = 0
    key_stats.play_time = 0
    key_stats.total_hits = 0
    key_stats.count_100 = 0
    key_stats.count_300 = 0
    key_stats.count_50 = 0
    key_stats.count_miss = 0
    key_stats.grade_ss = 0
    key_stats.grade_ssh = 0
    key_stats.grade_s = 0
    key_stats.grade_sh = 0
    key_stats.grade_a = 0
    key_stats.pp = 0.0
    key_stats.hit_accuracy = 0.0

    cached_best: dict[int, Score] = {}

    for score in scores:
        beatmap = score.beatmap
        if beatmap is None:
            continue

        ranked = beatmap.beatmap_status.has_pp() | app_settings.enable_all_beatmap_pp
        display_score = score.get_display_score()

        key_stats.total_score += display_score

        playtime, is_valid = calculate_playtime(score, beatmap.hit_length)
        if is_valid:
            key_stats.play_time += playtime
            key_stats.play_count += 1

        nlarge_tick_miss = score.nlarge_tick_miss or 0
        nsmall_tick_hit = score.nsmall_tick_hit or 0
        nlarge_tick_hit = score.nlarge_tick_hit or 0

        key_stats.count_300 += score.n300 + score.ngeki
        key_stats.count_100 += score.n100 + score.nkatu
        key_stats.count_50 += score.n50
        key_stats.count_miss += score.nmiss
        key_stats.total_hits += (
            score.n300
            + score.ngeki
            + score.n100
            + score.nkatu
            + score.n50
            + nlarge_tick_hit
            + nlarge_tick_miss
            + nsmall_tick_hit
        )

        if ranked and score.passed:
            key_stats.maximum_combo = max(key_stats.maximum_combo, score.max_combo)
            previous = cached_best.get(score.beatmap_id)
            previous_display = previous.get_display_score() if previous else 0
            difference = display_score - previous_display
            if difference > 0:
                cached_best[score.beatmap_id] = score
                key_stats.ranked_score += difference
                match score.rank:
                    case Rank.X:
                        key_stats.grade_ss += 1
                    case Rank.XH:
                        key_stats.grade_ssh += 1
                    case Rank.S:
                        key_stats.grade_s += 1
                    case Rank.SH:
                        key_stats.grade_sh += 1
                    case Rank.A:
                        key_stats.grade_a += 1
                if previous is not None:
                    match previous.rank:
                        case Rank.X:
                            key_stats.grade_ss -= 1
                        case Rank.XH:
                            key_stats.grade_ssh -= 1
                        case Rank.S:
                            key_stats.grade_s -= 1
                        case Rank.SH:
                            key_stats.grade_sh -= 1
                        case Rank.A:
                            key_stats.grade_a -= 1

    key_stats.level_current = calculate_score_to_level(key_stats.total_score)

    # Recalculate PP
    key_stats.pp, key_stats.hit_accuracy = await calculate_user_pp(session, user_id, gamemode, key_count=key_count)
    key_stats.is_ranked = key_stats.pp > 0

    await session.commit()


async def get_mania_key_rank(
    session: AsyncSession,
    statistics: ManiaKeyStatistics,
    country: str | None = None,
) -> int | None:
    """Get the global or country rank for a user's mania key statistics.

    Args:
        session: Database session.
        statistics: The mania key statistics record.
        country: Optional country code to get country rank.

    Returns:
        The rank, or None if unranked.
    """
    query = select(
        ManiaKeyStatistics.user_id,
        func.row_number().over(order_by=col(ManiaKeyStatistics.pp).desc()).label("rank"),
    ).where(
        ManiaKeyStatistics.key_count == statistics.key_count,
        ManiaKeyStatistics.pp > 0,
        col(ManiaKeyStatistics.is_ranked).is_(True),
    )

    if country is not None:
        query = query.join(User).where(User.country_code == country)

    subq = query.subquery()
    result = await session.exec(select(subq.c.rank).where(subq.c.user_id == statistics.user_id))

    rank = result.first()
    if rank is None:
        return None

    if country is None:
        today = utcnow().date()
        rank_history = (
            await session.exec(
                select(RankHistory).where(
                    RankHistory.user_id == statistics.user_id,
                    RankHistory.mode == GameMode.MANIA,
                    RankHistory.key_count == statistics.key_count,
                    RankHistory.date == today,
                )
            )
        ).first()
        if rank_history is None:
            rank_history = RankHistory(
                user_id=statistics.user_id,
                mode=GameMode.MANIA,
                key_count=statistics.key_count,
                rank=rank,
                date=today,
            )
            session.add(rank_history)
        else:
            rank_history.rank = rank
    return rank
