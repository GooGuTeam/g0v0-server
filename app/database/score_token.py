from datetime import datetime

from app.models.model import UTCBaseModel
from app.models.score import GameMode
from app.utils import utcnow

from .beatmap import Beatmap
from .user import User

from sqlalchemy import Column, DateTime, Index
from sqlalchemy.orm import Mapped
from sqlmodel import BigInteger, Field, ForeignKey, Relationship, SQLModel


class ScoreTokenBase(SQLModel, UTCBaseModel):
    id: int | None = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            primary_key=True,
            index=True,
            autoincrement=True,
        ),
    )
    score_id: int | None = Field(sa_column=Column(BigInteger), default=None)
    ruleset_id: GameMode
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id")))
    beatmap_id: int = Field(foreign_key="beatmaps.id")
    room_id: int | None = Field(default=None)
    playlist_item_id: int | None = Field(default=None)  # playlist
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime))


class ScoreToken(ScoreTokenBase, table=True):
    __tablename__: str = "score_tokens"
    __table_args__ = (
        Index("idx_user_playlist", "user_id", "playlist_item_id"),
        Index("idx_playlist_room", "playlist_item_id", "room_id"),
    )

    user: Mapped[User] = Relationship()
    beatmap: Mapped[Beatmap] = Relationship()


class ScoreTokenResp(ScoreTokenBase):
    @classmethod
    def from_db(cls, obj: ScoreToken) -> "ScoreTokenResp":
        return cls.model_validate(obj)
