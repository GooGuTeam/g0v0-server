"""Beatmapset cache service.

Caches beatmapset data to reduce database query frequency.
"""

import hashlib
import json
from typing import TYPE_CHECKING

from app.config import settings
from app.database import BeatmapsetDict
from app.log import logger
from app.utils import safe_json_dumps

from redis.asyncio import Redis

if TYPE_CHECKING:
    pass


def generate_hash(data) -> str:
    """Generate MD5 hash of data.

    Args:
        data: Data to hash (string or JSON-serializable object).

    Returns:
        MD5 hash string.
    """
    content = data if isinstance(data, str) else safe_json_dumps(data)
    return hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()


class BeatmapsetCacheService:
    """Beatmapset cache service.

    Provides caching functionality for beatmapset data, lookup results,
    and search results using Redis.
    """

    def __init__(self, redis: Redis):
        self.redis = redis
        self._default_ttl = settings.beatmapset_cache_expire_seconds

    def _get_beatmapset_cache_key(self, beatmapset_id: int) -> str:
        """Generate beatmapset cache key."""
        return f"beatmapset:{beatmapset_id}"

    def _get_beatmap_lookup_cache_key(self, beatmap_id: int) -> str:
        """Generate beatmap lookup cache key."""
        return f"beatmap_lookup:{beatmap_id}:beatmapset"

    def _get_search_cache_key(self, query_hash: str, cursor_hash: str) -> str:
        """Generate search result cache key."""
        return f"beatmapset_search:{query_hash}:{cursor_hash}"

    async def get_beatmapset_from_cache(self, beatmapset_id: int) -> BeatmapsetDict | None:
        """Get beatmapset info from cache.

        Args:
            beatmapset_id: The beatmapset ID.

        Returns:
            Beatmapset data if found, None otherwise.
        """
        try:
            cache_key = self._get_beatmapset_cache_key(beatmapset_id)
            cached_data = await self.redis.get(cache_key)
            if cached_data:
                logger.debug(f"Beatmapset cache hit for {beatmapset_id}")
                return json.loads(cached_data)
            return None
        except (ValueError, TypeError, AttributeError) as e:
            logger.error(f"Error getting beatmapset from cache: {e}")
            return None

    async def cache_beatmapset(
        self,
        beatmapset_resp: BeatmapsetDict,
        expire_seconds: int | None = None,
    ):
        """Cache beatmapset info.

        Args:
            beatmapset_resp: Beatmapset response data.
            expire_seconds: Cache expiration time in seconds.
        """
        try:
            if expire_seconds is None:
                expire_seconds = self._default_ttl
            cache_key = self._get_beatmapset_cache_key(beatmapset_resp["id"])
            cached_data = safe_json_dumps(beatmapset_resp)
            await self.redis.setex(cache_key, expire_seconds, cached_data)  # type: ignore
            logger.debug(f"Cached beatmapset {beatmapset_resp['id']} for {expire_seconds}s")
        except (ValueError, TypeError, AttributeError) as e:
            logger.error(f"Error caching beatmapset: {e}")

    async def get_beatmap_lookup_from_cache(self, beatmap_id: int) -> BeatmapsetDict | None:
        """Get beatmapset info from cache by beatmap ID lookup.

        Args:
            beatmap_id: The beatmap ID.

        Returns:
            Beatmapset data if found, None otherwise.
        """
        try:
            cache_key = self._get_beatmap_lookup_cache_key(beatmap_id)
            cached_data = await self.redis.get(cache_key)
            if cached_data:
                logger.debug(f"Beatmap lookup cache hit for {beatmap_id}")
                data = json.loads(cached_data)
                return data
            return None
        except (ValueError, TypeError, AttributeError) as e:
            logger.error(f"Error getting beatmap lookup from cache: {e}")
            return None

    async def cache_beatmap_lookup(
        self,
        beatmap_id: int,
        beatmapset_resp: BeatmapsetDict,
        expire_seconds: int | None = None,
    ):
        """Cache beatmapset info from beatmap ID lookup.

        Args:
            beatmap_id: The beatmap ID.
            beatmapset_resp: Beatmapset response data.
            expire_seconds: Cache expiration time in seconds.
        """
        try:
            if expire_seconds is None:
                expire_seconds = self._default_ttl
            cache_key = self._get_beatmap_lookup_cache_key(beatmap_id)
            cached_data = safe_json_dumps(beatmapset_resp)
            await self.redis.setex(cache_key, expire_seconds, cached_data)  # type: ignore
            logger.debug(f"Cached beatmap lookup {beatmap_id} for {expire_seconds}s")
        except (ValueError, TypeError, AttributeError) as e:
            logger.error(f"Error caching beatmap lookup: {e}")

    async def get_search_from_cache(self, query_hash: str, cursor_hash: str) -> dict | None:
        """Get search results from cache.

        Args:
            query_hash: Hash of the search query.
            cursor_hash: Hash of the cursor position.

        Returns:
            Search results if found, None otherwise.
        """
        try:
            cache_key = self._get_search_cache_key(query_hash, cursor_hash)
            cached_data = await self.redis.get(cache_key)
            if cached_data:
                logger.debug(f"Search cache hit for {query_hash[:8]}...{cursor_hash[:8]}")
                return json.loads(cached_data)
            return None
        except (ValueError, TypeError, AttributeError) as e:
            logger.error(f"Error getting search from cache: {e}")
            return None

    async def cache_search_result(
        self,
        query_hash: str,
        cursor_hash: str,
        search_result: dict,
        expire_seconds: int | None = None,
    ):
        """Cache search results.

        Args:
            query_hash: Hash of the search query.
            cursor_hash: Hash of the cursor position.
            search_result: Search result data.
            expire_seconds: Cache expiration time in seconds.
        """
        try:
            if expire_seconds is None:
                expire_seconds = min(self._default_ttl, 300)  # Search results have shorter cache time, max 5 minutes
            cache_key = self._get_search_cache_key(query_hash, cursor_hash)
            cached_data = safe_json_dumps(search_result)
            await self.redis.setex(cache_key, expire_seconds, cached_data)  # type: ignore
            logger.debug(f"Cached search result for {expire_seconds}s")
        except (ValueError, TypeError, AttributeError) as e:
            logger.error(f"Error caching search result: {e}")

    async def invalidate_beatmapset_cache(self, beatmapset_id: int):
        """Invalidate beatmapset cache.

        Args:
            beatmapset_id: The beatmapset ID.
        """
        try:
            cache_key = self._get_beatmapset_cache_key(beatmapset_id)
            await self.redis.delete(cache_key)
            logger.debug(f"Invalidated beatmapset cache for {beatmapset_id}")
        except (ValueError, TypeError, AttributeError) as e:
            logger.error(f"Error invalidating beatmapset cache: {e}")

    async def invalidate_beatmap_lookup_cache(self, beatmap_id: int):
        """Invalidate beatmap lookup cache.

        Args:
            beatmap_id: The beatmap ID.
        """
        try:
            cache_key = self._get_beatmap_lookup_cache_key(beatmap_id)
            await self.redis.delete(cache_key)
            logger.debug(f"Invalidated beatmap lookup cache for {beatmap_id}")
        except (ValueError, TypeError, AttributeError) as e:
            logger.error(f"Error invalidating beatmap lookup cache: {e}")

    async def get_cache_stats(self) -> dict:
        """Get cache statistics.

        Returns:
            Dictionary containing cache statistics.
        """
        try:
            beatmapset_keys = await self.redis.keys("beatmapset:*")
            lookup_keys = await self.redis.keys("beatmap_lookup:*")
            search_keys = await self.redis.keys("beatmapset_search:*")

            return {
                "cached_beatmapsets": len(beatmapset_keys),
                "cached_lookups": len(lookup_keys),
                "cached_searches": len(search_keys),
                "total_keys": len(beatmapset_keys) + len(lookup_keys) + len(search_keys),
            }
        except (ValueError, TypeError, AttributeError) as e:
            logger.error(f"Error getting cache stats: {e}")
            return {"error": str(e)}


# Global cache service instance
_cache_service: BeatmapsetCacheService | None = None


def get_beatmapset_cache_service(redis: Redis) -> BeatmapsetCacheService:
    """Get the beatmapset cache service instance.

    Args:
        redis: Redis client.

    Returns:
        The BeatmapsetCacheService singleton instance.
    """
    global _cache_service
    if _cache_service is None:
        _cache_service = BeatmapsetCacheService(redis)
    return _cache_service
