"""Favourite beatmapset database models.

This module tracks users' favourite/bookmarked beatmapsets.
"""

import datetime

from .beatmapset import Beatmapset
from .user import User

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import Mapped
from sqlmodel import Column, DateTime, Field, ForeignKey, Integer, Relationship, SQLModel


class FavouriteBeatmapset(AsyncAttrs, SQLModel, table=True):
    """Records user favourites for beatmapsets."""

    __tablename__: str = "favourite_beatmapset"

    user_id: int = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("lazer_users.id"),
            primary_key=True,
        ),
    )
    beatmapset_id: int = Field(
        default=None,
        sa_column=Column(
            ForeignKey("beatmapsets.id"),
            primary_key=True,
        ),
    )
    date: datetime.datetime = Field(
        default=datetime.datetime.now(datetime.UTC),
        sa_column=Column(
            DateTime,
        ),
    )

    user: Mapped[User] = Relationship(back_populates="favourite_beatmapsets")
    beatmapset: Mapped[Beatmapset] = Relationship(
        sa_relationship_kwargs={
            "lazy": "selectin",
        },
        back_populates="favourites",
    )
