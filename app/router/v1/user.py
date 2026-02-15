"""User endpoints module for osu! API v1.

This module provides endpoints for retrieving user information compatible
with the legacy osu! API v1 specification.
"""

from datetime import datetime
from typing import Annotated, Literal

from app.database.statistics import UserStatistics, UserStatisticsModel
from app.database.user import User
from app.dependencies.database import Database, get_redis
from app.log import logger
from app.models.error import ErrorType, RequestError
from app.models.score import GameMode
from app.service.user_cache_service import get_user_cache_service

from .router import AllStrModel, router

from fastapi import BackgroundTasks, Query
from sqlmodel import col, select


class V1User(AllStrModel):
    """V1 API user response model.

    This model represents a user in the format expected by the legacy osu! API v1.
    All fields are serialized to strings for compatibility.

    Attributes:
        user_id: Unique user ID.
        username: Player username.
        join_date: Account registration date.
        count300: Total number of 300s across all plays.
        count100: Total number of 100s across all plays.
        count50: Total number of 50s across all plays.
        playcount: Total number of plays.
        ranked_score: Total ranked score.
        total_score: Total score (including unranked).
        pp_rank: Global PP ranking.
        level: Player level.
        pp_raw: Total performance points.
        accuracy: Overall accuracy percentage.
        count_rank_ss: Number of SS ranks.
        count_rank_ssh: Number of SS+ (hidden) ranks.
        count_rank_s: Number of S ranks.
        count_rank_sh: Number of S+ (hidden) ranks.
        count_rank_a: Number of A ranks.
        country: Two-letter country code.
        total_seconds_played: Total play time in seconds.
        pp_country_rank: Country PP ranking.
        events: List of recent user events.
    """

    user_id: int
    username: str
    join_date: datetime
    count300: int
    count100: int
    count50: int
    playcount: int
    ranked_score: int
    total_score: int
    pp_rank: int
    level: float
    pp_raw: float
    accuracy: float
    count_rank_ss: int
    count_rank_ssh: int
    count_rank_s: int
    count_rank_sh: int
    count_rank_a: int
    country: str
    total_seconds_played: int
    pp_country_rank: int
    events: list[dict]

    @classmethod
    def _get_cache_key(cls, user_id: int, ruleset: GameMode | None = None) -> str:
        """Generate V1 user cache key.

        Args:
            user_id: The user ID.
            ruleset: Optional game mode for mode-specific cache.

        Returns:
            Cache key string.
        """
        if ruleset:
            return f"v1_user:{user_id}:ruleset:{ruleset}"
        return f"v1_user:{user_id}"

    @classmethod
    async def from_db(cls, db_user: User, ruleset: GameMode | None = None) -> "V1User":
        """Create a V1User instance from a database user record.

        Args:
            db_user: The user database record.
            ruleset: Optional game mode for statistics (defaults to user's preferred mode).

        Returns:
            A V1User instance with all fields populated.
        """
        ruleset = ruleset or db_user.playmode
        current_statistics: UserStatistics | None = None
        for i in await db_user.awaitable_attrs.statistics:
            if i.mode == ruleset:
                current_statistics = i
                break
        if current_statistics:
            statistics = await UserStatisticsModel.transform(
                current_statistics, country_code=db_user.country_code, includes=["country_rank"]
            )
        else:
            statistics = None
        return cls(
            user_id=db_user.id,
            username=db_user.username,
            join_date=db_user.join_date,
            count300=current_statistics.count_300 if current_statistics else 0,
            count100=current_statistics.count_100 if current_statistics else 0,
            count50=current_statistics.count_50 if current_statistics else 0,
            playcount=current_statistics.play_count if current_statistics else 0,
            ranked_score=current_statistics.ranked_score if current_statistics else 0,
            total_score=current_statistics.total_score if current_statistics else 0,
            pp_rank=statistics.get("global_rank") or 0 if statistics else 0,
            level=current_statistics.level_current if current_statistics else 0,
            pp_raw=current_statistics.pp if current_statistics else 0.0,
            accuracy=current_statistics.hit_accuracy if current_statistics else 0,
            count_rank_ss=current_statistics.grade_ss if current_statistics else 0,
            count_rank_ssh=current_statistics.grade_ssh if current_statistics else 0,
            count_rank_s=current_statistics.grade_s if current_statistics else 0,
            count_rank_sh=current_statistics.grade_sh if current_statistics else 0,
            count_rank_a=current_statistics.grade_a if current_statistics else 0,
            country=db_user.country_code,
            total_seconds_played=current_statistics.play_time if current_statistics else 0,
            pp_country_rank=statistics.get("country_rank") or 0 if statistics else 0,
            events=[],  # TODO
        )


