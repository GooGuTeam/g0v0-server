"""Ranking API endpoints.

This module provides endpoints for retrieving various leaderboards including
user rankings, team rankings, and country rankings across different game modes.
"""

from typing import Annotated, Literal

from app.config import settings
from app.database import Team, TeamMember, User, UserStatistics
from app.database.statistics import UserStatisticsModel
from app.dependencies.database import Database, get_redis
from app.dependencies.user import get_current_user
from app.helpers import api_doc
from app.models.score import GameMode
from app.service.ranking_cache_service import get_ranking_cache_service

from .router import router

from fastapi import BackgroundTasks, Path, Query, Security
from pydantic import BaseModel, Field
from sqlmodel import col, func, select


class TeamStatistics(BaseModel):
    """Statistics for a team in the rankings.

    Attributes:
        team_id: The team's ID.
        ruleset_id: The game mode ID.
        play_count: Total play count across all team members.
        ranked_score: Total ranked score across all team members.
        performance: Total performance points across all team members.
        team: The team information.
        member_count: Number of active members in the team.
    """

    team_id: int
    ruleset_id: int
    play_count: int
    ranked_score: int
    performance: int

    team: Team
    member_count: int


class TeamResponse(BaseModel):
    """Response model for team rankings.

    Attributes:
        ranking: List of team statistics.
        total: Total number of teams.
    """

    ranking: list[TeamStatistics]
    total: int = Field(0, description="Total number of teams")


SortType = Literal["performance", "score"]


