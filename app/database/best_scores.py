from typing import TYPE_CHECKING

from app.models.score import GameMode

from .statistics import UserStatistics
from .user import User

from sqlmodel import (
    BigInteger,
    Column,
    Field,
    Float,
    ForeignKey,
    Relationship,
    SQLModel,
    select,
)
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from .beatmap import Beatmap
    from .score import Score


class BestScore(SQLModel, table=True):
    __tablename__: str = "best_scores"
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    score_id: int = Field(sa_column=Column(BigInteger, ForeignKey("scores.id"), primary_key=True))
    beatmap_id: int = Field(foreign_key="beatmaps.id", index=True)
    gamemode: GameMode = Field(index=True)
    pp: float = Field(
        sa_column=Column(Float, default=0),
    )
    acc: float = Field(
        sa_column=Column(Float, default=0),
    )

    user: User = Relationship()
    score: "Score" = Relationship(
        back_populates="ranked_score",
    )
    beatmap: "Beatmap" = Relationship()

    async def delete(self, session: AsyncSession):
        from .score import calculate_user_pp

        gamemode = self.gamemode
        user_id = self.user_id
        await session.delete(self)
        await session.flush()

        statistics = await session.exec(
            select(UserStatistics).where(UserStatistics.user_id == user_id, UserStatistics.mode == gamemode)
        )
        statistics = statistics.first()
        if statistics:
            statistics.pp, statistics.hit_accuracy = await calculate_user_pp(session, statistics.user_id, gamemode)
