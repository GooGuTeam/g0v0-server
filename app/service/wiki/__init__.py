"""Wiki service.

Serves an osu-wiki repository (`ppy/osu-wiki` and its clones) through the osu! wiki
API (`GET /api/v2/wiki/{locale}/{path}`), in the shape osu!(lazer)'s wiki overlay
expects. The repository layout and the response fields follow what osu-web serves;
see `base` for the pieces shared with the repository client and `osu_wiki` for the
page resolution itself.
"""

import asyncio

from app.models.wiki import WikiPage

from .base import WikiBackend
from .osu_wiki import OsuWikiBackend

__all__ = ["WikiService", "close_wiki_service", "wiki_service"]


class WikiService:
    """Answers wiki requests from the osu-wiki repository.

    Attributes:
        _backend: The backend serving the requests, created on first use.
    """

    def __init__(self) -> None:
        self._backend: WikiBackend | None = None
        self._lock = asyncio.Lock()

    async def _get_backend(self) -> WikiBackend:
        """Return the active backend, creating it on first use."""
        if self._backend is None:
            async with self._lock:
                if self._backend is None:
                    self._backend = OsuWikiBackend()
        return self._backend

    async def get_page(self, locale: str | None, path: str) -> WikiPage | None:
        """Resolve a wiki page.

        Args:
            locale: The requested language code.
            path: The requested wiki path.

        Returns:
            WikiPage | None: The page, or `None` when it is not documented.
        """
        return await (await self._get_backend()).get_page(locale, path)

    async def get_image(self, path: str) -> tuple[bytes, str] | None:
        """Fetch an image referenced by the wiki.

        Args:
            path: The requested image path.

        Returns:
            tuple[bytes, str] | None: The image content and its media type, or `None`
                when it cannot be found.
        """
        return await (await self._get_backend()).get_image(path)

    async def close(self) -> None:
        """Release the resources held by the active backend."""
        if self._backend is not None:
            await self._backend.close()
            self._backend = None


wiki_service = WikiService()


async def close_wiki_service() -> None:
    """Release the resources held by the wiki service."""
    await wiki_service.close()
