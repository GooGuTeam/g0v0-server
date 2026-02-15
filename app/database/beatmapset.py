"""Beatmapset database models and related utilities.

This module provides models for beatmapsets (collections of beatmap difficulties),
including metadata, covers, nominations, and transformation logic.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, NotRequired, TypedDict

from app.config import settings
from app.models.beatmap import BeatmapRankStatus, Genre, Language
from app.models.score import GameMode

from ._base import DatabaseModel, OnDemand, included, ondemand
from .beatmap_playcounts import BeatmapPlaycounts
from .user import User, UserDict

from pydantic import BaseModel
from sqlalchemy import JSON, Boolean, Column, DateTime, Text
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlmodel import Field, Relationship, SQLModel, col, exists, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from app.fetcher import Fetcher

    from .beatmap import Beatmap, BeatmapDict
    from .favourite_beatmapset import FavouriteBeatmapset


BeatmapCovers = TypedDict(
    "BeatmapCovers",
    {
        "cover": str,
        "card": str,
        "list": str,
        "slimcover": str,
        "cover@2x": NotRequired[str | None],
        "card@2x": NotRequired[str | None],
        "list@2x": NotRequired[str | None],
        "slimcover@2x": NotRequired[str | None],
    },
)
"""TypedDict for beatmapset cover image URLs."""


class BeatmapHype(BaseModel):
    """Beatmapset hype status."""

    current: int
    required: int


class BeatmapAvailability(BaseModel):
    """Beatmapset download availability information."""

    more_information: str | None = Field(default=None)
    download_disabled: bool | None = Field(default=None)


class BeatmapNominations(SQLModel):
    """Beatmapset nomination status."""

    current: int | None = Field(default=None)
    required: int | None = Field(default=None)


class BeatmapNomination(TypedDict):
    """Single nomination record."""

    beatmapset_id: int
    reset: bool
    user_id: int
    rulesets: NotRequired[list[GameMode] | None]


class BeatmapDescription(TypedDict):
    """Beatmapset description in bbcode and rendered HTML."""

    bbcode: NotRequired[str | None]
    description: NotRequired[str | None]


class BeatmapTranslationText(BaseModel):
    """Localized text for genre/language."""

    name: str
    id: int | None = None


class BeatmapsetDict(TypedDict):
    """TypedDict representation of a beatmapset for API responses."""

    id: int
    artist: str
    artist_unicode: str
    covers: BeatmapCovers | None
    creator: str
    nsfw: bool
    preview_url: str
    source: str
    spotlight: bool
    title: str
    title_unicode: str
    track_id: int | None
    user_id: int
    video: bool
    current_nominations: list[BeatmapNomination] | None
    description: BeatmapDescription | None
    pack_tags: list[str]

    bpm: NotRequired[float]
    can_be_hyped: NotRequired[bool]
    discussion_locked: NotRequired[bool]
    last_updated: NotRequired[datetime]
    ranked_date: NotRequired[datetime | None]
    storyboard: NotRequired[bool]
    submitted_date: NotRequired[datetime]
    tags: NotRequired[str]
    discussion_enabled: NotRequired[bool]
    legacy_thread_url: NotRequired[str | None]
    status: NotRequired[str]
    ranked: NotRequired[int]
    is_scoreable: NotRequired[bool]
    favourite_count: NotRequired[int]
    genre_id: NotRequired[int]
    hype: NotRequired[BeatmapHype]
    language_id: NotRequired[int]
    play_count: NotRequired[int]
    availability: NotRequired[BeatmapAvailability]
    beatmaps: NotRequired[list["BeatmapDict"]]
    has_favourited: NotRequired[bool]
    recent_favourites: NotRequired[list[UserDict]]
    genre: NotRequired[BeatmapTranslationText]
    language: NotRequired[BeatmapTranslationText]
    nominations: NotRequired["BeatmapNominations"]
    ratings: NotRequired[list[int]]


class BeatmapsetModel(DatabaseModel[BeatmapsetDict]):
    """Base model for beatmapset data with transformation support."""

    BEATMAPSET_TRANSFORMER_INCLUDES: ClassVar[list[str]] = [
        "availability",
        "has_favourited",
        "bpm",
        "deleted_atcan_be_hyped",
        "discussion_locked",
        "is_scoreable",
        "last_updated",
        "legacy_thread_url",
        "ranked",
        "ranked_date",
        "submitted_date",
        "tags",
        "rating",
        "storyboard",
    ]
    API_INCLUDES: ClassVar[list[str]] = [
        *BEATMAPSET_TRANSFORMER_INCLUDES,
        "beatmaps.current_user_playcount",
        "beatmaps.current_user_tag_ids",
        "beatmaps.max_combo",
        "current_nominations",
        "current_user_attributes",
        "description",
        "genre",
        "language",
        "pack_tags",
        "ratings",
        "recent_favourites",
        "related_tags",
        "related_users",
        "user",
        "version_count",
        *[
            f"beatmaps.{inc}"
            for inc in {
                "failtimes",
                "owners",
                "top_tag_ids",
            }
        ],
    ]

    # Beatmapset
    id: int = Field(default=None, primary_key=True, index=True)
    artist: str = Field(index=True)
    artist_unicode: str = Field(index=True)
    covers: BeatmapCovers | None = Field(sa_column=Column(JSON))
    creator: str = Field(index=True)
    nsfw: bool = Field(default=False, sa_column=Column(Boolean))
    preview_url: str
    source: str = Field(default="")
    spotlight: bool = Field(default=False, sa_column=Column(Boolean))
    title: str = Field(index=True)
    title_unicode: str = Field(index=True)
    track_id: int | None = Field(default=None, index=True)  # feature artist?
    user_id: int = Field(index=True)
    video: bool = Field(sa_column=Column(Boolean, index=True))

    # optional
    # converts: list[Beatmap] = Relationship(back_populates="beatmapset")
    current_nominations: OnDemand[list[BeatmapNomination] | None] = Field(None, sa_column=Column(JSON))
    description: OnDemand[BeatmapDescription | None] = Field(default=None, sa_column=Column(JSON))
    # TODO: discussions: list[BeatmapsetDiscussion] = None
    # TODO: current_user_attributes: Optional[CurrentUserAttributes] = None
    # TODO: events: Optional[list[BeatmapsetEvent]] = None

    pack_tags: OnDemand[list[str]] = Field(default=[], sa_column=Column(JSON))
    # TODO: related_users: Optional[list[User]] = None
    # TODO: user: Optional[User] = Field(default=None)

    # BeatmapsetExtended
    bpm: OnDemand[float] = Field(default=0.0)
    can_be_hyped: OnDemand[bool] = Field(default=False, sa_column=Column(Boolean))
    discussion_locked: OnDemand[bool] = Field(default=False, sa_column=Column(Boolean))
    last_updated: OnDemand[datetime] = Field(sa_column=Column(DateTime, index=True))
    ranked_date: OnDemand[datetime | None] = Field(default=None, sa_column=Column(DateTime, index=True))
    storyboard: OnDemand[bool] = Field(default=False, sa_column=Column(Boolean, index=True))
    submitted_date: OnDemand[datetime] = Field(sa_column=Column(DateTime, index=True))
    tags: OnDemand[str] = Field(default="", sa_column=Column(Text))

    @ondemand
    @staticmethod
    async def legacy_thread_url(
        _session: AsyncSession,
        _beatmapset: "Beatmapset",
    ) -> str | None:
        return None

    @included
    @staticmethod
    async def discussion_enabled(
        _session: AsyncSession,
        _beatmapset: "Beatmapset",
    ) -> bool:
        return True

    @included
    @staticmethod
    async def status(
        _session: AsyncSession,
        beatmapset: "Beatmapset",
    ) -> str:
        beatmap_status = beatmapset.beatmap_status
        if settings.enable_all_beatmap_leaderboard and not beatmap_status.has_leaderboard():
            return BeatmapRankStatus.APPROVED.name.lower()
        return beatmap_status.name.lower()

    @included
    @staticmethod
    async def ranked(
        _session: AsyncSession,
        beatmapset: "Beatmapset",
    ) -> int:
        beatmap_status = beatmapset.beatmap_status
        if settings.enable_all_beatmap_leaderboard and not beatmap_status.has_leaderboard():
            return BeatmapRankStatus.APPROVED.value
        return beatmap_status.value

    @ondemand
    @staticmethod
    async def is_scoreable(
        _session: AsyncSession,
        beatmapset: "Beatmapset",
    ) -> bool:
        beatmap_status = beatmapset.beatmap_status
        if settings.enable_all_beatmap_leaderboard:
            return True
        return beatmap_status.has_leaderboard()

    @included
    @staticmethod
    async def favourite_count(
        session: AsyncSession,
        beatmapset: "Beatmapset",
    ) -> int:
        from .favourite_beatmapset import FavouriteBeatmapset

        count = await session.exec(
            select(func.count())
            .select_from(FavouriteBeatmapset)
            .where(FavouriteBeatmapset.beatmapset_id == beatmapset.id)
        )
        return count.one()

    @included
    @staticmethod
    async def genre_id(
        _session: AsyncSession,
        beatmapset: "Beatmapset",
    ) -> int:
        return beatmapset.beatmap_genre.value

    @ondemand
    @staticmethod
    async def hype(
        _session: AsyncSession,
        beatmapset: "Beatmapset",
    ) -> BeatmapHype:
        return BeatmapHype(current=beatmapset.hype_current, required=beatmapset.hype_required)

    @included
    @staticmethod
    async def language_id(
        _session: AsyncSession,
        beatmapset: "Beatmapset",
    ) -> int:
        return beatmapset.beatmap_language.value

    @included
    @staticmethod
    async def play_count(
        session: AsyncSession,
        beatmapset: "Beatmapset",
    ) -> int:
        from .beatmap import Beatmap

        playcount = await session.exec(
            select(func.sum(BeatmapPlaycounts.playcount)).where(
                col(BeatmapPlaycounts.beatmap).has(col(Beatmap.beatmapset_id) == beatmapset.id)
            )
        )
        return int(playcount.first() or 0)

    @ondemand
    @staticmethod
    async def availability(
        _session: AsyncSession,
        beatmapset: "Beatmapset",
    ) -> BeatmapAvailability:
        return BeatmapAvailability(
            more_information=beatmapset.availability_info,
            download_disabled=beatmapset.download_disabled,
        )

    @ondemand
    @staticmethod
    async def beatmaps(
        _session: AsyncSession,
        beatmapset: "Beatmapset",
        includes: list[str] | None = None,
        user: "User | None" = None,
    ) -> list["BeatmapDict"]:
        from .beatmap import BeatmapModel

        return [
            await BeatmapModel.transform(
                beatmap, includes=(includes or []) + BeatmapModel.BEATMAP_TRANSFORMER_INCLUDES, user=user
            )
            for beatmap in await beatmapset.awaitable_attrs.beatmaps
        ]

    # @ondemand
    # @staticmethod
    # async def current_nominations(
    #     _session: AsyncSession,
    #     beatmapset: "Beatmapset",
    # ) -> list[BeatmapNomination] | None:
    #     return beatmapset.current_nominations or []

    @ondemand
    @staticmethod
    async def has_favourited(
        session: AsyncSession,
        beatmapset: "Beatmapset",
        user: User | None = None,
    ) -> bool:
        from .favourite_beatmapset import FavouriteBeatmapset

        if session is None:
            return False
        query = select(FavouriteBeatmapset).where(FavouriteBeatmapset.beatmapset_id == beatmapset.id)
        if user is not None:
            query = query.where(FavouriteBeatmapset.user_id == user.id)
        existing = (await session.exec(query)).first()
        return existing is not None

    @ondemand
    @staticmethod
    async def recent_favourites(
        session: AsyncSession,
        beatmapset: "Beatmapset",
        includes: list[str] | None = None,
    ) -> list[UserDict]:
        from .favourite_beatmapset import FavouriteBeatmapset

        recent_favourites = (
            await session.exec(
                select(FavouriteBeatmapset)
                .where(FavouriteBeatmapset.beatmapset_id == beatmapset.id)
                .order_by(col(FavouriteBeatmapset.date).desc())
                .limit(50)
            )
        ).all()
        return [
            await User.transform(
                (await favourite.awaitable_attrs.user),
                includes=includes,
            )
            for favourite in recent_favourites
        ]

    @ondemand
    @staticmethod
    async def genre(
        _session: AsyncSession,
        beatmapset: "Beatmapset",
    ) -> BeatmapTranslationText:
        return BeatmapTranslationText(
            name=beatmapset.beatmap_genre.name,
            id=beatmapset.beatmap_genre.value,
        )

    @ondemand
    @staticmethod
    async def language(
        _session: AsyncSession,
        beatmapset: "Beatmapset",
    ) -> BeatmapTranslationText:
        return BeatmapTranslationText(
            name=beatmapset.beatmap_language.name,
            id=beatmapset.beatmap_language.value,
        )

    @ondemand
    @staticmethod
    async def nominations(
        _session: AsyncSession,
        beatmapset: "Beatmapset",
    ) -> BeatmapNominations:
        return BeatmapNominations(
            required=beatmapset.nominations_required,
            current=beatmapset.nominations_current,
        )

    # @ondemand
    # @staticmethod
    # async def user(
    #     session: AsyncSession,
    #     beatmapset: Beatmapset,
    #     includes: list[str] | None = None,
    # ) -> dict[str, Any] | None:
    #     db_user = await session.get(User, beatmapset.user_id)
    #     if not db_user:
    #         return None
    #     return await UserResp.transform(db_user, includes=includes)

    @ondemand
    @staticmethod
    async def ratings(
        session: AsyncSession,
        beatmapset: "Beatmapset",
    ) -> list[int]:
        # Provide a stable default shape if no session is available
        if session is None:
            return []

        from .beatmapset_ratings import BeatmapRating

        beatmapset_all_ratings = (
            await session.exec(select(BeatmapRating).where(BeatmapRating.beatmapset_id == beatmapset.id))
        ).all()
        ratings_list = [0] * 11
        for rating in beatmapset_all_ratings:
            ratings_list[rating.rating] += 1
        return ratings_list


class Beatmapset(AsyncAttrs, BeatmapsetModel, table=True):
    """Database table model for beatmapsets."""

    __tablename__: str = "beatmapsets"

    # Beatmapset
    beatmap_status: BeatmapRankStatus = Field(default=BeatmapRankStatus.GRAVEYARD, index=True)

    # optional
    beatmaps: list["Beatmap"] = Relationship(back_populates="beatmapset")
    beatmap_genre: Genre = Field(default=Genre.UNSPECIFIED, index=True)
    beatmap_language: Language = Field(default=Language.UNSPECIFIED, index=True)
    nominations_required: int = Field(default=0)
    nominations_current: int = Field(default=0)

    # BeatmapExtended
    hype_current: int = Field(default=0)
    hype_required: int = Field(default=0)
    availability_info: str | None = Field(default=None)
    download_disabled: bool = Field(default=False, sa_column=Column(Boolean))
    favourites: list["FavouriteBeatmapset"] = Relationship(back_populates="beatmapset")

    @classmethod
    async def from_resp_no_save(cls, resp: BeatmapsetDict) -> "Beatmapset":
        # make a shallow copy so we can mutate safely
        d: dict[str, Any] = dict(resp)

        # nominations = resp.get("nominations")
        # if nominations is not None:
        #     d["nominations_required"] = nominations.required
        #     d["nominations_current"] = nominations.current

        hype = resp.get("hype")
        if hype is not None:
            d["hype_current"] = hype.current
            d["hype_required"] = hype.required

        genre_id = resp.get("genre_id")
        genre = resp.get("genre")
        if genre_id is not None:
            d["beatmap_genre"] = Genre(genre_id)
        elif genre is not None:
            d["beatmap_genre"] = Genre(genre.id)

        language_id = resp.get("language_id")
        language = resp.get("language")
        if language_id is not None:
            d["beatmap_language"] = Language(language_id)
        elif language is not None:
            d["beatmap_language"] = Language(language.id)

        availability = resp.get("availability")
        ranked = resp.get("ranked")
        if ranked is None:
            raise ValueError("ranked field is required")

        beatmapset = Beatmapset.model_validate(
            {
                **d,
                "beatmap_status": BeatmapRankStatus(ranked),
                "availability_info": availability.more_information if availability is not None else None,
                "download_disabled": bool(availability.download_disabled) if availability is not None else False,
            }
        )
        return beatmapset

    @classmethod
    async def from_resp(
        cls,
        session: AsyncSession,
        resp: BeatmapsetDict,
        from_: int = 0,
    ) -> "Beatmapset":
        from .beatmap import Beatmap

        beatmapset_id = resp["id"]
        beatmapset = await cls.from_resp_no_save(resp)
        if not (await session.exec(select(exists()).where(Beatmapset.id == beatmapset_id))).first():
            session.add(beatmapset)
            await session.commit()
        beatmaps = resp.get("beatmaps", [])
        await Beatmap.from_resp_batch(session, beatmaps, from_=from_)
        beatmapset = (await session.exec(select(Beatmapset).where(Beatmapset.id == beatmapset_id))).one()
        return beatmapset

    @classmethod
    async def get_or_fetch(cls, session: AsyncSession, fetcher: "Fetcher", sid: int) -> "Beatmapset":
        from app.service.beatmapset_update_service import get_beatmapset_update_service

        beatmapset = await session.get(Beatmapset, sid)
        if not beatmapset:
            resp = await fetcher.get_beatmapset(sid)
            beatmapset = await cls.from_resp(session, resp)
            await get_beatmapset_update_service().add(resp)
            await session.refresh(beatmapset)
        return beatmapset
