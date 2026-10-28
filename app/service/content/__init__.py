"""Content services.

Both the changelog and the news are served from a repository the maintainers edit,
rather than from the database.

See `changelog` and `news` for the two formats, and `base` for the repository access
and the snapshot they share.
"""

import asyncio

from .base import close_content_repository
from .changelog import ChangelogService
from .news import NewsService

__all__ = [
    "ChangelogService",
    "NewsService",
    "changelog_service",
    "close_content_services",
    "news_service",
]

changelog_service = ChangelogService()
news_service = NewsService()


async def close_content_services() -> None:
    """Release the resources held by the content services."""
    await asyncio.gather(
        changelog_service.close(),
        news_service.close(),
        close_content_repository(),
    )
