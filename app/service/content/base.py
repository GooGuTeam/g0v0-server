"""Pieces shared by the news and changelog services.

Both services read their content from the same repository and answer requests from
a snapshot they parsed once, so this module holds the repository access, the
snapshot holder, and the timestamp and metadata-cache helpers they share.

A snapshot is refreshed behind the request that found it expired: a slow or
unreachable repository never delays a response once a snapshot exists, and the
parsed metadata of a file is cached by its object ID, so a refresh only parses what
changed.
"""

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from datetime import UTC, datetime
import json
import re
import time
from typing import Annotated, Any

from app.config import settings
from app.dependencies.database import redis_client
from app.log import log
from app.models.error import ErrorType, RequestError
from app.service.repository import Repository, RepositoryEntry

from pydantic import BeforeValidator, StringConstraints, ValidationError

logger = log("Content")

# Prefix of every cache key the news and changelog write. Bumped whenever the shape of
# a cached value changes, so that a deployment does not read metadata written by the
# version before it.
CONTENT_CACHE_PREFIX = "g0v0:content:v2"

# Parsed metadata is keyed by object ID, so it never goes stale; it only needs to
# outlive a deployment, not the content.
META_CACHE_EXPIRE_SECONDS = 30 * 24 * 3600

# Timestamps in front matter and changelog files carry an offset without a colon
# (`+0000`), which `datetime.fromisoformat` does not accept as written everywhere.
_TIMESTAMP_OFFSET_PATTERN = re.compile(r"([+-])(\d{2}):?(\d{2})$")

_repository: Repository | None = None


def content_repository() -> Repository:
    """Return the client reading the content repository, creating it on first use.

    Returns:
        Repository: The shared client, configured from the `content_` settings.
    """
    global _repository

    if _repository is None:
        _repository = Repository(
            settings.content_repository,
            settings.content_branch,
            settings.content_raw_base_url,
            listing_base_url=settings.content_listing_base_url,
            token=settings.content_github_token,
            timeout=settings.content_request_timeout_seconds,
            cache_expire=settings.content_cache_expire_seconds,
            resource_cache_expire=settings.content_listing_cache_expire_seconds,
            cache_prefix=CONTENT_CACHE_PREFIX,
            user_agent="g0v0-server-content",
        )

    assert _repository is not None
    return _repository


async def close_content_repository() -> None:
    """Release the shared client, and forget it so a later call builds a new one."""
    global _repository

    repository, _repository = _repository, None
    if repository is not None:
        await repository.close()


async def list_content_directory(directory: str) -> list[RepositoryEntry]:
    """List a directory of the content repository.

    Args:
        directory: Repository relative directory.

    Returns:
        list[RepositoryEntry]: The entries of the directory; empty when it does not
            exist, which is how a repository without that content yet looks.

    Raises:
        RequestError: When the repository cannot be listed at all, as the caller
            cannot distinguish an empty feature from a broken one.
    """
    repository = content_repository()
    if not repository.listing_enabled:
        logger.warning("Cannot list the content repository: CONTENT_LISTING_BASE_URL is empty")
        raise RequestError(ErrorType.INTERNAL, status_code=503)

    entries = await repository.list_entries(directory)
    if entries is None:
        logger.warning(f"Could not list {repository.repository}:{directory!r}, treating it as empty")
        return []
    return entries


def parse_timestamp(value: object) -> datetime | None:
    """Parse a timestamp written in front matter or in a changelog file.

    Args:
        value: The raw value, which may not be a string at all.

    Returns:
        datetime | None: The timestamp in UTC, or `None` when it cannot be read. A
            timestamp without an offset is taken as UTC, which is what the content
            formats use.
    """
    if not isinstance(value, str):
        return None

    text = re.sub(r"\s+", " ", value.strip())
    if not text:
        return None

    # `2025-01-01 02:30:00 +0000` -> `2025-01-01T02:30:00+0000`.
    text = text.replace(" ", "T", 1).replace(" ", "")
    text = _TIMESTAMP_OFFSET_PATTERN.sub(r"\1\2:\3", text)
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def require_timestamp(value: object) -> datetime:
    """Read a timestamp a content file has to state.

    Only the notation differs from what `datetime` accepts (`2026-10-09 12:00:00 +0000`
    is not ISO), so a value that is not a timestamp at all is refused rather than
    blanked, and the message quotes what was written.

    Args:
        value: The raw value, which may be a string or a value already parsed.

    Returns:
        datetime: The timestamp.

    Raises:
        ValueError: When the value is not a timestamp.
    """
    if isinstance(value, datetime):
        return value

    parsed = parse_timestamp(value)
    if parsed is None:
        raise ValueError(f"{value!r} is not a timestamp, write one as `2026-10-09 12:00:00 +0000`")
    return parsed


def _optional_timestamp(value: object) -> datetime | None:
    """Read a timestamp a content file may leave out."""
    return None if value is None else require_timestamp(value)


