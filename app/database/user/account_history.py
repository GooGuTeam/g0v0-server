from datetime import datetime
from enum import Enum

from app.models.model import UTCBaseModel
from app.utils import utcnow

from sqlmodel import BigInteger, Column, Field, ForeignKey, Integer, SQLModel


class UserAccountHistoryType(str, Enum):
    NOTE = "note"
    RESTRICTION = "restriction"
    SLIENCE = "silence"
    TOURNAMENT_BAN = "tournament_ban"


class UserAccountHistoryBase(SQLModel, UTCBaseModel):
    description: str | None = None
    length: int
    permanent: bool = False
    timestamp: datetime = Field(default_factory=utcnow)
    type: UserAccountHistoryType


class UserAccountHistory(UserAccountHistoryBase, table=True):
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


class UserAccountHistoryResp(UserAccountHistoryBase):
    id: int | None = None

    @classmethod
    def from_db(cls, db_model: UserAccountHistory) -> "UserAccountHistoryResp":
        return cls.model_validate(db_model)
