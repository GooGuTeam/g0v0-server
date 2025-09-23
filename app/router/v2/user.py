from __future__ import annotations

from datetime import timedelta
from typing import Literal

from app.config import settings
from app.const import BANCHOBOT_ID
from app.database import (
    BeatmapPlaycounts,
    BeatmapPlaycountsResp,
    BeatmapsetResp,
    User,
    UserResp,
)
from app.database.room.events import Event
from app.database.score import LegacyScoreResp, Score, ScoreResp, get_user_first_scores
from app.database.score.pp_best_score import PPBestScore
from app.database.user.lazer_user import SEARCH_INCLUDED
from app.dependencies.database import Database, get_redis
from app.dependencies.user import get_current_user
from app.dependencies.version import APIVersion
from app.log import logger
from app.models.score import GameMode
from app.models.user import BeatmapsetType
from app.service.assets.asset_proxy_helper import process_response_assets
from app.service.cache.user_cache_service import get_user_cache_service
from app.utils import utcnow

from .router import router

from fastapi import BackgroundTasks, HTTPException, Path, Query, Request, Security
from pydantic import BaseModel
from sqlmodel import exists, false, select
from sqlmodel.sql.expression import col


class BatchUserResponse(BaseModel):
    users: list[UserResp]


@router.get(
    "/users/",
    response_model=BatchUserResponse,
    name="批量获取用户信息",
    description="通过用户 ID 列表批量获取用户信息。",
    tags=["用户"],
)
@router.get("/users/lookup", response_model=BatchUserResponse, include_in_schema=False)
@router.get("/users/lookup/", response_model=BatchUserResponse, include_in_schema=False)
async def get_users(
    session: Database,
    request: Request,
    background_task: BackgroundTasks,
    user_ids: list[int] = Query(default_factory=list, alias="ids[]", description="要查询的用户 ID 列表"),
    # current_user: User = Security(get_current_user, scopes=["public"]),
    include_variant_statistics: bool = Query(default=False, description="是否包含各模式的统计信息"),  # TODO: future use
):
    redis = get_redis()
    cache_service = get_user_cache_service(redis)

    if user_ids:
        # 先尝试从缓存获取
        cached_users = []
        uncached_user_ids = []

        for user_id in user_ids[:50]:  # 限制50个
            cached_user = await cache_service.get_user_from_cache(user_id)
            if cached_user:
                cached_users.append(cached_user)
            else:
                uncached_user_ids.append(user_id)

        # 查询未缓存的用户
        if uncached_user_ids:
            searched_users = (await session.exec(select(User).where(col(User.id).in_(uncached_user_ids)))).all()

            # 将查询到的用户添加到缓存并返回
            for searched_user in searched_users:
                if searched_user.id != BANCHOBOT_ID:
                    user_resp = await UserResp.from_db(
                        searched_user,
                        session,
                        include=SEARCH_INCLUDED,
                    )
                    cached_users.append(user_resp)
                    # 异步缓存，不阻塞响应
                    background_task.add_task(cache_service.cache_user, user_resp)

        # 处理资源代理
        response = BatchUserResponse(users=cached_users)
        processed_response = await process_response_assets(response, request)
        return processed_response
    else:
        searched_users = (await session.exec(select(User).limit(50))).all()
        users = []
        for searched_user in searched_users:
            if searched_user.id != BANCHOBOT_ID:
                user_resp = await UserResp.from_db(
                    searched_user,
                    session,
                    include=SEARCH_INCLUDED,
                )
                users.append(user_resp)
                # 异步缓存
                background_task.add_task(cache_service.cache_user, user_resp)

        # 处理资源代理
        response = BatchUserResponse(users=users)
        processed_response = await process_response_assets(response, request)
        return processed_response


