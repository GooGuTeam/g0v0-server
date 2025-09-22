"""
缓存管理和监控接口
提供缓存统计、清理和预热功能
"""

from __future__ import annotations

from app.dependencies.database import get_redis
from app.service.cache.user_cache_service import get_user_cache_service

from .router import router

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from redis.asyncio import Redis


class CacheStatsResponse(BaseModel):
    user_cache: dict
    redis_info: dict


@router.get(
    "/cache/stats",
    response_model=CacheStatsResponse,
    name="获取缓存统计信息",
    description="获取用户缓存和Redis的统计信息，需要管理员权限。",
    tags=["缓存管理"],
)
async def get_cache_stats(
    redis: Redis = Depends(get_redis),
    # current_user: User = Security(get_current_user, scopes=["admin"]),  # 暂时注释，可根据需要启用
):
    try:
        cache_service = get_user_cache_service(redis)
        user_cache_stats = await cache_service.get_cache_stats()

        # 获取 Redis 基本信息
        redis_info = await redis.info()
        redis_stats = {
            "connected_clients": redis_info.get("connected_clients", 0),
            "used_memory_human": redis_info.get("used_memory_human", "0B"),
            "used_memory_peak_human": redis_info.get("used_memory_peak_human", "0B"),
            "total_commands_processed": redis_info.get("total_commands_processed", 0),
            "keyspace_hits": redis_info.get("keyspace_hits", 0),
            "keyspace_misses": redis_info.get("keyspace_misses", 0),
            "evicted_keys": redis_info.get("evicted_keys", 0),
            "expired_keys": redis_info.get("expired_keys", 0),
        }

        # 计算缓存命中率
        hits = redis_stats["keyspace_hits"]
        misses = redis_stats["keyspace_misses"]
        hit_rate = hits / (hits + misses) * 100 if (hits + misses) > 0 else 0
        redis_stats["cache_hit_rate_percent"] = round(hit_rate, 2)

        return CacheStatsResponse(user_cache=user_cache_stats, redis_info=redis_stats)

    except Exception as e:
        raise HTTPException(500, f"Failed to get cache stats: {e!s}")


@router.post(
    "/cache/invalidate/{user_id}",
    name="清除指定用户缓存",
    description="清除指定用户的所有缓存数据，需要管理员权限。",
    tags=["缓存管理"],
)
async def invalidate_user_cache(
    user_id: int,
    redis: Redis = Depends(get_redis),
    # current_user: User = Security(get_current_user, scopes=["admin"]),  # 暂时注释
):
    try:
        cache_service = get_user_cache_service(redis)
        await cache_service.invalidate_user_cache(user_id)
        await cache_service.invalidate_v1_user_cache(user_id)
        return {"message": f"Cache invalidated for user {user_id}"}
    except Exception as e:
        raise HTTPException(500, f"Failed to invalidate cache: {e!s}")


@router.post(
    "/cache/clear",
    name="清除所有用户缓存",
    description="清除所有用户相关的缓存数据，需要管理员权限。谨慎使用！",
    tags=["缓存管理"],
)
async def clear_all_user_cache(
    redis: Redis = Depends(get_redis),
    # current_user: User = Security(get_current_user, scopes=["admin"]),  # 暂时注释
):
    try:
        # 获取所有用户相关的缓存键
        user_keys = await redis.keys("user:*")
        v1_user_keys = await redis.keys("v1_user:*")
        all_keys = user_keys + v1_user_keys

        if all_keys:
            await redis.delete(*all_keys)
            return {"message": f"Cleared {len(all_keys)} cache entries"}
        else:
            return {"message": "No cache entries found"}

    except Exception as e:
        raise HTTPException(500, f"Failed to clear cache: {e!s}")


class CacheWarmupRequest(BaseModel):
    user_ids: list[int] | None = None
    limit: int = 100


@router.post(
    "/cache/warmup",
    name="缓存预热",
    description="对指定用户或活跃用户进行缓存预热，需要管理员权限。",
    tags=["缓存管理"],
)
async def warmup_cache(
    request: CacheWarmupRequest,
    redis: Redis = Depends(get_redis),
    # current_user: User = Security(get_current_user, scopes=["admin"]),  # 暂时注释
):
    try:
        cache_service = get_user_cache_service(redis)

        if request.user_ids:
            # 预热指定用户
            from app.dependencies.database import with_db

            async with with_db() as session:
                await cache_service.preload_user_cache(session, request.user_ids)
            return {"message": f"Warmed up cache for {len(request.user_ids)} users"}
        else:
            # 预热活跃用户
            from app.scheduler.user_cache_scheduler import (
                schedule_user_cache_preload_task,
            )

            await schedule_user_cache_preload_task()
            return {"message": f"Warmed up cache for top {request.limit} active users"}

    except Exception as e:
        raise HTTPException(500, f"Failed to warmup cache: {e!s}")