@router.get(
    "/rankings/{ruleset}/team",
    name="Get team rankings",
    description="Get team rankings sorted by pp for the specified game mode",
    tags=["Rankings"],
    response_model=TeamResponse,
)
async def get_team_ranking_pp(
    session: Database,
    background_tasks: BackgroundTasks,
    ruleset: Annotated[GameMode, Path(..., description="The specified ruleset")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
):
    """Get team rankings sorted by performance points.

    Args:
        session: Database session dependency.
        background_tasks: Background tasks handler.
        ruleset: The game mode to get rankings for.
        current_user: The authenticated user.
        page: Page number (1-indexed).

    Returns:
        TeamResponse: Team rankings with statistics.
    """
    return await get_team_ranking(session, background_tasks, "performance", ruleset, current_user, page)


@router.get(
    "/rankings/{ruleset}/team/{sort}",
    response_model=TeamResponse,
    name="Get team rankings",
    description="Get team rankings for the specified game mode",
    tags=["Rankings"],
)
async def get_team_ranking(
    session: Database,
    background_tasks: BackgroundTasks,
    sort: Annotated[
        SortType,
        Path(
            ...,
            description="Ranking type: performance (pp) / score (ranked score) "
            "**This parameter is an extension added by this server, not part of the v2 API**",
        ),
    ],
    ruleset: Annotated[GameMode, Path(..., description="The specified ruleset")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
):
    """Get team rankings with configurable sorting.

    Args:
        session: Database session dependency.
        background_tasks: Background tasks handler.
        sort: Sort type (performance or score).
        ruleset: The game mode to get rankings for.
        current_user: The authenticated user.
        page: Page number (1-indexed).

    Returns:
        TeamResponse: Team rankings with statistics.
    """
    # Get Redis connection and cache service
    redis = get_redis()
    cache_service = get_ranking_cache_service(redis)

    # Try to get data from cache (team rankings)
    cached_data = await cache_service.get_cached_team_ranking(ruleset, page)
    cached_stats = await cache_service.get_cached_team_stats(ruleset)

    if cached_data and cached_stats:
        # Return data from cache
        return TeamResponse(
            ranking=[TeamStatistics.model_validate(item) for item in cached_data],
            total=cached_stats.get("total", 0),
        )

    # Cache miss, query from database
    response = TeamResponse(ranking=[], total=0)
    teams = (await session.exec(select(Team))).all()
    valid_teams = []  # Store valid team statistics

    for team in teams:
        statistics = (
            await session.exec(
                select(UserStatistics).where(
                    UserStatistics.mode == ruleset,
                    UserStatistics.pp > 0,
                    col(UserStatistics.user).has(col(User.team_membership).has(col(TeamMember.team_id) == team.id)),
                    ~User.is_restricted_query(col(UserStatistics.user_id)),
                )
            )
        ).all()

        if not statistics:
            continue

        pp = 0
        total_ranked_score = 0
        total_play_count = 0
        member_count = 0

        for stat in statistics:
            total_ranked_score += stat.ranked_score
            total_play_count += stat.play_count
            pp += stat.pp
            member_count += 1

        stats = TeamStatistics(
            team_id=team.id,
            ruleset_id=int(ruleset),
            play_count=total_play_count,
            ranked_score=total_ranked_score,
            performance=round(pp),
            team=team,
            member_count=member_count,
        )
        valid_teams.append(stats)

    # Sort
    if sort == "performance":
        valid_teams.sort(key=lambda x: x.performance, reverse=True)
    else:
        valid_teams.sort(key=lambda x: x.ranked_score, reverse=True)

    # Calculate total
    total_count = len(valid_teams)

    # Pagination
    page_size = 50
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size

    # Get current page data
    current_page_data = valid_teams[start_idx:end_idx]

    # Async cache data (don't wait for completion)
    cache_data = [item.model_dump() for item in current_page_data]
    stats_data = {"total": total_count}

    # Create background task to cache data
    background_tasks.add_task(
        cache_service.cache_team_ranking,
        ruleset,
        cache_data,
        page,
        ttl=settings.ranking_cache_expire_minutes * 60,
    )

    # Cache statistics
    background_tasks.add_task(
        cache_service.cache_team_stats,
        ruleset,
        stats_data,
        ttl=settings.ranking_cache_expire_minutes * 60,
    )

    # Return current page results
    response.ranking = current_page_data
    response.total = total_count
    return response


class CountryStatistics(BaseModel):
    """Statistics for a country in the rankings.

    Attributes:
        code: Country code (ISO 3166-1 alpha-2).
        active_users: Number of active users from this country.
        play_count: Total play count for users from this country.
        ranked_score: Total ranked score for users from this country.
        performance: Total performance points for users from this country.
    """

    code: str
    active_users: int
    play_count: int
    ranked_score: int
    performance: int


class CountryResponse(BaseModel):
    """Response model for country rankings.

    Attributes:
        ranking: List of country statistics.
    """

    ranking: list[CountryStatistics]


@router.get(
    "/rankings/{ruleset}/country",
    name="Get country rankings",
    description="Get country rankings sorted by pp for the specified game mode",
    tags=["Rankings"],
    response_model=CountryResponse,
)
async def get_country_ranking_pp(
    session: Database,
    background_tasks: BackgroundTasks,
    ruleset: Annotated[GameMode, Path(..., description="The specified ruleset")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
):
    """Get country rankings sorted by performance points.

    Args:
        session: Database session dependency.
        background_tasks: Background tasks handler.
        ruleset: The game mode to get rankings for.
        current_user: The authenticated user.
        page: Page number (1-indexed).

    Returns:
        CountryResponse: Country rankings with statistics.
    """
    return await get_country_ranking(session, background_tasks, ruleset, "performance", current_user, page)


@router.get(
    "/rankings/{ruleset}/country/{sort}",
    response_model=CountryResponse,
    name="Get country rankings",
    description="Get country rankings for the specified game mode",
    tags=["Rankings"],
)
async def get_country_ranking(
    session: Database,
    background_tasks: BackgroundTasks,
    ruleset: Annotated[GameMode, Path(..., description="The specified ruleset")],
    sort: Annotated[
        SortType,
        Path(
            ...,
            description="Ranking type: performance (pp) / score (ranked score) "
            "**This parameter is an extension added by this server, not part of the v2 API**",
        ),
    ],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
):
    """Get country rankings with configurable sorting.

    Args:
        session: Database session dependency.
        background_tasks: Background tasks handler.
        ruleset: The game mode to get rankings for.
        sort: Sort type (performance or score).
        current_user: The authenticated user.
        page: Page number (1-indexed).

    Returns:
        CountryResponse: Country rankings with statistics.
    """
    # Get Redis connection and cache service
    redis = get_redis()
    cache_service = get_ranking_cache_service(redis)

    # Try to get data from cache
    cached_data = await cache_service.get_cached_country_ranking(ruleset, page)

    if cached_data:
        # Return data from cache
        return CountryResponse(ranking=[CountryStatistics.model_validate(item) for item in cached_data])

    # Cache miss, query from database
    response = CountryResponse(ranking=[])
    countries = (await session.exec(select(User.country_code).distinct())).all()

    for country in countries:
        if not country:  # Skip empty country codes
            continue

        statistics = (
            await session.exec(
                select(UserStatistics).where(
                    UserStatistics.mode == ruleset,
                    UserStatistics.pp > 0,
                    col(UserStatistics.user).has(country_code=country),
                    col(UserStatistics.user).has(is_active=True),
                    ~User.is_restricted_query(col(UserStatistics.user_id)),
                )
            )
        ).all()

        if not statistics:  # Skip countries with no data
            continue

        pp = 0
        active_users = 0
        total_play_count = 0
        total_ranked_score = 0

        for stat in statistics:
            active_users += 1
            total_play_count += stat.play_count
            total_ranked_score += stat.ranked_score
            pp += stat.pp

        country_stats = CountryStatistics(
            code=country,
            active_users=active_users,
            play_count=total_play_count,
            ranked_score=total_ranked_score,
            performance=round(pp),
        )
        response.ranking.append(country_stats)

    if sort == "performance":
        response.ranking.sort(key=lambda x: x.performance, reverse=True)
    else:
        response.ranking.sort(key=lambda x: x.ranked_score, reverse=True)

    # Pagination
    page_size = 50
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size

    # Get current page data
    current_page_data = response.ranking[start_idx:end_idx]

    # Async cache data (don't wait for completion)
    cache_data = [item.model_dump() for item in current_page_data]

    # Create background task to cache data
    background_tasks.add_task(
        cache_service.cache_country_ranking,
        ruleset,
        cache_data,
        page,
        ttl=settings.ranking_cache_expire_minutes * 60,
    )

    # Return current page results
    response.ranking = current_page_data
    return response


@router.get(
    "/rankings/{ruleset}/{sort}",
    responses={
        200: api_doc(
            "User rankings",
            {"ranking": list[UserStatisticsModel], "total": int},
            ["user.country", "user.cover"],
            name="TopUsersResponse",
        )
    },
    name="Get user rankings",
    description="Get user rankings for the specified game mode",
    tags=["Rankings"],
)
async def get_user_ranking(
    session: Database,
    background_tasks: BackgroundTasks,
    ruleset: Annotated[GameMode, Path(..., description="The specified ruleset")],
    sort: Annotated[SortType, Path(..., description="Ranking type: performance (pp) / score (ranked score)")],
    current_user: Annotated[User, Security(get_current_user, scopes=["public"])],
    country: Annotated[str | None, Query(description="Country code")] = None,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
):
    """Get user rankings with configurable sorting.

    Args:
        session: Database session dependency.
        background_tasks: Background tasks handler.
        ruleset: The game mode to get rankings for.
        sort: Sort type (performance or score).
        current_user: The authenticated user.
        country: Optional country code filter.
        page: Page number (1-indexed).

    Returns:
        dict: User rankings with statistics and total count.
    """
    # Get Redis connection and cache service
    redis = get_redis()
    cache_service = get_ranking_cache_service(redis)

    # Try to get data from cache
    cached_data = await cache_service.get_cached_ranking(ruleset, sort, country, page)
    cached_stats = await cache_service.get_cached_stats(ruleset, sort, country)

    if cached_data and cached_stats:
        # Return data from cache
        return {
            "ranking": cached_data,
            "total": cached_stats.get("total", 0),
        }

    # Cache miss, query from database
    wheres = [
        col(UserStatistics.mode) == ruleset,
        col(UserStatistics.pp) > 0,
        col(UserStatistics.is_ranked),
    ]
    include = UserStatistics.RANKING_INCLUDES.copy()
    if sort == "performance":
        order_by = col(UserStatistics.pp).desc()
        include.append("rank_change_since_30_days")
    else:
        order_by = col(UserStatistics.ranked_score).desc()
    if country:
        wheres.append(col(UserStatistics.user).has(country_code=country.upper()))
        include.append("country_rank")

    # Query total count
    count_query = select(func.count()).select_from(UserStatistics).where(*wheres)
    total_count_result = await session.exec(count_query)
    total_count = total_count_result.one()

    statistics_list = await session.exec(
        select(UserStatistics)
        .where(
            *wheres,
            ~User.is_restricted_query(col(UserStatistics.user_id)),
        )
        .order_by(order_by)
        .limit(50)
        .offset(50 * (page - 1))
    )

    # Transform to response format
    ranking_data = []
    for statistics in statistics_list:
        user_stats_resp = await UserStatisticsModel.transform(
            statistics, includes=include, user_country=current_user.country_code
        )
        ranking_data.append(user_stats_resp)

    # Async cache data (don't wait for completion)
    # Use TTL setting from config
    cache_data = ranking_data
    stats_data = {"total": total_count}

    # Create background task to cache data
    background_tasks.add_task(
        cache_service.cache_ranking,
        ruleset,
        sort,
        cache_data,
        country,
        page,
        ttl=settings.ranking_cache_expire_minutes * 60,
    )

    # Cache statistics
    background_tasks.add_task(
        cache_service.cache_stats,
        ruleset,
        sort,
        stats_data,
        country,
        ttl=settings.ranking_cache_expire_minutes * 60,
    )

    return {
        "ranking": ranking_data,
        "total": total_count,
    }
