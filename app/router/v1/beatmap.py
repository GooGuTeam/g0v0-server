"""Beatmap endpoint module for osu! API v1.

This module provides endpoints for retrieving beatmap information compatible
with the legacy osu! API v1 specification.
"""

from datetime import datetime
from typing import Annotated, Literal

from app.database.beatmap import Beatmap, calculate_beatmap_attributes
from app.database.beatmap_playcounts import BeatmapPlaycounts
from app.database.beatmapset import Beatmapset
from app.database.favourite_beatmapset import FavouriteBeatmapset
from app.database.score import Score
from app.dependencies.database import Database, Redis
from app.dependencies.fetcher import Fetcher
from app.models.beatmap import BeatmapRankStatus, Genre, Language
from app.models.mods import int_to_mods
from app.models.performance import OsuDifficultyAttributes
from app.models.score import GameMode

from .router import AllStrModel, router

from fastapi import Query
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession


class V1Beatmap(AllStrModel):
    """V1 API beatmap response model.

    This model represents a beatmap in the format expected by the legacy osu! API v1.
    All fields are serialized to strings for compatibility.

    Attributes:
        approved: Beatmap rank status (ranked, approved, etc.).
        submit_date: Date the beatmapset was submitted.
        approved_date: Date the beatmapset was ranked/approved.
        last_update: Date the beatmap was last updated.
        artist: Artist name (romanized).
        artist_unicode: Artist name (original).
        beatmap_id: Unique beatmap ID.
        beatmapset_id: Parent beatmapset ID.
        bpm: Beats per minute.
        creator: Mapper username.
        creator_id: Mapper user ID.
        difficultyrating: Star rating.
        diff_aim: Aim difficulty (osu! standard only).
        diff_speed: Speed difficulty (osu! standard only).
        diff_size: Circle Size (CS).
        diff_overall: Overall Difficulty (OD).
        diff_approach: Approach Rate (AR).
        diff_drain: HP Drain.
        hit_length: Playable length in seconds.
        source: Source material.
        genre_id: Genre classification.
        language_id: Language classification.
        title: Song title (romanized).
        title_unicode: Song title (original).
        total_length: Total length including breaks.
        version: Difficulty name.
        file_md5: Beatmap file checksum.
        mode: Game mode (0=osu!, 1=taiko, 2=catch, 3=mania).
        tags: Space-separated tags.
        favourite_count: Number of favorites.
        rating: User rating.
        playcount: Total play count.
        passcount: Total pass count.
        count_normal: Number of hit circles.
        count_slider: Number of sliders.
        count_spinner: Number of spinners.
        max_combo: Maximum achievable combo.
        storyboard: Whether beatmapset has storyboard.
        video: Whether beatmapset has video.
        download_unavailable: Whether download is disabled.
        audio_unavailable: Whether audio is unavailable.
    """

    approved: BeatmapRankStatus
    submit_date: datetime
    approved_date: datetime | None = None
    last_update: datetime
    artist: str
    artist_unicode: str
    beatmap_id: int
    beatmapset_id: int
    bpm: float
    creator: str
    creator_id: int
    difficultyrating: float
    diff_aim: float | None = None
    diff_speed: float | None = None
    diff_size: float  # CS
    diff_overall: float  # OD
    diff_approach: float  # AR
    diff_drain: float  # HP
    hit_length: int
    source: str
    genre_id: Genre
    language_id: Language
    title: str
    title_unicode: str
    total_length: int
    version: str
    file_md5: str
    mode: int
    tags: str
    favourite_count: int
    rating: float
    playcount: int
    passcount: int
    count_normal: int
    count_slider: int
    count_spinner: int
    max_combo: int | None = None
    storyboard: bool
    video: bool
    download_unavailable: bool
    audio_unavailable: bool

    @classmethod
    async def from_db(
        cls,
        session: AsyncSession,
        db_beatmap: Beatmap,
        diff_aim: float | None = None,
        diff_speed: float | None = None,
    ) -> "V1Beatmap":
        """Create a V1Beatmap instance from a database beatmap record.

        Args:
            session: Database session for querying related data.
            db_beatmap: The beatmap database record.
            diff_aim: Pre-calculated aim difficulty (optional).
            diff_speed: Pre-calculated speed difficulty (optional).

        Returns:
            A V1Beatmap instance with all fields populated.
        """
        return cls(
            approved=db_beatmap.beatmap_status,
            submit_date=db_beatmap.beatmapset.submitted_date,
            approved_date=db_beatmap.beatmapset.ranked_date,
            last_update=db_beatmap.last_updated,
            artist=db_beatmap.beatmapset.artist,
            beatmap_id=db_beatmap.id,
            beatmapset_id=db_beatmap.beatmapset.id,
            bpm=db_beatmap.bpm,
            creator=db_beatmap.beatmapset.creator,
            creator_id=db_beatmap.beatmapset.user_id,
            difficultyrating=db_beatmap.difficulty_rating,
            diff_aim=diff_aim,
            diff_speed=diff_speed,
            diff_size=db_beatmap.cs,
            diff_overall=db_beatmap.accuracy,
            diff_approach=db_beatmap.ar,
            diff_drain=db_beatmap.drain,
            hit_length=db_beatmap.hit_length,
            source=db_beatmap.beatmapset.source,
            genre_id=db_beatmap.beatmapset.beatmap_genre,
            language_id=db_beatmap.beatmapset.beatmap_language,
            title=db_beatmap.beatmapset.title,
            total_length=db_beatmap.total_length,
            version=db_beatmap.version,
            file_md5=db_beatmap.checksum,
            mode=int(db_beatmap.mode),
            tags=db_beatmap.beatmapset.tags,
            favourite_count=(
                await session.exec(
                    select(func.count())
                    .select_from(FavouriteBeatmapset)
                    .where(FavouriteBeatmapset.beatmapset_id == db_beatmap.beatmapset.id)
                )
            ).one(),
            rating=0,  # TODO
            playcount=(
                await session.exec(
                    select(func.count())
                    .select_from(BeatmapPlaycounts)
                    .where(BeatmapPlaycounts.beatmap_id == db_beatmap.id)
                )
            ).one(),
            passcount=(
                await session.exec(
                    select(func.count())
                    .select_from(Score)
                    .where(
                        Score.beatmap_id == db_beatmap.id,
                        col(Score.passed).is_(True),
                    )
                )
            ).one(),
            count_normal=db_beatmap.count_circles,
            count_slider=db_beatmap.count_sliders,
            count_spinner=db_beatmap.count_spinners,
            max_combo=db_beatmap.max_combo,
            storyboard=db_beatmap.beatmapset.storyboard,
            video=db_beatmap.beatmapset.video,
            download_unavailable=db_beatmap.beatmapset.download_disabled,
            audio_unavailable=db_beatmap.beatmapset.download_disabled,
            artist_unicode=db_beatmap.beatmapset.artist_unicode,
            title_unicode=db_beatmap.beatmapset.title_unicode,
        )


