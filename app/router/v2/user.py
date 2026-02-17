"""User endpoints for osu! API v2.

This module provides endpoints for retrieving user information, activity,
beatmapsets, scores, and other user-related data.
"""

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
from app.helpers import api_doc, asset_proxy_response, utcnow
from app.log import log
from app.models.error import ErrorType, RequestError
from app.models.mods import API_MODS
from app.models.score import GameMode
from app.models.user import BeatmapsetType
from app.service.user_cache_service import get_user_cache_service

from .router import router

from fastapi import BackgroundTasks, Path, Query, Request, Security
from sqlmodel import exists, select, tuple_
from sqlmodel.sql.expression import col


def _get_difficulty_reduction_mods() -> set[str]:
    """Get the set of all difficulty reduction mod acronyms.

    Returns:
        set[str]: Set of mod acronyms that are classified as difficulty reduction.
    """
    mods: set[str] = set()
    for ruleset_mods in API_MODS.values():
        for mod_acronym, mod_meta in ruleset_mods.items():
            if mod_meta.get("Type") == "DifficultyReduction":
                mods.add(mod_acronym)
    return mods


async def visible_to_current_user(user: User, current_user: User | None, session: Database) -> bool:
    """Check if a user should be visible to the current user.

    Args:
        user: The user to check visibility for.
        current_user: The currently authenticated user (may be None).
        session: Database session.

    Returns:
        bool: True if the user should be visible, False otherwise.
    """
    if user.id == BANCHOBOT_ID:
        return False
    if current_user and current_user.id == user.id:
        return True
    return not await user.is_restricted(session)


@router.get(
    "/users/",
    responses={
        200: api_doc(
            "Batch get user information",
            {"users": list[UserModel]},
            User.CARD_INCLUDES,
            name="UsersLookupResponse",
        )
    },
    name="Batch get user information",
    description="Batch retrieve user information by user ID list.",
    tags=["Users"],
)
@router.get("/users/lookup", include_in_schema=False)
@router.get("/users/lookup/", include_in_schema=False)
@asset_proxy_response
async def get_users(
    session: Database,
    request: Request,
    background_task: BackgroundTasks,
    user_ids: Annotated[list[int], Query(default_factory=list, alias="ids[]", description="List of user IDs to query")],
    # current_user: User = Security(get_current_user, scopes=["public"]),
    include_variant_statistics: Annotated[
        bool,
        Query(description="Whether to include variant statistics for each mode"),
    ] = False,  # TODO: future use
):
    """Batch retrieve user information by user ID list.

    Args:
        session: Database session dependency.
        request: FastAPI request object.
        background_task: Background tasks handler.
        user_ids: List of user IDs to query.
        include_variant_statistics: Whether to include variant statistics.

    Returns:
        dict: Dictionary containing list of user information.
    """
    redis = get_redis()
    cache_service = get_user_cache_service(redis)

    if user_ids:
        # Try to get from cache first
        cached_users = []
        uncached_user_ids = []

        for user_id in user_ids[:50]:  # Limit to 50
            cached_user = await cache_service.get_user_from_cache(user_id)
            if cached_user:
                cached_users.append(cached_user)
            else:
                uncached_user_ids.append(user_id)

        # Query uncached users
        if uncached_user_ids:
            searched_users = (
                await session.exec(
                    select(User).where(col(User.id).in_(uncached_user_ids), ~User.is_restricted_query(col(User.id)))
                )
            ).all()

            # Add queried users to cache and return
            for searched_user in searched_users:
                if searched_user.id != BANCHOBOT_ID:
                    user_resp = await UserModel.transform(
                        searched_user,
                        includes=User.CARD_INCLUDES,
                    )
                    cached_users.append(user_resp)
                    # Async cache, don't block response
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
            # Async cache
            background_task.add_task(cache_service.cache_user, user_resp)

        response = {"users": users}
        return response


