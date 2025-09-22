from typing import TYPE_CHECKING

from app.database.user.lazer_user import User
from app.models.score import GameMode

from sqlmodel import (
    BigInteger,
    Column,
    Field,
    Float,
    ForeignKey,
    Relationship,
    SQLModel,
)

if TYPE_CHECKING:
    from app.database.beatmap import Beatmap
    from app.database.score import Score


class PPBestScore(SQLModel, table=True):
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
    score: "Score" = Relationship()
    beatmap: "Beatmap" = Relationship()
