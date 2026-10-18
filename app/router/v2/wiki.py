"""Wiki API endpoints.

Implements the osu! wiki API on top of an osu-wiki repository, so that
osu!(lazer)'s wiki overlay can display the wiki from inside the client.

The client always addresses a page as `wiki/{locale}/{path}` and keeps the locale
out of the path (osu-web resolves it to a per-locale file). Untranslated pages
fall back to a configurable language, and the language actually used is returned
in `locale`.
"""

from typing import Annotated

from app.config import settings
from app.models.error import ErrorType, RequestError
from app.models.wiki import WikiPage
from app.service.wiki import wiki_service

from .router import router

from fastapi import Path


@router.get(
    "/wiki/{locale}/{path:path}",
    response_model=WikiPage,
    tags=["Wiki"],
    name="Get Wiki Page",
    description=(
        "Get a wiki page, in the shape the osu! client expects. "
        "In-site links are rewritten to `/wiki/{path}` so that the client browses the wiki in its "
        "overlay, and images are served from the wiki image route."
    ),
)
async def router_get_wiki_page(
    locale: Annotated[
        str,
        Path(description="Two-letter language code of the wiki page. Example: en"),
    ],
    path: Annotated[
        str,
        Path(description="The path name of the wiki page, without a language prefix. Example: Main_page"),
    ],
) -> WikiPage:
    """Retrieve a wiki page.

    Args:
        locale: The requested language code.
        path: The requested wiki path, without a language prefix.

    Returns:
        WikiPage: The resolved page, in the language it is actually available in.

    Raises:
        RequestError: If the wiki is disabled or the page does not exist.
    """
    if not settings.enable_wiki:
        raise RequestError(ErrorType.NOT_FOUND)

    page = await wiki_service.get_page(locale, path)
    if page is None:
        raise RequestError(ErrorType.NOT_FOUND)

    return page
