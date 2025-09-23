"""
Beatmapset缓存服务依赖注入
"""

from __future__ import annotations

from app.dependencies.database import get_redis
from app.service.cache.beatmapset_cache_service import (
    BeatmapsetCacheService,
    get_beatmapset_cache_service,
)

from fastapi import Depends
from redis.asyncio import Redis


def get_beatmapset_cache_dependency(redis: Redis = Depends(get_redis)) -> BeatmapsetCacheService:
    """获取beatmapset缓存服务依赖"""
    return get_beatmapset_cache_service(redis)
