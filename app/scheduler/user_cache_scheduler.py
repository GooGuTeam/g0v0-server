"""
用户缓存预热任务调度器
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from app.config import settings
from app.database.score import Score
from app.dependencies.database import get_redis
from app.log import logger
from app.service.cache.user_cache_service import get_user_cache_service
from app.utils import utcnow

from sqlmodel import col, func, select


async def schedule_user_cache_preload_task():
    """定时用户缓存预加载任务"""
    # 默认启用用户缓存预加载，除非明确禁用
    enable_user_cache_preload = getattr(settings, "enable_user_cache_preload", True)
    if not enable_user_cache_preload:
        return

    try:
        logger.info("Starting user cache preload task...")

        redis = get_redis()
        cache_service = get_user_cache_service(redis)

        # 使用独立的数据库会话
        from app.dependencies.database import with_db

        async with with_db() as session:
            # 获取最近24小时内活跃的用户（提交过成绩的用户）
            recent_time = utcnow() - timedelta(hours=24)

            score_count = func.count().label("score_count")
            active_user_ids = (
                await session.exec(
                    select(Score.user_id, score_count)
                    .where(col(Score.ended_at) >= recent_time)
                    .group_by(col(Score.user_id))
                    .order_by(score_count.desc())  # 使用标签对象而不是字符串
                    .limit(settings.user_cache_max_preload_users)  # 使用配置中的限制
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


async def schedule_user_cache_warmup_task():
    """定时用户缓存预热任务 - 预加载排行榜前100用户"""
    try:
        logger.info("Starting user cache warmup task...")

        redis = get_redis()
        cache_service = get_user_cache_service(redis)

        # 使用独立的数据库会话
        from app.dependencies.database import with_db

        async with with_db() as session:
            # 获取全球排行榜前100的用户
            from app.database.user.statistics import UserStatistics
            from app.models.score import GameMode

            for mode in GameMode:
                try:
                    top_users = (
                        await session.exec(
                            select(UserStatistics.user_id)
                            .where(UserStatistics.mode == mode)
                            .order_by(col(UserStatistics.pp).desc())
                            .limit(100)
                        )
                    ).all()

                    if top_users:
                        user_ids = list(top_users)
                        await cache_service.preload_user_cache(session, user_ids)
                        logger.info(f"Warmed cache for top 100 users in {mode}")

                        # 避免过载，稍微延迟
                        await asyncio.sleep(1)

                except Exception as e:
                    logger.error(f"Failed to warm cache for {mode}: {e}")
                    continue

        logger.info("User cache warmup task completed successfully")

    except Exception as e:
        logger.error(f"User cache warmup task failed: {e}")


async def schedule_user_cache_cleanup_task():
    """定时用户缓存清理任务"""
    try:
        logger.info("Starting user cache cleanup task...")

        redis = get_redis()

        # 清理过期的用户缓存（Redis会自动处理TTL，这里主要记录统计信息）
        cache_service = get_user_cache_service(redis)
        stats = await cache_service.get_cache_stats()

        logger.info(f"User cache stats: {stats}")
        logger.info("User cache cleanup task completed successfully")

    except Exception as e:
        logger.error(f"User cache cleanup task failed: {e}")