@router.get(
    "/get_beatmaps",
    name="Get Beatmaps",
    response_model=list[V1Beatmap],
    description="Search for beatmaps based on specified criteria.",
)
async def get_beatmaps(
    session: Database,
    redis: Redis,
    fetcher: Fetcher,
    since: Annotated[datetime | None, Query(description="Beatmaps ranked after this date")] = None,
    beatmapset_id: Annotated[int | None, Query(alias="s", description="Beatmapset ID")] = None,
    beatmap_id: Annotated[int | None, Query(alias="b", description="Beatmap ID")] = None,
    user: Annotated[str | None, Query(alias="u", description="Mapper")] = None,
    type: Annotated[
        Literal["string", "id"] | None, Query(description="User type: string for username / id for user ID")
    ] = None,
    ruleset_id: Annotated[int | None, Query(alias="m", description="Ruleset ID")] = None,  # TODO
    convert: Annotated[bool, Query(alias="a", description="Include converts")] = False,  # TODO
    checksum: Annotated[str | None, Query(alias="h", description="Beatmap file MD5 hash")] = None,
    limit: Annotated[int, Query(ge=1, le=500, description="Maximum number of results to return")] = 500,
    mods: Annotated[int, Query(description="Mods to apply to beatmap attributes")] = 0,
):
    """Retrieve beatmaps based on search criteria.

    This endpoint allows searching for beatmaps using various filters such as
    beatmap ID, beatmapset ID, mapper, or ranking date.

    Args:
        session: Database session.
        redis: Redis connection for caching.
        fetcher: External data fetcher.
        since: Return beatmaps ranked after this date.
        beatmapset_id: Filter by beatmapset ID.
        beatmap_id: Filter by specific beatmap ID.
        user: Filter by mapper username or ID.
        type: Interpret user parameter as 'string' (username) or 'id'.
        ruleset_id: Filter by game mode (not yet implemented).
        convert: Include converted beatmaps (not yet implemented).
        checksum: Filter by beatmap file MD5 hash.
        limit: Maximum number of results (1-500, default 500).
        mods: Mods to apply when calculating difficulty attributes.

    Returns:
        List of V1Beatmap objects matching the criteria.
    """
    beatmaps: list[Beatmap] = []
    results = []
    if beatmap_id is not None:
        beatmaps.append(await Beatmap.get_or_fetch(session, fetcher, beatmap_id))
    elif checksum is not None:
        beatmaps.append(await Beatmap.get_or_fetch(session, fetcher, md5=checksum))
    elif beatmapset_id is not None:
        beatmapset = await Beatmapset.get_or_fetch(session, fetcher, beatmapset_id)
        await beatmapset.awaitable_attrs.beatmaps
        beatmaps = beatmapset.beatmaps[:limit] if len(beatmapset.beatmaps) > limit else beatmapset.beatmaps
    elif user is not None:
        where = Beatmapset.user_id == user if type == "id" or user.isdigit() else Beatmapset.creator == user
        beatmapsets = (await session.exec(select(Beatmapset).where(where))).all()
        for beatmapset in beatmapsets:
            if len(beatmaps) >= limit:
                break
            beatmaps.extend(beatmapset.beatmaps)
    elif since is not None:
        beatmapsets = (
            await session.exec(select(Beatmapset).where(col(Beatmapset.ranked_date) > since).limit(limit))
        ).all()
        for beatmapset in beatmapsets:
            if len(beatmaps) >= limit:
                break
            beatmaps.extend(beatmapset.beatmaps)

    for beatmap in beatmaps:
        if beatmap.mode == GameMode.OSU:
            try:
                attrs = await calculate_beatmap_attributes(
                    beatmap.id,
                    beatmap.mode,
                    sorted(int_to_mods(mods), key=lambda m: m["acronym"]),
                    redis,
                    fetcher,
                )
                aim_diff = None
                speed_diff = None
                if isinstance(attrs, OsuDifficultyAttributes):
                    aim_diff = attrs.aim_difficulty
                    speed_diff = attrs.speed_difficulty
                results.append(await V1Beatmap.from_db(session, beatmap, aim_diff, speed_diff))
                continue
            except Exception:
                ...
        results.append(await V1Beatmap.from_db(session, beatmap, None, None))
    return results
