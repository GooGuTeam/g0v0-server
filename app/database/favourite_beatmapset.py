import datetime

from .beatmapset import Beatmapset
from .user import User

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlmodel import (
    BigInteger,
    Column,
    DateTime,
    Field,
    ForeignKey,
    Relationship,
    SQLModel,
)


class FavouriteBeatmapset(AsyncAttrs, SQLModel, table=True):
    __tablename__: str = "favourite_beatmapset"
    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, autoincrement=True, primary_key=True),
        exclude=True,
    )
    user_id: int = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("lazer_users.id"),
            index=True,
        ),
    )
    beatmapset_id: int = Field(
        default=None,
        sa_column=Column(
            ForeignKey("beatmapsets.id"),
            index=True,
        ),
    )
    date: datetime.datetime = Field(
        default=datetime.datetime.now(datetime.UTC),
        sa_column=Column(
            DateTime,
        ),
    )

    user: User = Relationship(back_populates="favourite_beatmapsets")
    beatmapset: Beatmapset = Relationship(
        sa_relationship_kwargs={
            "lazy": "selectin",
        },
        back_populates="favourites",
    )
