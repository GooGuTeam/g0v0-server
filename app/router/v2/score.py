from datetime import UTC, date
import time
from typing import Annotated

from app.calculator import clamp
from app.config import settings
from app.database import (
    Beatmap,
    Playlist,
    Room,
    Score,
    ScoreToken,
    ScoreTokenResp,
    User,
)
from app.database.achievement import process_achievements
from app.database.counts import ReplayWatchedCount
from app.database.daily_challenge import process_daily_challenge_score
from app.database.item_attempts_count import ItemAttemptsCount
from app.database.playlist_best_score import (
    PlaylistBestScore,
    get_position,
    process_playlist_best_score,
)
from app.database.relationship import Relationship, RelationshipType
from app.database.score import (
    LegacyScoreResp,
    MultiplayerScores,
    MultiplayScoreDict,
    ScoreModel,
    get_leaderboard,
    get_score_position_by_id,
    process_score,
    process_user,
)
from app.dependencies.api_version import APIVersion
from app.dependencies.cache import UserCacheService
from app.dependencies.database import Database, Redis, get_redis, with_db
from app.dependencies.fetcher import Fetcher, get_fetcher
from app.dependencies.storage import StorageService
from app.dependencies.user import ClientUser, get_current_user
from app.log import log
from app.models.beatmap import BeatmapRankStatus
from app.models.room import RoomCategory
from app.models.score import (
    GameMode,
    LeaderboardType,
    Rank,
    SoloScoreSubmissionInfo,
)
from app.service.beatmap_cache_service import get_beatmap_cache_service
from app.service.user_cache_service import refresh_user_cache_background
from app.utils import api_doc, utcnow

from .router import router

from fastapi import (
    BackgroundTasks,
    Body,
    Depends,
    Form,
    HTTPException,
    Path,
    Query,
    Security,
)
from fastapi.responses import RedirectResponse
from fastapi_limiter.depends import RateLimiter
from httpx import HTTPError
from pydantic import BaseModel
from sqlalchemy.orm import joinedload
from sqlmodel import col, exists, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

READ_SCORE_TIMEOUT = 10
DEFAULT_SCORE_INCLUDES = ["user", "user.country", "user.cover", "user.team"]
logger = log("Score")


async def _process_user_achievement(score_id: int):
    async with with_db() as session:
        await process_achievements(session, get_redis(), score_id)


async def _process_user(score_id: int, user_id: int, redis: Redis, fetcher: Fetcher):
    async with with_db() as session:
        user = await session.get(User, user_id)
        if not user:
            logger.warning(
                "User {user_id} not found when processing score {score_id}", user_id=user_id, score_id=score_id
            )
            return
        score = await session.get(Score, score_id)
        if not score:
            logger.warning(
                "Score {score_id} not found when processing user {user_id}", score_id=score_id, user_id=user_id
            )
            return
        score_token = (await session.exec(select(ScoreToken.id).where(ScoreToken.score_id == score_id))).first()
        if not score_token:
            logger.warning(
                "ScoreToken for score {score_id} not found when processing user {user_id}",
                score_id=score_id,
                user_id=user_id,
            )
            return
        beatmap = (
            await session.exec(
                select(Beatmap.total_length, Beatmap.beatmap_status).where(Beatmap.id == score.beatmap_id)
            )
        ).first()
        if not beatmap:
            logger.warning(
                "Beatmap {beatmap_id} not found when processing user {user_id} for score {score_id}",
                beatmap_id=score.beatmap_id,
                user_id=user_id,
                score_id=score_id,
            )
            return
        await process_user(session, redis, fetcher, user, score, score_token, beatmap[0], BeatmapRankStatus(beatmap[1]))


