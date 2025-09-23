from typing import TYPE_CHECKING

from app.database.user.lazer_user import User
from app.models.score import GameMode, Rank

from sqlmodel import (
    JSON,
    BigInteger,
    Column,
    Field,
    ForeignKey,
    Relationship,
    SQLModel,
)

if TYPE_CHECKING:
    from app.database.beatmap import Beatmap
    from app.database.score import Score


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
        }
    )
    beatmap: "Beatmap" = Relationship()
