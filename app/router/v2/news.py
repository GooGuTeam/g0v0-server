"""News API endpoints.

Implements the osu! news API on top of a content repository, so that osu!(lazer)'s
news overlay shows what this server publishes. Posts are written the way osu-wiki
writes them, one Markdown file per post, and are returned as Markdown only; the
client renders them.

The listing is paginated the way osu-web paginates it: a client either sends back
`cursor_string` whole, or splits it into `cursor[published_at]` and `cursor[id]`,
which is what the client does.
"""

from typing import Annotated

from app.config import settings
from app.models.error import ErrorType, RequestError
from app.models.news import NewsIndex, NewsPost
from app.service.content import news_service
from app.service.content.news import DEFAULT_LIMIT, MAX_LIMIT, decode_cursor

from .router import router

from fastapi import Path, Query, Request


def _cursor_values(request: Request) -> dict[str, str]:
    """Collect the position a client sent split per key.

    osu!(lazer) sends `cursor[published_at]` and `cursor[id]` rather than the encoded
    `cursor_string`, so both spellings have to be understood.

    Args:
        request: The request that carries the parameters.

    Returns:
        dict[str, str]: The position, per key.
    """
    values: dict[str, str] = {}
    for key, value in request.query_params.multi_items():
        if key.startswith("cursor[") and key.endswith("]"):
            values[key[len("cursor[") : -1]] = value
    return values


def _include_markdown(content_formats: list[str] | None) -> bool:
    """Decide whether the body of a post belongs in the response.

    Args:
        content_formats: The requested formats, as `content_formats[]`.

    Returns:
        bool: Whether the Markdown body should be included.
    """
    if not content_formats:
        return True
    return any(value.strip().lower() == "markdown" for value in content_formats)


@router.get(
    "/news",
    response_model=NewsIndex,
    tags=["News"],
    name="Get News Listing",
    description=(
        "Returns a list of news posts and related metadata, with the year navigation the client shows "
        "next to the listing. Posts are read from the server's content repository, and their bodies are "
        "available as Markdown."
    ),
)
async def router_get_news_index(
    request: Request,
    limit: Annotated[
        int,
        Query(description="Maximum number of posts (12 default, 1 minimum, 21 maximum)."),
    ] = DEFAULT_LIMIT,
    year: Annotated[
        int | None,
        Query(description="Year to return posts from."),
    ] = None,
    cursor_string: Annotated[
        str | None,
        Query(description="Position to continue the listing from, as returned by a previous request."),
    ] = None,
) -> NewsIndex:
    """Retrieve the news listing.

    Args:
        request: The request, which may carry the position split per key.
        limit: Maximum number of posts, clamped to what the API allows.
        year: Year to return posts from.
        cursor_string: Encoded position to continue from.

    Returns:
        NewsIndex: The page of posts, with the year navigation.

    Raises:
        RequestError: If the news are disabled.
    """
    if not settings.enable_news:
        raise RequestError(ErrorType.NOT_FOUND)

    return await news_service.get_listing(
        limit=max(1, min(limit, MAX_LIMIT)),
        year=year,
        cursor=decode_cursor(cursor_string, _cursor_values(request)),
    )


@router.get(
    "/news/{news}",
    response_model=NewsPost,
    tags=["News"],
    name="Get News Post",
    description=("Returns details of the specified news post, with its body and the posts published around it."),
)
async def router_get_news_post(
    news: Annotated[
        str,
        Path(description="News post slug or ID. Example: 2025-10-01-a-new-season"),
    ],
    key: Annotated[
        str | None,
        Query(description="Unset to query by slug, or `id` to query by ID."),
    ] = None,
    content_formats: Annotated[
        list[str] | None,
        Query(alias="content_formats[]", description="`html`, `markdown`. Default to both."),
    ] = None,
) -> NewsPost:
    """Retrieve a news post.

    Args:
        news: Slug of the post, or its identifier when `key` is `id`.
        key: `id` to read `news` as an identifier.
        content_formats: Formats the body is requested in.

    Returns:
        NewsPost: The post.

    Raises:
        RequestError: If the news are disabled or the post does not exist.
    """
    if not settings.enable_news:
        raise RequestError(ErrorType.NOT_FOUND)

    post = await news_service.get_post(
        news,
        by_id=key == "id",
        include_markdown=_include_markdown(content_formats),
    )
    if post is None:
        raise RequestError(ErrorType.NOT_FOUND)

    return post
