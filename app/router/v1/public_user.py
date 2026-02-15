"""Public user endpoints module for osu! API v1.

This module provides public (unauthenticated) endpoints for retrieving user
and player information compatible with the legacy osu! API v1 specification.
"""

from typing import Annotated, Literal

from app.database.statistics import UserStatistics
from app.database.user import User
from app.dependencies.database import Database, get_redis
from app.log import logger
from app.models.error import ErrorType, FieldMissingError, RequestError
from app.models.score import GameMode
from app.models.v1_user import (
    GetPlayerCountResponse,
    GetPlayerInfoResponse,
    PlayerAllResponse,
    PlayerCountData,
    PlayerEventsResponse,
    PlayerInfoResponse,
    PlayerStatsHistory,
    PlayerStatsResponse,
)
from app.router.v1.public_router import public_router

from fastapi import Query
from sqlmodel import select


async def _create_player_mode_stats(
    session: Database, user: User, mode: GameMode, user_statistics: list[UserStatistics]
):
    """Create player statistics for a specific game mode.

    Args:
        session: Database session.
        user: The user database record.
        mode: The game mode to get statistics for.
        user_statistics: List of pre-loaded user statistics.

    Returns:
        PlayerModeStats instance with statistics for the specified mode.
    """
    from app.models.v1_user import PlayerModeStats

    # Find statistics for the corresponding mode
    statistics = None
    for stats in user_statistics:
        if stats.mode == mode:
            statistics = stats
            break

    if not statistics:
        # If no statistics found, return default values
        return PlayerModeStats(
            id=user.id,
            mode=int(mode),
            tscore=0,
            rscore=0,
            pp=0.0,
            plays=0,
            playtime=0,
            acc=0.0,
            max_combo=0,
            total_hits=0,
            replay_views=0,
            xh_count=0,
            x_count=0,
            sh_count=0,
            s_count=0,
            a_count=0,
            level=1,
            level_progress=0,
            rank=0,
            country_rank=0,
            history=PlayerStatsHistory(),
        )

    return PlayerModeStats(
        id=user.id,
        mode=int(mode),
        tscore=statistics.total_score or 0,
        rscore=statistics.ranked_score or 0,
        pp=float(statistics.pp) if statistics.pp else 0.0,
        plays=statistics.play_count or 0,
        playtime=statistics.play_time or 0,
        acc=float(statistics.hit_accuracy) if statistics.hit_accuracy else 0.0,
        max_combo=statistics.maximum_combo or 0,
        total_hits=statistics.total_hits or 0,
        replay_views=statistics.replays_watched_by_others or 0,
        xh_count=statistics.grade_ssh or 0,
        x_count=statistics.grade_ss or 0,
        sh_count=statistics.grade_sh or 0,
        s_count=statistics.grade_s or 0,
        a_count=statistics.grade_a or 0,
        level=int(statistics.level_current) if statistics.level_current else 1,
        level_progress=0,  # TODO: Calculate level progress
        rank=0,  # global_rank needs to be retrieved from RankHistory
        country_rank=0,  # country_rank needs to be retrieved from elsewhere
        history=PlayerStatsHistory(),  # TODO: Get PP history data
    )


async def _create_player_info(user: User):
    """Create player basic information.

    Args:
        user: The user database record.

    Returns:
        PlayerInfo instance with basic user information.
    """
    from app.models.v1_user import PlayerInfo

    return PlayerInfo(
        id=user.id,
        name=user.username,
        safe_name=user.username,  # Use username as safe_name
        priv=user.priv or 1,
        country=user.country_code or "",
        silence_end=int(user.silence_end_at.timestamp()) if user.silence_end_at else 0,
        donor_end=int(user.donor_end_at.timestamp()) if user.donor_end_at else 0,
        creation_time=int(user.join_date.timestamp()) if user.join_date else 0,
        latest_activity=int(user.last_visit.timestamp()) if user.last_visit else 0,
        clan_id=0,  # TODO: Get clan info from user
        clan_priv=0,
        preferred_mode=int(user.playmode) if user.playmode else 0,
        preferred_type=0,
        play_style=0,  # TODO: Get play style from user.playstyle
        custom_badge_enabled=0,
        custom_badge_name="",
        custom_badge_icon="",
        custom_badge_color="",
        userpage_content=user.page["html"] if user.page and "html" in user.page else "",
        recentFailed=0,  # TODO: Get recent failed count
        social_discord=user.discord,
        social_youtube=None,
        social_twitter=user.twitter,
        social_twitch=None,
        social_github=None,
        social_osu=None,
        username_history=user.previous_usernames or [],
    )


async def _get_player_events(session: Database, user_id: int):
    """Get player events list.

    Args:
        session: Database session.
        user_id: The user ID.

    Returns:
        List of player events.
    """
    # TODO: Implement event query logic
    # Should query app.database.events table
    return []