async def submit_score(
    background_task: BackgroundTasks,
    info: SoloScoreSubmissionInfo,
    token: int,
    current_user: User,
    db: AsyncSession,
    redis: Redis,
    fetcher: Fetcher,
):
    # 立即获取用户ID，避免后续的懒加载问题
    user_id = current_user.id

    if not info.passed:
        info.rank = Rank.F
    score_token = (
        await db.exec(select(ScoreToken).options(joinedload(ScoreToken.beatmap)).where(ScoreToken.id == token))
    ).first()
    if not score_token or score_token.user_id != user_id:
        raise HTTPException(status_code=404, detail="Score token not found")
    if score_token.score_id:
        score = (
            await db.exec(
                select(Score).where(
                    Score.id == score_token.score_id,
                    Score.user_id == user_id,
                )
            )
        ).first()
        if not score:
            raise HTTPException(status_code=404, detail="Score not found")
    else:
        beatmap = score_token.beatmap_id
        try:
            cache_service = get_beatmap_cache_service(redis, fetcher)
            await cache_service.smart_preload_for_score(beatmap)
        except Exception as e:
            logger.debug(f"Beatmap preload failed for {beatmap}: {e}")

        try:
            db_beatmap = await Beatmap.get_or_fetch(db, fetcher, bid=beatmap)
        except HTTPError:
            raise HTTPException(status_code=404, detail="Beatmap not found")
        status = db_beatmap.beatmap_status
        score = await process_score(
            current_user,
            beatmap,
            status.has_pp() or settings.enable_all_beatmap_pp,
            score_token,
            info,
            db,
        )
        await db.refresh(score_token)
        score_id = score.id
        score_token.score_id = score_id
        await db.commit()
        await db.refresh(score)

        background_task.add_task(_process_user, score_id, user_id, redis, fetcher)
    resp = await ScoreModel.transform(
        score,
    )
    score_gamemode = score.gamemode

    await db.commit()
    if user_id is not None:
        background_task.add_task(refresh_user_cache_background, redis, user_id, score_gamemode)
    background_task.add_task(_process_user_achievement, resp["id"])
    return resp


async def _preload_beatmap_for_pp_calculation(beatmap_id: int) -> None:
    """
    预缓存beatmap文件以加速PP计算
    当玩家开始游玩时异步预加载beatmap原始文件到Redis缓存
    """
    # 检查是否启用了beatmap预加载功能
    if not settings.enable_beatmap_preload:
        return

    try:
        # 异步获取fetcher和redis连接
        fetcher = await get_fetcher()
        redis = get_redis()

        # 检查是否已经缓存，避免重复下载
        cache_key = f"beatmap:raw:{beatmap_id}"
        if await redis.exists(cache_key):
            logger.debug(f"Beatmap {beatmap_id} already cached, skipping preload")
            return

        await fetcher.get_or_fetch_beatmap_raw(redis, beatmap_id)
        logger.debug(f"Successfully preloaded beatmap {beatmap_id} for PP calculation")

    except Exception as e:
        # 预缓存失败不应该影响正常游戏流程
        logger.warning(f"Failed to preload beatmap {beatmap_id}: {e}")


LeaderboardScoreType = ScoreModel.generate_typeddict(tuple(DEFAULT_SCORE_INCLUDES)) | LegacyScoreResp


class BeatmapUserScore(BaseModel):
    position: int
    score: LeaderboardScoreType  # pyright: ignore[reportInvalidTypeForm]


class BeatmapScores(BaseModel):
    scores: list[LeaderboardScoreType]  # pyright: ignore[reportInvalidTypeForm]
    user_score: BeatmapUserScore | None = None
    score_count: int = 0