@router.get(
    "/users/{user_id}/recent_activity",
    tags=["用户"],
    response_model=list[Event],
    name="获取用户最近活动",
    description="获取用户在最近 30 天内的活动日志。",
)
async def get_user_events(
    session: Database,
    user_id: int = Path(description="用户 ID"),
    limit: int | None = Query(None, description="限制返回的活动数量"),
    offset: int | None = Query(None, description="活动日志的偏移量"),
):
    db_user = await session.get(User, user_id)
    if db_user is None or db_user.id == BANCHOBOT_ID:
        raise HTTPException(404, "User Not found")
    events = (
        await session.exec(
            select(Event)
            .where(Event.user_id == db_user.id, Event.created_at >= utcnow() - timedelta(days=30))
            .order_by(col(Event.created_at).desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return events


@router.get(
    "/users/{user_id}/kudosu",
    response_model=list,
    name="获取用户 kudosu 记录",
    description="获取指定用户的 kudosu 记录。TODO: 可能会实现",
    tags=["用户"],
)
async def get_user_kudosu(
    session: Database,
    user_id: int = Path(description="用户 ID"),
    offset: int = Query(default=0, description="偏移量"),
    limit: int = Query(default=6, description="返回记录数量限制"),
):
    """
    获取用户的 kudosu 记录

    TODO: 可能会实现
    目前返回空数组作为占位符
    """
    # 验证用户是否存在
    db_user = await session.get(User, user_id)
    if db_user is None or db_user.id == BANCHOBOT_ID:
        raise HTTPException(404, "User not found")

    # TODO: 实现 kudosu 记录获取逻辑
    return []


@router.get(
    "/users/{user_id}/{ruleset}",
    response_model=UserResp,
    name="获取用户信息(指定ruleset)",
    description="通过用户 ID 或用户名获取单个用户的详细信息，并指定特定 ruleset。",
    tags=["用户"],
)
async def get_user_info_ruleset(
    session: Database,
    background_task: BackgroundTasks,
    user_id: str = Path(description="用户 ID 或用户名"),
    ruleset: GameMode | None = Path(description="指定 ruleset"),
    # current_user: User = Security(get_current_user, scopes=["public"]),
):
    redis = get_redis()
    cache_service = get_user_cache_service(redis)

    # 如果是数字ID，先尝试从缓存获取
    if user_id.isdigit():
        user_id_int = int(user_id)
        cached_user = await cache_service.get_user_from_cache(user_id_int, ruleset)
        if cached_user:
            return cached_user

    searched_user = (
        await session.exec(
            select(User).where(
                User.id == int(user_id) if user_id.isdigit() else User.username == user_id.removeprefix("@")
            )
        )
    ).first()
    if not searched_user or searched_user.id == BANCHOBOT_ID:
        raise HTTPException(404, detail="User not found")

    user_resp = await UserResp.from_db(
        searched_user,
        session,
        include=SEARCH_INCLUDED,
        ruleset=ruleset,
    )

    # 异步缓存结果
    background_task.add_task(cache_service.cache_user, user_resp, ruleset)

    return user_resp


@router.get("/users/{user_id}/", response_model=UserResp, include_in_schema=False)
@router.get(
    "/users/{user_id}",
    response_model=UserResp,
    name="获取用户信息",
    description="通过用户 ID 或用户名获取单个用户的详细信息。",
    tags=["用户"],
)
async def get_user_info(
    background_task: BackgroundTasks,
    session: Database,
    request: Request,
    user_id: str = Path(description="用户 ID 或用户名"),
    # current_user: User = Security(get_current_user, scopes=["public"]),
):
    redis = get_redis()
    cache_service = get_user_cache_service(redis)

    # 如果是数字ID，先尝试从缓存获取
    if user_id.isdigit():
        user_id_int = int(user_id)
        cached_user = await cache_service.get_user_from_cache(user_id_int)
        if cached_user:
            # 处理资源代理
            processed_user = await process_response_assets(cached_user, request)
            return processed_user

    searched_user = (
        await session.exec(
            select(User).where(
                User.id == int(user_id) if user_id.isdigit() else User.username == user_id.removeprefix("@")
            )
        )
    ).first()
    if not searched_user or searched_user.id == BANCHOBOT_ID:
        raise HTTPException(404, detail="User not found")

    user_resp = await UserResp.from_db(
        searched_user,
        session,
        include=SEARCH_INCLUDED,
    )

    # 异步缓存结果
    background_task.add_task(cache_service.cache_user, user_resp)

    # 处理资源代理
    processed_user = await process_response_assets(user_resp, request)
    return processed_user


@router.get(
    "/users/{user_id}/beatmapsets/{type}",
    response_model=list[BeatmapsetResp | BeatmapPlaycountsResp],
    name="获取用户谱面集列表",
    description="获取指定用户特定类型的谱面集列表，如最常游玩、收藏等。",
    tags=["用户"],
)
async def get_user_beatmapsets(
    session: Database,
    background_task: BackgroundTasks,
    user_id: int = Path(description="用户 ID"),
    type: BeatmapsetType = Path(description="谱面集类型"),
    current_user: User = Security(get_current_user, scopes=["public"]),
    limit: int = Query(100, ge=1, le=1000, description="返回条数 (1-1000)"),
    offset: int = Query(0, ge=0, description="偏移量"),
):
    redis = get_redis()
    cache_service = get_user_cache_service(redis)

    # 先尝试从缓存获取
    cached_result = await cache_service.get_user_beatmapsets_from_cache(user_id, type.value, limit, offset)
    if cached_result is not None:
        # 根据类型恢复对象
        if type == BeatmapsetType.MOST_PLAYED:
            return [BeatmapPlaycountsResp(**item) for item in cached_result]
        else:
            return [BeatmapsetResp(**item) for item in cached_result]

    user = await session.get(User, user_id)
    if not user or user.id == BANCHOBOT_ID:
        raise HTTPException(404, detail="User not found")

    if type in {
        BeatmapsetType.GRAVEYARD,
        BeatmapsetType.GUEST,
        BeatmapsetType.LOVED,
        BeatmapsetType.NOMINATED,
        BeatmapsetType.PENDING,
        BeatmapsetType.RANKED,
    }:
        # TODO: mapping, modding
        resp = []

    elif type == BeatmapsetType.FAVOURITE:
        user = await session.get(User, user_id)
        if not user:
            raise HTTPException(404, detail="User not found")
        favourites = await user.awaitable_attrs.favourite_beatmapsets
        resp = [
            await BeatmapsetResp.from_db(favourite.beatmapset, session=session, user=user) for favourite in favourites
        ]

    elif type == BeatmapsetType.MOST_PLAYED:
        most_played = await session.exec(
            select(BeatmapPlaycounts)
            .where(BeatmapPlaycounts.user_id == user_id)
            .order_by(col(BeatmapPlaycounts.playcount).desc())
            .limit(limit)
            .offset(offset)
        )
        resp = [await BeatmapPlaycountsResp.from_db(most_played_beatmap) for most_played_beatmap in most_played]
    else:
        raise HTTPException(400, detail="Invalid beatmapset type")

    # 异步缓存结果
    async def cache_beatmapsets():
        try:
            await cache_service.cache_user_beatmapsets(user_id, type.value, resp, limit, offset)
        except Exception as e:
            logger.error(f"Error caching user beatmapsets for user {user_id}, type {type.value}: {e}")

    background_task.add_task(cache_beatmapsets)

    return resp


@router.get(
    "/users/{user_id}/scores/{type}",
    response_model=list[ScoreResp] | list[LegacyScoreResp],
    name="获取用户成绩列表",
    description=(
        "获取用户特定类型的成绩列表，如最好成绩、最近成绩等。\n\n"
        "如果 `x-api-version >= 20220705`，返回值为 `ScoreResp`列表，"
        "否则为 `LegacyScoreResp`列表。"
    ),
    tags=["用户"],
)
async def get_user_scores(
    request: Request,
    session: Database,
    api_version: APIVersion,
    background_task: BackgroundTasks,
    user_id: int = Path(description="用户 ID"),
    type: Literal["best", "recent", "firsts", "pinned"] = Path(
        description=("成绩类型: best 最好成绩 / recent 最近 24h 游玩成绩 / firsts 第一名成绩 / pinned 置顶成绩")
    ),
    legacy_only: bool = Query(False, description="是否只查询 Stable 成绩"),
    include_fails: bool = Query(False, description="是否包含失败的成绩"),
    mode: GameMode | None = Query(None, description="指定 ruleset (可选，默认为用户主模式)"),
    limit: int = Query(100, ge=1, le=1000, description="返回条数 (1-1000)"),
    offset: int = Query(0, ge=0, description="偏移量"),
    current_user: User = Security(get_current_user, scopes=["public"]),
):
    is_legacy_api = api_version < 20220705
    redis = get_redis()
    cache_service = get_user_cache_service(redis)

    # 先尝试从缓存获取（对于recent类型使用较短的缓存时间）
    cache_expire = 30 if type == "recent" else settings.user_scores_cache_expire_seconds
    cached_scores = await cache_service.get_user_scores_from_cache(
        user_id, type, include_fails, mode, limit, offset, is_legacy_api
    )
    if cached_scores is not None:
        processed_scores = await process_response_assets(cached_scores, request)
        return processed_scores

    db_user = await session.get(User, user_id)
    if not db_user or db_user.id == BANCHOBOT_ID:
        raise HTTPException(404, detail="User not found")

    gamemode = mode or db_user.playmode
    order_by = None
    where_clause = (col(Score.user_id) == db_user.id) & (col(Score.gamemode) == gamemode)
    if not include_fails:
        where_clause &= col(Score.passed).is_(True)
    if type == "pinned":
        where_clause &= Score.pinned_order > 0
        order_by = col(Score.pinned_order).asc()
    elif type == "best":
        where_clause &= exists().where(col(PPBestScore.score_id) == Score.id)
        order_by = col(Score.pp).desc()
    elif type == "recent":
        where_clause &= Score.ended_at > utcnow() - timedelta(hours=24)
        order_by = col(Score.ended_at).desc()
    elif type == "firsts":
        where_clause &= false()

    if type != "firsts":
        scores = (
            await session.exec(select(Score).where(where_clause).order_by(order_by).limit(limit).offset(offset))
        ).all()
        if not scores:
            return []
    else:
        best_scores = await get_user_first_scores(session, db_user.id, gamemode, limit)
        scores = [best_score.score for best_score in best_scores]

    score_responses = [
        await score.to_resp(
            session,
            api_version,
        )
        for score in scores
    ]

    # 异步缓存结果
    background_task.add_task(
        cache_service.cache_user_scores,
        user_id,
        type,
        score_responses,  # pyright: ignore[reportArgumentType]
        include_fails,
        mode,
        limit,
        offset,
        cache_expire,
        is_legacy_api,
    )

    # 处理资源代理
    processed_scores = await process_response_assets(score_responses, request)
    return processed_scores