@router.get(
    "/users/{user_id}/recent_activity",
    tags=["Users"],
    response_model=list[Event],
    name="Get user recent activity",
    description="Get user activity logs from the last 30 days.",
)
async def get_user_events(
    session: Database,
    user_id: Annotated[int, Path(description="User ID")],
    limit: Annotated[int, Query(description="Limit the number of activities returned")] = 50,
    offset: Annotated[int | None, Query(description="Activity log offset")] = None,
    current_user: User | None = Security(get_optional_user, scopes=["public"]),
):
    """Get user recent activity.

    Args:
        session: Database session dependency.
        user_id: The user ID.
        limit: Maximum number of activities to return.
        offset: Offset for pagination.
        current_user: The authenticated user (optional).

    Returns:
        list[Event]: List of user activity events.

    Raises:
        RequestError: If the user is not found.
    """
    db_user = await session.get(User, user_id)
    if db_user is None or not await visible_to_current_user(db_user, current_user, session):
        raise RequestError(ErrorType.USER_NOT_FOUND)
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
    name="Get user kudosu records",
    description="Get kudosu records for a specified user. TODO: May be implemented in the future",
    tags=["Users"],
)
async def get_user_kudosu(
    session: Database,
    user_id: Annotated[int, Path(description="User ID")],
    offset: Annotated[int, Query(description="Offset")] = 0,
    limit: Annotated[int, Query(description="Number of records to return")] = 6,
    current_user: User | None = Security(get_optional_user, scopes=["public"]),
):
    """Get user kudosu records.

    TODO: May be implemented in the future.
    Currently returns an empty array as a placeholder.

    Args:
        session: Database session dependency.
        user_id: The user ID.
        offset: Pagination offset.
        limit: Number of records to return.
        current_user: The authenticated user (optional).

    Returns:
        list: Empty list (placeholder).

    Raises:
        RequestError: If the user is not found.
    """
    # Verify user exists
    db_user = await session.get(User, user_id)
    if db_user is None or not await visible_to_current_user(db_user, current_user, session):
        raise RequestError(ErrorType.USER_NOT_FOUND)

    # TODO: Implement kudosu record retrieval logic
    return []


@router.get(
    "/users/{user_id}/beatmaps-passed",
    name="Get user passed beatmaps",
    description="Get list of passed beatmaps for a user within specified beatmapsets.",
    tags=["Users"],
    responses={
        200: api_doc(
            "User passed beatmaps list",
            {"beatmaps_passed": list[BeatmapModel]},
            name="BeatmapsPassedResponse",
        )
    },
)
@asset_proxy_response
async def get_user_beatmaps_passed(
    session: Database,
    user_id: Annotated[int, Path(description="User ID")],
    current_user: User | None = Security(get_optional_user, scopes=["public"]),
    beatmapset_ids: Annotated[
        list[int],
        Query(
            alias="beatmapset_ids[]",
            description="List of beatmapset IDs to query (max 50)",
        ),
    ] = [],
    ruleset_id: Annotated[
        int | None,
        Query(description="Specified ruleset ID"),
    ] = None,
    exclude_converts: Annotated[bool, Query(description="Whether to exclude converted beatmap scores")] = False,
    is_legacy: Annotated[bool | None, Query(description="Whether to only return Stable scores")] = None,
    no_diff_reduction: Annotated[bool, Query(description="Whether to exclude difficulty reduction mod scores")] = True,
):
    """Get user passed beatmaps within specified beatmapsets.

    Args:
        session: Database session dependency.
        user_id: The user ID.
        current_user: The authenticated user (optional).
        beatmapset_ids: List of beatmapset IDs to filter.
        ruleset_id: Optional ruleset ID filter.
        exclude_converts: Whether to exclude converted beatmap scores.
        is_legacy: Whether to only return Stable scores.
        no_diff_reduction: Whether to exclude difficulty reduction mod scores.

    Returns:
        dict: Dictionary containing list of passed beatmaps.

    Raises:
        RequestError: If user not found or too many beatmapset IDs.
    """
    if not beatmapset_ids:
        return {"beatmaps_passed": []}
    if len(beatmapset_ids) > 50:
        raise RequestError(ErrorType.BEATMAPSET_IDS_TOO_MANY)

    user = await session.get(User, user_id)
    if user is None or not await visible_to_current_user(user, current_user, session):
        raise RequestError(ErrorType.USER_NOT_FOUND)

    allowed_mode: GameMode | None = None
    if ruleset_id is not None:
        try:
            allowed_mode = GameMode.from_int_extra(ruleset_id)
        except KeyError as exc:
            raise RequestError(ErrorType.INVALID_RULESET_ID) from exc

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
    name="Get user information (with ruleset)",
    description="Get detailed information for a single user by user ID or username, with a specified ruleset.",
    tags=["Users"],
    responses={
        200: api_doc("User information", UserModel, User.USER_INCLUDES),
    },
)
@asset_proxy_response
async def get_user_info_ruleset(
    session: Database,
    background_task: BackgroundTasks,
    user_id: Annotated[str, Path(description="User ID or username")],
    ruleset: Annotated[GameMode | None, Path(description="Specified ruleset")],
    current_user: User | None = Security(get_optional_user, scopes=["public"]),
):
    """Get user information with a specified ruleset.

    Args:
        session: Database session dependency.
        background_task: Background tasks handler.
        user_id: User ID or username.
        ruleset: The game mode to get statistics for.
        current_user: The authenticated user (optional).

    Returns:
        UserModel: User information with statistics for the specified ruleset.

    Raises:
        RequestError: If the user is not found.
    """
    redis = get_redis()
    cache_service = get_user_cache_service(redis)

    # If numeric ID, try to get from cache first
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
        raise RequestError(ErrorType.USER_NOT_FOUND)
    searched_is_self = current_user is not None and current_user.id == searched_user.id
    should_not_show = not searched_is_self and await searched_user.is_restricted(session)
    if should_not_show:
        raise RequestError(ErrorType.USER_NOT_FOUND)

    user_resp = await UserModel.transform(
        searched_user,
        includes=User.USER_INCLUDES,
        ruleset=ruleset,
    )

    # Async cache result
    background_task.add_task(cache_service.cache_user, user_resp, ruleset)
    return user_resp


