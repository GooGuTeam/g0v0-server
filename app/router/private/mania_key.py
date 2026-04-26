"""Mania key-specific ranking API endpoints.

This module provides g0v0 extension endpoints for retrieving mania rankings
broken down by key count (derived from beatmap CS value).
"""

from typing import Annotated, Literal

from app.config import settings
from app.database import ManiaKeyStatistics, User
from app.database.mania_key_statistics import ManiaKeyStatisticsDict, ManiaKeyStatisticsModel
from app.dependencies.database import Database, get_redis
from app.dependencies.user import get_current_user
from app.helpers import api_doc
from app.models.error import ErrorType, RequestError
from app.service.ranking_cache_service import get_ranking_cache_service

from .router import router

from fastapi import BackgroundTasks, Path, Query, Security
from pydantic import BaseModel, Field
from sqlmodel import col, func, select

SortType = Literal["performance", "score"]


class ManiaKeyRankingResponse(BaseModel):
    """Response model for mania key rankings."""

    ranking: list[ManiaKeyStatisticsDict]
    total: int = Field(0, description="Total number of users")


class UserManiaKeyStatsResponse(BaseModel):
    """Response model for user's mania key statistics across all key counts."""

    user_id: int
    username: str
    statistics: list[ManiaKeyStatisticsDict]


@router.get(
    "/rankings/mania/{key_count}/{sort}",
    responses={
        200: api_doc(
            "Mania key-specific rankings",
            {"ranking": list[ManiaKeyStatisticsModel], "total": int},
            ["user.country", "user.cover"],
            name="ManiaKeyRankingResponse",
        )
    },
    name="Get mania key-specific rankings",
    description="Get user rankings for mania mode filtered by key count (derived from beatmap CS). "
    "This is a g0v0 extension API.",
    tags=["Rankings"],
)
async def get_mania_key_ranking(
    session: Database,
    background_tasks: BackgroundTasks,
    key_count: Annotated[int, Path(ge=1, le=18, description="Key count (e.g. 4 for 4K, 7 for 7K)")],
    sort: Annotated[SortType, Path(..., description="Ranking type: performance (pp) / score (ranked score)")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    country: Annotated[str | None, Query(description="Country code")] = None,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
):
    """Get mania key-specific rankings.

    Key count is derived from the beatmap's Circle Size (CS) value.
    For example, a beatmap with CS=4 is a 4K map, CS=7 is a 7K map.
    """
    redis = get_redis()
    cache_service = get_ranking_cache_service(redis)

    # Try to get data from cache
    cached_data = await cache_service.get_cached_mania_key_ranking(key_count, sort, country, page)
    cached_stats = await cache_service.get_cached_mania_key_stats(key_count, sort, country)

    if cached_data and cached_stats:
        return {
            "ranking": cached_data,
            "total": cached_stats.get("total", 0),
        }

    # Cache miss, query from database
    wheres = [
        col(ManiaKeyStatistics.key_count) == key_count,
        col(ManiaKeyStatistics.pp) > 0,
        col(ManiaKeyStatistics.is_ranked).is_(True),
    ]
    include = ManiaKeyStatistics.RANKING_INCLUDES.copy()

    if sort == "performance":
        order_by = col(ManiaKeyStatistics.pp).desc()
    else:
        order_by = col(ManiaKeyStatistics.ranked_score).desc()

    if country:
        wheres.append(col(ManiaKeyStatistics.user).has(country_code=country.upper()))
        include.append("country_rank")

    # Query total count
    count_query = (
        select(func.count())
        .select_from(ManiaKeyStatistics)
        .where(
            *wheres,
            ~User.is_restricted_query(col(ManiaKeyStatistics.user_id)),
        )
    )
    total_count_result = await session.exec(count_query)
    total_count = total_count_result.one()

    statistics_list = await session.exec(
        select(ManiaKeyStatistics)
        .where(
            *wheres,
            ~User.is_restricted_query(col(ManiaKeyStatistics.user_id)),
        )
        .order_by(order_by)
        .limit(50)
        .offset(50 * (page - 1))
    )

    # Transform to response format
    ranking_data = []
    for statistics in statistics_list:
        user_stats_resp = await ManiaKeyStatisticsModel.transform(
            statistics, includes=include, user_country=current_user.country_code
        )
        ranking_data.append(user_stats_resp)

    # Async cache data
    cache_data = ranking_data
    stats_data = {"total": total_count}

    background_tasks.add_task(
        cache_service.cache_mania_key_ranking,
        key_count,
        sort,
        cache_data,
        country,
        page,
        ttl=settings.ranking_cache_expire_minutes * 60,
    )

    background_tasks.add_task(
        cache_service.cache_mania_key_stats,
        key_count,
        sort,
        stats_data,
        country,
        ttl=settings.ranking_cache_expire_minutes * 60,
    )

    return {
        "ranking": ranking_data,
        "total": total_count,
    }


@router.get(
    "/rankings/mania/user/{user_id}",
    name="Get user mania key statistics",
    description="Get a user's mania mode statistics broken down by key count. This is a g0v0 extension API.",
    tags=["Rankings"],
)
async def get_user_mania_key_stats(
    session: Database,
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    user_id: Annotated[int, Path(description="User ID to query")],
):
    """Get a user's mania key-specific statistics across all key counts.

    Returns all ranked ManiaKeyStatistics records for the given user,
    sorted by key count ascending.
    """
    target_user = (await session.exec(select(User).where(User.id == user_id))).first()
    if not target_user:
        raise RequestError(ErrorType.USER_NOT_FOUND, "The requested user could not be found.")

    stats = (
        await session.exec(
            select(ManiaKeyStatistics)
            .where(
                ManiaKeyStatistics.user_id == user_id,
                ManiaKeyStatistics.pp > 0,
                col(ManiaKeyStatistics.is_ranked).is_(True),
            )
            .order_by(col(ManiaKeyStatistics.key_count))
        )
    ).all()

    statistics_data: list[ManiaKeyStatisticsDict] = []
    for stat in stats:
        resp = await ManiaKeyStatisticsModel.transform(stat, includes=[])
        # Remove nested user object to keep response compact
        resp.pop("user", None)
        statistics_data.append(resp)  # type: ignore[arg-type]

    return UserManiaKeyStatsResponse(
        user_id=user_id,
        username=target_user.username,
        statistics=statistics_data,
    )
