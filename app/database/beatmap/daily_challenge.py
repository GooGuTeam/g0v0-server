from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.models.model import UTCBaseModel
from app.utils import are_adjacent_weeks, utcnow

from sqlmodel import (
    BigInteger,
    Column,
    DateTime,
    Field,
    ForeignKey,
    Relationship,
    SQLModel,
    select,
)
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from app.database.user.lazer_user import User


class DailyChallengeStatsBase(SQLModel, UTCBaseModel):
    daily_streak_best: int = Field(default=0)
    daily_streak_current: int = Field(default=0)
    last_update: datetime | None = Field(default=None, sa_column=Column(DateTime))
    last_day_streak: datetime | None = Field(default=None, sa_column=Column(DateTime), exclude=True)
    last_weekly_streak: datetime | None = Field(default=None, sa_column=Column(DateTime))
    playcount: int = Field(default=0)
    top_10p_placements: int = Field(default=0)
    top_50p_placements: int = Field(default=0)
    weekly_streak_best: int = Field(default=0)
    weekly_streak_current: int = Field(default=0)


class DailyChallengeStats(DailyChallengeStatsBase, table=True):
    __tablename__: str = "daily_challenge_stats"

    user_id: int | None = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("lazer_users.id"),
            unique=True,
            index=True,
            primary_key=True,
        ),
    )
    user: "User" = Relationship(back_populates="daily_challenge_stats")


class DailyChallengeStatsResp(DailyChallengeStatsBase):
    user_id: int

    @classmethod
    def from_db(
        cls,
        obj: DailyChallengeStats,
    ) -> "DailyChallengeStatsResp":
        stats = cls.model_validate(obj)
        stats.last_update = obj.last_day_streak
        return stats


async def process_daily_challenge_score(session: AsyncSession, user_id: int, room_id: int):
    from app.database.score.playlist_best_score import PlaylistBestScore

    score = (
        await session.exec(
            select(PlaylistBestScore).where(
                PlaylistBestScore.user_id == user_id,
                PlaylistBestScore.room_id == room_id,
                PlaylistBestScore.playlist_id == 0,
            )
        )
    ).first()
    if not score or not score.score.passed:
        return
    stats = await session.get(DailyChallengeStats, user_id)
    if not stats:
        stats = DailyChallengeStats(user_id=user_id)
        session.add(stats)

    stats.playcount += 1
    now = utcnow()
    if stats.last_update is None:
        stats.daily_streak_best = 1
        stats.daily_streak_current = 1
    elif stats.last_update.replace(tzinfo=UTC).date() == now.date() - timedelta(days=1):
        stats.daily_streak_current += 1
        if stats.daily_streak_current > stats.daily_streak_best:
            stats.daily_streak_best = stats.daily_streak_current
    elif stats.last_update.replace(tzinfo=UTC).date() == now.date():
        stats.playcount -= 1
    else:
        stats.daily_streak_current = 1
    if stats.last_weekly_streak is None:
        stats.weekly_streak_current = 1
        stats.weekly_streak_best = 1
    elif are_adjacent_weeks(stats.last_weekly_streak, now):
        stats.weekly_streak_current += 1
        if stats.weekly_streak_current > stats.weekly_streak_best:
            stats.weekly_streak_best = stats.weekly_streak_current
    elif stats.last_weekly_streak.replace(tzinfo=UTC).date() == now.date():
        pass
    else:
        stats.weekly_streak_current = 1
    stats.last_update = now
    stats.last_day_streak = now
    stats.last_weekly_streak = now
