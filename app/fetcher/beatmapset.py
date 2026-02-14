"""Beatmapset fetcher for osu! API.

This module provides functionality to fetch and search for beatmapsets from the
osu! API v2. It includes caching support, prefetching capabilities, and homepage
cache warming features for improved performance.

Classes:
    BeatmapsetFetcher: Fetcher for retrieving and searching beatmapsets.
"""

import asyncio
import base64
import hashlib
import json

from app.database import BeatmapsetDict, BeatmapsetModel, SearchBeatmapsetsResp
from app.helpers import bg_tasks
from app.log import fetcher_logger
from app.models.beatmap import SearchQueryModel
from app.models.model import Cursor

from ._base import BaseFetcher

from pydantic import TypeAdapter
import redis.asyncio as redis

logger = fetcher_logger("BeatmapsetFetcher")


adapter = TypeAdapter(
    BeatmapsetModel.generate_typeddict(
        (
            "availability",
            "bpm",
            "last_updated",
            "ranked",
            "ranked_date",
            "submitted_date",
            "tags",
            "storyboard",
            "description",
            "genre",
            "language",
            *[
                f"beatmaps.{inc}"
                for inc in (
                    "checksum",
                    "accuracy",
                    "ar",
                    "bpm",
                    "convert",
                    "count_circles",
                    "count_sliders",
                    "count_spinners",
                    "cs",
                    "deleted_at",
                    "drain",
                    "hit_length",
                    "is_scoreable",
                    "last_updated",
                    "mode_int",
                    "ranked",
                    "url",
                    "max_combo",
                )
            ],
        )
    )
)