@router.get(
    "/beatmaps/{beatmap_id}/scores",
    tags=["成绩"],
    responses={
        200: {
            "model": BeatmapScores,
            "description": (
                "排行榜及当前用户成绩。\n\n"
                f"如果 `x-api-version >= 20220705`，返回值为 `BeatmapScores[Score]`"
                f" （包含：{', '.join([f'`{inc}`' for inc in DEFAULT_SCORE_INCLUDES])}），"
                "否则为 `BeatmapScores[LegacyScoreResp]`。"
            ),
        }
    },
    name="获取谱面排行榜",
    description="获取指定谱面在特定条件下的排行榜及当前用户成绩。",
)
async def get_beatmap_scores(
    db: Database,
    api_version: APIVersion,
    beatmap_id: Annotated[int, Path(description="谱面 ID")],
    mode: Annotated[GameMode, Query(description="指定 auleset")],
    mods: Annotated[list[str], Query(default_factory=set, alias="mods[]", description="筛选使用的 Mods (可选，多值)")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    legacy_only: Annotated[bool | None, Query(description="是否只查询 Stable 分数")] = None,
    type: Annotated[
        LeaderboardType,
        Query(
            description=("排行榜类型：GLOBAL 全局 / COUNTRY 国家 / FRIENDS 好友 / TEAM 战队"),
        ),
    ] = LeaderboardType.GLOBAL,
    limit: Annotated[int, Query(ge=1, le=200, description="返回条数 (1-200)")] = 50,
):
    all_scores, user_score, count = await get_leaderboard(
        db,
        beatmap_id,
        mode,
        type=type,
        user=current_user,
        limit=limit,
        mods=sorted(mods),
    )

    user_score_resp = await user_score.to_resp(db, api_version, includes=DEFAULT_SCORE_INCLUDES) if user_score else None
    return {
        "scores": [await score.to_resp(db, api_version, includes=DEFAULT_SCORE_INCLUDES) for score in all_scores],
        "user_score": (
            {
                "score": user_score_resp,
                "position": (
                    await get_score_position_by_id(
                        db,
                        user_score.beatmap_id,
                        user_score.id,
                        mode=user_score.gamemode,
                        user=user_score.user,
                    )
                    or 0
                ),
            }
            if user_score and user_score_resp
            else None
        ),
        "score_count": count,
    }


@router.get(
    "/beatmaps/{beatmap_id}/scores/users/{user_id}",
    tags=["成绩"],
    responses={
        200: {
            "model": BeatmapUserScore,
            "description": (
                "指定用户在指定谱面上的最高成绩\n\n"
                "如果 `x-api-version >= 20220705`，返回值为 `BeatmapUserScore[Score]`，"
                f" （包含：{', '.join([f'`{inc}`' for inc in DEFAULT_SCORE_INCLUDES])}），"
                "否则为 `BeatmapUserScore[LegacyScoreResp]`。"
            ),
        }
    },
    name="获取用户谱面最高成绩",
    description="获取指定用户在指定谱面上的最高成绩。",
)
async def get_user_beatmap_score(
    db: Database,
    api_version: APIVersion,
    beatmap_id: Annotated[int, Path(description="谱面 ID")],
    user_id: Annotated[int, Path(description="用户 ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    legacy_only: Annotated[bool | None, Query(description="是否只查询 Stable 分数")] = None,
    mode: Annotated[GameMode | None, Query(description="指定 ruleset (可选)")] = None,
    mods: Annotated[str | None, Query(description="筛选使用的 Mods (暂未实现)")] = None,
):
    user_score = (
        await db.exec(
            select(Score)
            .where(
                Score.gamemode == mode if mode is not None else True,
                Score.beatmap_id == beatmap_id,
                Score.user_id == user_id,
                col(Score.passed).is_(True),
            )
            .order_by(col(Score.total_score).desc())
            .limit(1)
        )
    ).first()

    if not user_score:
        raise HTTPException(
            status_code=404,
            detail=f"Cannot find user {user_id}'s score on this beatmap",
        )
    else:
        resp = await user_score.to_resp(db, api_version=api_version, includes=DEFAULT_SCORE_INCLUDES)
        return {
            "position": (
                await get_score_position_by_id(
                    db,
                    user_score.beatmap_id,
                    user_score.id,
                    mode=user_score.gamemode,
                    user=user_score.user,
                )
                or 0
            ),
            "score": resp,
        }


@router.get(
    "/beatmaps/{beatmap_id}/scores/users/{user_id}/all",
    tags=["成绩"],
    responses={
        200: api_doc(
            (
                "用户谱面全部成绩\n\n"
                "如果 `x-api-version >= 20220705`，返回值为 `Score`列表，"
                "否则为 `LegacyScoreResp`列表。"
            ),
            list[ScoreModel] | list[LegacyScoreResp],
            DEFAULT_SCORE_INCLUDES,
        )
    },
    name="获取用户谱面全部成绩",
    description="获取指定用户在指定谱面上的全部成绩列表。",
)
async def get_user_all_beatmap_scores(
    db: Database,
    api_version: APIVersion,
    beatmap_id: Annotated[int, Path(description="谱面 ID")],
    user_id: Annotated[int, Path(description="用户 ID")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    legacy_only: Annotated[bool | None, Query(description="是否只查询 Stable 分数")] = None,
    ruleset: Annotated[GameMode | None, Query(description="指定 ruleset (可选)")] = None,
):
    all_user_scores = (
        await db.exec(
            select(Score)
            .where(
                Score.gamemode == ruleset if ruleset is not None else True,
                Score.beatmap_id == beatmap_id,
                Score.user_id == user_id,
                col(Score.passed).is_(True),
                ~User.is_restricted_query(col(Score.user_id)),
            )
            .order_by(col(Score.total_score).desc())
        )
    ).all()

    return [await score.to_resp(db, api_version, includes=DEFAULT_SCORE_INCLUDES) for score in all_user_scores]


@router.post(
    "/beatmaps/{beatmap_id}/solo/scores",
    tags=["游玩"],
    response_model=ScoreTokenResp,
    name="创建单曲成绩提交令牌",
    description="\n为指定谱面创建一次性的成绩提交令牌。",
)
async def create_solo_score(
    background_task: BackgroundTasks,
    db: Database,
    beatmap_id: Annotated[int, Path(description="谱面 ID")],
    beatmap_hash: Annotated[str, Form(description="谱面文件哈希")],
    ruleset_id: Annotated[int, Form(..., description="ruleset 数字 ID (0-3)")],
    current_user: ClientUser,
    version_hash: Annotated[str, Form(description="游戏版本哈希")] = "",
    ruleset_hash: Annotated[str, Form(description="ruleset 版本哈希")] = "",
):
    # 立即获取用户ID，避免懒加载问题
    user_id = current_user.id

    try:
        gamemode = GameMode.from_int(ruleset_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ruleset ID")

    if not (result := gamemode.check_ruleset_version(ruleset_hash)):
        logger.info(
            f"Ruleset version check failed for user {current_user.id} on beatmap {beatmap_id} "
            f"(ruleset: {ruleset_id}, hash: {ruleset_hash})"
        )
        raise HTTPException(
            status_code=422,
            detail=result.error_msg or "Ruleset version check failed",
        )

    background_task.add_task(_preload_beatmap_for_pp_calculation, beatmap_id)
    async with db:
        score_token = ScoreToken(
            user_id=user_id,
            beatmap_id=beatmap_id,
            ruleset_id=GameMode.from_int(ruleset_id),
        )
        db.add(score_token)
        await db.commit()
        await db.refresh(score_token)
        return ScoreTokenResp.from_db(score_token)


@router.put(
    "/beatmaps/{beatmap_id}/solo/scores/{token}",
    tags=["游玩"],
    name="提交单曲成绩",
    description="\n使用令牌提交单曲成绩。",
    responses={200: api_doc("单曲成绩提交结果。", ScoreModel)},
)
async def submit_solo_score(
    background_task: BackgroundTasks,
    db: Database,
    beatmap_id: Annotated[int, Path(description="谱面 ID")],
    token: Annotated[int, Path(description="成绩令牌 ID")],
    info: Annotated[SoloScoreSubmissionInfo, Body(description="成绩提交信息")],
    current_user: ClientUser,
    redis: Redis,
    fetcher: Fetcher,
):
    return await submit_score(background_task, info, token, current_user, db, redis, fetcher)


@router.post(
    "/rooms/{room_id}/playlist/{playlist_id}/scores",
    tags=["游玩"],
    response_model=ScoreTokenResp,
    name="创建房间项目成绩令牌",
    description="\n为房间游玩项目创建成绩提交令牌。",
)
async def create_playlist_score(
    session: Database,
    background_task: BackgroundTasks,
    room_id: int,
    playlist_id: int,
    beatmap_id: Annotated[int, Form(description="谱面 ID")],
    beatmap_hash: Annotated[str, Form(description="游戏版本哈希")],
    ruleset_id: Annotated[int, Form(..., description="ruleset 数字 ID (0-3)")],
    current_user: ClientUser,
    version_hash: Annotated[str, Form(description="谱面版本哈希")] = "",
    ruleset_hash: Annotated[str, Form(description="ruleset 版本哈希")] = "",
):
    try:
        gamemode = GameMode.from_int(ruleset_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ruleset ID")

    if not (result := gamemode.check_ruleset_version(ruleset_hash)):
        logger.info(
            f"Ruleset version check failed for user {current_user.id} on room {room_id}, playlist {playlist_id},"
            f" (ruleset: {ruleset_id}, hash: {ruleset_hash})"
        )
        raise HTTPException(
            status_code=422,
            detail=result.error_msg or "Ruleset version check failed",
        )

    if await current_user.is_restricted(session):
        raise HTTPException(status_code=403, detail="You are restricted from submitting multiplayer scores")

    user_id = current_user.id

    room = await session.get(Room, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    db_room_time = room.ends_at.replace(tzinfo=UTC) if room.ends_at else None
    if db_room_time and db_room_time < utcnow().replace(tzinfo=UTC):
        raise HTTPException(status_code=400, detail="Room has ended")
    item = (await session.exec(select(Playlist).where(Playlist.id == playlist_id, Playlist.room_id == room_id))).first()
    if not item:
        raise HTTPException(status_code=404, detail="Playlist not found")

    # validate
    if not item.freestyle:
        if item.ruleset_id != ruleset_id:
            raise HTTPException(status_code=400, detail="Ruleset mismatch in playlist item")
        if item.beatmap_id != beatmap_id:
            raise HTTPException(status_code=400, detail="Beatmap ID mismatch in playlist item")
    agg = await session.exec(
        select(ItemAttemptsCount).where(
            ItemAttemptsCount.room_id == room_id,
            ItemAttemptsCount.user_id == user_id,
        )
    )
    agg = agg.first()
    if agg and room.max_attempts and agg.attempts >= room.max_attempts:
        raise HTTPException(
            status_code=422,
            detail="You have reached the maximum attempts for this room",
        )
    if item.expired:
        raise HTTPException(status_code=400, detail="Playlist item has expired")
    if item.played_at:
        raise HTTPException(status_code=400, detail="Playlist item has already been played")
    # 这里应该不用验证mod了吧。。。
    background_task.add_task(_preload_beatmap_for_pp_calculation, beatmap_id)
    score_token = ScoreToken(
        user_id=user_id,
        beatmap_id=beatmap_id,
        ruleset_id=GameMode.from_int(ruleset_id),
        playlist_item_id=playlist_id,
        room_id=room_id,
    )
    session.add(score_token)
    await session.commit()
    await session.refresh(score_token)
    return ScoreTokenResp.from_db(score_token)


@router.put(
    "/rooms/{room_id}/playlist/{playlist_id}/scores/{token}",
    tags=["游玩"],
    name="提交房间项目成绩",
    description="\n提交房间游玩项目成绩。",
    responses={200: api_doc("单曲成绩提交结果。", ScoreModel)},
)
async def submit_playlist_score(
    background_task: BackgroundTasks,
    session: Database,
    room_id: int,
    playlist_id: int,
    token: int,
    info: SoloScoreSubmissionInfo,
    current_user: ClientUser,
    redis: Redis,
    fetcher: Fetcher,
):
    if await current_user.is_restricted(session):
        raise HTTPException(status_code=403, detail="You are restricted from submitting multiplayer scores")

    user_id = current_user.id

    item = (await session.exec(select(Playlist).where(Playlist.id == playlist_id, Playlist.room_id == room_id))).first()
    if not item:
        raise HTTPException(status_code=404, detail="Playlist item not found")
    room = await session.get(Room, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    room_category = room.category
    score_resp = await submit_score(
        background_task,
        info,
        token,
        current_user,
        session,
        redis,
        fetcher,
    )
    await process_playlist_best_score(
        room_id,
        playlist_id,
        user_id,
        score_resp["id"],
        score_resp["total_score"],
        session,
        redis,
    )
    await session.commit()
    if room_category == RoomCategory.DAILY_CHALLENGE and score_resp["passed"]:
        await process_daily_challenge_score(session, user_id, room_id)
    await ItemAttemptsCount.get_or_create(room_id, user_id, session)
    await session.commit()
    return score_resp


class IndexedScoreResp(MultiplayerScores):
    total: int
    user_score: MultiplayScoreDict | None = None  # pyright: ignore[reportInvalidTypeForm]


@router.get(
    "/rooms/{room_id}/playlist/{playlist_id}/scores",
    # response_model=IndexedScoreResp,
    name="获取房间项目排行榜",
    description="获取房间游玩项目排行榜。",
    tags=["成绩"],
    responses={
        200: {
            "description": (
                f"房间项目排行榜。\n\n包含：{', '.join([f'`{inc}`' for inc in Score.MULTIPLAYER_BASE_INCLUDES])}"
            ),
            "model": IndexedScoreResp,
        }
    },
)
async def index_playlist_scores(
    session: Database,
    room_id: int,
    playlist_id: int,
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    limit: Annotated[int, Query(ge=1, le=50, description="返回条数 (1-50)")] = 50,
    cursor: Annotated[int, Query(alias="cursor[total_score]", description="分页游标（上一页最低分）")] = 2000000,
):
    # 立即获取用户ID，避免懒加载问题
    user_id = current_user.id

    room = await session.get(Room, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    limit = clamp(limit, 1, 50)

    scores = (
        await session.exec(
            select(PlaylistBestScore)
            .where(
                PlaylistBestScore.playlist_id == playlist_id,
                PlaylistBestScore.room_id == room_id,
                PlaylistBestScore.total_score < cursor,
                ~User.is_restricted_query(col(PlaylistBestScore.user_id)),
            )
            .order_by(col(PlaylistBestScore.total_score).desc())
            .limit(limit + 1)
        )
    ).all()
    has_more = len(scores) > limit
    if has_more:
        scores = scores[:-1]

    user_score = None
    score_resp = [await ScoreModel.transform(score.score, includes=Score.MULTIPLAYER_BASE_INCLUDES) for score in scores]
    for score in score_resp:
        if (room.category == RoomCategory.DAILY_CHALLENGE and score["user_id"] == user_id and score["passed"]) or score[
            "user_id"
        ] == user_id:
            user_score = score
            user_score["position"] = await get_position(room_id, playlist_id, score["id"], session)
            break

    resp = IndexedScoreResp(
        scores=score_resp,
        user_score=user_score,
        total=len(scores),
        params={
            "limit": limit,
        },
    )
    if has_more:
        resp.cursor = {
            "total_score": scores[-1].total_score,
        }
    return resp


@router.get(
    "/rooms/{room_id}/playlist/{playlist_id}/scores/{score_id}",
    name="获取房间项目单个成绩",
    description="获取指定房间游玩项目中单个成绩详情。",
    tags=["成绩"],
    responses={
        200: api_doc(
            "房间项目单个成绩详情。",
            ScoreModel,
            [*Score.MULTIPLAYER_BASE_INCLUDES, "position", "scores_around"],
        )
    },
)
async def show_playlist_score(
    session: Database,
    room_id: int,
    playlist_id: int,
    score_id: int,
    current_user: ClientUser,
    redis: Redis,
):
    room = await session.get(Room, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    start_time = time.time()
    score_record = None
    is_playlist = room.category != RoomCategory.REALTIME
    completed = is_playlist
    while time.time() - start_time < READ_SCORE_TIMEOUT:
        if score_record is None:
            score_record = (
                await session.exec(
                    select(PlaylistBestScore).where(
                        PlaylistBestScore.score_id == score_id,
                        PlaylistBestScore.playlist_id == playlist_id,
                        PlaylistBestScore.room_id == room_id,
                        ~User.is_restricted_query(col(PlaylistBestScore.user_id)),
                    )
                )
            ).first()
        if completed_players := await redis.get(f"multiplayer:{room_id}:gameplay:players"):
            completed = completed_players == "0"
        if score_record and completed:
            break
    if not score_record:
        raise HTTPException(status_code=404, detail="Score not found")
    includes = [
        *Score.MULTIPLAYER_BASE_INCLUDES,
        "position",
    ]
    if completed:
        includes.append("scores_around")
    resp = await ScoreModel.transform(
        score_record.score, includes=includes, playlist_id=playlist_id, room_id=room_id, is_playlist=is_playlist,
    )
    return resp


@router.get(
    "rooms/{room_id}/playlist/{playlist_id}/scores/users/{user_id}",
    responses={
        200: api_doc(
            "房间项目单个成绩详情。",
            ScoreModel,
            [*Score.MULTIPLAYER_BASE_INCLUDES, "position", "scores_around"],
        )
    },
    name="获取房间项目用户成绩",
    description="获取指定用户在房间游玩项目中的成绩。",
    tags=["成绩"],
)
async def get_user_playlist_score(
    session: Database,
    room_id: int,
    playlist_id: int,
    user_id: int,
    current_user: ClientUser,
):
    score_record = None
    start_time = time.time()
    while time.time() - start_time < READ_SCORE_TIMEOUT:
        score_record = (
            await session.exec(
                select(PlaylistBestScore).where(
                    PlaylistBestScore.user_id == user_id,
                    PlaylistBestScore.playlist_id == playlist_id,
                    PlaylistBestScore.room_id == room_id,
                    ~User.is_restricted_query(col(PlaylistBestScore.user_id)),
                )
            )
        ).first()
        if score_record:
            break
    if not score_record:
        raise HTTPException(status_code=404, detail="Score not found")

    resp = await ScoreModel.transform(
        score_record.score,
        includes=[
            *Score.MULTIPLAYER_BASE_INCLUDES,
            "position",
            "scores_around",
        ],
    )
    return resp


@router.put(
    "/score-pins/{score_id}",
    status_code=204,
    name="置顶成绩",
    description="\n将指定成绩置顶到用户主页 (按顺序)。",
    tags=["成绩"],
)
async def pin_score(
    db: Database,
    current_user: ClientUser,
    user_cache_service: UserCacheService,
    score_id: Annotated[int, Path(description="成绩 ID")],
):
    # 立即获取用户ID，避免懒加载问题
    user_id = current_user.id

    score_record = (
        await db.exec(
            select(Score).where(
                Score.id == score_id,
                Score.user_id == user_id,
                col(Score.passed).is_(True),
            )
        )
    ).first()
    if not score_record:
        raise HTTPException(status_code=404, detail="Score not found")

    if score_record.pinned_order > 0:
        return

    next_order = (
        (
            await db.exec(
                select(func.max(Score.pinned_order)).where(
                    Score.user_id == current_user.id,
                    Score.gamemode == score_record.gamemode,
                )
            )
        ).first()
        or 0
    ) + 1
    score_record.pinned_order = next_order
    await user_cache_service.invalidate_user_scores_cache(user_id, score_record.gamemode)
    await db.commit()


@router.delete(
    "/score-pins/{score_id}",
    status_code=204,
    name="取消置顶成绩",
    description="\n取消置顶指定成绩。",
    tags=["成绩"],
)
async def unpin_score(
    db: Database,
    user_cache_service: UserCacheService,
    score_id: Annotated[int, Path(description="成绩 ID")],
    current_user: ClientUser,
):
    # 立即获取用户ID，避免懒加载问题
    user_id = current_user.id

    score_record = (await db.exec(select(Score).where(Score.id == score_id, Score.user_id == user_id))).first()
    if not score_record:
        raise HTTPException(status_code=404, detail="Score not found")

    if score_record.pinned_order == 0:
        return
    changed_score = (
        await db.exec(
            select(Score).where(
                Score.user_id == user_id,
                Score.pinned_order > score_record.pinned_order,
                Score.gamemode == score_record.gamemode,
            )
        )
    ).all()
    for s in changed_score:
        s.pinned_order -= 1
    score_record.pinned_order = 0
    await user_cache_service.invalidate_user_scores_cache(user_id, score_record.gamemode)
    await db.commit()


@router.post(
    "/score-pins/{score_id}/reorder",
    status_code=204,
    name="调整置顶成绩顺序",
    description=("\n调整已置顶成绩的展示顺序。仅提供 after_score_id 或 before_score_id 之一。"),
    tags=["成绩"],
)
async def reorder_score_pin(
    db: Database,
    user_cache_service: UserCacheService,
    current_user: ClientUser,
    score_id: Annotated[int, Path(description="成绩 ID")],
    after_score_id: Annotated[int | None, Body(description="放在该成绩之后")] = None,
    before_score_id: Annotated[int | None, Body(description="放在该成绩之前")] = None,
):
    # 立即获取用户ID，避免懒加载问题
    user_id = current_user.id

    score_record = (await db.exec(select(Score).where(Score.id == score_id, Score.user_id == user_id))).first()
    if not score_record:
        raise HTTPException(status_code=404, detail="Score not found")

    if score_record.pinned_order == 0:
        raise HTTPException(status_code=400, detail="Score is not pinned")

    if (after_score_id is None) == (before_score_id is None):
        raise HTTPException(
            status_code=400,
            detail="Either after_score_id or before_score_id must be provided (but not both)",
        )

    all_pinned_scores = (
        await db.exec(
            select(Score)
            .where(
                Score.user_id == current_user.id,
                Score.pinned_order > 0,
                Score.gamemode == score_record.gamemode,
            )
            .order_by(col(Score.pinned_order))
        )
    ).all()

    target_order = None
    reference_score_id = after_score_id or before_score_id

    reference_score = next((s for s in all_pinned_scores if s.id == reference_score_id), None)
    if not reference_score:
        detail = "After score not found" if after_score_id else "Before score not found"
        raise HTTPException(status_code=404, detail=detail)

    target_order = reference_score.pinned_order + 1 if after_score_id else reference_score.pinned_order

    current_order = score_record.pinned_order

    if current_order == target_order:
        return

    updates = []

    if current_order < target_order:
        for s in all_pinned_scores:
            if current_order < s.pinned_order <= target_order and s.id != score_id:
                updates.append((s.id, s.pinned_order - 1))
        if after_score_id:
            final_target = target_order - 1 if target_order > current_order else target_order
        else:
            final_target = target_order
    else:
        for s in all_pinned_scores:
            if target_order <= s.pinned_order < current_order and s.id != score_id:
                updates.append((s.id, s.pinned_order + 1))
        final_target = target_order

    for score_id, new_order in updates:
        await db.exec(select(Score).where(Score.id == score_id))
        score_to_update = (await db.exec(select(Score).where(Score.id == score_id))).first()
        if score_to_update:
            score_to_update.pinned_order = new_order

    score_record.pinned_order = final_target
    await user_cache_service.invalidate_user_scores_cache(user_id, score_record.gamemode)
    await db.commit()


@router.get(
    "/scores/{score_id}/download",
    name="下载成绩回放",
    description="下载指定成绩的回放文件。",
    tags=["成绩"],
    dependencies=[Depends(RateLimiter(times=10, minutes=1))],
)
async def download_score_replay(
    score_id: int,
    db: Database,
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    storage_service: StorageService,
):
    # 立即获取用户ID，避免懒加载问题
    user_id = current_user.id

    score = (await db.exec(select(Score).where(Score.id == score_id))).first()
    if not score:
        raise HTTPException(status_code=404, detail="Score not found")

    filepath = score.replay_filename

    if not await storage_service.is_exists(filepath):
        raise HTTPException(status_code=404, detail="Replay file not found")

    is_friend = (
        score.user_id == user_id
        or (
            await db.exec(
                select(exists()).where(
                    Relationship.user_id == user_id,
                    Relationship.target_id == score.user_id,
                    Relationship.type == RelationshipType.FOLLOW,
                )
            )
        ).first()
    )
    if not is_friend:
        replay_watched_count = (
            await db.exec(
                select(ReplayWatchedCount).where(
                    ReplayWatchedCount.user_id == score.user_id,
                    ReplayWatchedCount.year == date.today().year,
                    ReplayWatchedCount.month == date.today().month,
                )
            )
        ).first()
        if replay_watched_count is None:
            replay_watched_count = ReplayWatchedCount(
                user_id=score.user_id, year=date.today().year, month=date.today().month
            )
            db.add(replay_watched_count)
        replay_watched_count.count += 1
        await db.commit()
    return RedirectResponse(
        await storage_service.get_file_url(filepath), 301, headers={"Content-Type": "application/x-osu-replay"}
    )
