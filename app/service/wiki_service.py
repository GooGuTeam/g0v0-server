"""Wiki service.

Serves the g0v0 documentation repository (<https://github.com/GooGuTeam/g0v0-docs>)
through an osu!web compatible wiki API, so that osu!(lazer)'s wiki overlay can
browse the documentation from inside the client.

The documentation repository is a VitePress site, which differs from
`ppy/osu-wiki` in ways that this module bridges:

* VitePress keeps the default locale (Simplified Chinese) at the repository root
  and every other locale in its own directory (`en/`), while the client always
  addresses a page as `wiki/{locale}/{path}` and `osu-wiki` uses
  `wiki/{path}/{locale}.md`. Locale directories are therefore mapped both ways
  here, and the wiki path itself never carries a locale.
* Links are written for a static site (`./page`, `../dir/page.md`, `/lazer/...`)
  while the client only navigates inside the wiki overlay when it is given a
  `{website}/wiki/...` URL. All in-site links are rewritten to locale-agnostic
  `/wiki/...` paths, and VitePress-only syntax is reduced to plain Markdown where
  the renderer has no use for it (Vue components) but kept otherwise: containers
  stay in VitePress form (`::: tip 提示`) so each renderer can style them.

Only Markdown is fetched at request time; responses are cached in Redis so the
repository is hit at most once per cache window and page.
"""

import asyncio
from dataclasses import dataclass
import json
import posixpath
import re
from typing import Any
from urllib.parse import quote, unquote, urlparse

from app.config import settings
from app.dependencies.database import redis_client
from app.log import log
from app.models.error import ErrorType, RequestError
from app.models.wiki import WikiPage, WikiPageLayout

import httpx

logger = log("Wiki")

# Page that the osu! client's `WikiOverlay` requests when it opens, mapped onto the documentation index.
MAIN_PAGE_PATH = "Main_page"

# The VitePress directory index page (`index.md`).
INDEX_PAGE_PATH = "index"

# The site landing page uses the VitePress `home` layout and has an empty body, so the page the
# client asks for on startup (`Main_page`) is replaced with the documentation entry page instead.
DOCUMENTATION_ENTRY_PATH = "lazer"

# VitePress public directory: `/images/a.png` and `/public/images/a.png` are both `public/images/a.png`.
VITEPRESS_PUBLIC_DIR = "public"

# Extensions used to tell a link to a documentation page from a link to an image asset.
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".avif", ".bmp"})

# `Main_page`, `main-page` and similar spellings name the same page, so separators are ignored.
_MAIN_PAGE_KEY = re.sub(r"[^a-z]", "", MAIN_PAGE_PATH.lower())

# The client sends culture codes (`zh-CN`, `en-GB`, ...) while `wiki_locales` is keyed by primary subtag.
LOCALE_ALIASES: dict[str, str] = {
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zh-sg": "zh",
    "zh-my": "zh",
    "en-us": "en",
    "en-gb": "en",
}

# Upon version changes, this could be used to expire old cache.
CACHE_KEY_PREFIX = "g0v0:wiki:v1"

