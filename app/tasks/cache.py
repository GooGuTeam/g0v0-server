"""Cache-related APScheduler task entry points.

Provides scheduled tasks for cache warmup, ranking refresh,
user cache preloading, and cache cleanup operations.
"""

import asyncio
from datetime import UTC, timedelta
from typing import Final

from app.config import settings
from app.database.score import Score
from app.database.user import User
from app.dependencies.database import get_redis, with_db
from app.dependencies.fetcher import get_fetcher
from app.dependencies.scheduler import get_scheduler
from app.log import logger
from app.service.ranking_cache_service import schedule_ranking_refresh_task
from app.service.user_cache_service import get_user_cache_service
from app.utils import utcnow

from apscheduler.jobstores.base import JobLookupError
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import col, func, select

CACHE_JOB_IDS: Final[dict[str, str]] = {
    "beatmap_warmup": "cache:beatmap:warmup",
    "ranking_refresh": "cache:ranking:refresh",
    "user_preload": "cache:user:preload",
    "user_cleanup": "cache:user:cleanup",
}


async def warmup_cache() -> None:
    """Perform beatmap cache warmup.

    Warms up the homepage beatmap cache to improve initial load times.
    """
    try:
        logger.info("Starting beatmap cache warmup...")

        fetcher = await get_fetcher()
        redis = get_redis()

        await fetcher.warmup_homepage_cache(redis)

        logger.info("Beatmap cache warmup completed successfully")

    except Exception as e:
        logger.error(f"Beatmap cache warmup failed: {e}")


async def refresh_ranking_cache() -> None:
    """Refresh ranking cache.

    Updates cached ranking data for all game modes.
    """
    try:
        logger.info("Starting ranking cache refresh...")

        redis = get_redis()

        from app.dependencies.database import with_db

        async with with_db() as session:
            await schedule_ranking_refresh_task(session, redis)

        logger.info("Ranking cache refresh completed successfully")

    except Exception as e:
        logger.error(f"Ranking cache refresh failed: {e}")


async def schedule_user_cache_preload_task() -> None:
    """Scheduled user cache preload task.

    Preloads cache for recently active users (within 24 hours)
    to improve response times for subsequent requests.
    """
    if not settings.enable_user_cache_preload:
        return

    try:
        logger.info("Starting user cache preload task...")

        redis = get_redis()
        cache_service = get_user_cache_service(redis)

        from app.dependencies.database import with_db

        async with with_db() as session:
            recent_time = utcnow() - timedelta(hours=24)

            score_count = func.count().label("score_count")
            active_user_ids = (
                await session.exec(
                    select(Score.user_id, score_count)
                    .where(col(Score.ended_at) >= recent_time)
                    .group_by(col(Score.user_id))
                    .order_by(score_count.desc())
                    .limit(settings.user_cache_max_preload_users)
                )
            ).all()

            if active_user_ids:
                user_ids = [row[0] for row in active_user_ids]
                await cache_service.preload_user_cache(session, user_ids)
                logger.info(f"Preloaded cache for {len(user_ids)} active users")
            else:
                logger.info("No active users found for cache preload")

        logger.info("User cache preload task completed successfully")

    except Exception as e:
        logger.error(f"User cache preload task failed: {e}")


async def schedule_user_cache_warmup_task() -> None:
    """Scheduled user cache warmup task.

    Preloads cache for the top 100 users in each game mode's leaderboard.
    """
    try:
        logger.info("Starting user cache warmup task...")

        redis = get_redis()
        cache_service = get_user_cache_service(redis)
        async with with_db() as session:
            from app.database.statistics import UserStatistics
            from app.models.score import GameMode

            for mode in GameMode:
                try:
                    top_users = (
                        await session.exec(
                            select(UserStatistics.user_id)
                            .where(
                                UserStatistics.mode == mode,
                                ~User.is_restricted_query(col(UserStatistics.user_id)),
                            )
                            .order_by(col(UserStatistics.pp).desc())
                            .limit(100)
                        )
                    ).all()

                    if top_users:
                        user_ids = list(top_users)
                        await cache_service.preload_user_cache(session, user_ids)
                        logger.info(f"Warmed cache for top 100 users in {mode}")

                        await asyncio.sleep(1)

                except Exception as e:
                    logger.error(f"Failed to warm cache for {mode}: {e}")
                    continue

        logger.info("User cache warmup task completed successfully")

    except Exception as e:
        logger.error(f"User cache warmup task failed: {e}")


async def schedule_user_cache_cleanup_task() -> None:
    """Scheduled user cache cleanup task.

    Logs cache statistics and cleans up stale cache entries.
    """
    try:
        logger.info("Starting user cache cleanup task...")

        redis = get_redis()

        cache_service = get_user_cache_service(redis)
        stats = await cache_service.get_cache_stats()

        logger.info(f"User cache stats: {stats}")
        logger.info("User cache cleanup task completed successfully")

    except Exception as e:
        logger.error(f"User cache cleanup task failed: {e}")


async def warmup_user_cache() -> None:
    """Warm up user cache.

    Wrapper for schedule_user_cache_warmup_task with error handling.
    """
    try:
        await schedule_user_cache_warmup_task()
    except Exception as e:
        logger.error(f"User cache warmup failed: {e}")


async def preload_user_cache() -> None:
    """Preload user cache.

    Wrapper for schedule_user_cache_preload_task with error handling.
    """
    try:
        await schedule_user_cache_preload_task()
    except Exception as e:
        logger.error(f"User cache preload failed: {e}")


async def cleanup_user_cache() -> None:
    """Clean up user cache.

    Wrapper for schedule_user_cache_cleanup_task with error handling.
    """
    try:
        await schedule_user_cache_cleanup_task()
    except Exception as e:
        logger.error(f"User cache cleanup failed: {e}")


def register_cache_jobs() -> None:
    """Register cache-related APScheduler jobs.

    Registers the following scheduled jobs:
    - Beatmap warmup (every 30 minutes)
    - Ranking refresh (configurable interval)
    - User preload (every 15 minutes)
    - User cleanup (every hour)
    """
    scheduler = get_scheduler()

    scheduler.add_job(
        warmup_cache,
        trigger=IntervalTrigger(minutes=30, timezone=UTC),
        id=CACHE_JOB_IDS["beatmap_warmup"],
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        refresh_ranking_cache,
        trigger=IntervalTrigger(
            minutes=settings.ranking_cache_refresh_interval_minutes,
            timezone=UTC,
        ),
        id=CACHE_JOB_IDS["ranking_refresh"],
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        preload_user_cache,
        trigger=IntervalTrigger(minutes=15, timezone=UTC),
        id=CACHE_JOB_IDS["user_preload"],
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        cleanup_user_cache,
        trigger=IntervalTrigger(hours=1, timezone=UTC),
        id=CACHE_JOB_IDS["user_cleanup"],
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    logger.info("Registered cache APScheduler jobs")


async def start_cache_tasks() -> None:
    """Register APScheduler jobs and execute startup tasks.

    Called during application startup to initialize cache management.
    """
    register_cache_jobs()
    logger.info("Cache APScheduler jobs registered; running initial tasks")


async def stop_cache_tasks() -> None:
    """Remove APScheduler jobs.

    Called during application shutdown to clean up scheduled jobs.
    """
    scheduler = get_scheduler()
    for job_id in CACHE_JOB_IDS.values():
        try:
            scheduler.remove_job(job_id)
        except JobLookupError:
            continue

    logger.info("Cache APScheduler jobs removed")
