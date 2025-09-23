"""
Beatmap缓存预取服务
用于提前缓存热门beatmap，减少成绩计算时的获取延迟
"""

from __future__ import annotations

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
    def __init__(self, redis: Redis, fetcher: "Fetcher"):
        self.redis = redis
        self.fetcher = fetcher
        self._preloading = False
        self._background_tasks: set = set()

    async def preload_popular_beatmaps(self, session: AsyncSession, limit: int = 100):
        """
        预加载热门beatmap到Redis缓存
        """
        if self._preloading:
            logger.info("Beatmap preloading already in progress")
            return

        self._preloading = True
        try:
            logger.info(f"Starting preload of top {limit} popular beatmaps")

            # 获取过去24小时内最热门的beatmap
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

            # 并发预取这些beatmap
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
        """
        预加载单个beatmap
        """
        try:
            cache_key = f"beatmap:{beatmap_id}:raw"
            if await self.redis.exists(cache_key):
                # 已经在缓存中，延长过期时间
                await self.redis.expire(cache_key, 60 * 60 * 24)
                return True

            # 获取并缓存beatmap
            content = await self.fetcher.get_beatmap_raw(beatmap_id)
            await self.redis.set(cache_key, content, ex=60 * 60 * 24)
            return True

        except Exception as e:
            logger.debug(f"Failed to preload beatmap {beatmap_id}: {e}")
            return False

    async def smart_preload_for_score(self, beatmap_id: int):
        """
        智能预加载：为即将提交的成绩预加载beatmap
        """
        task = asyncio.create_task(self._preload_single_beatmap(beatmap_id))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def get_cache_stats(self) -> dict:
        """
        获取缓存统计信息
        """
        try:
            keys = await self.redis.keys("beatmap:*:raw")
            total_size = 0

            for key in keys[:100]:  # 限制检查数量以避免性能问题
                try:
                    size = await self.redis.memory_usage(key)
                    if size:
                        total_size += size
                except Exception:
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
        """
        清理过期的缓存
        """
        try:
            logger.info(f"Cleaning up beatmap cache older than {max_age_hours} hours")
            # Redis会自动清理过期的key，这里主要是记录日志
            keys = await self.redis.keys("beatmap:*:raw")
            logger.info(f"Current cache contains {len(keys)} beatmaps")
        except Exception as e:
            logger.error(f"Error during cache cleanup: {e}")


# 全局缓存服务实例
_cache_service: BeatmapCacheService | None = None


def get_beatmap_cache_service(redis: Redis, fetcher: "Fetcher") -> BeatmapCacheService:
    """
    获取beatmap缓存服务实例
    """
    global _cache_service
    if _cache_service is None:
        _cache_service = BeatmapCacheService(redis, fetcher)
    return _cache_service


async def schedule_preload_task(session: AsyncSession, redis: Redis, fetcher: "Fetcher"):
    """
    定时预加载任务
    """
    # 默认启用预加载，除非明确禁用
    enable_preload = getattr(settings, "enable_beatmap_preload", True)
    if not enable_preload:
        return

    cache_service = get_beatmap_cache_service(redis, fetcher)
    try:
        await cache_service.preload_popular_beatmaps(session, limit=200)
    except Exception as e:
        logger.error(f"Scheduled preload task failed: {e}")
