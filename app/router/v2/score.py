from __future__ import annotations

from datetime import UTC, date
import math
import time

from app.calculator import clamp
from app.config import settings
from app.database import (
    Beatmap,
    Playlist,
    Room,
    Score,
    ScoreResp,
    ScoreToken,
    ScoreTokenResp,
    User,
)
from app.database.achievement import process_achievements
from app.database.best_score import BestScore
from app.database.counts import ReplayWatchedCount
from app.database.daily_challenge import process_daily_challenge_score
from app.database.events import Event, EventType
from app.database.playlist_attempts import ItemAttemptsCount
from app.database.playlist_best_score import (
    PlaylistBestScore,
    get_position,
    process_playlist_best_score,
)
from app.database.relationship import Relationship, RelationshipType
from app.database.score import (
    MultiplayerScores,
    ScoreAround,
    get_leaderboard,
    process_score,
    process_user,
)
from app.dependencies.database import Database, get_redis, with_db
from app.dependencies.fetcher import get_fetcher
from app.dependencies.storage import get_storage_service
from app.dependencies.user import get_client_user, get_current_user
from app.fetcher import Fetcher
from app.log import logger
from app.models.room import RoomCategory
from app.models.score import (
    GameMode,
    LeaderboardType,
    Rank,
    SoloScoreSubmissionInfo,
)
from app.service.user_cache_service import get_user_cache_service
from app.storage.base import StorageService
from app.storage.local import LocalStorageService
from app.utils import utcnow

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
from fastapi.responses import FileResponse, RedirectResponse
from httpx import HTTPError
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.orm import joinedload
from sqlmodel import col, exists, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

READ_SCORE_TIMEOUT = 10


async def process_user_achievement(score_id: int):
    from app.dependencies.database import engine

    from sqlmodel.ext.asyncio.session import AsyncSession

    session = AsyncSession(engine)
    try:
        await process_achievements(session, get_redis(), score_id)
    finally:
        await session.close()


