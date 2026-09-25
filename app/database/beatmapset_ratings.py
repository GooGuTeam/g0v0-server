"""Beatmapset rating database models.

This module handles user ratings (1-10 stars) for beatmapsets.
"""

from .beatmapset import Beatmapset
from .user import User

from sqlalchemy.orm import Mapped
from sqlmodel import Column, Field, ForeignKey, Integer, Relationship, SQLModel


class BeatmapRating(SQLModel, table=True):
    """Records user ratings for beatmapsets."""

    __tablename__: str = "beatmap_ratings"
    beatmapset_id: int = Field(sa_column=Column(ForeignKey("beatmapsets.id"), primary_key=True))
    user_id: int = Field(sa_column=Column(Integer, ForeignKey("lazer_users.id"), primary_key=True))
    rating: int

    beatmapset: Mapped[Beatmapset] = Relationship()
    user: Mapped[User] = Relationship()
