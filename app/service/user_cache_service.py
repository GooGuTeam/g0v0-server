"""User cache service.

Caches user information with hot caching and real-time refresh capabilities.
"""

import json
from typing import TYPE_CHECKING, Any

from app.config import settings
from app.const import BANCHOBOT_ID
from app.database import User
from app.database.score import LegacyScoreResp
from app.database.user import UserDict, UserModel
from app.dependencies.database import with_db
from app.helpers import replace_asset_urls, safe_json_dumps
from app.log import logger
from app.models.score import GameMode

from redis.asyncio import Redis
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    pass


class UserCacheService:
    """User cache service.

    Provides caching for user data, scores, and beatmapsets
    with invalidation support.
    """

    def __init__(self, redis: Redis):
        self.redis = redis
        self._refreshing = False
        self._background_tasks: set = set()

    def _get_v1_user_cache_key(self, user_id: int, ruleset: GameMode | None = None) -> str:
        """Generate V1 user cache key."""
        if ruleset:
            return f"v1_user:{user_id}:ruleset:{ruleset}"
        return f"v1_user:{user_id}"

    async def get_v1_user_from_cache(self, user_id: int, ruleset: GameMode | None = None) -> dict | None:
        """Get V1 user info from cache."""
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
        """Cache V1 user info."""
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
        """Invalidate V1 user cache."""
        try:
            # Delete V1 user info cache
            pattern = f"v1_user:{user_id}*"
            keys = await self.redis.keys(pattern)
            if keys:
                await self.redis.delete(*keys)
                logger.info(f"Invalidated {len(keys)} V1 cache entries for user {user_id}")
        except Exception as e:
            logger.error(f"Error invalidating V1 user cache: {e}")

    def _get_user_cache_key(self, user_id: int, ruleset: GameMode | None = None) -> str:
        """Generate user cache key."""
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
        """Generate user scores cache key."""
        mode_part = f":{mode}" if mode else ""
        return (
            f"user:{user_id}:scores:{score_type}{mode_part}:limit:{limit}:offset:"
            f"{offset}:include_fail:{include_fail}:is_legacy:{is_legacy}"
        )

    def _get_user_beatmapsets_cache_key(
        self, user_id: int, beatmapset_type: str, limit: int = 100, offset: int = 0
    ) -> str:
        """Generate user beatmapsets cache key."""
        return f"user:{user_id}:beatmapsets:{beatmapset_type}:limit:{limit}:offset:{offset}"

    async def get_user_from_cache(self, user_id: int, ruleset: GameMode | None = None) -> UserDict | None:
        """Get user info from cache."""
        try:
            cache_key = self._get_user_cache_key(user_id, ruleset)
            cached_data = await self.redis.get(cache_key)
            if cached_data:
                logger.debug(f"User cache hit for user {user_id}")
                data = json.loads(cached_data)
                return data
            return None
        except Exception as e:
            logger.error(f"Error getting user from cache: {e}")
            return None

    async def cache_user(
        self,
        user_resp: UserDict,
        ruleset: GameMode | None = None,
        expire_seconds: int | None = None,
    ):
        """Cache user info."""
        try:
            if expire_seconds is None:
                expire_seconds = settings.user_cache_expire_seconds
            cache_key = self._get_user_cache_key(user_resp["id"], ruleset)
            cached_data = safe_json_dumps(user_resp)
            await self.redis.setex(cache_key, expire_seconds, cached_data)
            logger.debug(f"Cached user {user_resp['id']} for {expire_seconds}s")
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
    ) -> list[UserDict] | list[LegacyScoreResp] | None:
        """Get user scores from cache."""
        try:
            cache_key = self._get_user_scores_cache_key(
                user_id, score_type, include_fail, mode, limit, offset, is_legacy
            )
            cached_data = await self.redis.get(cache_key)
            if cached_data:
                logger.debug(f"User scores cache hit for user {user_id}, type {score_type}")
                data = json.loads(cached_data)
                return [LegacyScoreResp(**score_data) for score_data in data] if is_legacy else data
            return None
        except Exception as e:
            logger.error(f"Error getting user scores from cache: {e}")
            return None

    async def cache_user_scores(
        self,
        user_id: int,
        score_type: str,
        scores: list[UserDict] | list[LegacyScoreResp],
        include_fail: bool,
        mode: GameMode | None = None,
        limit: int = 100,
        offset: int = 0,
        expire_seconds: int | None = None,
        is_legacy: bool = False,
    ):
        """Cache user scores."""
        try:
            if expire_seconds is None:
                expire_seconds = settings.user_scores_cache_expire_seconds
            cache_key = self._get_user_scores_cache_key(
                user_id, score_type, include_fail, mode, limit, offset, is_legacy
            )
            if len(scores) == 0:
                return
            if isinstance(scores[0], dict):
                scores_json_list = [safe_json_dumps(score) for score in scores]
            else:
                scores_json_list = [score.model_dump_json() for score in scores]  # pyright: ignore[reportAttributeAccessIssue]
            cached_data = f"[{','.join(scores_json_list)}]"
            await self.redis.setex(cache_key, expire_seconds, cached_data)
            logger.debug(f"Cached user {user_id} scores ({score_type}) for {expire_seconds}s")
        except Exception as e:
            logger.error(f"Error caching user scores: {e}")

    async def get_user_beatmapsets_from_cache(
        self, user_id: int, beatmapset_type: str, limit: int = 100, offset: int = 0
    ) -> list[Any] | None:
        """Get user beatmapsets from cache."""
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
        """Cache user beatmapsets."""
        try:
            if expire_seconds is None:
                expire_seconds = settings.user_beatmapsets_cache_expire_seconds
            cache_key = self._get_user_beatmapsets_cache_key(user_id, beatmapset_type, limit, offset)
            # Use model_dump_json() for objects with that method, otherwise use safe_json_dumps
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
        """Invalidate user cache."""
        try:
            # Delete user info cache
            pattern = f"user:{user_id}:ruleset:*"
            keys = await self.redis.keys(pattern)
            if keys:
                await self.redis.delete(*keys)
                logger.info(f"Invalidated {len(keys)} cache entries for user {user_id}")
        except Exception as e:
            logger.error(f"Error invalidating user cache: {e}")

    async def invalidate_user_all_cache(self, user_id: int):
        """Invalidate all user caches."""
        try:
            # Delete user info cache
            pattern = f"user:{user_id}*"
            keys = await self.redis.keys(pattern)
            if keys:
                await self.redis.delete(*keys)
                logger.info(f"Invalidated {len(keys)} all cache entries for user {user_id}")
        except Exception as e:
            logger.error(f"Error invalidating user all cache: {e}")

    async def invalidate_user_scores_cache(self, user_id: int, mode: GameMode | None = None):
        """Invalidate user scores cache."""
        try:
            # Delete user scores related cache
            mode_pattern = f":{mode}" if mode else "*"
            pattern = f"user:{user_id}:scores:*{mode_pattern}*"
            keys = await self.redis.keys(pattern)
            if keys:
                await self.redis.delete(*keys)
                logger.info(f"Invalidated {len(keys)} score cache entries for user {user_id}")
        except Exception as e:
            logger.error(f"Error invalidating user scores cache: {e}")

    async def invalidate_user_beatmapsets_cache(self, user_id: int):
        """Invalidate user beatmapsets cache."""
        try:
            # Delete user beatmapsets related cache
            pattern = f"user:{user_id}:beatmapsets:*"
            keys = await self.redis.keys(pattern)
            if keys:
                await self.redis.delete(*keys)
                logger.info(f"Invalidated {len(keys)} beatmapset cache entries for user {user_id}")
        except Exception as e:
            logger.error(f"Error invalidating user beatmapsets cache: {e}")

    async def preload_user_cache(self, session: AsyncSession, user_ids: list[int]):
        """Preload user cache."""
        if self._refreshing:
            return

        self._refreshing = True
        try:
            logger.info(f"Preloading cache for {len(user_ids)} users")

            # Batch fetch users
            users = (await session.exec(select(User).where(col(User.id).in_(user_ids)))).all()

            # Serialize user info caching to avoid concurrent database access issues
            cached_count = 0
            for user in users:
                if user.id != BANCHOBOT_ID:
                    try:
                        await self._cache_single_user(user)
                        cached_count += 1
                    except Exception as e:
                        logger.error(f"Failed to cache user {user.id}: {e}")

            logger.info(f"Preloaded cache for {cached_count} users")

        except Exception as e:
            logger.error(f"Error preloading user cache: {e}")
        finally:
            self._refreshing = False

    async def _cache_single_user(self, user: User):
        """Cache a single user."""
        try:
            user_resp = await UserModel.transform(user, includes=User.USER_INCLUDES)

            # Apply asset proxy processing
            if settings.enable_asset_proxy:
                try:
                    user_resp = await replace_asset_urls(user_resp)
                except Exception as e:
                    logger.warning(f"Asset proxy processing failed for user cache {user.id}: {e}")

            await self.cache_user(user_resp)
        except Exception as e:
            logger.error(f"Error caching single user {user.id}: {e}")

    async def refresh_user_cache_on_score_submit(self, session: AsyncSession, user_id: int, mode: GameMode):
        """Refresh user cache after score submission."""
        try:
            # Invalidate related caches (both v1 and v2)
            await self.invalidate_user_cache(user_id)
            await self.invalidate_v1_user_cache(user_id)
            await self.invalidate_user_scores_cache(user_id, mode)

            # Immediately reload user info
            user = await session.get(User, user_id)
            if user and user.id != BANCHOBOT_ID:
                await self._cache_single_user(user)
                logger.info(f"Refreshed cache for user {user_id} after score submit")
        except Exception as e:
            logger.error(f"Error refreshing user cache on score submit: {e}")

    async def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        try:
            user_keys = await self.redis.keys("user:*")
            v1_user_keys = await self.redis.keys("v1_user:*")
            all_keys = user_keys + v1_user_keys
            total_size = 0

            for key in all_keys[:100]:  # Limit check count
                try:
                    size = await self.redis.memory_usage(key)
                    if size:
                        total_size += size
                except Exception:
                    logger.warning(f"Failed to get memory usage for key {key}")
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


# Global cache service instance
_user_cache_service: UserCacheService | None = None


def get_user_cache_service(redis: Redis) -> UserCacheService:
    """Get the user cache service instance.

    Args:
        redis: Redis client.

    Returns:
        The UserCacheService singleton instance.
    """
    global _user_cache_service
    if _user_cache_service is None:
        _user_cache_service = UserCacheService(redis)
    return _user_cache_service


async def refresh_user_cache_background(redis: Redis, user_id: int, mode: GameMode):
    """Background task: refresh user cache.

    Args:
        redis: Redis client.
        user_id: User ID.
        mode: Game mode.
    """
    try:
        user_cache_service = get_user_cache_service(redis)
        # Create independent database session
        async with with_db() as session:
            await user_cache_service.refresh_user_cache_on_score_submit(session, user_id, mode)
    except Exception as e:
        logger.error(f"Failed to refresh user cache after score submit: {e}")