async def submit_score(
    background_task: BackgroundTasks,
    info: SoloScoreSubmissionInfo,
    beatmap: int,
    token: int,
    current_user: User,
    db: AsyncSession,
    redis: Redis,
    fetcher: Fetcher,
    item_id: int | None = None,
    room_id: int | None = None,
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
        # 智能预取beatmap缓存（异步进行，不阻塞主流程）
        try:
            from app.service.beatmap_cache_service import get_beatmap_cache_service

            cache_service = get_beatmap_cache_service(redis, fetcher)
            await cache_service.smart_preload_for_score(beatmap)
        except Exception as e:
            logger.debug(f"Beatmap preload failed for {beatmap}: {e}")

        try:
            db_beatmap = await Beatmap.get_or_fetch(db, fetcher, bid=beatmap)
        except HTTPError:
            raise HTTPException(status_code=404, detail="Beatmap not found")
        has_pp = db_beatmap.beatmap_status.has_pp() | settings.enable_all_beatmap_pp
        has_leaderboard = db_beatmap.beatmap_status.has_leaderboard() | settings.enable_all_beatmap_leaderboard
        beatmap_length = db_beatmap.total_length
        score = await process_score(
            current_user,
            beatmap,
            has_pp,
            score_token,
            info,
            fetcher,
            db,
            redis,
            item_id,
            room_id,
        )
        await db.refresh(current_user)
        score_id = score.id
        score_token.score_id = score_id
        await process_user(
            db,
            current_user,
            score,
            token,
            beatmap_length,
            has_pp,
            has_leaderboard,
        )
        score = (await db.exec(select(Score).options(joinedload(Score.user)).where(Score.id == score_id))).one()

    resp: ScoreResp = await ScoreResp.from_db(db, score)
    score_gamemode = score.gamemode
    total_users = (await db.exec(select(func.count()).select_from(User))).one()
    if resp.rank_global is not None and resp.rank_global <= min(math.ceil(float(total_users) * 0.01), 50):
        rank_event = Event(
            created_at=utcnow(),
            type=EventType.RANK,
            user_id=score.user_id,
            user=score.user,
        )
        rank_event.event_payload = {
            "scorerank": score.rank.value,
            "rank": resp.rank_global,
            "mode": score.gamemode.readable(),
            "beatmap": {
                "title": f"{resp.beatmap.beatmapset.artist} - {resp.beatmap.beatmapset.title} [{resp.beatmap.version}]",  # pyright: ignore[reportOptionalMemberAccess]
                "url": resp.beatmap.url,  # pyright: ignore[reportOptionalMemberAccess]
            },
            "user": {
                "username": score.user.username,
                "url": settings.web_url + "users/" + str(score.user.id),
            },
        }
        db.add(rank_event)
    if resp.rank_global is not None and resp.rank_global == 1:
        displaced_score = (
            await db.exec(
                select(BestScore)
                .where(
                    BestScore.beatmap_id == score.beatmap_id,
                    BestScore.gamemode == score.gamemode,
                )
                .order_by(col(BestScore.total_score).desc())
                .limit(1)
                .offset(1)
            )
        ).first()
        if displaced_score and displaced_score.user_id != resp.user_id:
            username = (await db.exec(select(User.username).where(User.id == displaced_score.user_id))).one()

            rank_lost_event = Event(
                created_at=utcnow(),
                type=EventType.RANK_LOST,
                user_id=displaced_score.user_id,
            )
            rank_lost_event.event_payload = {
                "mode": score.gamemode.readable(),
                "beatmap": {
                    "title": score.beatmap.version,
                    "url": score.beatmap.url,
                },
                "user": {
                    "username": username,
                    "url": settings.web_url + "users/" + str(displaced_score.user.id),
                },
            }
            db.add(rank_lost_event)

    await db.commit()
    if user_id is not None:
        background_task.add_task(_refresh_user_cache_background, redis, user_id, score_gamemode)
    background_task.add_task(process_user_achievement, resp.id)
    return resp


async def _refresh_user_cache_background(redis: Redis, user_id: int, mode: GameMode):
    """后台任务：刷新用户缓存"""
    try:
        user_cache_service = get_user_cache_service(redis)
        # 创建独立的数据库会话
        async with with_db() as session:
            await user_cache_service.refresh_user_cache_on_score_submit(session, user_id, mode)
    except Exception as e:
        logger.error(f"Failed to refresh user cache after score submit: {e}")


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


class BeatmapScores(BaseModel):
    scores: list[ScoreResp]
    user_score: BeatmapUserScore | None = None
    score_count: int = 0


@router.get(
    "/beatmaps/{beatmap_id}/scores",
    tags=["成绩"],
    response_model=BeatmapScores,
    name="获取谱面排行榜",
    description="获取指定谱面在特定条件下的排行榜及当前用户成绩。",
)
async def get_beatmap_scores(
    db: Database,
    beatmap_id: int = Path(description="谱面 ID"),
    mode: GameMode = Query(description="指定 auleset"),
    legacy_only: bool = Query(None, description="是否只查询 Stable 分数"),
    mods: list[str] = Query(default_factory=set, alias="mods[]", description="筛选使用的 Mods (可选，多值)"),
    type: LeaderboardType = Query(
        LeaderboardType.GLOBAL,
        description=("排行榜类型：GLOBAL 全局 / COUNTRY 国家 / FRIENDS 好友 / TEAM 战队"),
    ),
    current_user: User = Security(get_current_user, scopes=["public"]),
    limit: int = Query(50, ge=1, le=200, description="返回条数 (1-200)"),
):
    if legacy_only:
        raise HTTPException(status_code=404, detail="this server only contains lazer scores")

    all_scores, user_score, count = await get_leaderboard(
        db,
        beatmap_id,
        mode,
        type=type,
        user=current_user,
        limit=limit,
        mods=sorted(mods),
    )

    user_score_resp = await ScoreResp.from_db(db, user_score) if user_score else None
    resp = BeatmapScores(
        scores=[await ScoreResp.from_db(db, score) for score in all_scores],
        user_score=BeatmapUserScore(score=user_score_resp, position=user_score_resp.rank_global or 0)
        if user_score_resp
        else None,
        score_count=count,
    )
    return resp


class BeatmapUserScore(BaseModel):
    position: int
    score: ScoreResp


@router.get(
    "/beatmaps/{beatmap_id}/scores/users/{user_id}",
    tags=["成绩"],
    response_model=BeatmapUserScore,
    name="获取用户谱面最高成绩",
    description="获取指定用户在指定谱面上的最高成绩。",
)
async def get_user_beatmap_score(
    db: Database,
    beatmap_id: int = Path(description="谱面 ID"),
    user_id: int = Path(description="用户 ID"),
    legacy_only: bool = Query(None, description="是否只查询 Stable 分数"),
    mode: GameMode | None = Query(None, description="指定 ruleset (可选)"),
    mods: str = Query(None, description="筛选使用的 Mods (暂未实现)"),
    current_user: User = Security(get_current_user, scopes=["public"]),
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
        resp = await ScoreResp.from_db(db, user_score)
        return BeatmapUserScore(
            position=resp.rank_global or 0,
            score=resp,
        )


@router.get(
    "/beatmaps/{beatmap_id}/scores/users/{user_id}/all",
    tags=["成绩"],
    response_model=list[ScoreResp],
    name="获取用户谱面全部成绩",
    description="获取指定用户在指定谱面上的全部成绩列表。",
)
async def get_user_all_beatmap_scores(
    db: Database,
    beatmap_id: int = Path(description="谱面 ID"),
    user_id: int = Path(description="用户 ID"),
    legacy_only: bool = Query(None, description="是否只查询 Stable 分数"),
    ruleset: GameMode | None = Query(None, description="指定 ruleset (可选)"),
    current_user: User = Security(get_current_user, scopes=["public"]),
):
    all_user_scores = (
        await db.exec(
            select(Score)
            .where(
                Score.gamemode == ruleset if ruleset is not None else True,
                Score.beatmap_id == beatmap_id,
                Score.user_id == user_id,
                col(Score.passed).is_(True),
            )
            .order_by(col(Score.total_score).desc())
        )
    ).all()

    return [await ScoreResp.from_db(db, score) for score in all_user_scores]


@router.post(
    "/beatmaps/{beatmap_id}/solo/scores",
    tags=["游玩"],
    response_model=ScoreTokenResp,
    name="创建单曲成绩提交令牌",
    description="**客户端专属**\n为指定谱面创建一次性的成绩提交令牌。",
)
async def create_solo_score(
    background_task: BackgroundTasks,
    db: Database,
    beatmap_id: int = Path(description="谱面 ID"),
    version_hash: str = Form("", description="游戏版本哈希"),
    beatmap_hash: str = Form(description="谱面文件哈希"),
    ruleset_id: int = Form(..., ge=0, le=3, description="ruleset 数字 ID (0-3)"),
    current_user: User = Security(get_client_user),
):
    # 立即获取用户ID，避免懒加载问题
    user_id = current_user.id

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
    response_model=ScoreResp,
    name="提交单曲成绩",
    description="**客户端专属**\n使用令牌提交单曲成绩。",
)
async def submit_solo_score(
    background_task: BackgroundTasks,
    db: Database,
    beatmap_id: int = Path(description="谱面 ID"),
    token: int = Path(description="成绩令牌 ID"),
    info: SoloScoreSubmissionInfo = Body(description="成绩提交信息"),
    current_user: User = Security(get_client_user),
    redis: Redis = Depends(get_redis),
    fetcher=Depends(get_fetcher),
):
    return await submit_score(background_task, info, beatmap_id, token, current_user, db, redis, fetcher)


@router.post(
    "/rooms/{room_id}/playlist/{playlist_id}/scores",
    tags=["游玩"],
    response_model=ScoreTokenResp,
    name="创建房间项目成绩令牌",
    description="**客户端专属**\n为房间游玩项目创建成绩提交令牌。",
)
async def create_playlist_score(
    session: Database,
    background_task: BackgroundTasks,
    room_id: int,
    playlist_id: int,
    beatmap_id: int = Form(description="谱面 ID"),
    beatmap_hash: str = Form(description="游戏版本哈希"),
    ruleset_id: int = Form(..., ge=0, le=3, description="ruleset 数字 ID (0-3)"),
    version_hash: str = Form("", description="谱面版本哈希"),
    current_user: User = Security(get_client_user),
):
    # 立即获取用户ID，避免懒加载问题
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
    )
    session.add(score_token)
    await session.commit()
    await session.refresh(score_token)
    return ScoreTokenResp.from_db(score_token)


