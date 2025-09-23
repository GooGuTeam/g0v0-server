"""缓存相关调度器。"""

from __future__ import annotations

import asyncio

from app.config import settings
from app.dependencies.database import get_redis
from app.dependencies.fetcher import get_fetcher
from app.log import logger
from app.scheduler.user import (
    schedule_user_cache_cleanup_task,
    schedule_user_cache_preload_task,
    schedule_user_cache_warmup_task,
)

__all__ = [
    "BeatmapsetCacheScheduler",
    "CacheScheduler",
    "beatmapset_cache_scheduler",
    "cache_scheduler",
    "start_cache_scheduler",
    "stop_cache_scheduler",
]


class CacheScheduler:
    """缓存调度器 - 统一管理各种缓存任务"""

    def __init__(self) -> None:
        self.running = False
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """启动调度器"""
        if self.running:
            return

        self.running = True
        self.task = asyncio.create_task(self._run_scheduler())
        logger.info("CacheScheduler started")

    async def stop(self) -> None:
        """停止调度器"""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("CacheScheduler stopped")

    async def _run_scheduler(self) -> None:
        """运行调度器主循环"""
        # 启动时立即执行一次预热
        await self._warmup_cache()

        # 启动时执行一次排行榜缓存刷新
        await self._refresh_ranking_cache()

        # 启动时执行一次用户缓存预热
        await self._warmup_user_cache()

        beatmap_cache_counter = 0
        ranking_cache_counter = 0
        user_cache_counter = 0
        user_cleanup_counter = 0

        # 从配置文件获取间隔设置
        check_interval = 5 * 60  # 5分钟检查间隔
        beatmap_cache_interval = 30 * 60  # 30分钟beatmap缓存间隔
        ranking_cache_interval = settings.ranking_cache_refresh_interval_minutes * 60  # 从配置读取
        user_cache_interval = 15 * 60  # 15分钟用户缓存预加载间隔
        user_cleanup_interval = 60 * 60  # 60分钟用户缓存清理间隔

        beatmap_cache_cycles = beatmap_cache_interval // check_interval
        ranking_cache_cycles = ranking_cache_interval // check_interval
        user_cache_cycles = user_cache_interval // check_interval
        user_cleanup_cycles = user_cleanup_interval // check_interval

        while self.running:
            try:
                # 每5分钟检查一次
                await asyncio.sleep(check_interval)

                if not self.running:
                    break

                beatmap_cache_counter += 1
                ranking_cache_counter += 1
                user_cache_counter += 1
                user_cleanup_counter += 1

                # beatmap缓存预热
                if beatmap_cache_counter >= beatmap_cache_cycles:
                    await self._warmup_cache()
                    beatmap_cache_counter = 0

                # 排行榜缓存刷新
                if ranking_cache_counter >= ranking_cache_cycles:
                    await self._refresh_ranking_cache()
                    ranking_cache_counter = 0

                # 用户缓存预加载
                if user_cache_counter >= user_cache_cycles:
                    await self._preload_user_cache()
                    user_cache_counter = 0

                # 用户缓存清理
                if user_cleanup_counter >= user_cleanup_cycles:
                    await self._cleanup_user_cache()
                    user_cleanup_counter = 0

            except asyncio.CancelledError:
                break
            except Exception as e:  # pragma: no cover - logging branch
                logger.error(f"Cache scheduler error: {e}")
                await asyncio.sleep(60)  # 出错后等待1分钟再继续

    async def _warmup_cache(self) -> None:
        """执行缓存预热"""
        try:
            logger.info("Starting beatmap cache warmup...")

            fetcher = await get_fetcher()
            redis = get_redis()

            # 预热主页缓存
            await fetcher.warmup_homepage_cache(redis)

            logger.info("Beatmap cache warmup completed successfully")

        except Exception as e:  # pragma: no cover - logging branch
            logger.error(f"Beatmap cache warmup failed: {e}")

    async def _refresh_ranking_cache(self) -> None:
        """刷新排行榜缓存"""
        try:
            logger.info("Starting ranking cache refresh...")

            redis = get_redis()

            # 导入排行榜缓存服务
            # 使用独立的数据库会话
            from app.dependencies.database import with_db
            from app.service.cache.ranking_cache_service import (
                schedule_ranking_refresh_task,
            )

            async with with_db() as session:
                await schedule_ranking_refresh_task(session, redis)

            logger.info("Ranking cache refresh completed successfully")

        except Exception as e:  # pragma: no cover - logging branch
            logger.error(f"Ranking cache refresh failed: {e}")

    async def _warmup_user_cache(self) -> None:
        """用户缓存预热"""
        try:
            await schedule_user_cache_warmup_task()
        except Exception as e:  # pragma: no cover - logging branch
            logger.error(f"User cache warmup failed: {e}")

    async def _preload_user_cache(self) -> None:
        """用户缓存预加载"""
        try:
            await schedule_user_cache_preload_task()
        except Exception as e:  # pragma: no cover - logging branch
            logger.error(f"User cache preload failed: {e}")

    async def _cleanup_user_cache(self) -> None:
        """用户缓存清理"""
        try:
            await schedule_user_cache_cleanup_task()
        except Exception as e:  # pragma: no cover - logging branch
            logger.error(f"User cache cleanup failed: {e}")


class BeatmapsetCacheScheduler(CacheScheduler):
    """谱面集缓存调度器 - 为了向后兼容"""

    pass


cache_scheduler = CacheScheduler()
beatmapset_cache_scheduler = BeatmapsetCacheScheduler()


async def start_cache_scheduler() -> None:
    """启动缓存调度器"""
    await cache_scheduler.start()


async def stop_cache_scheduler() -> None:
    """停止缓存调度器"""
    await cache_scheduler.stop()