async def _count_online_users_optimized(redis):
    """Optimized online user count function.

    First attempts to use a pre-computed set for counting,
    falls back to SCAN if the set is not available.

    Args:
        redis: Redis connection.

    Returns:
        Number of online users.
    """
    try:
        online_set_key = "metadata:online_users_set"
        if await redis.exists(online_set_key):
            count = await redis.scard(online_set_key)
            logger.debug(f"Using online users set, count: {count}")
            return count

    except Exception as e:
        logger.debug(f"Online users set not available: {e}")

    # Fallback: Optimized SCAN operation
    online_count = 0
    cursor = 0
    scan_iterations = 0
    max_iterations = 50  # Reduced max iterations
    batch_size = 10000  # Increased batch size

    try:
        while cursor != 0 or scan_iterations == 0:
            if scan_iterations >= max_iterations:
                logger.warning(f"Redis SCAN reached max iterations ({max_iterations}), breaking")
                break

            cursor, keys = await redis.scan(cursor, match="metadata:online:*", count=batch_size)
            online_count += len(keys)
            scan_iterations += 1

            # If no keys found for several iterations, scan likely complete
            if len(keys) == 0 and scan_iterations > 2:
                break

        logger.debug(f"Found {online_count} online users after {scan_iterations} scan iterations")
        return online_count

    except Exception as e:
        logger.error(f"Error counting online users: {e}")
        # If SCAN fails, return 0 instead of failing the entire API
        return 0


@public_router.get(
    "/get_player_info",
    name="Get Player Info",
    description="Return information for a specified player.",
)
async def api_get_player_info(
    session: Database,
    scope: Annotated[Literal["stats", "events", "info", "all"], Query(..., description="Information scope")],
    id: Annotated[int | None, Query(ge=3, le=2147483647, description="User ID")] = None,
    name: Annotated[str | None, Query(regex=r"^[\w \[\]-]{2,32}$", description="Username")] = None,
):
    """Get information for a specified player.

    Args:
        session: Database session.
        scope: Information scope - stats (statistics), events, info (basic), or all.
        id: User ID (optional).
        name: Username (optional).

    Returns:
        GetPlayerInfoResponse with the requested information scope.

    Raises:
        FieldMissingError: If neither id nor name is provided.
    """
    # Validate parameters
    if not id and not name:
        raise FieldMissingError(["id", "name"])

    # Query user
    if id:
        user = await session.get(User, id)
    else:
        user = (await session.exec(select(User).where(User.username == name))).first()

    if not user:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=200, content={"status": "Player not found."})

    try:
        if scope == "stats":
            # Get statistics for all modes
            user_statistics = list(
                (await session.exec(select(UserStatistics).where(UserStatistics.user_id == user.id))).all()
            )

            stats_dict = {}
            # Get statistics for all game modes
            all_modes = [GameMode.OSU, GameMode.TAIKO, GameMode.FRUITS, GameMode.MANIA, GameMode.OSURX, GameMode.OSUAP]

            for mode in all_modes:
                mode_stats = await _create_player_mode_stats(session, user, mode, user_statistics)
                stats_dict[str(int(mode))] = mode_stats

            return GetPlayerInfoResponse(player=PlayerStatsResponse(stats=stats_dict))

        elif scope == "events":
            # Get events data
            events = await _get_player_events(session, user.id)
            return GetPlayerInfoResponse(player=PlayerEventsResponse(events=events))

        elif scope == "info":
            # Get basic info
            info = await _create_player_info(user)
            return GetPlayerInfoResponse(player=PlayerInfoResponse(info=info))

        elif scope == "all":
            # Get all information
            # Statistics
            user_statistics = list(
                (await session.exec(select(UserStatistics).where(UserStatistics.user_id == user.id))).all()
            )

            stats_dict = {}
            all_modes = [GameMode.OSU, GameMode.TAIKO, GameMode.FRUITS, GameMode.MANIA, GameMode.OSURX, GameMode.OSUAP]

            for mode in all_modes:
                mode_stats = await _create_player_mode_stats(session, user, mode, user_statistics)
                stats_dict[str(int(mode))] = mode_stats

            # Basic info
            info = await _create_player_info(user)

            # Events
            events = await _get_player_events(session, user.id)

            return GetPlayerInfoResponse(player=PlayerAllResponse(info=info, stats=stats_dict, events=events))

    except Exception as e:
        logger.error(f"Error processing get_player_info for user {user.id}: {e}")
        raise RequestError(ErrorType.INTERNAL)


@public_router.get(
    "/get_player_count",
    response_model=GetPlayerCountResponse,
    name="Get Player Count",
    description="Return online and total user counts.",
)
async def api_get_player_count(
    session: Database,
):
    """Get player count statistics.

    Args:
        session: Database session.

    Returns:
        GetPlayerCountResponse with online and total user counts.

    Raises:
        RequestError: If an internal error occurs.
    """
    try:
        redis = get_redis()

        online_cache_key = "stats:online_users_count"
        cached_online = await redis.get(online_cache_key)

        if cached_online is not None:
            online_count = int(cached_online)
            logger.debug(f"Using cached online user count: {online_count}")
        else:
            logger.debug("Cache miss, scanning Redis for online users")
            online_count = await _count_online_users_optimized(redis)

            await redis.setex(online_cache_key, 30, str(online_count))
            logger.debug(f"Cached online user count: {online_count} for 30 seconds")

        cache_key = "stats:total_users"
        cached_total = await redis.get(cache_key)

        if cached_total is not None:
            total_count = int(cached_total)
            logger.debug(f"Using cached total user count: {total_count}")
        else:
            logger.debug("Cache miss, querying database for total user count")
            from sqlmodel import func, select

            total_count_result = await session.exec(select(func.count()).select_from(User))
            total_count = total_count_result.one()

            await redis.setex(cache_key, 3600, str(total_count))
            logger.debug(f"Cached total user count: {total_count} for 1 hour")

        return GetPlayerCountResponse(
            counts=PlayerCountData(
                online=online_count,
                total=max(0, total_count - 1),  # Subtract 1 bot account, ensure non-negative
            )
        )

    except Exception as e:
        logger.error(f"Error getting player count: {e}")
        raise RequestError(ErrorType.INTERNAL)