@router.put(
    "/rooms/{room_id}/playlist/{playlist_id}/scores/{token}",
    tags=["游玩"],
    name="提交房间项目成绩",
    description="**客户端专属**\n提交房间游玩项目成绩。",
)
async def submit_playlist_score(
    background_task: BackgroundTasks,
    session: Database,
    room_id: int,
    playlist_id: int,
    token: int,
    info: SoloScoreSubmissionInfo,
    current_user: User = Security(get_client_user),
    redis: Redis = Depends(get_redis),
    fetcher: Fetcher = Depends(get_fetcher),
):
    # 立即获取用户ID，避免懒加载问题
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
        item.beatmap_id,
        token,
        current_user,
        session,
        redis,
        fetcher,
        item.id,
        room_id,
    )
    await process_playlist_best_score(
        room_id,
        playlist_id,
        user_id,
        score_resp.id,
        score_resp.total_score,
        session,
        redis,
    )
    await session.commit()
    if room_category == RoomCategory.DAILY_CHALLENGE and score_resp.passed:
        await process_daily_challenge_score(session, user_id, room_id)
    await ItemAttemptsCount.get_or_create(room_id, user_id, session)
    await session.commit()
    return score_resp


class IndexedScoreResp(MultiplayerScores):
    total: int
    user_score: ScoreResp | None = None


