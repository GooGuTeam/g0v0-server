from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, NotRequired, Self, TypedDict

from app.config import settings
from app.models.beatmap import BeatmapRankStatus, Genre, Language
from app.models.score import GameMode

from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import JSON, Boolean, Column, DateTime, Text
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import relationship
from sqlmodel import Field, Relationship, SQLModel, col, exists, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from app.database.beatmap import Beatmap, BeatmapResp
    from app.database.beatmap.favourites import FavouriteBeatmapset
    from app.database.user.lazer_user import User, UserResp
    from app.fetcher import Fetcher


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


class BeatmapHype(BaseModel):
    current: int
    required: int


class BeatmapAvailability(BaseModel):
    more_information: str | None = Field(default=None)
    download_disabled: bool | None = Field(default=None)


class BeatmapNominations(SQLModel):
    current: int | None = Field(default=None)
    required: int | None = Field(default=None)


class BeatmapNomination(TypedDict):
    beatmapset_id: int
    reset: bool
    user_id: int
    rulesets: NotRequired[list[GameMode] | None]


class BeatmapDescription(TypedDict):
    bbcode: NotRequired[str | None]
    description: NotRequired[str | None]


class BeatmapTranslationText(BaseModel):
    name: str
    id: int | None = None


class BeatmapsetBase(SQLModel):
    # Beatmapset
    artist: str = Field(index=True)
    artist_unicode: str = Field(index=True)
    covers: BeatmapCovers | None = Field(sa_column=Column(JSON))
    creator: str = Field(index=True)
    nsfw: bool = Field(default=False, sa_column=Column(Boolean))
    play_count: int = Field(index=True)
    preview_url: str
    source: str = Field(default="")

    spotlight: bool = Field(default=False, sa_column=Column(Boolean))
    title: str = Field(index=True)
    title_unicode: str = Field(index=True)
    user_id: int = Field(index=True)
    video: bool = Field(sa_column=Column(Boolean, index=True))

    # optional
    # converts: list[Beatmap] = Relationship(back_populates="beatmapset")
    current_nominations: list[BeatmapNomination] | None = Field(None, sa_column=Column(JSON))
    description: BeatmapDescription | None = Field(default=None, sa_column=Column(JSON))
    # TODO: discussions: list[BeatmapsetDiscussion] = None
    # TODO: current_user_attributes: Optional[CurrentUserAttributes] = None
    # TODO: events: Optional[list[BeatmapsetEvent]] = None

    pack_tags: list[str] = Field(default=[], sa_column=Column(JSON))
    # TODO: related_users: Optional[list[User]] = None
    # TODO: user: Optional[User] = Field(default=None)
    track_id: int | None = Field(default=None, index=True)  # feature artist?

    # BeatmapsetExtended
    bpm: float = Field(default=0.0)
    can_be_hyped: bool = Field(default=False, sa_column=Column(Boolean))
    discussion_locked: bool = Field(default=False, sa_column=Column(Boolean))
    last_updated: datetime = Field(sa_column=Column(DateTime, index=True))
    ranked_date: datetime | None = Field(default=None, sa_column=Column(DateTime, index=True))
    storyboard: bool = Field(default=False, sa_column=Column(Boolean, index=True))
    submitted_date: datetime = Field(sa_column=Column(DateTime, index=True))
    tags: str = Field(default="", sa_column=Column(Text))


class Beatmapset(AsyncAttrs, BeatmapsetBase, table=True):
    __tablename__: str = "beatmapsets"

    id: int = Field(default=None, primary_key=True, index=True)
    # Beatmapset
    beatmap_status: BeatmapRankStatus = Field(default=BeatmapRankStatus.GRAVEYARD, index=True)

    # optional
    beatmaps: list["Beatmap"] = Relationship(sa_relationship=relationship("Beatmap", back_populates="beatmapset"))
    beatmap_genre: Genre = Field(default=Genre.UNSPECIFIED, index=True)
    beatmap_language: Language = Field(default=Language.UNSPECIFIED, index=True)
    nominations_required: int = Field(default=0)
    nominations_current: int = Field(default=0)

    # BeatmapExtended
    hype_current: int = Field(default=0)
    hype_required: int = Field(default=0)
    availability_info: str | None = Field(default=None)
    download_disabled: bool = Field(default=False, sa_column=Column(Boolean))
    favourites: list["FavouriteBeatmapset"] = Relationship(
        sa_relationship=relationship("FavouriteBeatmapset", back_populates="beatmapset")
    )

    @classmethod
    async def from_resp(cls, session: AsyncSession, resp: "BeatmapsetResp", from_: int = 0) -> "Beatmapset":
        from app.database.beatmap import Beatmap

        d = resp.model_dump()
        update = {}
        if resp.nominations:
            update["nominations_required"] = resp.nominations.required
            update["nominations_current"] = resp.nominations.current
        if resp.hype:
            update["hype_current"] = resp.hype.current
            update["hype_required"] = resp.hype.required
        if resp.genre_id:
            update["beatmap_genre"] = Genre(resp.genre_id)
        elif resp.genre:
            update["beatmap_genre"] = Genre(resp.genre.id)
        if resp.language_id:
            update["beatmap_language"] = Language(resp.language_id)
        elif resp.language:
            update["beatmap_language"] = Language(resp.language.id)
        beatmapset = Beatmapset.model_validate(
            {
                **d,
                "id": resp.id,
                "beatmap_status": BeatmapRankStatus(resp.ranked),
                "availability_info": resp.availability.more_information,
                "download_disabled": resp.availability.download_disabled or False,
            }
        )
        if not (await session.exec(select(exists()).where(Beatmapset.id == resp.id))).first():
            session.add(beatmapset)
            await session.commit()
        await Beatmap.from_resp_batch(session, resp.beatmaps, from_=from_)
        return beatmapset

    @classmethod
    async def get_or_fetch(cls, session: AsyncSession, fetcher: "Fetcher", sid: int) -> "Beatmapset":
        beatmapset = await session.get(Beatmapset, sid)
        if not beatmapset:
            resp = await fetcher.get_beatmapset(sid)
            beatmapset = await cls.from_resp(session, resp)
        return beatmapset


