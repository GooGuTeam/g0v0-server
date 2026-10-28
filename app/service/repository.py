"""Reads a content repository over HTTP, caching what it reads in Redis.

The wiki, the news and the changelog are all served out of Git repositories: the
files are read from the raw host (`raw.githubusercontent.com` or a mirror of it),
and directories are listed through the repository API. This module holds that
client, the path helpers that keep a request inside the repository, and the flat
front matter parser shared by the article formats.

Nothing here knows what the files mean; the services built on top decide that.
"""

import asyncio
from dataclasses import asdict, dataclass
import json
import re
from typing import Any, cast
from urllib.parse import quote

from app.dependencies.database import redis_binary_client, redis_client
from app.log import log
from app.models.error import ErrorType, RequestError

import httpx

logger = log("Repository")

# Default prefix of the cache keys, so that two repositories served by the same
# server never read each other's entries. Services pass their own.
DEFAULT_CACHE_PREFIX = "g0v0:repository:v1"

# Stored in place of the media type when the repository has no such asset, so that a broken image
# link is not looked up again on every request. Media types always contain a slash, so it cannot
# collide with a real one.
MISSING_MEDIA_TYPE = "missing"

_FRONT_MATTER_PATTERN = re.compile(r"\A---[ \t]*\r?\n(?P<body>.*?)(?:\r?\n)?---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


@dataclass(frozen=True)
class RepositoryEntry:
    """An entry of a repository directory.

    Attributes:
        name: File or directory name, without any parent directory.
        path: Repository relative path of the entry.
        kind: `file`, `dir`, or `other` for anything the API reports differently
            (submodules and symlinks).
        sha: Object ID of the entry, which changes whenever its content does.
    """

    name: str
    path: str
    kind: str
    sha: str


def is_safe_repo_path(path: str) -> bool:
    """Check that a repository path cannot escape the repository.

    Args:
        path: The repository relative path to validate.

    Returns:
        bool: Whether the path is a plain, relative path.
    """
    if not path:
        return False
    return all(segment not in ("", ".", "..") for segment in path.split("/"))


def quote_repo_path(path: str) -> str:
    """Percent-encode a repository path so that it can be used inside a URL.

    Args:
        path: The repository relative path to encode.

    Returns:
        str: The encoded path, slashes preserved.
    """
    return "/".join(quote(segment, safe="") for segment in path.split("/"))


def join_repo_path(*parts: str) -> str:
    """Join repository path segments, dropping the empty ones.

    Args:
        parts: The segments to join.

    Returns:
        str: The joined path.
    """
    return "/".join(part.strip("/") for part in parts if part and part.strip("/"))


def split_front_matter(markdown: str) -> tuple[dict[str, Any], str]:
    """Split the leading YAML front matter off a Markdown document.

    Only the flat scalars, inline lists and block lists used by articles are
    parsed, so no YAML dependency is needed. Nested blocks are ignored.

    Args:
        markdown: The Markdown document.

    Returns:
        tuple[dict[str, Any], str]: The parsed front matter and the remaining body.
    """
    match = _FRONT_MATTER_PATTERN.match(markdown.lstrip("\ufeff"))
    if match is None:
        return {}, markdown
    return parse_front_matter(match.group("body")), markdown[match.end() :]


def parse_front_matter(text: str) -> dict[str, Any]:
    """Parse the flat subset of a YAML front matter block.

    Args:
        text: The front matter content, without the `---` delimiters.

    Returns:
        dict[str, Any]: The parsed keys. Nested blocks are ignored, so no YAML
            dependency is needed.
    """
    result: dict[str, Any] = {}

    for line in text.splitlines():
        content = line.strip()
        if not content or content.startswith("#") or line[0] in " \t":
            continue

        key, separator, value = line.partition(":")
        if not separator or not value.strip():
            continue

        result[key.strip()] = parse_scalar(value)

    return result


def parse_scalar(value: str) -> Any:
    """Parse a single YAML scalar or inline list.

    Only the shapes the content repositories use are recognised: quoted strings, inline
    lists, booleans, nulls, and integers. A bare number is an integer, as YAML reads it,
    which is what lets a front matter declare an identifier; everything else stays text,
    including dates, which are parsed by the model that expects one.

    Args:
        value: The raw scalar text.

    Returns:
        Any: The parsed value.
    """
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [parse_scalar(part) for part in inner.split(",")] if inner else []
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    if value.lower() in ("null", "~"):
        return None
    if value.lstrip("-").isdigit():
        return int(value)
    return value


class Repository:
    """Reads a Git repository over HTTP, caching what it reads in Redis.

    Attributes:
        repository: Repository slug, e.g. `ppy/osu-wiki`.
        branch: Branch the files are read from.
        listing_base_url: Base URL of the repository API; empty disables listing.
    """

    def __init__(
        self,
        repository: str,
        branch: str,
        raw_base_url: str,
        *,
        listing_base_url: str = "",
        token: str = "",
        timeout: float = 10.0,
        cache_expire: int = 300,
        resource_cache_expire: int = 3600,
        cache_prefix: str = DEFAULT_CACHE_PREFIX,
        user_agent: str = "g0v0-server",
    ) -> None:
        self.repository = repository
        self.branch = branch
        self.raw_url_base = f"{raw_base_url.rstrip('/')}/{repository}/{branch}"
        self.listing_base_url = listing_base_url.rstrip("/")
        self.cache_expire = cache_expire
        self.resource_cache_expire = resource_cache_expire
        self.cache_prefix = cache_prefix
        self._token = token
        self._user_agent = user_agent
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def close(self) -> None:
        """Release the shared HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def listing_enabled(self) -> bool:
        """Whether directories of the repository can be listed."""
        return bool(self.listing_base_url)

    @property
    def _headers(self) -> dict[str, str]:
        """Headers every request to the repository is sent with."""
        headers = {"User-Agent": self._user_agent}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _http(self) -> httpx.AsyncClient:
        """Return the shared HTTP client, creating it on first use."""
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        timeout=self._timeout,
                        follow_redirects=True,
                        headers=self._headers,
                    )
        assert self._client is not None
        return self._client

    def url(self, file_path: str) -> str:
        """Build the raw URL of a file in the repository."""
        return f"{self.raw_url_base}/{quote_repo_path(file_path)}"

    def _cache_key(self, file_path: str) -> str:
        return f"{self.cache_prefix}:{self.repository}@{self.branch}:{file_path}"

    def _listing_cache_key(self, directory: str) -> str:
        return f"{self.cache_prefix}:listing:{self.repository}@{self.branch}:{directory}"

    def _asset_cache_key(self, file_path: str) -> str:
        """Build the cache key holding the bytes of a binary asset."""
        return f"{self.cache_prefix}:asset:{self.repository}@{self.branch}:{file_path}"

    async def fetch_text(self, file_path: str) -> str | None:
        """Fetch a text file, caching both hits and misses.

        Args:
            file_path: Repository relative path of the file.

        Returns:
            str | None: The file content, or `None` when the repository has no such file.

        Raises:
            RequestError: When the repository cannot be reached.
        """
        if not is_safe_repo_path(file_path):
            return None

        cache_key = self._cache_key(file_path)
        cached = await redis_client.get(cache_key)
        if cached is not None:
            payload = json.loads(cached)
            return payload["content"] if payload["found"] else None

        client = await self._http()
        try:
            response = await client.get(self.url(file_path))
        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch {self.repository}:{file_path}: {e}")
            raise RequestError(ErrorType.INTERNAL, status_code=503) from e

        if response.status_code == 404:
            logger.debug(f"{self.repository}:{file_path} does not exist")
            await redis_client.set(cache_key, json.dumps({"found": False, "content": ""}), ex=self.cache_expire)
            return None

        if response.status_code != 200:
            logger.warning(f"Unexpected status {response.status_code} when fetching {self.repository}:{file_path}")
            raise RequestError(ErrorType.INTERNAL, status_code=503)

        content = response.text
        await redis_client.set(cache_key, json.dumps({"found": True, "content": content}), ex=self.cache_expire)
        return content

    async def fetch_binary(self, file_path: str) -> tuple[bytes, str] | None:
        """Fetch a binary asset from the repository, caching it in Redis.

        Args:
            file_path: Repository relative path of the asset.

        Returns:
            tuple[bytes, str] | None: The content and its media type, or `None` when missing.

        Raises:
            RequestError: When the repository cannot be reached.
        """
        if not is_safe_repo_path(file_path):
            return None

        cache_key = self._asset_cache_key(file_path)
        media_type_key = f"{cache_key}:media-type"

        # The text database is opened with response decoding, so this is a str at runtime.
        media_type = cast(str | None, await redis_client.get(media_type_key))
        if media_type is not None:
            if media_type == MISSING_MEDIA_TYPE:
                return None

            # The binary database is opened without response decoding, so this is bytes at runtime.
            content = cast(bytes | None, await redis_binary_client.get(cache_key))
            # The two databases are evicted independently, so the media type surviving on its
            # own only means the bytes are gone: fall through and fetch them again.
            if content is not None:
                return content, media_type

        client = await self._http()
        try:
            response = await client.get(self.url(file_path))
        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch asset {self.repository}:{file_path}: {e}")
            raise RequestError(ErrorType.INTERNAL, status_code=503) from e

        if response.status_code == 404:
            logger.debug(f"Asset {self.repository}:{file_path} does not exist")
            await redis_client.set(media_type_key, MISSING_MEDIA_TYPE, ex=self.cache_expire)
            return None

        if response.status_code != 200:
            logger.warning(
                f"Unexpected status {response.status_code} when fetching asset {self.repository}:{file_path}"
            )
            raise RequestError(ErrorType.INTERNAL, status_code=503)

        media_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
        if not media_type.startswith("image/"):
            # The raw host answers with a Markdown file or an error page for paths that are
            # linked but not shipped.
            logger.warning(f"Asset {self.repository}:{file_path} is not an image (content type {media_type!r})")
            await redis_client.set(media_type_key, MISSING_MEDIA_TYPE, ex=self.cache_expire)
            return None

        content = response.content
        await redis_binary_client.set(cache_key, content, ex=self.resource_cache_expire)
        await redis_client.set(media_type_key, media_type, ex=self.resource_cache_expire)
        return content, media_type

    async def list_entries(self, directory: str) -> list[RepositoryEntry] | None:
        """List the entries of a repository directory.

        Listing needs the repository API rather than a raw file, which a mirror may
        not provide, so `None` means the listing is not available at all, while a
        repository that is merely unreachable raises like every other request.

        Args:
            directory: Repository relative directory, empty for the repository root.

        Returns:
            list[RepositoryEntry] | None: The entries of the directory, or `None` when
                the API is not configured or the directory does not exist.

        Raises:
            RequestError: When the repository cannot be listed.
        """
        directory = directory.strip("/")
        if not self.listing_enabled or (directory and not is_safe_repo_path(directory)):
            return None

        cache_key = self._listing_cache_key(directory)
        cached = await redis_client.get(cache_key)
        if cached is not None:
            return [RepositoryEntry(**entry) for entry in json.loads(cached)]

        client = await self._http()
        url = f"{self.listing_base_url}/repos/{self.repository}/contents/{quote_repo_path(directory)}"
        try:
            response = await client.get(url, params={"ref": self.branch})
        except httpx.HTTPError as e:
            logger.warning(f"Failed to list {self.repository}:{directory}: {e}")
            raise RequestError(ErrorType.INTERNAL, status_code=503) from e

        if response.status_code == 404:
            logger.debug(f"{self.repository}:{directory} does not exist")
            return None

        if response.status_code != 200:
            logger.warning(f"Unexpected status {response.status_code} when listing {self.repository}:{directory}")
            raise RequestError(ErrorType.INTERNAL, status_code=503)

        try:
            payload = response.json()
        except ValueError as e:
            logger.warning(f"Unexpected response when listing {self.repository}:{directory}")
            raise RequestError(ErrorType.INTERNAL, status_code=503) from e

        if not isinstance(payload, list):
            logger.warning(f"Unexpected response when listing {self.repository}:{directory}")
            raise RequestError(ErrorType.INTERNAL, status_code=503)

        entries: list[RepositoryEntry] = []
        for entry in payload:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                continue

            name = entry["name"]
            path = entry.get("path")
            sha = entry.get("sha")
            kind = {"file": "file", "dir": "dir"}.get(str(entry.get("type")), "other")

            entries.append(
                RepositoryEntry(
                    name=name,
                    path=path if isinstance(path, str) and path else join_repo_path(directory, name),
                    kind=kind,
                    sha=sha if isinstance(sha, str) else "",
                )
            )

        payload_json = json.dumps([asdict(entry) for entry in entries])
        await redis_client.set(cache_key, payload_json, ex=self.resource_cache_expire)
        return entries

    async def list_directory(self, directory: str) -> list[str] | None:
        """List the files of a repository directory, or `None` when listing is unavailable.

        The listing only refines a response that can be served without it, so an
        unreachable repository costs the refinement rather than the response.

        Args:
            directory: Repository relative directory, empty for the repository root.

        Returns:
            list[str] | None: The file names in the directory, or `None` when the API
                is unavailable or the directory does not exist.
        """
        try:
            entries = await self.list_entries(directory)
        except RequestError:
            return None

        if entries is None:
            return None
        return [entry.name for entry in entries if entry.kind == "file"]
