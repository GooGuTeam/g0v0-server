"""Wiki page models.

Describes the payload of the osu! wiki API (`GET /api/v2/wiki/{locale}/{path}`),
which osu!(lazer)'s wiki overlay consumes directly.

Reference: <https://osu.ppy.sh/docs/index.html> (`WikiPage`).
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class WikiPageLayout(StrEnum):
    """Layout the client uses to render a wiki page.

    The client only special cases `main_page`; everything else is rendered as a
    regular Markdown article.
    """

    MARKDOWN_PAGE = "markdown_page"
    MAIN_PAGE = "main_page"


class WikiPage(BaseModel):
    """A single wiki page.

    Attributes:
        locale: Language code of the returned Markdown, after locale fallback.
        markdown: Raw Markdown of the page, rewritten for the osu! client.
        path: Locale-agnostic path of the page.
        title: Title of the page.
        available_locales: Language codes the page is translated into.
        layout: Layout the client should use to render the page.
        subtitle: Title of the parent page, when the page has a browsable parent.
        tags: Tags declared by the page.
    """

    locale: str
    markdown: str
    path: str
    title: str
    available_locales: list[str] = Field(default_factory=list)
    layout: WikiPageLayout = WikiPageLayout.MARKDOWN_PAGE
    subtitle: str | None = None
    tags: list[str] = Field(default_factory=list)
