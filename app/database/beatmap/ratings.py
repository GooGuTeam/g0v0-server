from __future__ import annotations

from app.database.beatmap.beatmapset import Beatmapset
from app.database.user.lazer_user import User

from sqlmodel import BigInteger, Column, Field, ForeignKey, Relationship, SQLModel


class BeatmapRating(SQLModel, table=True):
    __tablename__: str = "beatmap_ratings"
    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    beatmapset_id: int = Field(foreign_key="beatmapsets.id", index=True)
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    rating: int

    beatmapset: Beatmapset = Relationship()
    user: User = Relationship()
