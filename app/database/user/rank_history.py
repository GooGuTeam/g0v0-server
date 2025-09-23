from datetime import (
    date as dt,
)
from typing import TYPE_CHECKING, Optional

from app.models.score import GameMode
from app.utils import utcnow

from pydantic import BaseModel
from sqlmodel import (
    BigInteger,
    Column,
    Date,
    Field,
    ForeignKey,
    Relationship,
    SQLModel,
    col,
    select,
)
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from .lazer_user import User


class RankHistory(SQLModel, table=True):
    __tablename__: str = "rank_history"

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    mode: GameMode
    rank: int
    date: dt = Field(
        default_factory=lambda: utcnow().date(),
        sa_column=Column(Date, index=True),
    )

    user: Optional["User"] = Relationship(back_populates="rank_history")


class RankTop(SQLModel, table=True):
    __tablename__: str = "rank_top"

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    mode: GameMode
    rank: int
    date: dt = Field(
        default_factory=lambda: utcnow().date(),
        sa_column=Column(Date, index=True),
    )


class RankHistoryResp(BaseModel):
    mode: GameMode
    data: list[int]

    @classmethod
    async def from_db(cls, session: AsyncSession, user_id: int, mode: GameMode) -> "RankHistoryResp":
        results = (
            await session.exec(
                select(RankHistory)
                .where(RankHistory.user_id == user_id, RankHistory.mode == mode)
                .order_by(col(RankHistory.date).desc())
                .limit(90)
            )
        ).all()
        data = [result.rank for result in results]
        if len(data) != 90:
            data.extend([0] * (90 - len(data)))
        data.reverse()
        return cls(mode=mode, data=data)
