"""User account history database models.

This module tracks user account actions such as restrictions,
silences, and tournament bans.
"""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from app.models.model import UTCBaseModel
from app.utils import utcnow

from sqlmodel import BigInteger, Column, Field, ForeignKey, Integer, Relationship, SQLModel

if TYPE_CHECKING:
    from .user import User


class UserAccountHistoryType(StrEnum):
    """Types of account history entries."""

    NOTE = "note"
    RESTRICTION = "restriction"
    SLIENCE = "silence"
    TOURNAMENT_BAN = "tournament_ban"


class UserAccountHistoryBase(SQLModel, UTCBaseModel):
    """Base fields for account history entries."""

    description: str | None = None
    length: int
    permanent: bool = False
    timestamp: datetime = Field(default_factory=utcnow)
    type: UserAccountHistoryType


class UserAccountHistory(UserAccountHistoryBase, table=True):
    """Database table for user account history entries."""

    __tablename__: str = "user_account_history"

    id: int | None = Field(
        sa_column=Column(
            Integer,
            autoincrement=True,
            index=True,
            primary_key=True,
        )
    )
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))

    user: "User" = Relationship(back_populates="account_history")


class UserAccountHistoryResp(UserAccountHistoryBase):
    """Response model for account history entries."""

    id: int | None = None

    @classmethod
    def from_db(cls, db_model: UserAccountHistory) -> "UserAccountHistoryResp":
        return cls.model_validate(db_model)
