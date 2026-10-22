"""Pieces shared by the wiki service.

The wiki API answers with the osu-web page shape, built from an osu-wiki
repository (`ppy/osu-wiki` and its clones): one Markdown file per language,
`{path}/{locale}.md`. This module holds what the service needs to read that
repository: the repository client (HTTP + Redis cache), the front matter helpers
and the resolved page source.
"""

import asyncio
from dataclasses import dataclass
import json
import posixpath
import re
from typing import Any, Protocol
from urllib.parse import quote, unquote

from app.dependencies.database import redis_client
from app.log import log
from app.models.error import ErrorType, RequestError
from app.models.wiki import WikiPage, WikiPageLayout

import httpx

logger = log("Wiki")

# Bump when the served Markdown or the response shape changes, so old cache entries expire.
CACHE_KEY_PREFIX = "g0v0:wiki:v2"

LISTING_CACHE_KEY_PREFIX = "g0v0:wiki-listing:v1"

# Extensions the image route is willing to serve.
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".avif", ".bmp"})

_FRONT_MATTER_PATTERN = re.compile(r"\A---[ \t]*\r?\n(?P<body>.*?)(?:\r?\n)?---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_HEADING_PATTERN = re.compile(r"^#[ \t]+(?P<title>\S.*?)[ \t]*#*[ \t]*$", re.MULTILINE)
_MARKDOWN_LINK_PATTERN = re.compile(r"\[(?P<text>[^\]]*)\]\([^)]*\)")


@dataclass(frozen=True)
class WikiSource:
    """A page resolved inside the repository.

    Attributes:
        locale: Language code of the served content.
        path: Locale-agnostic wiki path of the page.
        file_path: Repository relative path of the Markdown file.
        markdown: Raw Markdown of the page.
    """

    locale: str
    path: str
    file_path: str
    markdown: str


class WikiBackend(Protocol):
    """A repository layout that can answer wiki requests."""

    async def get_page(self, locale: str | None, path: str) -> WikiPage | None:
        """Resolve a wiki page.

        Args:
            locale: The requested language code.
            path: The requested wiki path.

        Returns:
            WikiPage | None: The page, or `None` when it is not documented.
        """
        ...

    async def get_image(self, path: str) -> tuple[bytes, str] | None:
        """Fetch an image referenced by the wiki.

        Args:
            path: The requested image path.

        Returns:
            tuple[bytes, str] | None: The image content and its media type, or `None`
                when it cannot be found.
        """
        ...

    async def warm(self) -> None:
        """Load anything the backend keeps in memory, ahead of the first request."""
        ...

    async def close(self) -> None:
        """Release the resources held by the backend."""
        ...


def normalize_locale_code(code: str) -> str:
    """Normalise a language code to lowercase, hyphen separated form.

    Args:
        code: The language code to normalise, e.g. `zh_CN`.

    Returns:
        str: The normalised code, e.g. `zh-cn`.
    """
    return code.strip().lower().replace("_", "-")


def locale_candidates(locale: str | None, fallback: str) -> list[str]:
    """List the language codes to try for a page, in order.

    A client asks with the culture code of its interface language, while
    repositories store the files under a language subtag, so the requested code is
    tried first, then its primary subtag, then the configured fallback.

    Args:
        locale: The requested language code, e.g. `zh-CN`.
        fallback: The language code to fall back to, e.g. `en`.

    Returns:
        list[str]: The codes to try, without duplicates.
    """
    requested = normalize_locale_code(locale or "") or normalize_locale_code(fallback)
    candidates = [requested] if requested else []
    primary = requested.split("-", 1)[0]
    if primary and primary not in candidates:
        candidates.append(primary)
    fallback_code = normalize_locale_code(fallback)
    if fallback_code and fallback_code not in candidates:
        candidates.append(fallback_code)
    return candidates


def normalize_path(path: str, default: str) -> str | None:
    """Normalise a wiki path.

    Accepts anything the client or a Markdown link can produce (`/Gameplay/Accuracy`,
    `./Accuracy.md`, ...) and returns the path itself, without slashes or extension.

    Args:
        path: The path to normalise.
        default: Path to return when the request carries none.

    Returns:
        str | None: The normalised path, or `None` when it escapes the repository root.
    """
    path = unquote(path or "")
    path = path.split("#", 1)[0].split("?", 1)[0].replace("\\", "/")
    path = re.sub(r"/{2,}", "/", path).strip("/")
    path = posixpath.normpath(path) if path else ""
    path = path.strip("/")

    if path in ("", "."):
        return default
    # Once `normpath` has resolved inner `..` segments, a leading `..` means the path escaped the root.
    if path.startswith(".."):
        return None

    return re.sub(r"\.(?:md|html?)$", "", path, flags=re.IGNORECASE).strip("/") or default


def normalize_asset_path(path: str) -> str | None:
    """Normalise a repository relative asset path.

    Args:
        path: The encoded path from the request.

    Returns:
        str | None: The normalised path, or `None` when it is not a usable image.
    """
    path = unquote(path or "").replace("\\", "/").split("#", 1)[0].split("?", 1)[0]
    path = re.sub(r"/{2,}", "/", path).strip("/")
    path = posixpath.normpath(path) if path else ""

    if not is_safe_repo_path(path) or not path.lower().endswith(tuple(IMAGE_EXTENSIONS)):
        return None

    return path


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

    Only the flat scalars, inline lists and block lists used by wiki articles are
    parsed, so no YAML dependency is needed. Nested blocks are ignored.

    Args:
        markdown: The Markdown document.

    Returns:
        tuple[dict[str, Any], str]: The parsed front matter and the remaining body.
    """
    match = _FRONT_MATTER_PATTERN.match(markdown)
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
    return value


def title_from_path(path: str) -> str:
    """Derive a page title from its wiki path.

    Args:
        path: The locale-agnostic wiki path.

    Returns:
        str: The derived title.
    """
    return path.rpartition("/")[2].replace("-", " ").replace("_", " ")


def title_from_markdown(front_matter: dict[str, Any], body: str, path: str) -> str:
    """Resolve the title of a page from its front matter, first heading or path.

    Args:
        front_matter: The parsed front matter of the page.
        body: The Markdown body of the page.
        path: The locale-agnostic wiki path.

    Returns:
        str: The resolved title.
    """
    title = front_matter.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()

    heading = _HEADING_PATTERN.search(body)
    if heading is not None:
        # Headings may carry a link (`# [Game client](/wiki/Client)`); the link text is the title.
        return _MARKDOWN_LINK_PATTERN.sub(r"\g<text>", heading.group("title")).strip()

    return title_from_path(path)


def page_title(markdown: str, path: str) -> str:
    """Resolve the title of a page.

    Args:
        markdown: The Markdown of the page.
        path: The locale-agnostic wiki path.

    Returns:
        str: The resolved title.
    """
    front_matter, body = split_front_matter(markdown)
    return title_from_markdown(front_matter, body, path)


def page_tags(front_matter: dict[str, Any]) -> list[str]:
    """Resolve the tags declared by a page.

    Args:
        front_matter: The parsed front matter of the page.

    Returns:
        list[str]: The declared tags.
    """
    tags = front_matter.get("tags")
    if isinstance(tags, list):
        return [str(tag) for tag in tags]
    if isinstance(tags, str) and tags.strip():
        return [tags.strip()]
    return []


def page_layout(front_matter: dict[str, Any]) -> WikiPageLayout:
    """Resolve the layout the client should render a page with.

    Args:
        front_matter: The parsed front matter of the page.

    Returns:
        WikiPageLayout: `main_page` only when the page asks for it, otherwise a
            regular Markdown article.
    """
    layout = front_matter.get("layout")
    if isinstance(layout, str) and layout.strip().lower() == WikiPageLayout.MAIN_PAGE.value:
        return WikiPageLayout.MAIN_PAGE
    return WikiPageLayout.MARKDOWN_PAGE


class Repository:
    """Reads a Markdown repository over HTTP, caching what it reads in Redis.

    Attributes:
        repository: Repository slug, e.g. `ppy/osu-wiki`.
        branch: Branch the files are read from.
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
        listing_cache_expire: int = 3600,
    ) -> None:
        self.repository = repository
        self.branch = branch
        self.raw_url_base = f"{raw_base_url.rstrip('/')}/{repository}/{branch}"
        self.listing_base_url = listing_base_url.rstrip("/")
        self.cache_expire = cache_expire
        self.listing_cache_expire = listing_cache_expire
        self._token = token
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def close(self) -> None:
        """Release the shared HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _http(self) -> httpx.AsyncClient:
        """Return the shared HTTP client, creating it on first use."""
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        timeout=self._timeout,
                        follow_redirects=True,
                        headers={"User-Agent": "g0v0-server-wiki"},
                    )
        return self._client

    def url(self, file_path: str) -> str:
        """Build the raw URL of a file in the repository."""
        return f"{self.raw_url_base}/{quote_repo_path(file_path)}"

    def _cache_key(self, file_path: str) -> str:
        return f"{CACHE_KEY_PREFIX}:{self.repository}@{self.branch}:{file_path}"

    async def fetch_text(self, file_path: str) -> str | None:
        """Fetch a Markdown file, caching both hits and misses.

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
            logger.warning(f"Failed to fetch wiki file {file_path}: {e}")
            raise RequestError(ErrorType.INTERNAL, status_code=503) from e

        if response.status_code == 404:
            logger.debug(f"Wiki file {file_path} does not exist")
            await redis_client.set(cache_key, json.dumps({"found": False, "content": ""}), ex=self.cache_expire)
            return None

        if response.status_code != 200:
            logger.warning(f"Unexpected status {response.status_code} when fetching wiki file {file_path}")
            raise RequestError(ErrorType.INTERNAL, status_code=503)

        content = response.text
        await redis_client.set(cache_key, json.dumps({"found": True, "content": content}), ex=self.cache_expire)
        return content

    async def fetch_binary(self, file_path: str) -> tuple[bytes, str] | None:
        """Fetch a binary asset from the repository.

        Args:
            file_path: Repository relative path of the asset.

        Returns:
            tuple[bytes, str] | None: The content and its media type, or `None` when missing.

        Raises:
            RequestError: When the repository cannot be reached.
        """
        if not is_safe_repo_path(file_path):
            return None

        client = await self._http()
        try:
            response = await client.get(self.url(file_path))
        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch wiki asset {file_path}: {e}")
            raise RequestError(ErrorType.INTERNAL, status_code=503) from e

        if response.status_code == 404:
            return None

        if response.status_code != 200:
            logger.warning(f"Unexpected status {response.status_code} when fetching wiki asset {file_path}")
            raise RequestError(ErrorType.INTERNAL, status_code=503)

        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
        if not content_type.startswith("image/"):
            logger.warning(f"Wiki asset {file_path} is not an image (content type {content_type!r})")
            return None

        return response.content, content_type

    async def list_directory(self, directory: str) -> list[str] | None:
        """List the repository files in a directory, or `None` when listing is unavailable.

        Listing needs the repository API rather than a raw file, which a mirror may
        not provide, so callers have to cope with `None`.

        Args:
            directory: Repository relative directory, empty for the repository root.

        Returns:
            list[str]: The file names in the directory, or `None` when the API is
                unavailable or the directory does not exist.
        """
        directory = directory.strip("/")
        if not self.listing_base_url or (directory and not is_safe_repo_path(directory)):
            return None

        cache_key = f"{LISTING_CACHE_KEY_PREFIX}:{self.repository}@{self.branch}:{directory}"
        cached = await redis_client.get(cache_key)
        if cached is not None:
            return json.loads(cached)

        client = await self._http()
        url = f"{self.listing_base_url}/repos/{self.repository}/contents/{quote_repo_path(directory)}"
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        try:
            response = await client.get(url, params={"ref": self.branch}, headers=headers)
        except httpx.HTTPError as e:
            logger.warning(f"Failed to list wiki directory {directory}: {e}")
            return None

        if response.status_code != 200:
            logger.warning(f"Unexpected status {response.status_code} when listing wiki directory {directory}")
            return None

        try:
            payload = response.json()
        except ValueError:
            logger.warning(f"Unexpected response when listing wiki directory {directory}")
            return None

        if not isinstance(payload, list):
            return None

        names = [
            entry["name"]
            for entry in payload
            if isinstance(entry, dict) and entry.get("type") == "file" and isinstance(entry.get("name"), str)
        ]
        await redis_client.set(cache_key, json.dumps(names), ex=self.listing_cache_expire)
        return names
