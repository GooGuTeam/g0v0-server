"""Audio proxy service.

Provides functionality to fetch beatmapset audio previews from osu! official
servers and cache them locally in Redis.
"""

from app.log import logger
from app.models.error import ErrorType, RequestError

import httpx
import redis.asyncio as redis


class AudioProxyService:
    """Audio proxy service for fetching and caching beatmapset audio previews.

    Attributes:
        redis_binary: Redis client for binary data (audio).
        redis_text: Redis client for text data (metadata).
        http_client: HTTP client for fetching audio from osu! servers.
    """

    def __init__(self, redis_binary_client: redis.Redis, redis_text_client: redis.Redis):
        self.redis_binary = redis_binary_client
        self.redis_text = redis_text_client
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self._cache_ttl = 7 * 24 * 60 * 60

    async def close(self):
        """Close the HTTP client."""
        await self.http_client.aclose()

    def _get_beatmapset_cache_key(self, beatmapset_id: int) -> str:
        """Generate cache key for beatmapset audio."""
        return f"beatmapset_audio:{beatmapset_id}"

    def _get_beatmapset_metadata_key(self, beatmapset_id: int) -> str:
        """Generate cache key for beatmapset audio metadata."""
        return f"beatmapset_audio_meta:{beatmapset_id}"

    async def get_beatmapset_audio_from_cache(self, beatmapset_id: int) -> tuple[bytes, str] | None:
        """Get beatmapset audio data and content type from cache.

        Args:
            beatmapset_id: The beatmapset ID.

        Returns:
            Tuple of (audio_data, content_type) if found, None otherwise.
        """
        try:
            cache_key = self._get_beatmapset_cache_key(beatmapset_id)
            metadata_key = self._get_beatmapset_metadata_key(beatmapset_id)

            # Get audio data (binary) and metadata (text)
            audio_data = await self.redis_binary.get(cache_key)
            metadata = await self.redis_text.get(metadata_key)

            if audio_data and metadata:
                logger.debug(f"Beatmapset audio cache hit for ID: {beatmapset_id}")
                # audio_data is already bytes type, metadata is str type
                return audio_data, metadata
            return None
        except (redis.RedisError, redis.ConnectionError) as e:
            logger.error(f"Error getting beatmapset audio from cache: {e}")
            return None

    async def cache_beatmapset_audio(self, beatmapset_id: int, audio_data: bytes, content_type: str):
        """Cache beatmapset audio data.

        Args:
            beatmapset_id: The beatmapset ID.
            audio_data: The audio binary data.
            content_type: The MIME content type.
        """
        try:
            cache_key = self._get_beatmapset_cache_key(beatmapset_id)
            metadata_key = self._get_beatmapset_metadata_key(beatmapset_id)

            # Cache audio data (binary) and metadata (text)
            await self.redis_binary.setex(cache_key, self._cache_ttl, audio_data)
            await self.redis_text.setex(metadata_key, self._cache_ttl, content_type)

            logger.debug(f"Cached beatmapset audio for ID: {beatmapset_id}, size: {len(audio_data)} bytes")
        except (redis.RedisError, redis.ConnectionError) as e:
            logger.error(f"Error caching beatmapset audio: {e}")

    async def fetch_beatmapset_audio(self, beatmapset_id: int) -> tuple[bytes, str]:
        """Fetch beatmapset audio preview from osu! official servers.

        Args:
            beatmapset_id: The beatmapset ID.

        Returns:
            Tuple of (audio_data, content_type).

        Raises:
            RequestError: If the audio cannot be fetched.
        """
        try:
            # Build osu! official preview audio URL
            preview_url = f"https://b.ppy.sh/preview/{beatmapset_id}.mp3"
            logger.info(f"Fetching beatmapset audio from: {preview_url}")

            response = await self.http_client.get(preview_url)
            response.raise_for_status()

            # osu! preview audio is typically mp3 format
            content_type = response.headers.get("content-type", "audio/mpeg")
            audio_data = response.content

            # Check file size limit (10MB, preview audio shouldn't be too large)
            max_size = 10 * 1024 * 1024  # 10MB
            if len(audio_data) > max_size:
                raise RequestError(
                    ErrorType.AUDIO_FILE_TOO_LARGE,
                    {"size": len(audio_data), "max_size": max_size},
                )

            if len(audio_data) == 0:
                raise RequestError(ErrorType.AUDIO_PREVIEW_NOT_AVAILABLE)

            logger.info(f"Successfully fetched beatmapset audio: {len(audio_data)} bytes, type: {content_type}")
            return audio_data, content_type

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching beatmapset audio for ID {beatmapset_id}: {e}")
            if e.response.status_code == 404:
                raise RequestError(ErrorType.AUDIO_PREVIEW_NOT_FOUND) from e
            else:
                raise RequestError(
                    ErrorType.INTERNAL_ERROR_FETCHING_AUDIO,
                    {"status_code": e.response.status_code},
                    status_code=e.response.status_code,
                ) from e
        except httpx.RequestError as e:
            logger.error(f"Request error fetching beatmapset audio for ID {beatmapset_id}: {e}")
            raise RequestError(ErrorType.FAILED_CONNECT_OSU_SERVERS) from e
        except RequestError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching beatmapset audio for ID {beatmapset_id}: {e}")
            raise RequestError(ErrorType.INTERNAL_ERROR_FETCHING_AUDIO) from e

    async def get_beatmapset_audio(self, beatmapset_id: int) -> tuple[bytes, str]:
        """Get audio preview by beatmapset ID.

        Attempts to retrieve from cache first, then fetches from osu! servers
        if not cached.

        Args:
            beatmapset_id: The beatmapset ID.

        Returns:
            Tuple of (audio_data, content_type).
        """
        # Try to get from cache first
        cached_result = await self.get_beatmapset_audio_from_cache(beatmapset_id)
        if cached_result:
            return cached_result

        # Cache miss, fetch from osu! official
        audio_data, content_type = await self.fetch_beatmapset_audio(beatmapset_id)

        # Cache newly fetched audio data
        await self.cache_beatmapset_audio(beatmapset_id, audio_data, content_type)

        return audio_data, content_type


def get_audio_proxy_service(redis_binary_client: redis.Redis, redis_text_client: redis.Redis) -> AudioProxyService:
    """Get an audio proxy service instance.

    Args:
        redis_binary_client: Redis client for binary data.
        redis_text_client: Redis client for text data.

    Returns:
        A new AudioProxyService instance.
    """
    # Create new instance each time to avoid global state
    return AudioProxyService(redis_binary_client, redis_text_client)
