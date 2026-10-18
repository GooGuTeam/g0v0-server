"""The osu! wiki backend.

Serves `ppy/osu-wiki` (or a clone of it) the way osu-web does: a page is one file
per language, `{content_root}/{path}/{locale}.md`, and its Markdown is passed on
untouched. The wiki is written for the client already (`/wiki/...` links,
`/wiki/shared/...` images), so the only work left is resolving the locale, the
page metadata and the image route.
"""

import asyncio

from app.config import settings
from app.models.wiki import WikiPage, WikiPageLayout

from .base import (
    Repository,
    WikiSource,
    join_repo_path,
    locale_candidates,
    normalize_asset_path,
    normalize_path,
    page_layout,
    page_tags,
    page_title,
    split_front_matter,
    title_from_markdown,
    title_from_path,
)

# Page the client asks for when it opens the wiki overlay without a path.
DEFAULT_PAGE_PATH = "Main_page"


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
            listing_cache_expire=settings.wiki_listing_cache_expire_seconds,
        )

    async def close(self) -> None:
        """Release the resources held by the backend."""
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