# Types shared by the news and changelog models. A content file is validated against
# the schema of its repository before it is pushed, so these read it strictly: a blank
# title or a date written as prose is a mistake to report, not a value to repair.
Timestamp = Annotated[datetime, BeforeValidator(require_timestamp)]
OptionalTimestamp = Annotated[datetime | None, BeforeValidator(_optional_timestamp)]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def timestamp_to_iso(timestamp: datetime) -> str:
    """Format a timestamp for the metadata cache.

    Args:
        timestamp: The timestamp to format.

    Returns:
        str: The timestamp as an ISO 8601 string, in UTC.
    """
    return timestamp.astimezone(UTC).isoformat()


def describe_errors(error: ValueError) -> str:
    """Render the reasons a content file was refused, on one line each.

    The content repository validates its files before they are pushed, so a file that
    reaches this point broken is a mistake somebody has to fix. The message names the
    fields it broke on, the way the editor of the repository would.

    Args:
        error: The error raised while reading the file.

    Returns:
        str: One `path: reason` per refusal, or the message of a plain error.
    """
    if not isinstance(error, ValidationError):
        return str(error)

    return "; ".join(
        f"{'.'.join(str(part) for part in details['loc']) or '<root>'}: {details['msg']}" for details in error.errors()
    )


async def read_metadata(kind: str, sha: str) -> dict[str, Any] | None:
    """Read the parsed metadata of a file, as it was left by a previous parse.

    Args:
        kind: Kind of content the file holds, e.g. `news`.
        sha: Object ID of the file, which changes whenever its content does.

    Returns:
        dict[str, Any] | None: The cached metadata, or `None` when it was never parsed.
    """
    if not sha:
        return None

    cached = await redis_client.get(f"{CONTENT_CACHE_PREFIX}:meta:{kind}:{sha}")
    if cached is None:
        return None

    try:
        metadata = json.loads(cached)
    except ValueError:
        return None

    return metadata if isinstance(metadata, dict) else None


async def write_metadata(kind: str, sha: str, metadata: dict[str, Any]) -> None:
    """Remember the parsed metadata of a file.

    Args:
        kind: Kind of content the file holds, e.g. `news`.
        sha: Object ID of the file the metadata was parsed from.
        metadata: The metadata to store.
    """
    if not sha:
        return

    await redis_client.set(
        f"{CONTENT_CACHE_PREFIX}:meta:{kind}:{sha}",
        json.dumps(metadata),
        ex=META_CACHE_EXPIRE_SECONDS,
    )


class ContentIndex[T]:
    """A parsed snapshot of a content directory, refreshed behind the request.

    Attributes:
        _load: Callable building the snapshot from the repository.
        _expire: Seconds a snapshot is served before it is refreshed.
        _retry: Seconds to wait before retrying a repository that just failed.
    """

    def __init__(self, load: Callable[[], Awaitable[T]], expire: float, retry: float = 60.0) -> None:
        self._load = load
        self._expire = expire
        self._retry = retry
        self._value: T | None = None
        self._error: Exception | None = None
        self._refresh_at = 0.0
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    async def get(self) -> T:
        """Return the snapshot, building or refreshing it as needed.

        Returns:
            T: The parsed snapshot.

        Raises:
            RequestError: When the snapshot was never built and cannot be built now.
        """
        if self._value is not None:
            if time.monotonic() < self._refresh_at:
                return self._value
            self._refresh_in_background()
            return self._value

        await self._reload()
        assert self._value is not None
        return self._value

    async def warm(self) -> None:
        """Build the snapshot ahead of the first request.

        Meant to run as a background task, as it waits on the repository.
        """
        if self._value is not None:
            return
        try:
            await self._reload()
        except Exception as e:
            # The first request retries once the backoff has elapsed, so warming is best effort.
            logger.warning(f"Could not build the content index ahead of the first request: {e}")

    async def close(self) -> None:
        """Stop a refresh that is still running."""
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            # A refresh only updates a snapshot, so neither its failure nor its cancellation matters.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def _refresh_in_background(self) -> None:
        """Refresh an expired snapshot without making a request wait for it."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._reload())

    async def _reload(self) -> None:
        """Rebuild the snapshot, keeping the previous one when the build fails."""
        async with self._lock:
            # Another request may have built or refreshed the snapshot while this one waited.
            if time.monotonic() < self._refresh_at:
                if self._value is not None:
                    return
                if self._error is not None:
                    # A repository that just failed is not asked again until the backoff elapses.
                    raise self._error

            try:
                value = await self._load()
            except Exception as e:
                logger.warning(f"Failed to refresh the content index: {e}")
                self._error = e
                self._refresh_at = time.monotonic() + self._retry
                if self._value is None:
                    raise
                return

            self._value = value
            self._error = None
            self._refresh_at = time.monotonic() + self._expire


async def gather_limited[T](coroutines: list[Awaitable[T]], limit: int = 8) -> list[T]:
    """Run awaits concurrently, with a bound on how many are in flight.

    Args:
        coroutines: The awaits to run.
        limit: Maximum number of awaits running at the same time.

    Returns:
        list[T]: The results, in the order of `coroutines`.
    """
    semaphore = asyncio.Semaphore(limit)

    async def run(coroutine: Awaitable[T]) -> T:
        async with semaphore:
            return await coroutine

    return list(await asyncio.gather(*(run(coroutine) for coroutine in coroutines)))
