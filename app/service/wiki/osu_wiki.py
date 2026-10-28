"""The osu! wiki backend.

Serves `ppy/osu-wiki` (or a clone of it) the way osu-web does: a page is one file
per language, `{content_root}/{path}/{locale}.md`, and its Markdown is passed on
untouched. The wiki is written for the client already (`/wiki/...` links,
`/wiki/shared/...` images), so the only work left is resolving the locale, the
page metadata, the image route and the redirects declared next to the articles.
"""

import asyncio
import contextlib
import re
import time

from app.config import settings
from app.log import log
from app.models.error import RequestError
from app.models.wiki import WikiPage, WikiPageLayout
from app.service.repository import Repository, join_repo_path, split_front_matter

from .base import (
    WikiSource,
    locale_candidates,
    normalize_asset_path,
    normalize_path,
    page_layout,
    page_tags,
    page_title,
    title_from_markdown,
    title_from_path,
)

logger = log("Wiki")

# Prefix of everything the wiki caches, so that the news and changelog, which read their own
# repository through the same client, never read a wiki entry by accident.
WIKI_CACHE_PREFIX = "g0v0:wiki:v2"

# Page the client asks for when it opens the wiki overlay without a path.
DEFAULT_PAGE_PATH = "Main_page"

# Redirect map osu-web reads from the repository, next to the articles.
REDIRECT_FILE_NAME = "redirect.yaml"

# Redirects followed for a single request. osu-web answers with one redirect per
# request and lets the client walk the rest, so this only bounds that walk.
MAX_REDIRECT_HOPS = 5

# How long a failed read of the redirect map is remembered, so that an unreachable
# repository is not retried for every page that misses.
REDIRECT_RETRY_SECONDS = 60

# A `key: value` line separates on the first colon followed by whitespace, which is
# also how YAML ends a plain key: `osu!:rules` stays in one piece.
_REDIRECT_SEPARATOR = re.compile(r":(?=\s|$)")

_REDIRECT_QUOTES = "\"'"


def redirect_key(path: str) -> str:
    """Normalise a path the way it is keyed in the redirect map.

    This mirrors `WikiRedirect::normalizePath` in osu-web: the map is lower case and
    uses underscores, whatever spelling the request used.

    Args:
        path: The wiki path.

    Returns:
        str: The lookup key of the path.
    """
    return path.replace(" ", "_").lower()


def parse_redirects(text: str) -> dict[str, str]:
    """Parse the redirect map kept in `redirect.yaml`.

    The file is a flat YAML mapping of old path to current path, so it is read line
    by line instead of pulling in a YAML parser for one file. Comments and blank
    lines are skipped; `#` is only a comment when it starts the line, because
    targets carry anchors (`Article_styling_criteria#images`).

    Args:
        text: Content of the redirect map.

    Returns:
        dict[str, str]: Lookup key to the path it redirects to, as written in the file.
    """
    redirects: dict[str, str] = {}

    for line in text.lstrip("\ufeff").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue

        separator = _REDIRECT_SEPARATOR.search(entry)
        if separator is None:
            continue

        key = entry[: separator.start()].strip().strip(_REDIRECT_QUOTES)
        target = entry[separator.end() :].strip().strip(_REDIRECT_QUOTES)
        if key and target:
            redirects[redirect_key(key)] = target

    return redirects