@router.get("/users/{user_id}/", include_in_schema=False)
@router.get(
    "/users/{user_id}",
    name="Get user information",
    description="Get detailed information for a single user by user ID or username.",
    tags=["Users"],
    responses={
        200: api_doc("User information", UserModel, User.USER_INCLUDES),
    },
)
@asset_proxy_response
async def get_user_info(
    background_task: BackgroundTasks,
    session: Database,
    request: Request,
    user_id: Annotated[str, Path(description="User ID or username")],
    current_user: User | None = Security(get_optional_user, scopes=["public"]),
):
    """Get user information.

    Args:
        background_task: Background tasks handler.
        session: Database session dependency.
        request: FastAPI request object.
        user_id: User ID or username.
        current_user: The authenticated user (optional).

    Returns:
        UserModel: User information.

    Raises:
        RequestError: If the user is not found.
    """
    redis = get_redis()
    cache_service = get_user_cache_service(redis)

    # If numeric ID, try to get from cache first
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
        raise RequestError(ErrorType.USER_NOT_FOUND)
    searched_is_self = current_user is not None and current_user.id == searched_user.id
    should_not_show = not searched_is_self and await searched_user.is_restricted(session)
    if should_not_show:
        raise RequestError(ErrorType.USER_NOT_FOUND)

    user_resp = await UserModel.transform(
        searched_user,
        includes=User.USER_INCLUDES,
    )

    # Async cache result
    background_task.add_task(cache_service.cache_user, user_resp)
    return user_resp


beatmapset_includes = [*BeatmapsetModel.BEATMAPSET_TRANSFORMER_INCLUDES, "beatmaps"]


