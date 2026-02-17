"""User statistics database models.

This module provides models for user performance statistics
including PP, rank, play counts, and accuracy.
"""

from datetime import timedelta
import math
from typing import TYPE_CHECKING, ClassVar, NotRequired, TypedDict

from app.helpers import utcnow
from app.models.score import GameMode

from ._base import DatabaseModel, included, ondemand
from .rank_history import RankHistory

from pydantic import field_validator
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlmodel import (
    BigInteger,
    Column,
    Field,
    ForeignKey,
    Relationship,
    col,
    func,
    select,
)
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from .user import User, UserDict


class UserStatisticsDict(TypedDict):
    """TypedDict representation of user statistics."""

    mode: GameMode
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
    replays_watched_by_others: int
    is_ranked: bool
    level: NotRequired[dict[str, int]]
    global_rank: NotRequired[int | None]
    grade_counts: NotRequired[dict[str, int]]
    rank_change_since_30_days: NotRequired[int]
    country_rank: NotRequired[int | None]
    user: NotRequired["UserDict"]


class UserStatisticsModel(DatabaseModel[UserStatisticsDict]):
    """Base model for user statistics with transformation support."""

    RANKING_INCLUDES: ClassVar[list[str]] = [
        "user.country",
        "user.cover",
        "user.team",
    ]

    mode: GameMode = Field(index=True)
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
    replays_watched_by_others: int = Field(default=0)
    is_ranked: bool = Field(default=True)

    @field_validator("mode", mode="before")
    @classmethod
    def validate_mode(cls, v):
        """Convert string to GameMode enum."""
        if isinstance(v, str):
            try:
                return GameMode(v)
            except ValueError:
                # If conversion fails, return default value
                return GameMode.OSU
        return v

    @included
    @staticmethod
    async def level(_session: AsyncSession, statistics: "UserStatistics") -> dict[str, int]:
        return {
            "current": int(statistics.level_current),
            "progress": int(math.fmod(statistics.level_current, 1) * 100),
        }

    @included
    @staticmethod
    async def global_rank(session: AsyncSession, statistics: "UserStatistics") -> int | None:
        return await get_rank(session, statistics)

    @included
    @staticmethod
    async def grade_counts(_session: AsyncSession, statistics: "UserStatistics") -> dict[str, int]:
        return {
            "ss": statistics.grade_ss,
            "ssh": statistics.grade_ssh,
            "s": statistics.grade_s,
            "sh": statistics.grade_sh,
            "a": statistics.grade_a,
        }

    @ondemand
    @staticmethod
    async def rank_change_since_30_days(session: AsyncSession, statistics: "UserStatistics") -> int:
        global_rank = await get_rank(session, statistics)
        rank_best = (
            await session.exec(
                select(func.max(RankHistory.rank)).where(
                    RankHistory.date > utcnow() - timedelta(days=30),
                    RankHistory.user_id == statistics.user_id,
                )
            )
        ).first()
        if rank_best is None or global_rank is None:
            return 0
        return rank_best - global_rank

    @ondemand
    @staticmethod
    async def country_rank(
        session: AsyncSession, statistics: "UserStatistics", user_country: str | None = None
    ) -> int | None:
        return await get_rank(session, statistics, user_country)

    @ondemand
    @staticmethod
    async def user(_session: AsyncSession, statistics: "UserStatistics") -> "UserDict":
        from .user import UserModel

        user_instance = await statistics.awaitable_attrs.user
        return await UserModel.transform(user_instance)


class UserStatistics(AsyncAttrs, UserStatisticsModel, table=True):
    """Database table for user statistics per game mode."""

    __tablename__: str = "lazer_user_statistics"
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

    user: "User" = Relationship(back_populates="statistics")


async def get_rank(session: AsyncSession, statistics: UserStatistics, country: str | None = None) -> int | None:
    """Get the global or country rank for a user's statistics.

    Args:
        session: Database session.
        statistics: The user statistics record.
        country: Optional country code to get country rank.

    Returns:
        The rank, or None if unranked.
    """
    from .user import User

    query = select(
        UserStatistics.user_id,
        func.row_number().over(order_by=col(UserStatistics.pp).desc()).label("rank"),
    ).where(
        UserStatistics.mode == statistics.mode,
        UserStatistics.pp > 0,
        col(UserStatistics.is_ranked).is_(True),
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
                    RankHistory.mode == statistics.mode,
                    RankHistory.date == today,
                )
            )
        ).first()
        if rank_history is None:
            rank_history = RankHistory(
                user_id=statistics.user_id,
                mode=statistics.mode,
                date=today,
                rank=rank,
            )
            session.add(rank_history)
        else:
            rank_history.rank = rank
    return rank