class OsuWikiBackend:
    """Serves an osu-wiki repository with the semantics of osu-web.

    Attributes:
        _repository: Client used to read the wiki repository.
    """

    def __init__(self) -> None:
        self._repository = Repository(
            settings.wiki_repository,
            settings.wiki_branch,
            settings.wiki_raw_base_url,
            listing_base_url=settings.wiki_listing_base_url,
            token=settings.wiki_github_token,
            timeout=settings.wiki_request_timeout_seconds,
            cache_expire=settings.wiki_cache_expire_seconds,
            resource_cache_expire=settings.wiki_resource_cache_expire_seconds,
            cache_prefix=WIKI_CACHE_PREFIX,
            user_agent="g0v0-server-wiki",
        )
        # The redirect map is small but read on every miss, so it is parsed once per process.
        self._redirects_map: dict[str, str] = {}
        self._redirects_loaded = False
        self._redirects_refresh_at = 0.0
        self._redirects_lock = asyncio.Lock()
        self._redirects_task: asyncio.Task[None] | None = None

    async def close(self) -> None:
        """Release the resources held by the backend."""
        task, self._redirects_task = self._redirects_task, None
        if task is not None and not task.done():
            task.cancel()
            # A refresh is only a cache update, so neither its failure nor its cancellation matters.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self._repository.close()

    @property
    def _content_root(self) -> str:
        """Repository directory holding the articles."""
        return settings.wiki_content_root.strip("/")

    def _file_path(self, path: str, locale: str) -> str:
        """Build the repository path of a page in a language."""
        return join_repo_path(self._content_root, path, f"{locale}.md")

    async def _resolve(self, locale: str | None, path: str) -> WikiSource | None:
        """Find the file backing a wiki path, applying locale fallback.

        Args:
            locale: The requested language code.
            path: The requested wiki path.

        Returns:
            WikiSource | None: The resolved page, or `None` when it is not documented.
        """
        normalized = normalize_path(path, DEFAULT_PAGE_PATH)
        if normalized is None:
            return None

        for candidate in locale_candidates(locale, settings.wiki_fallback_locale):
            file_path = self._file_path(normalized, candidate)
            markdown = await self._repository.fetch_text(file_path)
            if markdown is not None:
                return WikiSource(locale=candidate, path=normalized, file_path=file_path, markdown=markdown)

        return None

    async def _redirects(self) -> dict[str, str]:
        """Return the redirect map, from the in-process copy whenever possible.

        The map is only consulted for paths that have no page of their own, so it is
        kept parsed in memory rather than read and parsed again for every miss.

        An expired copy is handed out as it is and refreshed in the background, so a
        slow or unreachable repository never delays a request. A refresh that fails
        keeps the previous copy and backs off before trying again.

        Returns:
            dict[str, str]: Lookup key to the path it redirects to; empty when the
                repository has no map or it could not be read yet.
        """
        if time.monotonic() < self._redirects_refresh_at:
            return self._redirects_map
        if self._redirects_loaded:
            self._refresh_redirects_in_background()
            return self._redirects_map

        await self._load_redirects()
        return self._redirects_map

    async def _load_redirects(self) -> None:
        """Read and parse the redirect map, replacing the in-process copy."""
        async with self._redirects_lock:
            # Another request may have refreshed the map while this one waited for the lock.
            if time.monotonic() < self._redirects_refresh_at:
                return

            try:
                text = await self._repository.fetch_text(join_repo_path(self._content_root, REDIRECT_FILE_NAME))
            except RequestError as e:
                # The page being served is unaffected, so a broken map only costs redirects.
                logger.warning(f"Failed to load wiki redirects: {e}")
                # Keep the previous copy, and do not retry the repository for every miss.
                self._redirects_refresh_at = time.monotonic() + REDIRECT_RETRY_SECONDS
                return

            self._redirects_map = parse_redirects(text) if text is not None else {}
            self._redirects_loaded = True
            self._redirects_refresh_at = time.monotonic() + settings.wiki_resource_cache_expire_seconds

    def _refresh_redirects_in_background(self) -> None:
        """Refresh an expired redirect map without making a request wait for it."""
        if self._redirects_task is not None and not self._redirects_task.done():
            return
        self._redirects_task = asyncio.create_task(self._load_redirects())

    async def warm(self) -> None:
        """Load the redirect map before the first request needs it.

        Called on startup so that the first unknown path does not pay for the
        download; the caller must not wait on it, as the repository may be slow or
        unreachable at boot.
        """
        if not self._redirects_loaded:
            await self._load_redirects()

    async def _resolve_redirect(self, locale: str | None, path: str) -> WikiSource | None:
        """Follow the redirect map for a path that has no page of its own.

        Args:
            locale: The requested language code.
            path: The requested wiki path.

        Returns:
            WikiSource | None: The page the path redirects to, or `None` when the path
                does not redirect or its target is not documented either.
        """
        normalized = normalize_path(path, DEFAULT_PAGE_PATH)
        if normalized is None:
            return None

        redirects = await self._redirects()
        seen = {redirect_key(normalized)}
        current = normalized

        for _ in range(MAX_REDIRECT_HOPS):
            target = redirects.get(redirect_key(current))
            if target is None:
                return None

            # Targets may point at a section (`Article_styling_criteria#images`), which the
            # API has no way to honour.
            target = target.split("#", 1)[0].strip().strip("/")
            key = redirect_key(target)
            # A target that loops back (`sitemap: Sitemap`) would otherwise be followed forever.
            if not target or key in seen:
                return None
            seen.add(key)

            source = await self._resolve(locale, target)
            if source is not None:
                return source
            current = target

        return None

    async def _parent_title(self, source: WikiSource) -> str | None:
        """Resolve the title of the parent page, when there is one.

        Args:
            source: The resolved page.

        Returns:
            str | None: The parent title, or `None` when the parent is not an article.
        """
        parent_path = source.path.rpartition("/")[0]
        if not parent_path:
            return None

        parent = await self._resolve(source.locale, parent_path)
        if parent is None:
            return None

        return page_title(parent.markdown, parent.path)

    async def _available_locales(self, source: WikiSource) -> list[str]:
        """List the languages a page is translated into.

        Args:
            source: The resolved page.

        Returns:
            list[str]: The languages with a file for the page, falling back to the
                served one when the repository cannot be listed.
        """
        names = await self._repository.list_directory(join_repo_path(self._content_root, source.path))
        if names is None:
            return [source.locale]

        locales = sorted({name[: -len(".md")] for name in names if name.endswith(".md") and len(name) > len(".md")})
        return locales or [source.locale]

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
            # osu-web answers with a redirect to the current location and lets the client
            # follow it; the client applies the `path` of the response (`WikiOverlay`), so
            # resolving it here saves a round-trip and keeps the request authenticated.
            source = await self._resolve_redirect(locale, path)
        if source is None:
            return None

        front_matter, body = split_front_matter(source.markdown)
        layout = page_layout(front_matter)
        declared_title = front_matter.get("title")
        if layout is WikiPageLayout.MAIN_PAGE:
            # A main page is a panel layout without a title heading of its own.
            title = declared_title.strip() if isinstance(declared_title, str) and declared_title.strip() else ""
            title = title or title_from_path(source.path)
        else:
            title = title_from_markdown(front_matter, body, source.path)

        subtitle, available_locales = await asyncio.gather(
            self._parent_title(source),
            self._available_locales(source),
        )

        return WikiPage(
            locale=source.locale,
            markdown=source.markdown,
            path=source.path,
            title=title,
            available_locales=available_locales,
            layout=layout,
            subtitle=subtitle,
            tags=page_tags(front_matter),
        )

    async def get_image(self, path: str) -> tuple[bytes, str] | None:
        """Fetch an image referenced by the wiki.

        The client rewrites `/wiki/...` image URLs into `/wiki/images/...`, so the
        requested path is relative to the wiki content root.

        Args:
            path: Wiki image path, e.g. `shared/mode/osu.png`.

        Returns:
            tuple[bytes, str] | None: The image content and its media type, or `None`
                when it cannot be found.
        """
        normalized = normalize_asset_path(path)
        if normalized is None:
            return None

        return await self._repository.fetch_binary(join_repo_path(self._content_root, normalized))
