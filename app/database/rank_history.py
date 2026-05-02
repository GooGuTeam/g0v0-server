"""Rank history database models.

This module tracks historical rank data for users over time.
"""

from datetime import (
    date as dt,
)
from typing import TYPE_CHECKING, Optional

from app.helpers import utcnow
from app.models.score import GameMode

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
    from .user import User


class RankHistory(SQLModel, table=True):
    """Daily rank history records for users."""

    __tablename__: str = "rank_history"

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    mode: GameMode
    rank: int
    key_count: int | None = Field(
        default=None, index=True, description="Mania key count (e.g. 4, 7). Null for non-mania modes."
    )
    date: dt = Field(
        default_factory=lambda: utcnow().date(),
        sa_column=Column(Date, index=True),
    )

    user: Optional["User"] = Relationship(back_populates="rank_history")


class RankTop(SQLModel, table=True):
    """Tracks users' peak/highest ranks achieved."""

    __tablename__: str = "rank_top"

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    mode: GameMode
    rank: int
    key_count: int | None = Field(
        default=None, index=True, description="Mania key count (e.g. 4, 7). Null for non-mania modes."
    )
    date: dt = Field(
        default_factory=lambda: utcnow().date(),
        sa_column=Column(Date, index=True),
    )


class RankHistoryResp(BaseModel):
    """Response model for rank history data."""

    mode: GameMode
    data: list[int]
    key_count: int | None = None

    @classmethod
    async def from_db(
        cls,
        session: AsyncSession,
        user_id: int,
        mode: GameMode,
        key_count: int | None = None,
    ) -> "RankHistoryResp":
        wheres = [RankHistory.user_id == user_id, RankHistory.mode == mode]
        if key_count is not None:
            wheres.append(RankHistory.key_count == key_count)

        results = (
            await session.exec(select(RankHistory).where(*wheres).order_by(col(RankHistory.date).desc()).limit(90))
        ).all()
        data = [result.rank for result in results]
        if len(data) != 90:
            data.extend([0] * (90 - len(data)))
        data.reverse()
        return cls(mode=mode, data=data, key_count=key_count)