class BeatmapsetFetcher(BaseFetcher):
    """Fetcher for retrieving and searching beatmapsets from the osu! API.

    Provides methods for fetching individual beatmapsets, searching with
    pagination support, and prefetching/caching strategies for improved
    performance.
    """

    @staticmethod
    def _get_homepage_queries() -> list[tuple[SearchQueryModel, Cursor]]:
        """Get the list of homepage pre-cache queries.

        Returns:
            A list of tuples containing SearchQueryModel and Cursor pairs
            for common homepage search combinations.
        """
        # Common homepage query combinations
        homepage_queries = []

        # Primary sort options
        sorts = ["ranked_desc", "updated_desc", "favourites_desc", "plays_desc"]

        for sort in sorts:
            # First page - use minimal parameter set to match user requests
            query = SearchQueryModel(
                q="",
                s="leaderboard",
                sort=sort,  # type: ignore
            )
            homepage_queries.append((query, {}))

        return homepage_queries

    @staticmethod
    def _generate_cache_key(query: SearchQueryModel, cursor: Cursor) -> str:
        """Generate a cache key for search results.

        Args:
            query: The search query model.
            cursor: The cursor for pagination.

        Returns:
            A Redis cache key string based on the query parameters hash.
        """
        # Only include core query parameters, ignore default values
        cache_data = {}

        # Add non-default/non-empty query parameters
        if query.q:
            cache_data["q"] = query.q
        if query.s != "leaderboard":  # Only add non-default values
            cache_data["s"] = query.s
        if hasattr(query, "sort") and query.sort:
            cache_data["sort"] = query.sort
        if query.nsfw is not False:  # Only add non-default values
            cache_data["nsfw"] = query.nsfw
        if query.m is not None:
            cache_data["m"] = query.m
        if query.c:
            cache_data["c"] = query.c
        if query.l != "any":  # Check language default value
            cache_data["l"] = query.l
        if query.e:
            cache_data["e"] = query.e
        if query.r:
            cache_data["r"] = query.r
        if query.played is not False:
            cache_data["played"] = query.played

        # Add cursor
        if cursor:
            cache_data["cursor"] = cursor

        # Serialize to JSON and generate MD5 hash
        cache_json = json.dumps(cache_data, sort_keys=True, separators=(",", ":"))
        cache_hash = hashlib.md5(cache_json.encode(), usedforsecurity=False).hexdigest()

        logger.opt(colors=True).debug(f"<blue>[CacheKey]</blue> Query: {cache_data}, Hash: {cache_hash}")

        return f"beatmapset:search:{cache_hash}"

    @staticmethod
    def _encode_cursor(cursor_dict: dict[str, int | float]) -> str:
        """Encode a cursor dictionary to a base64 string.

        Args:
            cursor_dict: The cursor dictionary to encode.

        Returns:
            A base64-encoded string representation of the cursor.
        """
        cursor_json = json.dumps(cursor_dict, separators=(",", ":"))
        return base64.b64encode(cursor_json.encode()).decode()

    @staticmethod
    def _decode_cursor(cursor_string: str) -> dict[str, int | float]:
        """Decode a base64 string to a cursor dictionary.

        Args:
            cursor_string: The base64-encoded cursor string.

        Returns:
            The decoded cursor dictionary, or an empty dict if decoding fails.
        """
        try:
            cursor_json = base64.b64decode(cursor_string).decode()
            return json.loads(cursor_json)
        except Exception:
            return {}

    async def get_beatmapset(self, beatmap_set_id: int) -> BeatmapsetDict:
        """Fetch a beatmapset by its ID.

        Args:
            beatmap_set_id: The ID of the beatmapset to fetch.

        Returns:
            A dictionary containing the beatmapset data with extended
            information including beatmaps, availability, and metadata.
        """
        logger.opt(colors=True).debug(f"get_beatmapset: <y>{beatmap_set_id}</y>")
        return adapter.validate_python(  # pyright: ignore[reportReturnType]
            await self.request_api(f"https://osu.ppy.sh/api/v2/beatmapsets/{beatmap_set_id}")
        )

    async def search_beatmapset(
        self, query: SearchQueryModel, cursor: Cursor, redis_client: redis.Redis
    ) -> SearchBeatmapsetsResp:
        """Search for beatmapsets with caching support.

        Checks Redis cache first and returns cached results if available.
        If not cached, fetches from the API and stores the result in cache.

        Args:
            query: The search query model containing filters and options.
            cursor: The cursor for pagination.
            redis_client: The Redis client instance for caching.

        Returns:
            SearchBeatmapsetsResp containing the search results and pagination info.
        """
        logger.opt(colors=True).debug(f"search_beatmapset: <y>{query}</y>")

        # Generate cache key
        cache_key = self._generate_cache_key(query, cursor)

        # Try to get result from cache
        cached_result = await redis_client.get(cache_key)
        if cached_result:
            logger.opt(colors=True).debug(f"Cache hit for key: <y>{cache_key}</y>")
            try:
                cached_data = json.loads(cached_result)
                return SearchBeatmapsetsResp.model_validate(cached_data)
            except Exception as e:
                logger.warning(f"Cache data invalid, fetching from API: {e}")

        # Cache miss, fetch from API
        logger.debug("Cache miss, fetching from API")

        params = query.model_dump(exclude_none=True, exclude_unset=True, exclude_defaults=True)

        if query.cursor_string:
            params["cursor_string"] = query.cursor_string
        else:
            for k, v in cursor.items():
                params[f"cursor[{k}]"] = v

        api_response = await self.request_api(
            "https://osu.ppy.sh/api/v2/beatmapsets/search",
            params=params,
        )

        # Process cursor info in response
        if api_response.get("cursor"):
            cursor_dict = api_response["cursor"]
            api_response["cursor_string"] = self._encode_cursor(cursor_dict)

        # Cache result for 15 minutes
        cache_ttl = 15 * 60  # 15 minutes
        await redis_client.set(cache_key, json.dumps(api_response, separators=(",", ":")), ex=cache_ttl)

        logger.opt(colors=True).debug(f"Cached result for key: <y>{cache_key}</y> (TTL: {cache_ttl}s)")

        resp = SearchBeatmapsetsResp.model_validate(api_response)

        # Smart prefetching: only prefetch when user explicitly searches,
        # avoid excessive API requests
        # Only prefetch when there's a search term or specific conditions,
        # avoid excessive prefetching during homepage browsing
        if api_response.get("cursor") and (query.q or query.s != "leaderboard" or cursor):
            # Prefetch next 1 page in background (reduced prefetch amount)
            import asyncio

            # Delay prefetching instead of creating task immediately
            async def delayed_prefetch():
                await asyncio.sleep(3.0)  # 3 second delay
                await self.prefetch_next_pages(query, api_response["cursor"], redis_client, pages=1)

            bg_tasks.add_task(delayed_prefetch)

        return resp

    async def prefetch_next_pages(
        self,
        query: SearchQueryModel,
        current_cursor: Cursor,
        redis_client: redis.Redis,
        pages: int = 3,
    ) -> None:
        """Prefetch the next several pages of search results.

        Fetches and caches subsequent pages in the background to improve
        perceived performance when users paginate through results.

        Args:
            query: The search query model.
            current_cursor: The current cursor position.
            redis_client: The Redis client instance for caching.
            pages: The number of pages to prefetch. Defaults to 3.
        """
        if not current_cursor:
            return

        cursor = current_cursor.copy()

        for page in range(1, pages + 1):
            # Use current cursor to request next page
            next_query = query.model_copy()

            logger.debug(f"Prefetching page {page + 1}")

            # Generate cache key for next page
            next_cache_key = self._generate_cache_key(next_query, cursor)

            # Check if already cached
            if await redis_client.exists(next_cache_key):
                logger.debug(f"Page {page + 1} already cached")
                # Try to get cursor from cache to continue prefetching
                cached_data = await redis_client.get(next_cache_key)
                if cached_data:
                    try:
                        data = json.loads(cached_data)
                        if data.get("cursor"):
                            cursor = data["cursor"]
                            continue
                    except Exception:
                        logger.warning("Failed to parse cached data for cursor")
                break

            # Add delay between prefetch pages to avoid burst requests
            if page > 1:
                await asyncio.sleep(1.5)  # 1.5 second delay

            # Request next page data
            params = next_query.model_dump(exclude_none=True, exclude_unset=True, exclude_defaults=True)

            for k, v in cursor.items():
                params[f"cursor[{k}]"] = v

            api_response = await self.request_api(
                "https://osu.ppy.sh/api/v2/beatmapsets/search",
                params=params,
            )

            # Process cursor info in response
            if api_response.get("cursor"):
                cursor_dict = api_response["cursor"]
                api_response["cursor_string"] = self._encode_cursor(cursor_dict)
                cursor = cursor_dict  # Update cursor for next page
            else:
                # No more pages
                break

            # Cache result (shorter TTL for prefetch)
            prefetch_ttl = 10 * 60  # 10 minutes
            await redis_client.set(
                next_cache_key,
                json.dumps(api_response, separators=(",", ":")),
                ex=prefetch_ttl,
            )

            logger.debug(f"Prefetched page {page + 1} (TTL: {prefetch_ttl}s)")

    async def warmup_homepage_cache(self, redis_client: redis.Redis) -> None:
        """Warm up the homepage cache with common search queries.

        Pre-fetches and caches common homepage search combinations to
        improve initial load times for users.

        Args:
            redis_client: The Redis client instance for caching.
        """
        homepage_queries = self._get_homepage_queries()

        logger.info(f"Starting homepage cache warmup ({len(homepage_queries)} queries)")

        for i, (query, cursor) in enumerate(homepage_queries):
            try:
                # Add delay between requests to avoid burst requests
                if i > 0:
                    await asyncio.sleep(2.0)  # 2 second delay

                cache_key = self._generate_cache_key(query, cursor)

                # Check if already cached
                if await redis_client.exists(cache_key):
                    logger.debug(f"Query {query.sort} already cached")
                    continue

                # Request and cache
                params = query.model_dump(exclude_none=True, exclude_unset=True, exclude_defaults=True)

                api_response = await self.request_api(
                    "https://osu.ppy.sh/api/v2/beatmapsets/search",
                    params=params,
                )

                if api_response.get("cursor"):
                    cursor_dict = api_response["cursor"]
                    api_response["cursor_string"] = self._encode_cursor(cursor_dict)

                # Cache result
                cache_ttl = 20 * 60  # 20 minutes
                await redis_client.set(
                    cache_key,
                    json.dumps(api_response, separators=(",", ":")),
                    ex=cache_ttl,
                )

                logger.info(f"Warmed up cache for {query.sort} (TTL: {cache_ttl}s)")

                if api_response.get("cursor"):
                    await self.prefetch_next_pages(query, api_response["cursor"], redis_client, pages=2)

            except Exception as e:
                logger.error(f"Failed to warmup cache for {query.sort}: {e}")