_FRONT_MATTER_PATTERN = re.compile(r"\A---[ \t]*\r?\n(?P<body>.*?)(?:\r?\n)?---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_HEADING_PATTERN = re.compile(r"^#[ \t]+(?P<title>\S.*?)[ \t]*#*[ \t]*$", re.MULTILINE)
# Code blocks and inline code: links inside them must not be rewritten, so they are masked first.
_CODE_PATTERN = re.compile(r"```.*?```|~~~.*?~~~|``[^`]*``|`[^`\n]*`", re.DOTALL)
_LINK_PATTERN = re.compile(
    r"(?P<bang>!?)\[(?P<text>[^\]]*)\]\((?P<target>[^)\s]+)(?P<title>\s+(?:\"[^\"]*\"|'[^']*'))?\)"
)
_AUTOLINK_PATTERN = re.compile(r"<(?P<url>(?:https?:)?//[^>\s]+)>")
_SCRIPT_PATTERN = re.compile(r"<script\b[^>]*>.*?</script>[ \t]*\r?\n?", re.DOTALL | re.IGNORECASE)
_EXPRESSION_PATTERN = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_CONTAINER_PATTERN = re.compile(r"^(?P<fence>:{3,})[ \t]*(?P<type>[A-Za-z][A-Za-z0-9-]*)?[ \t]*(?P<title>.*)$")
# A closing fence written at the end of a text line (`...some text:::`); the text must not end with ':'.
_TRAILING_CONTAINER_PATTERN = re.compile(r"^(?P<body>.*[^:])[ \t]*(?P<fence>:{3,})$")


def normalize_locale_code(code: str) -> str:
    """Normalise a language code to lowercase, hyphen separated form.

    Args:
        code: The language code to normalise, e.g. `zh_CN`.

    Returns:
        str: The normalised code, e.g. `zh-cn`.
    """
    return code.strip().lower().replace("_", "-")


def resolve_locale(code: str | None) -> str | None:
    """Map a client supplied language code to a key of `wiki_locales`.

    Args:
        code: The language code to resolve, e.g. `zh-CN`.

    Returns:
        str | None: The configured locale, or `None` when the language is not documented.
    """
    if not code:
        return None

    normalized = normalize_locale_code(code)
    normalized = LOCALE_ALIASES.get(normalized, normalized)
    if normalized in settings.wiki_locales:
        return normalized

    # Also accept variants such as `zh-CN-Hans`.
    primary = normalized.split("-", 1)[0]
    return primary if primary in settings.wiki_locales else None


def fallback_locale() -> str:
    """Return the locale used when a page is not translated into the requested one.

    Returns:
        str: A key of `wiki_locales`.
    """
    resolved = resolve_locale(settings.wiki_fallback_locale)
    if resolved is not None:
        return resolved
    return next(iter(settings.wiki_locales), "")


def locale_directory(locale: str) -> str:
    """Return the directory holding a locale inside the documentation repository.

    Args:
        locale: A key of `wiki_locales`.

    Returns:
        str: The directory, empty for the repository root.
    """
    return str(settings.wiki_locales.get(locale, "")).strip("/")


def normalize_path(path: str) -> str | None:
    """Normalise a wiki path.

    Accepts anything the client, a Markdown link or the documentation site can
    produce (`Main_page`, `/lazer/deploy/x/`, `./a.md`, ...) and returns the
    locale-agnostic wiki path.

    Args:
        path: The path to normalise.

    Returns:
        str | None: The normalised path, or `None` when it escapes the documentation root.
    """
    path = unquote(path or "")
    path = path.split("#", 1)[0].split("?", 1)[0].replace("\\", "/")
    path = re.sub(r"/{2,}", "/", path).strip("/")
    path = posixpath.normpath(path) if path else ""
    path = path.strip("/")

    if path in ("", "."):
        return INDEX_PAGE_PATH
    # Once `normpath` has resolved inner `..` segments, a leading `..` means the link escaped the docs root.
    if path.startswith(".."):
        return None

    path = re.sub(r"\.(?:md|html?)$", "", path, flags=re.IGNORECASE).strip("/")
    if re.sub(r"[^a-z]", "", path.lower()) == _MAIN_PAGE_KEY:
        return INDEX_PAGE_PATH
    if path.endswith(f"/{INDEX_PAGE_PATH}"):
        path = path[: -len(INDEX_PAGE_PATH) - 1]

    return path or INDEX_PAGE_PATH


def file_candidates(directory: str, path: str) -> list[str]:
    """List the repository files a wiki path may be stored in.

    Args:
        directory: The locale directory inside the repository.
        path: The normalised, locale-agnostic wiki path.

    Returns:
        list[str]: Repository relative paths, in resolution order.
    """
    prefix = f"{directory}/" if directory else ""
    if path == INDEX_PAGE_PATH:
        # The client asks for `Main_page`: the repository may shadow the VitePress landing page with that file.
        return [f"{prefix}{MAIN_PAGE_PATH}.md", f"{prefix}{INDEX_PAGE_PATH}.md"]
    return [f"{prefix}{path}.md", f"{prefix}{path}/{INDEX_PAGE_PATH}.md"]


def is_safe_repo_path(path: str) -> bool:
    """Check that a repository path cannot escape the documentation repository.

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


def split_front_matter(markdown: str) -> tuple[dict[str, Any], str]:
    """Split the leading YAML front matter off a Markdown document.

    Only the flat scalars, inline lists and block lists used by the documentation
    are parsed, so no YAML dependency is needed. Nested blocks (such as a
    VitePress home page `hero:`) are ignored.

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
        dict[str, Any]: The parsed keys. Nested blocks (such as a VitePress home
            page `hero:`) are ignored, so no YAML dependency is needed.
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
    if path == INDEX_PAGE_PATH:
        return MAIN_PAGE_PATH.replace("_", " ")
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
        return heading.group("title").strip()

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
        WikiPageLayout: `main_page` only when the page explicitly opts in, otherwise
            a regular Markdown article.
    """
    layout = front_matter.get("layout")
    if isinstance(layout, str) and layout.strip().lower() == WikiPageLayout.MAIN_PAGE.value:
        return WikiPageLayout.MAIN_PAGE
    return WikiPageLayout.MARKDOWN_PAGE


def strip_vitepress_scripts(markdown: str) -> str:
    """Remove VitePress `<script setup>` blocks and template expressions.

    They are evaluated on the documentation site only and would otherwise show up
    as raw source in the client.

    Args:
        markdown: The Markdown document.

    Returns:
        str: The document without VitePress expressions.
    """
    return _EXPRESSION_PATTERN.sub("", _SCRIPT_PATTERN.sub("", markdown))


def repair_container_fences(markdown: str) -> str:
    """Keep VitePress containers intact, repairing the fences Markdig cannot pair up.

    Containers are passed through verbatim: `::: tip 提示` stays `::: tip 提示`, so
    each renderer decides how to style it (a web front-end can use the VitePress
    names directly). The only rewriting is what is needed for the container to
    parse at all:

    * Markdig opens a container on any line starting with `:::` and closes it on a
      bare fence, so a bare fence with nothing open is dropped instead of becoming
      an empty container that swallows the rest of the page.
    * A closing fence is emitted with the fence of the container it closes, which
      keeps nesting (`::::` around `:::`) paired whatever length the source used.
    * A closing fence glued to the end of a line (see
      `lazer/deploy/login-from-client.md`) is split off and treated as the closing
      fence it was meant to be; Markdig only closes on a fence that starts a line,
      so leaving it in place would let the container swallow the rest of the page.

    Args:
        markdown: The Markdown document.

    Returns:
        str: The document with its container fences repaired.
    """
    lines: list[str] = []
    # Fence of every open container, so a closer can be emitted with the length of
    # the fence it actually closes.
    opened: list[str] = []

    for line in markdown.splitlines():
        content = line.strip()
        stripped = line.rstrip()
        glued = None if content.startswith(":") else _TRAILING_CONTAINER_PATTERN.match(stripped)
        if glued is not None:
            lines.append(stripped[: glued.start("fence")].rstrip())
            lines.append(opened.pop() if opened else "")
            continue

        match = _CONTAINER_PATTERN.match(content)
        if match is None:
            lines.append(line)
            continue

        if match.group("type") or match.group("title").strip():
            opened.append(match.group("fence"))
            lines.append(line)
            continue

        lines.append(opened.pop() if opened else "")

    return "\n".join(lines)


@dataclass(frozen=True)
class WikiSource:
    """A page resolved inside the documentation repository.

    Attributes:
        locale: Language code of the returned content.
        path: Locale-agnostic wiki path of the page.
        file_path: Repository relative path of the Markdown file.
        base_path: Wiki path that relative links of the page resolve against.
        markdown: Raw Markdown of the page.
    """

    locale: str
    path: str
    file_path: str
    base_path: str
    markdown: str

    @property
    def asset_directory(self) -> str:
        """Repository directory that relative asset references resolve against."""
        return posixpath.dirname(self.file_path)


class WikiService:
    """Fetches and normalises documentation pages.

    Attributes:
        _client: Shared HTTP client used to reach the documentation repository.
    """

    def __init__(self) -> None:
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
                        timeout=settings.wiki_request_timeout_seconds,
                        follow_redirects=True,
                        headers={"User-Agent": "g0v0-server-wiki"},
                    )
        return self._client

    def _raw_url(self, file_path: str) -> str:
        """Build the repository URL of a file."""
        return f"{settings.wiki_raw_url_base}/{quote_repo_path(file_path)}"

    @staticmethod
    def _cache_key(file_path: str) -> str:
        return f"{CACHE_KEY_PREFIX}:{settings.wiki_repository}@{settings.wiki_branch}:{file_path}"

    async def _fetch_text(self, file_path: str) -> str | None:
        """Fetch a Markdown file, caching both hits and misses.

        Args:
            file_path: Repository relative path of the file.

        Returns:
            str | None: The file content, or `None` when the repository has no such file.

        Raises:
            RequestError: When the documentation repository cannot be reached.
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
            response = await client.get(self._raw_url(file_path))
        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch wiki file {file_path}: {e}")
            raise RequestError(ErrorType.INTERNAL, status_code=503) from e

        if response.status_code == 404:
            logger.debug(f"Wiki file {file_path} does not exist")
            await redis_client.set(
                cache_key, json.dumps({"found": False, "content": ""}), ex=settings.wiki_cache_expire_seconds
            )
            return None

        if response.status_code != 200:
            logger.warning(f"Unexpected status {response.status_code} when fetching wiki file {file_path}")
            raise RequestError(ErrorType.INTERNAL, status_code=503)

        content = response.text
        await redis_client.set(
            cache_key, json.dumps({"found": True, "content": content}), ex=settings.wiki_cache_expire_seconds
        )
        return content

    async def _fetch_binary(self, file_path: str) -> tuple[bytes, str] | None:
        """Fetch a binary asset from the documentation repository.

        Args:
            file_path: Repository relative path of the asset.

        Returns:
            tuple[bytes, str] | None: The content and its media type, or `None` when missing.

        Raises:
            RequestError: When the documentation repository cannot be reached.
        """
        if not is_safe_repo_path(file_path):
            return None

        client = await self._http()
        try:
            response = await client.get(self._raw_url(file_path))
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

    async def _resolve(self, locale: str | None, path: str, *, allow_fallback: bool = True) -> WikiSource | None:
        """Find the file backing a wiki path, applying locale fallback.

        Args:
            locale: The requested language code.
            path: The requested wiki path.
            allow_fallback: Whether an untranslated page may fall back to the configured
                fallback locale. Disabled when probing which locales a page exists in.

        Returns:
            WikiSource | None: The resolved page, or `None` when the path is not documented.
        """
        normalized = normalize_path(path)
        if normalized is None:
            return None

        # A path may carry a locale prefix (`/wiki/en/Foo`), which wins, matching how the client parses paths.
        head, _, tail = normalized.partition("/")
        prefix_locale = resolve_locale(head)
        if prefix_locale is not None:
            locale = prefix_locale
            normalized = tail or INDEX_PAGE_PATH

        requested = resolve_locale(locale) or fallback_locale()
        locales = [requested]
        if allow_fallback and fallback_locale() not in locales:
            locales.append(fallback_locale())

        for candidate_locale in locales:
            for file_path in file_candidates(locale_directory(candidate_locale), normalized):
                markdown = await self._fetch_text(file_path)
                if markdown is None:
                    continue

                is_index = file_path.endswith(f"/{INDEX_PAGE_PATH}.md") or file_path == f"{INDEX_PAGE_PATH}.md"
                if normalized == INDEX_PAGE_PATH:
                    base_path = ""
                elif is_index:
                    base_path = normalized
                else:
                    base_path = posixpath.dirname(normalized)

                return WikiSource(
                    locale=candidate_locale,
                    path=normalized,
                    file_path=file_path,
                    base_path=base_path,
                    markdown=markdown,
                )

        return None

    async def _parent_title(self, source: WikiSource) -> str | None:
        """Resolve the title of the parent page, when there is one.

        Args:
            source: The resolved page.

        Returns:
            str | None: The parent title, or `None` when the group has no landing page.
        """
        parent_path = source.path.rpartition("/")[0]
        if not parent_path:
            return None

        parent = await self._resolve(source.locale, parent_path)
        if parent is None:
            return None

        return page_title(parent.markdown, parent.path)

    async def _available_locales(self, path: str) -> list[str]:
        """List the locales a page is translated into.

        Args:
            path: The locale-agnostic wiki path.

        Returns:
            list[str]: The locales holding a translation of the page.
        """
        results = await asyncio.gather(
            *(self._resolve(locale, path, allow_fallback=False) for locale in settings.wiki_locales)
        )
        return sorted(
            locale for locale, source in zip(settings.wiki_locales, results, strict=True) if source is not None
        )

    async def _landing_page_fallback(self, source: WikiSource) -> WikiSource | None:
        """Replace an empty site landing page with the documentation entry page.

        Args:
            source: The resolved page.

        Returns:
            WikiSource | None: The entry page, or `None` when no substitution is needed.
        """
        _, body = split_front_matter(source.markdown)
        if body.strip() or source.path != INDEX_PAGE_PATH:
            return None

        entry = await self._resolve(source.locale, DOCUMENTATION_ENTRY_PATH)
        if entry is None or entry.path == source.path:
            return None

        logger.debug(f"Wiki landing page is empty, serving {entry.path} instead")
        return entry

    async def get_page(self, locale: str | None, path: str) -> WikiPage | None:
        """Resolve a wiki page.

        Args:
            locale: The requested language code.
            path: The requested wiki path.

        Returns:
            WikiPage | None: The page, or `None` when it is not documented.
        """
        source = await self._resolve(locale, path)
        if source is None:
            return None

        if (landing_page := await self._landing_page_fallback(source)) is not None:
            source = landing_page

        front_matter, body = split_front_matter(source.markdown)
        subtitle, available_locales = await asyncio.gather(
            self._parent_title(source),
            self._available_locales(source.path),
        )

        return WikiPage(
            locale=source.locale,
            markdown=self.rewrite_markdown(source),
            path=source.path,
            title=title_from_markdown(front_matter, body, source.path),
            available_locales=available_locales,
            layout=page_layout(front_matter),
            subtitle=subtitle,
            tags=page_tags(front_matter),
        )

    async def get_image(self, path: str) -> tuple[bytes, str] | None:
        """Fetch an image referenced by the documentation.

        Args:
            path: Repository relative path of the image. Paths relative to the
                VitePress public directory (`images/a.png`) are accepted as well.

        Returns:
            tuple[bytes, str] | None: The image content and its media type, or `None`
                when it cannot be found.
        """
        normalized = normalize_asset_path(path)
        if normalized is None:
            return None

        candidates = [normalized]
        if not normalized.startswith(f"{VITEPRESS_PUBLIC_DIR}/"):
            candidates.append(f"{VITEPRESS_PUBLIC_DIR}/{normalized}")

        for candidate in candidates:
            asset = await self._fetch_binary(candidate)
            if asset is not None:
                return asset

        return None

    def rewrite_markdown(self, source: WikiSource) -> str:
        """Rewrite a page so that it renders correctly in the osu! client.

        Code is masked so that only real links are touched, then in-site links are
        pointed at the wiki API, images at the wiki asset endpoint, Vue-only syntax
        is dropped, and container fences are repaired without changing what the
        containers say.

        Args:
            source: The resolved page.

        Returns:
            str: The Markdown to serve to the client.
        """
        stash: list[str] = []

        def mask_code(match: re.Match[str]) -> str:
            stash.append(match.group(0))
            return f"\x00{len(stash) - 1}\x00"

        markdown = _CODE_PATTERN.sub(mask_code, source.markdown)
        markdown = _LINK_PATTERN.sub(lambda match: self._rewrite_link(match, source), markdown)
        markdown = _AUTOLINK_PATTERN.sub(lambda match: self._rewrite_autolink(match), markdown)
        markdown = strip_vitepress_scripts(markdown)
        markdown = repair_container_fences(markdown)

        for index, code in enumerate(stash):
            markdown = markdown.replace(f"\x00{index}\x00", code)

        return markdown

    def _rewrite_link(self, match: re.Match[str], source: WikiSource) -> str:
        """Rewrite a single inline Markdown link or image."""
        target = match.group("target")
        rewritten = self._rewrite_target(target, source)
        if rewritten == target:
            return match.group(0)
        title = match.group("title") or ""
        return f"{match.group('bang')}[{match.group('text')}]({rewritten}{title})"

    def _rewrite_autolink(self, match: re.Match[str]) -> str:
        """Rewrite an autolink pointing at the documentation site."""
        url = match.group("url")
        path = documentation_site_path(url)
        if path is None:
            return match.group(0)

        normalized = normalize_path(path)
        if normalized is None:
            return match.group(0)

        return f"[{url}]({wiki_url(normalized, None)})"

    def _rewrite_target(self, target: str, source: WikiSource) -> str:
        """Rewrite an in-site link target, leaving external targets untouched."""
        raw = target.strip()
        if not raw or raw.startswith(("#", "mailto:", "tel:", "data:")):
            return target

        fragment = ""
        if "#" in raw:
            raw, _, anchor = raw.partition("#")
            fragment = f"#{anchor}"

        if raw.startswith(("http://", "https://", "//")):
            path = documentation_site_path(raw)
            if path is None:
                return target
            raw = path

        if raw.lower().endswith(tuple(IMAGE_EXTENSIONS)):
            return self._rewrite_image(raw, source, target)

        return self._rewrite_page(raw, source, fragment, target)

    def _rewrite_page(self, raw: str, source: WikiSource, fragment: str, original: str) -> str:
        """Point a page link at the wiki API."""
        if raw.startswith("/wiki/"):
            return original

        # Relative path to the site root (VitePress style)
        if raw.startswith("/"):
            resolved = normalize_path(raw)
        elif raw:
            resolved = normalize_path(posixpath.join(source.base_path, raw))
        else:
            resolved = None

        if resolved is None:
            return original

        # A link may point at another locale directory explicitly (e.g. `/en/lazer` from a Chinese page).
        head, _, tail = resolved.partition("/")
        target_locale = resolve_locale(head)
        locale_override: str | None = None
        if target_locale is not None:
            resolved = tail or INDEX_PAGE_PATH
            if target_locale != source.locale:
                locale_override = target_locale

        return f"{wiki_url(resolved, locale_override)}{fragment}"

    def _rewrite_image(self, raw: str, source: WikiSource, original: str) -> str:
        """Point an image at the wiki asset endpoint."""
        if raw.startswith("/wiki-images/"):
            return original

        if raw.startswith("/"):
            # Root relative assets live in the VitePress public directory.
            repo_path = raw.lstrip("/")
            if not repo_path.startswith(f"{VITEPRESS_PUBLIC_DIR}/"):
                repo_path = f"{VITEPRESS_PUBLIC_DIR}/{repo_path}"
        else:
            repo_path = posixpath.normpath(posixpath.join(source.asset_directory, raw))

        if not is_safe_repo_path(repo_path):
            return original

        return f"{settings.wiki_image_url_base}/{quote_repo_path(repo_path)}"


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


def wiki_url(path: str, locale: str | None) -> str:
    """Build a root relative wiki URL the client navigates to in its wiki overlay.

    Args:
        path: The locale-agnostic wiki path.
        locale: The locale to force, omitted to follow the client language.

    Returns:
        str: The wiki URL.
    """
    prefix = f"/{locale}" if locale else ""
    return f"/wiki{prefix}/{path}"


def documentation_site_path(url: str) -> str | None:
    """Return the path of a URL when it points at the documentation site.

    Args:
        url: The absolute or protocol relative URL to inspect.

    Returns:
        str | None: The path on the documentation site, or `None` for external URLs.
    """
    parsed = urlparse(url if not url.startswith("//") else f"https:{url}")
    if parsed.netloc.lower() != urlparse(str(settings.wiki_docs_url)).netloc.lower():
        return None
    return parsed.path or "/"


wiki_service = WikiService()


async def close_wiki_service() -> None:
    """Release the resources held by the wiki service."""
    await wiki_service.close()