@router.get(
    "/get_user",
    response_model=list[V1User],
    name="Get User",
    description="Get information for a specified user.",
)
async def get_user(
    session: Database,
    background_tasks: BackgroundTasks,
    user: Annotated[str, Query(..., alias="u", description="User")],
    ruleset_id: Annotated[int | None, Query(alias="m", description="Ruleset ID", ge=0)] = None,
    type: Annotated[
        Literal["string", "id"] | None, Query(description="User type: string for username / id for user ID")
    ] = None,
    event_days: Annotated[int, Query(ge=1, le=31, description="Maximum number of days for events from now")] = 1,
):
    """Retrieve user information.

    Returns user profile information including statistics for the specified game mode.
    Results are cached for improved performance.

    Args:
        session: Database session.
        background_tasks: FastAPI background tasks for async caching.
        user: The user (username or ID based on type parameter).
        ruleset_id: Game mode for statistics (defaults to user's preferred mode).
        type: Interpret user parameter as 'string' (username) or 'id'.
        event_days: Maximum number of days to include events for.

    Returns:
        List containing a single V1User object, or empty list if not found.

    Raises:
        RequestError: If the request parameters are invalid.
    """
    redis = get_redis()
    cache_service = get_user_cache_service(redis)

    # Determine query method and user ID
    is_id_query = type == "id" or user.isdigit()

    # Parse ruleset
    ruleset = GameMode.from_int_extra(ruleset_id) if ruleset_id else None

    # If ID query, try to get from cache first
    cached_v1_user = None
    user_id_for_cache = None

    if is_id_query:
        try:
            user_id_for_cache = int(user)
            cached_v1_user = await cache_service.get_v1_user_from_cache(user_id_for_cache, ruleset)
            if cached_v1_user:
                return [V1User(**cached_v1_user)]
        except (ValueError, TypeError):
            pass  # Not a valid user ID, continue with database query

    # Query user from database
    db_user = (
        await session.exec(
            select(User).where(
                User.id == user if is_id_query else User.username == user,
                ~User.is_restricted_query(col(User.id)),
            )
        )
    ).first()

    if not db_user:
        return []

    try:
        # Generate user data
        v1_user = await V1User.from_db(db_user, ruleset)

        # Async cache result (if user ID available)
        if db_user.id is not None:
            user_data = v1_user.model_dump()
            background_tasks.add_task(cache_service.cache_v1_user, user_data, db_user.id, ruleset)

        return [v1_user]

    except KeyError:
        raise RequestError(ErrorType.INVALID_REQUEST)
    except ValueError as e:
        logger.error(f"Error processing V1 user data: {e}")
        raise RequestError(ErrorType.INTERNAL)


# Helper functions for get_player_info endpoint implementation


async def _get_pp_history_for_mode(session: Database, user_id: int, mode: GameMode, days: int = 30) -> list[float]:
    """Get PP history for a specific game mode.

    Args:
        session: Database session.
        user_id: The user ID.
        mode: The game mode.
        days: Number of days of history to return.

    Returns:
        List of PP values for the specified number of days.
    """
    try:
        # Get last 30 days rank history (since we don't have PP history, use current PP)
        stats = (
            await session.exec(
                select(UserStatistics).where(
                    UserStatistics.user_id == user_id,
                    UserStatistics.mode == mode,
                    ~User.is_restricted_query(col(UserStatistics.user_id)),
                )
            )
        ).first()

        current_pp = stats.pp if stats else 0.0
        # Create 30 days of PP history (filled with current PP value)
        return [current_pp] * days
    except Exception as e:
        logger.error(f"Error getting PP history for user {user_id}, mode {mode}: {e}")
        return [0.0] * days
