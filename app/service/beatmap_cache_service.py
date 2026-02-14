"""Beatmap cache prefetch service.

Pre-caches popular beatmaps to reduce latency during score calculation.
"""

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from app.config import settings
from app.log import logger
from app.utils import utcnow

from redis.asyncio import Redis
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from app.fetcher import Fetcher


class BeatmapCacheService:
    """Service for prefetching and caching popular beatmaps.

    Attributes:
        redis: Redis client for caching.
        fetcher: Beatmap fetcher service.
    """

    def __init__(self, redis: Redis, fetcher: "Fetcher"):
        self.redis = redis
        self.fetcher = fetcher
        self._preloading = False
        self._background_tasks: set = set()

    async def preload_popular_beatmaps(self, session: AsyncSession, limit: int = 100):
        """Preload popular beatmaps into Redis cache.

        Args:
            session: Database session.
            limit: Maximum number of beatmaps to preload.
        """
        if self._preloading:
            logger.info("Beatmap preloading already in progress")
            return

        self._preloading = True
        try:
            logger.info(f"Starting preload of top {limit} popular beatmaps")

            # Get most popular beatmaps from past 24 hours
            recent_time = utcnow() - timedelta(hours=24)

            from app.database.score import Score

            popular_beatmaps = (
                await session.exec(
                    select(Score.beatmap_id, func.count().label("play_count"))
                    .where(col(Score.ended_at) >= recent_time)
                    .group_by(col(Score.beatmap_id))
                    .order_by(col("play_count").desc())
                    .limit(limit)
                )
            ).all()

            # Concurrently preload these beatmaps
            preload_tasks = []
            for beatmap_id, _ in popular_beatmaps:
                task = self._preload_single_beatmap(beatmap_id)
                preload_tasks.append(task)

            if preload_tasks:
                results = await asyncio.gather(*preload_tasks, return_exceptions=True)
                success_count = sum(1 for r in results if r is True)
                logger.info(f"Preloaded {success_count}/{len(preload_tasks)} beatmaps successfully")

        except Exception as e:
            logger.error(f"Error during beatmap preloading: {e}")
        finally:
            self._preloading = False

    async def _preload_single_beatmap(self, beatmap_id: int) -> bool:
        """Preload a single beatmap.

        Args:
            beatmap_id: The beatmap ID to preload.

        Returns:
            True if successful, False otherwise.
        """
        try:
            cache_key = f"beatmap:{beatmap_id}:raw"
            if await self.redis.exists(cache_key):
                # Already in cache, extend expiration time
                await self.redis.expire(cache_key, 60 * 60 * 24)
                return True

            # Get and cache beatmap
            content = await self.fetcher.get_beatmap_raw(beatmap_id)
            await self.redis.set(cache_key, content, ex=60 * 60 * 24)
            return True

        except Exception as e:
            logger.debug(f"Failed to preload beatmap {beatmap_id}: {e}")
            return False

    async def smart_preload_for_score(self, beatmap_id: int):
        """Smart preload: preload beatmap for upcoming score submission.

        Args:
            beatmap_id: The beatmap ID to preload.
        """
        task = asyncio.create_task(self._preload_single_beatmap(beatmap_id))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def get_cache_stats(self) -> dict:
        """Get cache statistics.

        Returns:
            Dictionary containing cache statistics.
        """
        try:
            keys = await self.redis.keys("beatmap:*:raw")
            total_size = 0

            for key in keys[:100]:  # Limit check count to avoid performance issues
                try:
                    size = await self.redis.memory_usage(key)
                    if size:
                        total_size += size
                except Exception:
                    logger.debug(f"Failed to get size for key {key}")
                    continue

            return {
                "cached_beatmaps": len(keys),
                "estimated_total_size_mb": (round(total_size / 1024 / 1024, 2) if total_size > 0 else 0),
                "preloading": self._preloading,
            }
        except Exception as e:
            logger.error(f"Error getting cache stats: {e}")
            return {"error": str(e)}

    async def cleanup_old_cache(self, max_age_hours: int = 48):
        """Clean up expired cache entries.

        Args:
            max_age_hours: Maximum cache age in hours.
        """
        try:
            logger.info(f"Cleaning up beatmap cache older than {max_age_hours} hours")
            # Redis auto-cleans expired keys, this is mainly for logging
            keys = await self.redis.keys("beatmap:*:raw")
            logger.info(f"Current cache contains {len(keys)} beatmaps")
        except Exception as e:
            logger.error(f"Error during cache cleanup: {e}")


# Global cache service instance
_cache_service: BeatmapCacheService | None = None


def get_beatmap_cache_service(redis: Redis, fetcher: "Fetcher") -> BeatmapCacheService:
    """Get the beatmap cache service instance.

    Args:
        redis: Redis client.
        fetcher: Beatmap fetcher.

    Returns:
        The BeatmapCacheService singleton instance.
    """
    global _cache_service
    if _cache_service is None:
        _cache_service = BeatmapCacheService(redis, fetcher)
    return _cache_service


async def schedule_preload_task(session: AsyncSession, redis: Redis, fetcher: "Fetcher"):
    """Scheduled preload task.

    Args:
        session: Database session.
        redis: Redis client.
        fetcher: Beatmap fetcher.
    """
    # Preloading enabled by default unless explicitly disabled
    if not settings.enable_beatmap_preload:
        return

    cache_service = get_beatmap_cache_service(redis, fetcher)
    try:
        await cache_service.preload_popular_beatmaps(session, limit=200)
    except Exception as e:
        logger.error(f"Scheduled preload task failed: {e}")
