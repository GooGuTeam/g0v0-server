"""
用户缓存服务
用于缓存用户信息，提供热缓存和实时刷新功能
"""

from __future__ import annotations

from datetime import datetime
import json
from typing import TYPE_CHECKING, Any

from app.config import settings
from app.const import BANCHOBOT_ID
from app.database import User, UserResp
from app.database.lazer_user import SEARCH_INCLUDED
from app.database.score import LegacyScoreResp, ScoreResp
from app.dependencies.database import with_db
from app.log import logger
from app.models.score import GameMode
from app.service.asset_proxy_service import get_asset_proxy_service

from redis.asyncio import Redis
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    pass


class DateTimeEncoder(json.JSONEncoder):
    """自定义 JSON 编码器，支持 datetime 序列化"""

    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def safe_json_dumps(data: Any) -> str:
    """安全的 JSON 序列化，支持 datetime 对象"""
    return json.dumps(data, cls=DateTimeEncoder, ensure_ascii=False)


class UserCacheService:
    """用户缓存服务"""

    def __init__(self, redis: Redis):
        self.redis = redis
        self._refreshing = False
        self._background_tasks: set = set()

    def _get_v1_user_cache_key(self, user_id: int, ruleset: GameMode | None = None) -> str:
        """生成 V1 用户缓存键"""
        if ruleset:
            return f"v1_user:{user_id}:ruleset:{ruleset}"
        return f"v1_user:{user_id}"

    async def get_v1_user_from_cache(self, user_id: int, ruleset: GameMode | None = None) -> dict | None:
        """从缓存获取 V1 用户信息"""
        try:
            cache_key = self._get_v1_user_cache_key(user_id, ruleset)
            cached_data = await self.redis.get(cache_key)
            if cached_data:
                logger.debug(f"V1 User cache hit for user {user_id}")
                return json.loads(cached_data)
            return None
        except Exception as e:
            logger.error(f"Error getting V1 user from cache: {e}")
            return None

    async def cache_v1_user(
        self,
        user_data: dict,
        user_id: int,
        ruleset: GameMode | None = None,
        expire_seconds: int | None = None,
    ):
        """缓存 V1 用户信息"""
        try:
            if expire_seconds is None:
                expire_seconds = settings.user_cache_expire_seconds
            cache_key = self._get_v1_user_cache_key(user_id, ruleset)
            cached_data = safe_json_dumps(user_data)
            await self.redis.setex(cache_key, expire_seconds, cached_data)
            logger.debug(f"Cached V1 user {user_id} for {expire_seconds}s")
        except Exception as e:
            logger.error(f"Error caching V1 user: {e}")

    async def invalidate_v1_user_cache(self, user_id: int):
        """使 V1 用户缓存失效"""
        try:
            # 删除 V1 用户信息缓存
            pattern = f"v1_user:{user_id}*"
            keys = await self.redis.keys(pattern)
            if keys:
                await self.redis.delete(*keys)
                logger.info(f"Invalidated {len(keys)} V1 cache entries for user {user_id}")
        except Exception as e:
            logger.error(f"Error invalidating V1 user cache: {e}")

    def _get_user_cache_key(self, user_id: int, ruleset: GameMode | None = None) -> str:
        """生成用户缓存键"""
        if ruleset:
            return f"user:{user_id}:ruleset:{ruleset}"
        return f"user:{user_id}"

    def _get_user_scores_cache_key(
        self,
        user_id: int,
        score_type: str,
        include_fail: bool,
        mode: GameMode | None = None,
        limit: int = 100,
        offset: int = 0,
        is_legacy: bool = False,
    ) -> str:
        """生成用户成绩缓存键"""
        mode_part = f":{mode}" if mode else ""
        return (
            f"user:{user_id}:scores:{score_type}{mode_part}:limit:{limit}:offset:"
            f"{offset}:include_fail:{include_fail}:is_legacy:{is_legacy}"
        )

    def _get_user_beatmapsets_cache_key(
        self, user_id: int, beatmapset_type: str, limit: int = 100, offset: int = 0
    ) -> str:
        """生成用户谱面集缓存键"""
        return f"user:{user_id}:beatmapsets:{beatmapset_type}:limit:{limit}:offset:{offset}"

    async def get_user_from_cache(self, user_id: int, ruleset: GameMode | None = None) -> UserResp | None:
        """从缓存获取用户信息"""
        try:
            cache_key = self._get_user_cache_key(user_id, ruleset)
            cached_data = await self.redis.get(cache_key)
            if cached_data:
                logger.debug(f"User cache hit for user {user_id}")
                data = json.loads(cached_data)
                return UserResp(**data)
            return None
        except Exception as e:
            logger.error(f"Error getting user from cache: {e}")
            return None

    async def cache_user(
        self,
        user_resp: UserResp,
        ruleset: GameMode | None = None,
        expire_seconds: int | None = None,
    ):
        """缓存用户信息"""
        try:
            if expire_seconds is None:
                expire_seconds = settings.user_cache_expire_seconds
            if user_resp.id is None:
                logger.warning("Cannot cache user with None id")
                return
            cache_key = self._get_user_cache_key(user_resp.id, ruleset)
            cached_data = user_resp.model_dump_json()
            await self.redis.setex(cache_key, expire_seconds, cached_data)
            logger.debug(f"Cached user {user_resp.id} for {expire_seconds}s")
        except Exception as e:
            logger.error(f"Error caching user: {e}")

    async def get_user_scores_from_cache(
        self,
        user_id: int,
        score_type: str,
        include_fail: bool,
        mode: GameMode | None = None,
        limit: int = 100,
        offset: int = 0,
        is_legacy: bool = False,
    ) -> list[ScoreResp] | list[LegacyScoreResp] | None:
        """从缓存获取用户成绩"""
        try:
            model = LegacyScoreResp if is_legacy else ScoreResp
            cache_key = self._get_user_scores_cache_key(
                user_id, score_type, include_fail, mode, limit, offset, is_legacy
            )
            cached_data = await self.redis.get(cache_key)
            if cached_data:
                logger.debug(f"User scores cache hit for user {user_id}, type {score_type}")
                data = json.loads(cached_data)
                return [model(**score_data) for score_data in data]  # pyright: ignore[reportReturnType]
            return None
        except Exception as e:
            logger.error(f"Error getting user scores from cache: {e}")
            return None

    async def cache_user_scores(
        self,
        user_id: int,
        score_type: str,
        scores: list[ScoreResp] | list[LegacyScoreResp],
        include_fail: bool,
        mode: GameMode | None = None,
        limit: int = 100,
        offset: int = 0,
        expire_seconds: int | None = None,
        is_legacy: bool = False,
    ):
        """缓存用户成绩"""
        try:
            if expire_seconds is None:
                expire_seconds = settings.user_scores_cache_expire_seconds
            cache_key = self._get_user_scores_cache_key(
                user_id, score_type, include_fail, mode, limit, offset, is_legacy
            )
            # 使用 model_dump_json() 而不是 model_dump() + json.dumps()
            scores_json_list = [score.model_dump_json() for score in scores]
            cached_data = f"[{','.join(scores_json_list)}]"
            await self.redis.setex(cache_key, expire_seconds, cached_data)
            logger.debug(f"Cached user {user_id} scores ({score_type}) for {expire_seconds}s")
        except Exception as e:
            logger.error(f"Error caching user scores: {e}")

    async def get_user_beatmapsets_from_cache(
        self, user_id: int, beatmapset_type: str, limit: int = 100, offset: int = 0
    ) -> list[Any] | None:
        """从缓存获取用户谱面集"""
        try:
            cache_key = self._get_user_beatmapsets_cache_key(user_id, beatmapset_type, limit, offset)
            cached_data = await self.redis.get(cache_key)
            if cached_data:
                logger.debug(f"User beatmapsets cache hit for user {user_id}, type {beatmapset_type}")
                return json.loads(cached_data)
            return None
        except Exception as e:
            logger.error(f"Error getting user beatmapsets from cache: {e}")
            return None

    async def cache_user_beatmapsets(
        self,
        user_id: int,
        beatmapset_type: str,
        beatmapsets: list[Any],
        limit: int = 100,
        offset: int = 0,
        expire_seconds: int | None = None,
    ):
        """缓存用户谱面集"""
        try:
            if expire_seconds is None:
                expire_seconds = settings.user_beatmapsets_cache_expire_seconds
            cache_key = self._get_user_beatmapsets_cache_key(user_id, beatmapset_type, limit, offset)
            # 使用 model_dump_json() 处理有 model_dump_json 方法的对象，否则使用 safe_json_dumps
            serialized_beatmapsets = []
            for bms in beatmapsets:
                if hasattr(bms, "model_dump_json"):
                    serialized_beatmapsets.append(bms.model_dump_json())
                else:
                    serialized_beatmapsets.append(safe_json_dumps(bms))
            cached_data = f"[{','.join(serialized_beatmapsets)}]"
            await self.redis.setex(cache_key, expire_seconds, cached_data)
            logger.debug(f"Cached user {user_id} beatmapsets ({beatmapset_type}) for {expire_seconds}s")
        except Exception as e:
            logger.error(f"Error caching user beatmapsets: {e}")

    async def invalidate_user_cache(self, user_id: int):
        """使用户缓存失效"""
        try:
            # 删除用户信息缓存
            pattern = f"user:{user_id}*"
            keys = await self.redis.keys(pattern)
            if keys:
                await self.redis.delete(*keys)
                logger.info(f"Invalidated {len(keys)} cache entries for user {user_id}")
        except Exception as e:
            logger.error(f"Error invalidating user cache: {e}")

    async def invalidate_user_scores_cache(self, user_id: int, mode: GameMode | None = None):
        """使用户成绩缓存失效"""
        try:
            # 删除用户成绩相关缓存
            mode_pattern = f":{mode}" if mode else "*"
            pattern = f"user:{user_id}:scores:*{mode_pattern}*"
            keys = await self.redis.keys(pattern)
            if keys:
                await self.redis.delete(*keys)
                logger.info(f"Invalidated {len(keys)} score cache entries for user {user_id}")
        except Exception as e:
            logger.error(f"Error invalidating user scores cache: {e}")

    async def preload_user_cache(self, session: AsyncSession, user_ids: list[int]):
        """预加载用户缓存"""
        if self._refreshing:
            return

        self._refreshing = True
        try:
            logger.info(f"Preloading cache for {len(user_ids)} users")

            # 批量获取用户
            users = (await session.exec(select(User).where(col(User.id).in_(user_ids)))).all()

            # 串行缓存用户信息，避免并发数据库访问问题
            cached_count = 0
            for user in users:
                if user.id != BANCHOBOT_ID:
                    try:
                        await self._cache_single_user(user, session)
                        cached_count += 1
                    except Exception as e:
                        logger.error(f"Failed to cache user {user.id}: {e}")

            logger.info(f"Preloaded cache for {cached_count} users")

        except Exception as e:
            logger.error(f"Error preloading user cache: {e}")
        finally:
            self._refreshing = False

    async def _cache_single_user(self, user: User, session: AsyncSession):
        """缓存单个用户"""
        try:
            user_resp = await UserResp.from_db(user, session, include=SEARCH_INCLUDED)

            # 应用资源代理处理
            if settings.enable_asset_proxy:
                try:
                    asset_proxy_service = get_asset_proxy_service()
                    user_resp = await asset_proxy_service.replace_asset_urls(user_resp)
                except Exception as e:
                    logger.warning(f"Asset proxy processing failed for user cache {user.id}: {e}")

            await self.cache_user(user_resp)
        except Exception as e:
            logger.error(f"Error caching single user {user.id}: {e}")

    async def refresh_user_cache_on_score_submit(self, session: AsyncSession, user_id: int, mode: GameMode):
        """成绩提交后刷新用户缓存"""
        try:
            # 使相关缓存失效（包括 v1 和 v2）
            await self.invalidate_user_cache(user_id)
            await self.invalidate_v1_user_cache(user_id)
            await self.invalidate_user_scores_cache(user_id, mode)

            # 立即重新加载用户信息
            user = await session.get(User, user_id)
            if user and user.id != BANCHOBOT_ID:
                await self._cache_single_user(user, session)
                logger.info(f"Refreshed cache for user {user_id} after score submit")
        except Exception as e:
            logger.error(f"Error refreshing user cache on score submit: {e}")

    async def get_cache_stats(self) -> dict:
        """获取缓存统计信息"""
        try:
            user_keys = await self.redis.keys("user:*")
            v1_user_keys = await self.redis.keys("v1_user:*")
            all_keys = user_keys + v1_user_keys
            total_size = 0

            for key in all_keys[:100]:  # 限制检查数量
                try:
                    size = await self.redis.memory_usage(key)
                    if size:
                        total_size += size
                except Exception:
                    continue

            return {
                "cached_users": len([k for k in user_keys if ":scores:" not in k and ":beatmapsets:" not in k]),
                "cached_v1_users": len([k for k in v1_user_keys if ":scores:" not in k]),
                "cached_user_scores": len([k for k in user_keys if ":scores:" in k]),
                "cached_user_beatmapsets": len([k for k in user_keys if ":beatmapsets:" in k]),
                "total_cached_entries": len(all_keys),
                "estimated_total_size_mb": (round(total_size / 1024 / 1024, 2) if total_size > 0 else 0),
                "refreshing": self._refreshing,
            }
        except Exception as e:
            logger.error(f"Error getting user cache stats: {e}")
            return {"error": str(e)}


# 全局缓存服务实例
_user_cache_service: UserCacheService | None = None


def get_user_cache_service(redis: Redis) -> UserCacheService:
    """获取用户缓存服务实例"""
    global _user_cache_service
    if _user_cache_service is None:
        _user_cache_service = UserCacheService(redis)
    return _user_cache_service


async def refresh_user_cache_background(redis: Redis, user_id: int, mode: GameMode):
    """后台任务：刷新用户缓存"""
    try:
        user_cache_service = get_user_cache_service(redis)
        # 创建独立的数据库会话
        async with with_db() as session:
            await user_cache_service.refresh_user_cache_on_score_submit(session, user_id, mode)
    except Exception as e:
        logger.error(f"Failed to refresh user cache after score submit: {e}")
