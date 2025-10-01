from typing import TYPE_CHECKING

from app.calculator import calculate_score_to_level
from app.database.statistics import UserStatistics
from app.models.score import GameMode, Rank

from .lazer_user import User

from sqlmodel import (
    JSON,
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
    from .beatmap import Beatmap
    from .score import Score


class BestScore(SQLModel, table=True):
    __tablename__: str = "total_score_best_scores"
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    score_id: int = Field(sa_column=Column(BigInteger, ForeignKey("scores.id"), primary_key=True))
    beatmap_id: int = Field(foreign_key="beatmaps.id", index=True)
    gamemode: GameMode = Field(index=True)
    total_score: int = Field(default=0, sa_column=Column(BigInteger))
    mods: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON),
    )
    rank: Rank

    user: User = Relationship()
    score: "Score" = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "[BestScore.score_id]",
            "lazy": "joined",
        },
        back_populates="best_score",
    )
    beatmap: "Beatmap" = Relationship()

    async def delete(self, session: AsyncSession):
        from .score import Score

        statistics = await session.exec(
            select(UserStatistics).where(UserStatistics.user_id == self.user_id, UserStatistics.mode == self.gamemode)
        )
        statistics = statistics.first()
        if statistics:
            statistics.total_score -= self.total_score
            statistics.ranked_score -= self.total_score
            statistics.level_current = calculate_score_to_level(statistics.total_score)
            match self.rank:
                case Rank.X:
                    statistics.grade_ss -= 1
                case Rank.XH:
                    statistics.grade_ssh -= 1
                case Rank.S:
                    statistics.grade_s -= 1
                case Rank.SH:
                    statistics.grade_sh -= 1
                case Rank.A:
                    statistics.grade_a -= 1

            max_combo = (
                await session.exec(
                    select(func.max(Score.max_combo)).where(
                        Score.user_id == self.user_id, col(Score.id).in_(select(BestScore.score_id))
                    )
                )
            ).first()
            statistics.maximum_combo = max(0, max_combo or 0)

        await session.delete(self)
