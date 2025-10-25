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
    name="获取谱面",
    response_model=list[V1Beatmap],
    description="根据指定条件搜索谱面。",
)
async def get_beatmaps(
    session: Database,
    redis: Redis,
    fetcher: Fetcher,
    since: Annotated[datetime | None, Query(description="自指定时间后拥有排行榜的谱面")] = None,
    beatmapset_id: Annotated[int | None, Query(alias="s", description="谱面集 ID")] = None,
    beatmap_id: Annotated[int | None, Query(alias="b", description="谱面 ID")] = None,
    user: Annotated[str | None, Query(alias="u", description="谱师")] = None,
    type: Annotated[Literal["string", "id"] | None, Query(description="用户类型：string 用户名称 / id 用户 ID")] = None,
    ruleset_id: Annotated[int | None, Query(alias="m", description="Ruleset ID")] = None,  # TODO
    convert: Annotated[bool, Query(alias="a", description="转谱")] = False,  # TODO
    checksum: Annotated[str | None, Query(alias="h", description="谱面文件 MD5")] = None,
    limit: Annotated[int, Query(ge=1, le=500, description="返回结果数量限制")] = 500,
    mods: Annotated[int, Query(description="应用到谱面属性的 MOD")] = 0,
):
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
