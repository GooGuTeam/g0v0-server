from datetime import date
from typing import TYPE_CHECKING

from sqlmodel import (
    BigInteger,
    Column,
    Field,
    ForeignKey,
    Relationship,
    SQLModel,
)

if TYPE_CHECKING:
    from .lazer_user import User


class CountBase(SQLModel):
    year: int = Field(index=True)
    month: int = Field(index=True)
    count: int = Field(default=0)


class MonthlyPlaycounts(CountBase, table=True):
    __tablename__: str = "monthly_playcounts"

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    user: "User" = Relationship(back_populates="monthly_playcounts")


class ReplayWatchedCount(CountBase, table=True):
    __tablename__: str = "replays_watched_counts"

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    user: "User" = Relationship(back_populates="replays_watched_counts")


class CountResp(SQLModel):
    start_date: date
    count: int

    @classmethod
    def from_db(cls, db_model: CountBase) -> "CountResp":
        return cls(
            start_date=date(db_model.year, db_model.month, 1),
            count=db_model.count,
        )
