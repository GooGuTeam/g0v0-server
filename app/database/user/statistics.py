from datetime import timedelta
import math
from typing import TYPE_CHECKING

from app.models.score import GameMode
from app.utils import utcnow

from .rank_history import RankHistory

from pydantic import field_validator
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlmodel import (
    BigInteger,
    Column,
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
    from .lazer_user import User, UserResp


class UserStatisticsBase(SQLModel):
    mode: GameMode = Field(index=True)
    count_100: int = Field(default=0, sa_column=Column(BigInteger))
    count_300: int = Field(default=0, sa_column=Column(BigInteger))
    count_50: int = Field(default=0, sa_column=Column(BigInteger))
    count_miss: int = Field(default=0, sa_column=Column(BigInteger))

    pp: float = Field(default=0.0, index=True)
    ranked_score: int = Field(default=0)
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
        """将字符串转换为 GameMode 枚举"""
        if isinstance(v, str):
            try:
                return GameMode(v)
            except ValueError:
                # 如果转换失败，返回默认值
                return GameMode.OSU
        return v


class UserStatistics(AsyncAttrs, UserStatisticsBase, table=True):
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

    user: "User" = Relationship(back_populates="statistics")  # type: ignore[valid-type]


class UserStatisticsResp(UserStatisticsBase):
    user: "UserResp | None" = None
    rank_change_since_30_days: int | None = 0
    global_rank: int | None = Field(default=None)
    country_rank: int | None = Field(default=None)
    grade_counts: dict[str, int] = Field(
        default_factory=lambda: {
            "ss": 0,
            "ssh": 0,
            "s": 0,
            "sh": 0,
            "a": 0,
        }
    )
    level: dict[str, int] = Field(
        default_factory=lambda: {
            "current": 1,
            "progress": 0,
        }
    )

    @classmethod
    async def from_db(
        cls,
        obj: UserStatistics,
        session: AsyncSession,
        user_country: str | None = None,
        include: list[str] = [],
    ) -> "UserStatisticsResp":
        s = cls.model_validate(obj.model_dump())
        s.grade_counts = {
            "ss": obj.grade_ss,
            "ssh": obj.grade_ssh,
            "s": obj.grade_s,
            "sh": obj.grade_sh,
            "a": obj.grade_a,
        }
        s.level = {
            "current": int(obj.level_current),
            "progress": int(math.fmod(obj.level_current, 1) * 100),
        }
        if "user" in include:
            from .lazer_user import RANKING_INCLUDES, UserResp

            user = await UserResp.from_db(await obj.awaitable_attrs.user, session, include=RANKING_INCLUDES)
            s.user = user
            user_country = user.country_code

        s.global_rank = await get_rank(session, obj)
        s.country_rank = await get_rank(session, obj, user_country)

        if "rank_change_since_30_days" in include:
            rank_best = (
                await session.exec(
                    select(func.max(RankHistory.rank)).where(
                        RankHistory.date > utcnow() - timedelta(days=30),
                        RankHistory.user_id == obj.user_id,
                    )
                )
            ).first()
            if rank_best is None or s.global_rank is None:
                s.rank_change_since_30_days = 0
            else:
                s.rank_change_since_30_days = rank_best - s.global_rank

        return s


async def get_rank(session: AsyncSession, statistics: UserStatistics, country: str | None = None) -> int | None:
    from .lazer_user import User

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
