from datetime import datetime, timedelta
import sys
from typing import Annotated, Literal

from app.config import settings
from app.const import BANCHOBOT_ID
from app.database import (
    Beatmap,
    BeatmapModel,
    BeatmapPlaycounts,
    BeatmapsetModel,
    FavouriteBeatmapset,
    User,
)
from app.database.beatmap_playcounts import BeatmapPlaycountsModel
from app.database.best_scores import BestScore
from app.database.events import Event
from app.database.score import Score, get_user_first_scores
from app.database.user import UserModel
from app.dependencies.api_version import APIVersion
from app.dependencies.cache import UserCacheService
from app.dependencies.database import Database, get_redis
from app.dependencies.user import get_current_user, get_optional_user
from app.helpers.asset_proxy_helper import asset_proxy_response
from app.log import log
from app.models.mods import API_MODS
from app.models.score import GameMode
from app.models.user import BeatmapsetType
from app.service.user_cache_service import get_user_cache_service
from app.utils import api_doc, utcnow

from .router import router

from fastapi import BackgroundTasks, HTTPException, Path, Query, Request, Security
from sqlmodel import exists, select, tuple_
from sqlmodel.sql.expression import col


def _get_difficulty_reduction_mods() -> set[str]:
    mods: set[str] = set()
    for ruleset_mods in API_MODS.values():
        for mod_acronym, mod_meta in ruleset_mods.items():
            if mod_meta.get("Type") == "DifficultyReduction":
                mods.add(mod_acronym)
    return mods


async def visible_to_current_user(user: User, current_user: User | None, session: Database) -> bool:
    if user.id == BANCHOBOT_ID:
        return False
    if current_user and current_user.id == user.id:
        return True
    return not await user.is_restricted(session)


