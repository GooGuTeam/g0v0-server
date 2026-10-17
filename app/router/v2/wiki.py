"""Wiki API endpoints.

Implements the osu! wiki API on top of the g0v0 documentation repository
(<https://github.com/GooGuTeam/g0v0-docs>), so that osu!(lazer)'s wiki overlay
can display the documentation.

Unlike `ppy/osu-wiki`, the documentation keeps its default language at the
repository root and every other language in a sub-directory, so the locale is
always passed in the URL while `path` itself stays locale-agnostic. Untranslated
pages fall back to a configurable language, and the language actually used is
returned in `locale`.
"""

from typing import Annotated

from app.config import settings
from app.models.error import ErrorType, RequestError
from app.models.wiki import WikiPage
from app.service.wiki_service import wiki_service

from .router import router

from fastapi import Path


@router.get(
    "/wiki/{locale}/{path:path}",
    response_model=WikiPage,
    tags=["Wiki"],
    name="Get Wiki Page",
    description=(
        "Get a documentation page, in the shape the osu! client expects. "
        "In-site links are rewritten to `/wiki/{path}` so that the client can browse the "
        "documentation in its wiki overlay, and images are served from `/wiki-images/{path}`."
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
    """Retrieve a documentation page.

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