@router.get(
    "/users/{user_id}/beatmapsets/{type}",
    name="Get user beatmapsets",
    description="Get user's beatmapsets of a specific type, such as most played, favourites, etc.",
    tags=["Users"],
    responses={
        200: api_doc(
            "Returns `list[BeatmapPlaycountsModel]` when type is `most_played`, otherwise `list[BeatmapsetModel]`",
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
    user_id: Annotated[int, Path(description="User ID")],
    type: Annotated[BeatmapsetType, Path(description="Beatmapset type")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    limit: Annotated[int, Query(ge=1, le=1000, description="Number of results (1-1000)")] = 100,
    offset: Annotated[int, Query(ge=0, description="Offset")] = 0,
):
    """Get user beatmapsets of a specific type.

    Args:
        session: Database session dependency.
        background_task: Background tasks handler.
        cache_service: User cache service dependency.
        user_id: The user ID.
        type: Beatmapset type (favourite, most_played, etc.).
        current_user: The authenticated user.
        limit: Maximum number of results.
        offset: Pagination offset.

    Returns:
        list: List of beatmapsets or beatmap playcounts depending on type.

    Raises:
        RequestError: If user not found or invalid beatmapset type.
    """
    # Try to get from cache first
    cached_result = await cache_service.get_user_beatmapsets_from_cache(user_id, type.value, limit, offset)
    if cached_result is not None:
        return cached_result

    user = await session.get(User, user_id)
    if not user or user.id == BANCHOBOT_ID or not await visible_to_current_user(user, current_user, session):
        raise RequestError(ErrorType.USER_NOT_FOUND)

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
        raise RequestError(ErrorType.INVALID_BEATMAPSET_TYPE)

    # Async cache result
    async def cache_beatmapsets():
        try:
            await cache_service.cache_user_beatmapsets(user_id, type.value, resp, limit, offset)
        except Exception as e:
            log("Beatmapset").error(f"Error caching user beatmapsets for user {user_id}, type {type.value}: {e}")

    background_task.add_task(cache_beatmapsets)

    return resp


@router.get(
    "/users/{user_id}/scores/{type}",
    name="Get user scores",
    description=(
        "Get user scores of a specific type, such as best, recent, etc.\n\n"
        "If `x-api-version >= 20220705`, returns `ScoreResp` list, "
        "otherwise returns `LegacyScoreResp` list."
    ),
    tags=["Users"],
)
@asset_proxy_response
async def get_user_scores(
    session: Database,
    api_version: APIVersion,
    background_task: BackgroundTasks,
    user_id: Annotated[int, Path(description="User ID")],
    type: Annotated[
        Literal["best", "recent", "firsts", "pinned"],
        Path(
            description=(
                "Score type: best (best scores) / recent (scores in last 24h) / "
                "firsts (first place scores) / pinned (pinned scores)"
            )
        ),
    ],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    legacy_only: Annotated[bool, Query(description="Whether to only query Stable scores")] = False,
    include_fails: Annotated[bool, Query(description="Whether to include failed scores")] = False,
    mode: Annotated[
        GameMode | None, Query(description="Specified ruleset (optional, defaults to user's main mode)")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000, description="Number of results (1-1000)")] = 100,
    offset: Annotated[int, Query(ge=0, description="Offset")] = 0,
):
    """Get user scores of a specific type.

    Args:
        session: Database session dependency.
        api_version: API version from request headers.
        background_task: Background tasks handler.
        user_id: The user ID.
        type: Score type (best, recent, firsts, pinned).
        current_user: The authenticated user.
        legacy_only: Whether to only query Stable scores.
        include_fails: Whether to include failed scores.
        mode: Optional game mode filter.
        limit: Maximum number of results.
        offset: Pagination offset.

    Returns:
        list: List of score responses.

    Raises:
        RequestError: If the user is not found.
    """
    is_legacy_api = api_version < 20220705
    redis = get_redis()
    cache_service = get_user_cache_service(redis)

    # Try to get from cache first (use shorter cache time for recent type)
    cache_expire = 30 if type == "recent" else settings.user_scores_cache_expire_seconds
    cached_scores = await cache_service.get_user_scores_from_cache(
        user_id, type, include_fails, mode, limit, offset, is_legacy_api
    )
    if cached_scores is not None:
        return cached_scores

    db_user = await session.get(User, user_id)
    if db_user is None or not await visible_to_current_user(db_user, current_user, session):
        raise RequestError(ErrorType.USER_NOT_FOUND)

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