@router.get(
    "/rooms/{room_id}/playlist/{playlist_id}/scores",
    response_model=IndexedScoreResp,
    name="获取房间项目排行榜",
    description="获取房间游玩项目排行榜。",
    tags=["成绩"],
)
async def index_playlist_scores(
    session: Database,
    room_id: int,
    playlist_id: int,
    limit: int = Query(50, ge=1, le=50, description="返回条数 (1-50)"),
    cursor: int = Query(2000000, alias="cursor[total_score]", description="分页游标（上一页最低分）"),
    current_user: User = Security(get_current_user, scopes=["public"]),
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
            )
            .order_by(col(PlaylistBestScore.total_score).desc())
            .limit(limit + 1)
        )
    ).all()
    has_more = len(scores) > limit
    if has_more:
        scores = scores[:-1]

    user_score = None
    score_resp = [await ScoreResp.from_db(session, score.score) for score in scores]
    for score in score_resp:
        score.position = await get_position(room_id, playlist_id, score.id, session)
        if score.user_id == user_id:
            user_score = score

    if room.category == RoomCategory.DAILY_CHALLENGE:
        score_resp = [s for s in score_resp if s.passed]
        if user_score and not user_score.passed:
            user_score = None

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
    response_model=ScoreResp,
    name="获取房间项目单个成绩",
    description="获取指定房间游玩项目中单个成绩详情。",
    tags=["成绩"],
)
async def show_playlist_score(
    session: Database,
    room_id: int,
    playlist_id: int,
    score_id: int,
    current_user: User = Security(get_client_user),
    redis: Redis = Depends(get_redis),
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
                    )
                )
            ).first()
        if completed_players := await redis.get(f"multiplayer:{room_id}:gameplay:players"):
            completed = completed_players == "0"
        if score_record and completed:
            break
    if not score_record:
        raise HTTPException(status_code=404, detail="Score not found")
    resp = await ScoreResp.from_db(session, score_record.score)
    resp.position = await get_position(room_id, playlist_id, score_id, session)
    if completed:
        scores = (
            await session.exec(
                select(PlaylistBestScore).where(
                    PlaylistBestScore.playlist_id == playlist_id,
                    PlaylistBestScore.room_id == room_id,
                )
            )
        ).all()
        higher_scores = []
        lower_scores = []
        for score in scores:
            resp = await ScoreResp.from_db(session, score.score)
            if is_playlist and not resp.passed:
                continue
            if score.total_score > resp.total_score:
                higher_scores.append(resp)
            elif score.total_score < resp.total_score:
                lower_scores.append(resp)
        resp.scores_around = ScoreAround(
            higher=MultiplayerScores(scores=higher_scores),
            lower=MultiplayerScores(scores=lower_scores),
        )

    return resp