class BeatmapsetResp(BeatmapsetBase):
    id: int
    beatmaps: list["BeatmapResp"] = Field(default_factory=list)
    discussion_enabled: bool = True
    status: str
    ranked: int
    legacy_thread_url: str | None = ""
    is_scoreable: bool
    hype: BeatmapHype | None = None
    availability: BeatmapAvailability
    genre: BeatmapTranslationText | None = None
    genre_id: int
    language: BeatmapTranslationText | None = None
    language_id: int
    nominations: BeatmapNominations | None = None
    has_favourited: bool = False
    favourite_count: int = 0
    recent_favourites: list["UserResp"] = Field(default_factory=list)

    @field_validator(
        "nsfw",
        "spotlight",
        "video",
        "can_be_hyped",
        "discussion_locked",
        "storyboard",
        "discussion_enabled",
        "is_scoreable",
        "has_favourited",
        mode="before",
    )
    @classmethod
    def validate_bool_fields(cls, v):
        """将整数 0/1 转换为布尔值，处理数据库中的布尔字段"""
        if isinstance(v, int):
            return bool(v)
        return v

    @model_validator(mode="after")
    def fix_genre_language(self) -> Self:
        if self.genre is None:
            self.genre = BeatmapTranslationText(name=Genre(self.genre_id).name, id=self.genre_id)
        if self.language is None:
            self.language = BeatmapTranslationText(name=Language(self.language_id).name, id=self.language_id)
        return self

    @classmethod
    async def from_db(
        cls,
        beatmapset: Beatmapset,
        include: list[str] = [],
        session: AsyncSession | None = None,
        user: "User | None" = None,
    ) -> "BeatmapsetResp":
        from app.database.beatmap import BeatmapResp
        from app.database.beatmap.favourites import FavouriteBeatmapset

        update = {
            "beatmaps": [
                await BeatmapResp.from_db(beatmap, from_set=True, session=session)
                for beatmap in await beatmapset.awaitable_attrs.beatmaps
            ],
            "hype": BeatmapHype(current=beatmapset.hype_current, required=beatmapset.hype_required),
            "availability": BeatmapAvailability(
                more_information=beatmapset.availability_info,
                download_disabled=beatmapset.download_disabled,
            ),
            "genre": BeatmapTranslationText(
                name=beatmapset.beatmap_genre.name,
                id=beatmapset.beatmap_genre.value,
            ),
            "language": BeatmapTranslationText(
                name=beatmapset.beatmap_language.name,
                id=beatmapset.beatmap_language.value,
            ),
            "genre_id": beatmapset.beatmap_genre.value,
            "language_id": beatmapset.beatmap_language.value,
            "nominations": BeatmapNominations(
                required=beatmapset.nominations_required,
                current=beatmapset.nominations_current,
            ),
            "is_scoreable": beatmapset.beatmap_status.has_leaderboard(),
            **beatmapset.model_dump(),
        }

        if session is not None:
            # 从数据库读取对应谱面集的评分
            from app.database.beatmap.ratings import BeatmapRating

            beatmapset_all_ratings = (
                await session.exec(select(BeatmapRating).where(BeatmapRating.beatmapset_id == beatmapset.id))
            ).all()
            ratings_list = [0] * 11
            for rating in beatmapset_all_ratings:
                ratings_list[rating.rating] += 1
            update["ratings"] = ratings_list
        else:
            # 返回非空值避免客户端崩溃
            if update.get("ratings") is None:
                update["ratings"] = []

        beatmap_status = beatmapset.beatmap_status
        if settings.enable_all_beatmap_leaderboard and not beatmap_status.has_leaderboard():
            update["status"] = BeatmapRankStatus.APPROVED.name.lower()
            update["ranked"] = BeatmapRankStatus.APPROVED.value
        else:
            update["status"] = beatmap_status.name.lower()
            update["ranked"] = beatmap_status.value

        if session and user:
            existing_favourite = (
                await session.exec(
                    select(FavouriteBeatmapset).where(FavouriteBeatmapset.beatmapset_id == beatmapset.id)
                )
            ).first()
            update["has_favourited"] = existing_favourite is not None

        if session and "recent_favourites" in include:
            from app.database.user.lazer_user import BASE_INCLUDES, UserResp

            recent_favourites = (
                await session.exec(
                    select(FavouriteBeatmapset)
                    .where(
                        FavouriteBeatmapset.beatmapset_id == beatmapset.id,
                    )
                    .order_by(col(FavouriteBeatmapset.date).desc())
                    .limit(50)
                )
            ).all()
            update["recent_favourites"] = [
                await UserResp.from_db(
                    await favourite.awaitable_attrs.user,
                    session=session,
                    include=BASE_INCLUDES,
                )
                for favourite in recent_favourites
            ]

        if session:
            update["favourite_count"] = (
                await session.exec(
                    select(func.count())
                    .select_from(FavouriteBeatmapset)
                    .where(FavouriteBeatmapset.beatmapset_id == beatmapset.id)
                )
            ).one()
        return cls.model_validate(
            update,
        )


class SearchBeatmapsetsResp(SQLModel):
    beatmapsets: list[BeatmapsetResp]
    total: int
    cursor: dict[str, int | float] | None = None
    cursor_string: str | None = None
