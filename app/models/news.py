"""News models.

The posts are written as Markdown, in the layout `ppy/osu-wiki` uses for its
`news/` directory, so a post is stored once and consumed by both the client and the
website.

Reference: <https://osu.ppy.sh/docs/index.html> (`NewsPost`, `NewsSidebar`).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NewsNavigation(BaseModel):
    """The neighbouring posts of a post, in publication order."""

    newer: "NewsPost | None" = None
    older: "NewsPost | None" = None


class NewsPost(BaseModel):
    """A news post.

    Attributes:
        edit_url: Link to the source file, which is where a post is edited.
        first_image: Banner image of the post, taken from its first image reference.
        first_image_2x: Retina variant of `first_image`. Always empty, as the content
            repository ships one file per image.
        preview: Opening paragraph, as plain text.
        content: The post rendered as HTML. Empty, because the body is not rendered on
            the server; consumers use `content_markdown`.
        navigation: Neighbouring posts, included when a single post is read.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: int
    author: str
    edit_url: str | None = None
    first_image: str | None = None
    first_image_2x: str | None = Field(default=None, serialization_alias="first_image@2x")
    published_at: datetime
    updated_at: datetime
    slug: str
    title: str
    preview: str | None = None
    content: str | None = None
    content_markdown: str | None = None
    navigation: NewsNavigation | None = None


class NewsSidebar(BaseModel):
    """The year navigation the news overlay shows next to a listing.

    Attributes:
        current_year: Year of the first listed post, or the current year when the
            listing is empty.
        news_posts: Every post published during `current_year`.
        years: Every year that has at least one published post, newest first.
    """

    current_year: int
    news_posts: list[NewsPost] = Field(default_factory=list)
    years: list[int] = Field(default_factory=list)


class NewsSearch(BaseModel):
    """The filters a listing was answered with."""

    limit: int
    sort: str = "published_desc"
    year: int | None = None


class NewsIndex(BaseModel):
    """Response of the news listing.

    Attributes:
        cursor: Keyset of the last returned post, which a client sends back as
            `cursor[published_at]` and `cursor[id]` to continue the listing.
        cursor_string: The same keyset, encoded, for clients that pass it back whole.
    """

    news_posts: list[NewsPost] = Field(default_factory=list)
    news_sidebar: NewsSidebar
    search: NewsSearch
    cursor_string: str | None = None
    cursor: dict[str, str | int] | None = None


NewsNavigation.model_rebuild()

__all__ = ["NewsIndex", "NewsNavigation", "NewsPost", "NewsSearch", "NewsSidebar"]
