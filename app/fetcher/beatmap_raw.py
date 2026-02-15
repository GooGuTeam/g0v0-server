"""Raw beatmap file fetcher.

This module provides functionality to fetch raw beatmap files (.osu) from
multiple sources including the official osu! server and mirror servers.
It implements request deduplication and caching for improved performance.

Classes:
    NoBeatmapError: Exception raised when a beatmap cannot be found.
    BeatmapRawFetcher: Fetcher for retrieving raw beatmap file content.
"""

import asyncio

from app.log import fetcher_logger
from app.models.events.fetcher import BeatmapRawFetchedEvent, FetchingBeatmapRawEvent
from app.plugins import event_hub

from ._base import BaseFetcher

from httpx import AsyncClient, HTTPError, Limits
import redis.asyncio as redis

urls = [
    "https://osu.ppy.sh/osu/{beatmap_id}",
    "https://osu.direct/api/osu/{beatmap_id}",
    "https://catboy.best/osu/{beatmap_id}",
]

logger = fetcher_logger("BeatmapRawFetcher")


class NoBeatmapError(Exception):
    """Exception raised when a beatmap does not exist."""

    pass


class BeatmapRawFetcher(BaseFetcher):
    """Fetcher for retrieving raw beatmap file content (.osu files).

    This fetcher does not require OAuth authentication and implements
    request deduplication to avoid redundant requests for the same beatmap.

    Attributes:
        _client: Shared HTTP client for connection pooling.
        _pending_requests: Dictionary of pending requests for deduplication.
        _request_lock: Lock for thread-safe access to pending requests.
    """

    def __init__(self, client_id: str = "", client_secret: str = "", **kwargs):
        """Initialize the raw beatmap fetcher.

        BeatmapRawFetcher does not require OAuth credentials, so empty values
        are passed to the parent class.

        Args:
            client_id: OAuth client ID (not required). Defaults to "".
            client_secret: OAuth client secret (not required). Defaults to "".
            **kwargs: Additional arguments passed to BaseFetcher.
        """
        super().__init__(client_id, client_secret, **kwargs)
        # Shared HTTP client with connection pooling
        self._client: AsyncClient | None = None
        # Dictionary for concurrent request deduplication
        self._pending_requests: dict[int, asyncio.Future[str]] = {}
        self._request_lock = asyncio.Lock()

    async def _get_client(self) -> AsyncClient:
        """Get or create the shared HTTP client.

        Returns:
            The shared AsyncClient instance with configured connection pooling.
        """
        if self._client is None:
            # Configure connection pool limits
            limits = Limits(
                max_keepalive_connections=20,
                max_connections=50,
                keepalive_expiry=30.0,
            )
            self._client = AsyncClient(
                timeout=10.0,  # 10 second timeout per request
                limits=limits,
                follow_redirects=True,
            )
        return self._client

    async def close(self):
        """Close the HTTP client and release resources."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_beatmap_raw(self, beatmap_id: int) -> str:
        """Fetch the raw beatmap file content with request deduplication.

        If a request for the same beatmap is already in progress, this method
        will wait for that request to complete instead of making a duplicate
        request.

        Args:
            beatmap_id: The ID of the beatmap to fetch.

        Returns:
            The raw beatmap file content as a string.

        Raises:
            NoBeatmapError: If the beatmap cannot be found on any source.
            HTTPError: If all fetch attempts fail.
        """
        future: asyncio.Future[str] | None = None
        event_hub.emit(FetchingBeatmapRawEvent(beatmap_id=beatmap_id))

        # Check if there is an in-progress request
        async with self._request_lock:
            if beatmap_id in self._pending_requests:
                logger.debug(f"Beatmap {beatmap_id} request already in progress, waiting...")
                future = self._pending_requests[beatmap_id]

        # If there is an in-progress request, wait for it
        if future is not None:
            try:
                return await future
            except Exception as e:
                logger.warning(f"Waiting for beatmap {beatmap_id} failed: {e}")
                # If waiting fails, continue to make our own request
                future = None

        # Create a new request Future
        async with self._request_lock:
            if beatmap_id in self._pending_requests:
                # Double-check: another coroutine may have created it while waiting for lock
                future = self._pending_requests[beatmap_id]
                if future is not None:
                    try:
                        return await future
                    except Exception as e:
                        logger.debug(f"Concurrent request for beatmap {beatmap_id} failed: {e}")
                        # Continue to create new request

            # Create new Future
            future = asyncio.get_event_loop().create_future()
            self._pending_requests[beatmap_id] = future

        try:
            # Execute the actual request
            result = await self._fetch_beatmap_raw(beatmap_id)
            if not future.done():
                future.set_result(result)
            return result
        except asyncio.CancelledError:
            if not future.done():
                future.cancel()
            raise
        except Exception as e:
            if not future.done():
                future.set_exception(e)
            return await future
        finally:
            # Cleanup
            async with self._request_lock:
                self._pending_requests.pop(beatmap_id, None)

    async def _fetch_beatmap_raw(self, beatmap_id: int) -> str:
        """Internal method to fetch beatmap from multiple sources.

        Tries each URL in the urls list until successful.

        Args:
            beatmap_id: The ID of the beatmap to fetch.

        Returns:
            The raw beatmap file content as a string.

        Raises:
            NoBeatmapError: If the beatmap cannot be found.
            HTTPError: If all sources fail.
        """
        client = await self._get_client()
        last_error = None

        for url_template in urls:
            req_url = url_template.format(beatmap_id=beatmap_id)
            try:
                logger.opt(colors=True).debug(f"get_beatmap_raw: <y>{req_url}</y>")
                resp = await client.get(req_url)

                if resp.status_code >= 400:
                    logger.warning(f"Beatmap {beatmap_id} from {req_url}: HTTP {resp.status_code}")
                    last_error = NoBeatmapError(f"HTTP {resp.status_code}")
                    continue

                if not resp.text:
                    logger.warning(f"Beatmap {beatmap_id} from {req_url}: empty response")
                    last_error = NoBeatmapError("Empty response")
                    continue

                logger.debug(f"Successfully fetched beatmap {beatmap_id} from {req_url}")
                event_hub.emit(BeatmapRawFetchedEvent(beatmap_id=beatmap_id, beatmap_raw=resp.text))
                return resp.text

            except Exception as e:
                logger.warning(f"Error fetching beatmap {beatmap_id} from {req_url}: {e}")
                last_error = e
                continue

        # All URLs failed
        error_msg = f"Failed to fetch beatmap {beatmap_id} from all sources"
        if last_error and isinstance(last_error, NoBeatmapError):
            raise last_error
        raise HTTPError(error_msg) from last_error

    async def get_or_fetch_beatmap_raw(self, redis: redis.Redis, beatmap_id: int) -> str:
        """Fetch a beatmap with Redis caching.

        Checks the Redis cache first and returns cached content if available.
        If not cached, fetches from the sources and stores in cache.

        Args:
            redis: The Redis client instance.
            beatmap_id: The ID of the beatmap to fetch.

        Returns:
            The raw beatmap file content as a string.
        """
        from app.config import settings

        cache_key = f"beatmap:{beatmap_id}:raw"
        cache_expire = settings.beatmap_cache_expire_hours * 60 * 60

        # Check cache
        if await redis.exists(cache_key):
            content = await redis.get(cache_key)
            if content:
                # Extend cache TTL
                await redis.expire(cache_key, cache_expire)
                return content

        # Fetch and cache
        raw = await self.get_beatmap_raw(beatmap_id)
        await redis.set(cache_key, raw, ex=cache_expire)
        return raw