@router.get(
    "/users/",
    responses={
        200: api_doc("批量获取用户信息", {"users": list[UserModel]}, User.CARD_INCLUDES, name="UsersLookupResponse")
    },
    name="批量获取用户信息",
    description="通过用户 ID 列表批量获取用户信息。",
    tags=["用户"],
)
@router.get("/users/lookup", include_in_schema=False)
@router.get("/users/lookup/", include_in_schema=False)
@asset_proxy_response
async def get_users(
    session: Database,
    request: Request,
    background_task: BackgroundTasks,
    user_ids: Annotated[list[int], Query(default_factory=list, alias="ids[]", description="要查询的用户 ID 列表")],
    # current_user: User = Security(get_current_user, scopes=["public"]),
    include_variant_statistics: Annotated[
        bool,
        Query(description="是否包含各模式的统计信息"),
    ] = False,  # TODO: future use
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
            searched_users = (
                await session.exec(
                    select(User).where(col(User.id).in_(uncached_user_ids), ~User.is_restricted_query(col(User.id)))
                )
            ).all()

            # 将查询到的用户添加到缓存并返回
            for searched_user in searched_users:
                if searched_user.id != BANCHOBOT_ID:
                    user_resp = await UserModel.transform(
                        searched_user,
                        includes=User.CARD_INCLUDES,
                    )
                    cached_users.append(user_resp)
                    # 异步缓存，不阻塞响应
                    background_task.add_task(cache_service.cache_user, user_resp)

        response = {"users": cached_users}
        return response
    else:
        searched_users = (
            await session.exec(select(User).limit(50).where(~User.is_restricted_query(col(User.id))))
        ).all()
        users = []
        for searched_user in searched_users:
            if searched_user.id == BANCHOBOT_ID:
                continue
            user_resp = await UserModel.transform(
                searched_user,
                includes=User.CARD_INCLUDES,
            )
            users.append(user_resp)
            # 异步缓存
            background_task.add_task(cache_service.cache_user, user_resp)

        response = {"users": users}
        return response


@router.get(
    "/users/{user_id}/recent_activity",
    tags=["用户"],
    response_model=list[Event],
    name="获取用户最近活动",
    description="获取用户在最近 30 天内的活动日志。",
)
async def get_user_events(
    session: Database,
    user_id: Annotated[int, Path(description="用户 ID")],
    limit: Annotated[int, Query(description="限制返回的活动数量")] = 50,
    offset: Annotated[int | None, Query(description="活动日志的偏移量")] = None,
    current_user: User | None = Security(get_optional_user, scopes=["public"]),
):
    db_user = await session.get(User, user_id)
    if db_user is None or not await visible_to_current_user(db_user, current_user, session):
        raise HTTPException(404, "User Not found")
    if offset is None:
        offset = 0
    if limit > 100:
        limit = 100

    if offset == 0:
        cursor = sys.maxsize
    else:
        cursor = (
            await session.exec(
                select(Event.id)
                .where(Event.user_id == db_user.id, Event.created_at >= utcnow() - timedelta(days=30))
                .order_by(col(Event.id).desc())
                .limit(1)
                .offset(offset - 1)
            )
        ).first()
        if cursor is None:
            return []

    events = (
        await session.exec(
            select(Event)
            .where(Event.user_id == db_user.id, Event.created_at >= utcnow() - timedelta(days=30), Event.id < cursor)
            .order_by(col(Event.id).desc())
            .limit(limit)
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
    user_id: Annotated[int, Path(description="用户 ID")],
    offset: Annotated[int, Query(description="偏移量")] = 0,
    limit: Annotated[int, Query(description="返回记录数量限制")] = 6,
    current_user: User | None = Security(get_optional_user, scopes=["public"]),
):
    """
    获取用户的 kudosu 记录

    TODO: 可能会实现
    目前返回空数组作为占位符
    """
    # 验证用户是否存在
    db_user = await session.get(User, user_id)
    if db_user is None or not await visible_to_current_user(db_user, current_user, session):
        raise HTTPException(404, "User not found")

    # TODO: 实现 kudosu 记录获取逻辑
    return []


@router.get(
    "/users/{user_id}/beatmaps-passed",
    name="获取用户已通过谱面",
    description="获取指定用户在给定谱面集中的已通过谱面列表。",
    tags=["用户"],
    responses={
        200: api_doc("用户已通过谱面列表", {"beatmaps_passed": list[BeatmapModel]}, name="BeatmapsPassedResponse")
    },
)
@asset_proxy_response
async def get_user_beatmaps_passed(
    session: Database,
    user_id: Annotated[int, Path(description="用户 ID")],
    current_user: User | None = Security(get_optional_user, scopes=["public"]),
    beatmapset_ids: Annotated[
        list[int],
        Query(
            alias="beatmapset_ids[]",
            description="要查询的谱面集 ID 列表 (最多 50 个)",
        ),
    ] = [],
    ruleset_id: Annotated[
        int | None,
        Query(description="指定 ruleset ID"),
    ] = None,
    exclude_converts: Annotated[bool, Query(description="是否排除转谱成绩")] = False,
    is_legacy: Annotated[bool | None, Query(description="是否仅返回 Stable 成绩")] = None,
    no_diff_reduction: Annotated[bool, Query(description="是否排除减难 MOD 成绩")] = True,
):
    if not beatmapset_ids:
        return {"beatmaps_passed": []}
    if len(beatmapset_ids) > 50:
        raise HTTPException(status_code=413, detail="beatmapset_ids cannot exceed 50 items")

    user = await session.get(User, user_id)
    if user is None or not await visible_to_current_user(user, current_user, session):
        raise HTTPException(404, detail="User not found")

    allowed_mode: GameMode | None = None
    if ruleset_id is not None:
        try:
            allowed_mode = GameMode.from_int_extra(ruleset_id)
        except KeyError as exc:
            raise HTTPException(status_code=422, detail="Invalid ruleset_id") from exc

    score_query = (
        select(Score.beatmap_id, Score.mods, Score.gamemode, Beatmap.mode)
        .where(
            Score.user_id == user.id,
            col(Score.beatmap_id).in_(select(Beatmap.id).where(col(Beatmap.beatmapset_id).in_(beatmapset_ids))),
            col(Score.passed).is_(True),
        )
        .join(Beatmap, col(Beatmap.id) == Score.beatmap_id)
    )
    if allowed_mode:
        score_query = score_query.where(Score.gamemode == allowed_mode)

    scores = (await session.exec(score_query)).all()
    if not scores:
        return {"beatmaps_passed": []}

    difficulty_reduction_mods = _get_difficulty_reduction_mods() if no_diff_reduction else set()
    passed_beatmap_ids: set[int] = set()
    for beatmap_id, mods, _mode, _beatmap_mode in scores:
        gamemode = GameMode(_mode)
        beatmap_mode = GameMode(_beatmap_mode)

        if exclude_converts and gamemode.to_base_ruleset() != beatmap_mode:
            continue
        if difficulty_reduction_mods and any(mod["acronym"] in difficulty_reduction_mods for mod in mods):
            continue
        passed_beatmap_ids.add(beatmap_id)
    if not passed_beatmap_ids:
        return {"beatmaps_passed": []}

    beatmaps = (
        await session.exec(
            select(Beatmap)
            .where(col(Beatmap.id).in_(passed_beatmap_ids))
            .order_by(col(Beatmap.difficulty_rating).desc())
        )
    ).all()

    return {
        "beatmaps_passed": [
            await BeatmapModel.transform(
                beatmap,
            )
            for beatmap in beatmaps
        ]
    }


@router.get(
    "/users/{user_id}/{ruleset}",
    name="获取用户信息(指定ruleset)",
    description="通过用户 ID 或用户名获取单个用户的详细信息，并指定特定 ruleset。",
    tags=["用户"],
    responses={
        200: api_doc("用户信息", UserModel, User.USER_INCLUDES),
    },
)
@asset_proxy_response
async def get_user_info_ruleset(
    session: Database,
    background_task: BackgroundTasks,
    user_id: Annotated[str, Path(description="用户 ID 或用户名")],
    ruleset: Annotated[GameMode | None, Path(description="指定 ruleset")],
    current_user: User | None = Security(get_optional_user, scopes=["public"]),
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
    searched_is_self = current_user is not None and current_user.id == searched_user.id
    should_not_show = not searched_is_self and await searched_user.is_restricted(session)
    if should_not_show:
        raise HTTPException(404, detail="User not found")

    user_resp = await UserModel.transform(
        searched_user,
        includes=User.USER_INCLUDES,
        ruleset=ruleset,
    )

    # 异步缓存结果
    background_task.add_task(cache_service.cache_user, user_resp, ruleset)
    return user_resp


@router.get("/users/{user_id}/", include_in_schema=False)
@router.get(
    "/users/{user_id}",
    name="获取用户信息",
    description="通过用户 ID 或用户名获取单个用户的详细信息。",
    tags=["用户"],
    responses={
        200: api_doc("用户信息", UserModel, User.USER_INCLUDES),
    },
)
@asset_proxy_response
async def get_user_info(
    background_task: BackgroundTasks,
    session: Database,
    request: Request,
    user_id: Annotated[str, Path(description="用户 ID 或用户名")],
    current_user: User | None = Security(get_optional_user, scopes=["public"]),
):
    redis = get_redis()
    cache_service = get_user_cache_service(redis)

    # 如果是数字ID，先尝试从缓存获取
    if user_id.isdigit():
        user_id_int = int(user_id)
        cached_user = await cache_service.get_user_from_cache(user_id_int)
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
    searched_is_self = current_user is not None and current_user.id == searched_user.id
    should_not_show = not searched_is_self and await searched_user.is_restricted(session)
    if should_not_show:
        raise HTTPException(404, detail="User not found")

    user_resp = await UserModel.transform(
        searched_user,
        includes=User.USER_INCLUDES,
    )

    # 异步缓存结果
    background_task.add_task(cache_service.cache_user, user_resp)
    return user_resp


beatmapset_includes = [*BeatmapsetModel.BEATMAPSET_TRANSFORMER_INCLUDES, "beatmaps"]


@router.get(
    "/users/{user_id}/beatmapsets/{type}",
    name="获取用户谱面集列表",
    description="获取指定用户特定类型的谱面集列表，如最常游玩、收藏等。",
    tags=["用户"],
    responses={
        200: api_doc(
            "当类型为 `most_played` 时返回 `list[BeatmapPlaycountsModel]`，其他为 `list[BeatmapsetModel]`",
            list[BeatmapsetModel] | list[BeatmapPlaycountsModel],
            beatmapset_includes,
        )
    },
)
@asset_proxy_response
async def get_user_beatmapsets(
    session: Database,
    background_task: BackgroundTasks,
    cache_service: UserCacheService,
    user_id: Annotated[int, Path(description="用户 ID")],
    type: Annotated[BeatmapsetType, Path(description="谱面集类型")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    limit: Annotated[int, Query(ge=1, le=1000, description="返回条数 (1-1000)")] = 100,
    offset: Annotated[int, Query(ge=0, description="偏移量")] = 0,
):
    # 先尝试从缓存获取
    cached_result = await cache_service.get_user_beatmapsets_from_cache(user_id, type.value, limit, offset)
    if cached_result is not None:
        return cached_result

    user = await session.get(User, user_id)
    if not user or user.id == BANCHOBOT_ID or not await visible_to_current_user(user, current_user, session):
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
        if offset == 0:
            cursor = sys.maxsize
        else:
            cursor = (
                await session.exec(
                    select(FavouriteBeatmapset.id)
                    .where(FavouriteBeatmapset.user_id == user_id)
                    .order_by(col(FavouriteBeatmapset.id).desc())
                    .limit(1)
                    .offset(offset - 1)
                )
            ).first()
        if cursor is None:
            return []
        favourites = (
            await session.exec(
                select(FavouriteBeatmapset)
                .where(FavouriteBeatmapset.user_id == user_id, FavouriteBeatmapset.id < cursor)
                .order_by(col(FavouriteBeatmapset.id).desc())
                .limit(limit)
            )
        ).all()
        resp = [
            await BeatmapsetModel.transform(
                favourite.beatmapset, session=session, user=user, includes=beatmapset_includes
            )
            for favourite in favourites
        ]

    elif type == BeatmapsetType.MOST_PLAYED:
        if offset == 0:
            cursor = sys.maxsize, sys.maxsize
        else:
            cursor = (
                await session.exec(
                    select(BeatmapPlaycounts.playcount, BeatmapPlaycounts.id)
                    .where(BeatmapPlaycounts.user_id == user_id)
                    .order_by(col(BeatmapPlaycounts.playcount).desc(), col(BeatmapPlaycounts.id).desc())
                    .limit(1)
                    .offset(offset - 1)
                )
            ).first()
        if cursor is None:
            return []
        cursor_pc, cursor_id = cursor
        most_played = await session.exec(
            select(BeatmapPlaycounts)
            .where(
                BeatmapPlaycounts.user_id == user_id,
                tuple_(BeatmapPlaycounts.playcount, BeatmapPlaycounts.id) < tuple_(cursor_pc, cursor_id),
            )
            .order_by(col(BeatmapPlaycounts.playcount).desc(), col(BeatmapPlaycounts.id).desc())
            .limit(limit)
        )
        resp = [
            await BeatmapPlaycountsModel.transform(most_played_beatmap, user=user, includes=beatmapset_includes)
            for most_played_beatmap in most_played
        ]
    else:
        raise HTTPException(400, detail="Invalid beatmapset type")

    # 异步缓存结果
    async def cache_beatmapsets():
        try:
            await cache_service.cache_user_beatmapsets(user_id, type.value, resp, limit, offset)
        except Exception as e:
            log("Beatmapset").error(f"Error caching user beatmapsets for user {user_id}, type {type.value}: {e}")

    background_task.add_task(cache_beatmapsets)

    return resp


@router.get(
    "/users/{user_id}/scores/{type}",
    name="获取用户成绩列表",
    description=(
        "获取用户特定类型的成绩列表，如最好成绩、最近成绩等。\n\n"
        "如果 `x-api-version >= 20220705`，返回值为 `ScoreResp`列表，"
        "否则为 `LegacyScoreResp`列表。"
    ),
    tags=["用户"],
)
@asset_proxy_response
async def get_user_scores(
    session: Database,
    api_version: APIVersion,
    background_task: BackgroundTasks,
    user_id: Annotated[int, Path(description="用户 ID")],
    type: Annotated[
        Literal["best", "recent", "firsts", "pinned"],
        Path(description=("成绩类型: best 最好成绩 / recent 最近 24h 游玩成绩 / firsts 第一名成绩 / pinned 置顶成绩")),
    ],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    legacy_only: Annotated[bool, Query(description="是否只查询 Stable 成绩")] = False,
    include_fails: Annotated[bool, Query(description="是否包含失败的成绩")] = False,
    mode: Annotated[GameMode | None, Query(description="指定 ruleset (可选，默认为用户主模式)")] = None,
    limit: Annotated[int, Query(ge=1, le=1000, description="返回条数 (1-1000)")] = 100,
    offset: Annotated[int, Query(ge=0, description="偏移量")] = 0,
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
        return cached_scores

    db_user = await session.get(User, user_id)
    if db_user is None or not await visible_to_current_user(db_user, current_user, session):
        raise HTTPException(404, detail="User not found")

    gamemode = mode or db_user.playmode
    where_clause = (col(Score.user_id) == db_user.id) & (col(Score.gamemode) == gamemode)
    includes = Score.USER_PROFILE_INCLUDES.copy()
    if not include_fails:
        where_clause &= col(Score.passed).is_(True)

    scores = []
    if type == "pinned":
        where_clause &= Score.pinned_order > 0
        if offset == 0:
            cursor = 0, sys.maxsize
        else:
            cursor = (
                await session.exec(
                    select(Score.pinned_order, Score.id)
                    .where(where_clause)
                    .order_by(col(Score.pinned_order).asc(), col(Score.id).desc())
                    .limit(1)
                    .offset(offset - 1)
                )
            ).first()
        if cursor:
            cursor_pinned, cursor_id = cursor
            where_clause &= (col(Score.pinned_order) > cursor_pinned) | (
                (col(Score.pinned_order) == cursor_pinned) & (col(Score.id) < cursor_id)
            )
            scores = (
                await session.exec(
                    select(Score)
                    .where(where_clause)
                    .order_by(col(Score.pinned_order).asc(), col(Score.id).desc())
                    .limit(limit)
                )
            ).all()

    elif type == "best":
        where_clause &= exists().where(col(BestScore.score_id) == Score.id)
        includes.append("weight")

        if offset == 0:
            cursor = sys.maxsize, sys.maxsize
        else:
            cursor = (
                await session.exec(
                    select(Score.pp, Score.id)
                    .where(where_clause)
                    .order_by(col(Score.pp).desc(), col(Score.id).desc())
                    .limit(1)
                    .offset(offset - 1)
                )
            ).first()
        if cursor:
            cursor_pp, cursor_id = cursor
            where_clause &= tuple_(col(Score.pp), col(Score.id)) < tuple_(cursor_pp, cursor_id)
            scores = (
                await session.exec(
                    select(Score).where(where_clause).order_by(col(Score.pp).desc(), col(Score.id).desc()).limit(limit)
                )
            ).all()

    elif type == "recent":
        where_clause &= Score.ended_at > utcnow() - timedelta(hours=24)
        if offset == 0:
            cursor = datetime.max, sys.maxsize
        else:
            cursor = (
                await session.exec(
                    select(Score.ended_at, Score.id)
                    .where(where_clause)
                    .order_by(col(Score.ended_at).desc(), col(Score.id).desc())
                    .limit(1)
                    .offset(offset - 1)
                )
            ).first()
        if cursor:
            cursor_date, cursor_id = cursor
            where_clause &= tuple_(col(Score.ended_at), col(Score.id)) < tuple_(cursor_date, cursor_id)
            scores = (
                await session.exec(
                    select(Score)
                    .where(where_clause)
                    .order_by(col(Score.ended_at).desc(), col(Score.id).desc())
                    .limit(limit)
                )
            ).all()

    elif type == "firsts":
        best_scores = await get_user_first_scores(session, db_user.id, gamemode, limit, offset)
        scores = [best_score.score for best_score in best_scores]

    score_responses = [
        await score.to_resp(
            session,
            api_version,
            includes=includes,
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

    return score_responses