@router.get(
    "rooms/{room_id}/playlist/{playlist_id}/scores/users/{user_id}",
    response_model=ScoreResp,
    name="获取房间项目用户成绩",
    description="获取指定用户在房间游玩项目中的成绩。",
    tags=["成绩"],
)
async def get_user_playlist_score(
    session: Database,
    room_id: int,
    playlist_id: int,
    user_id: int,
    current_user: User = Security(get_client_user),
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
                )
            )
        ).first()
        if score_record:
            break
    if not score_record:
        raise HTTPException(status_code=404, detail="Score not found")

    resp = await ScoreResp.from_db(session, score_record.score)
    resp.position = await get_position(room_id, playlist_id, score_record.score_id, session)
    return resp


@router.put(
    "/score-pins/{score_id}",
    status_code=204,
    name="置顶成绩",
    description="**客户端专属**\n将指定成绩置顶到用户主页 (按顺序)。",
    tags=["成绩"],
)
async def pin_score(
    db: Database,
    score_id: int = Path(description="成绩 ID"),
    current_user: User = Security(get_client_user),
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
    await db.commit()


@router.delete(
    "/score-pins/{score_id}",
    status_code=204,
    name="取消置顶成绩",
    description="**客户端专属**\n取消置顶指定成绩。",
    tags=["成绩"],
)
async def unpin_score(
    db: Database,
    score_id: int = Path(description="成绩 ID"),
    current_user: User = Security(get_client_user),
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
    await db.commit()


@router.post(
    "/score-pins/{score_id}/reorder",
    status_code=204,
    name="调整置顶成绩顺序",
    description=("**客户端专属**\n调整已置顶成绩的展示顺序。仅提供 after_score_id 或 before_score_id 之一。"),
    tags=["成绩"],
)
async def reorder_score_pin(
    db: Database,
    score_id: int = Path(description="成绩 ID"),
    after_score_id: int | None = Body(default=None, description="放在该成绩之后"),
    before_score_id: int | None = Body(default=None, description="放在该成绩之前"),
    current_user: User = Security(get_client_user),
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

    if after_score_id:
        target_order = reference_score.pinned_order + 1
    else:
        target_order = reference_score.pinned_order

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

    await db.commit()


@router.get(
    "/scores/{score_id}/download",
    name="下载成绩回放",
    description="下载指定成绩的回放文件。",
    tags=["成绩"],
)
async def download_score_replay(
    score_id: int,
    db: Database,
    current_user: User = Security(get_current_user, scopes=["public"]),
    storage_service: StorageService = Depends(get_storage_service),
):
    # 立即获取用户ID，避免懒加载问题
    user_id = current_user.id

    score = (await db.exec(select(Score).where(Score.id == score_id))).first()
    if not score:
        raise HTTPException(status_code=404, detail="Score not found")

    filepath = f"replays/{score.id}_{score.beatmap_id}_{score.user_id}_lazer_replay.osr"

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
    if isinstance(storage_service, LocalStorageService):
        return FileResponse(
            path=await storage_service.get_file_url(filepath),
            filename=filepath,
            media_type="application/x-osu-replay",
        )
    else:
        return RedirectResponse(
            await storage_service.get_file_url(filepath),
            301,
        )
