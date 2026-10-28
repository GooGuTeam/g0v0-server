"""The news service.

The response shape follows osu-web's `NewsPost`, except that the body is returned as
Markdown only: the server does not render HTML, and the client renders Markdown.
"""

import base64
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import json
import posixpath
import re
from typing import Any

from app.config import settings
from app.log import log
from app.models.news import NewsIndex, NewsNavigation, NewsPost, NewsSearch, NewsSidebar
from app.service.content.base import (
    ContentIndex,
    NonEmptyText,
    OptionalTimestamp,
    Timestamp,
    content_repository,
    describe_errors,
    gather_limited,
    list_content_directory,
    parse_timestamp,
    read_metadata,
    timestamp_to_iso,
    write_metadata,
)
from app.service.repository import (
    Repository,
    RepositoryEntry,
    join_repo_path,
    quote_repo_path,
    split_front_matter,
)

from pydantic import BaseModel, ConfigDict, Field

logger = log("News")

# Listing sizes, as osu-web serves them.
DEFAULT_LIMIT = 12
MAX_LIMIT = 21

# Series a post is listed under when its file names none. The field itself accepts any
# text for now: the set of series the client filters by has not been settled here, and a
# closed list would refuse a post over a taxonomy question rather than over its content.
SERIES_NONE = "misc"

_IMAGE_REFERENCE_PATTERN = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)")
_HTML_IMAGE_PATTERN = re.compile(r"<img\b[^>]*?\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE)
_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_PATTERN = re.compile(r"\[(?P<text>[^\]]*)\]\([^)]*\)")
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_EMPHASIS_PATTERN = re.compile(r"\*\*|__|\*|~~|`")
_HEADING_PATTERN = re.compile(r"^#[ \t]+(?P<title>\S.*?)[ \t]*#*[ \t]*$")
_FENCE_PATTERN = re.compile(r"^(?:```|~~~)")

# Lines that open something other than a paragraph, and so cannot be part of the preview text.
_BLOCK_START_PATTERN = re.compile(r"^(?:#{1,6}[ \t]|:::|[|>]|[-+*][ \t]|\d+\.[ \t]|[-*_]{3,}[ \t]*$)")


@dataclass(frozen=True)
class _NewsPost:
    """A post as the listing needs it: its metadata, without the article body.

    Attributes:
        preview: Opening paragraph, as plain text.
        first_image: Banner image, already resolved to an absolute URL.
    """

    slug: str
    file_path: str
    id: int
    title: str
    author: str
    published_at: datetime
    updated_at: datetime
    series: str
    preview: str
    first_image: str | None


def inline_text(markdown: str) -> str:
    """Reduce a line of Markdown to the text it shows.

    Args:
        markdown: The Markdown to reduce.

    Returns:
        str: The visible text, with images dropped, links replaced by their text and
            emphasis markers removed.
    """
    text = _IMAGE_PATTERN.sub("", markdown)
    text = _LINK_PATTERN.sub(r"\g<text>", text)
    text = _HTML_TAG_PATTERN.sub("", text)
    text = _EMPHASIS_PATTERN.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def first_paragraph(body: str) -> str:
    """Read the opening paragraph of an article, as plain text.

    osu-web derives the same value from the rendered HTML, which the server does not
    produce; the blocks that cannot hold prose (headings, images, lists, containers)
    are skipped until a paragraph with text shows up.

    Args:
        body: The Markdown body of the article.

    Returns:
        str: The opening paragraph, or an empty string when the article has none.
    """
    blocks: list[list[str]] = []
    current: list[str] = []
    in_fence = False

    for line in body.splitlines():
        stripped = line.strip()

        if _FENCE_PATTERN.match(stripped):
            in_fence = not in_fence
            if current:
                blocks.append(current)
                current = []
            continue
        if in_fence:
            continue

        if not stripped:
            if current:
                blocks.append(current)
                current = []
            continue

        if _BLOCK_START_PATTERN.match(stripped) or stripped.startswith("!["):
            if current:
                blocks.append(current)
                current = []
            continue

        current.append(stripped)

    if current:
        blocks.append(current)

    for block in blocks:
        text = inline_text(" ".join(block))
        if text:
            return text
    return ""


def first_image(body: str) -> str | None:
    """Find the first image an article references.

    Args:
        body: The Markdown body of the article.

    Returns:
        str | None: The reference as written, or `None` when the article has no image.
    """
    found: list[tuple[int, str]] = []
    for pattern in (_IMAGE_REFERENCE_PATTERN, _HTML_IMAGE_PATTERN):
        match = pattern.search(body)
        if match is not None:
            found.append((match.start(), match.group(1)))
    return min(found)[1] if found else None


def resolve_image(reference: str | None, directory: str, repository: Repository) -> str | None:
    """Turn an image reference of an article into a URL a client can load.

    Args:
        reference: The reference, as written in the article, when it has one.
        directory: Repository directory holding the article.
        repository: Client of the content repository, used to build raw URLs.

    Returns:
        str | None: An absolute URL, or `None` when there is no reference or the
            reference leaves the repository.
    """
    if not reference:
        return None

    if reference.startswith(("http://", "https://")):
        return reference
    if reference.startswith("//"):
        return f"https:{reference}"

    if reference.startswith("/wiki/images/"):
        return f"{str(settings.server_url).rstrip('/')}{reference}"
    if reference.startswith("/wiki/"):
        # The wiki serves its own images, and the client loads them from this server.
        return f"{str(settings.server_url).rstrip('/')}/wiki/images/{reference[len('/wiki/') :]}"

    if reference.startswith("/"):
        # A rooted path addresses the repository itself.
        path = posixpath.normpath(reference.split("#", 1)[0].split("?", 1)[0].strip("/"))
    else:
        path = posixpath.normpath(join_repo_path(directory, reference.split("#", 1)[0].split("?", 1)[0]))

    if not path or path.startswith(".."):
        return None
    return repository.url(path)


class _PostFrontMatter(BaseModel):
    """The front matter of a post, as its file writes it.

    Everything here has to be written: a post without a title, a date or an author is
    one the listing cannot show, and the repository's validator refuses it long before
    it reaches this service. `series` may be left out, and holds free text when it is
    written: see `SERIES_NONE` for why it is not checked against a list.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    title: NonEmptyText
    author: NonEmptyText
    date: Timestamp
    updated: OptionalTimestamp = None
    id: int | None = Field(default=None, ge=1)
    series: NonEmptyText | None = None


class _PostMetadata(_PostFrontMatter):
    """What gets cached: the front matter, plus what the body says about the post.

    The body itself is not cached, because the listing never shows it and a request for
    a single post reads the file again.
    """

    preview: str = ""
    first_image: str | None = None


def parse_post_document(text: str) -> dict[str, Any]:
    """Read the metadata of a news post, leaving the body aside.

    The result is what gets cached by object ID, so it holds no path-dependent value:
    the first image is stored as written and resolved when the index is built.

    Args:
        text: The whole Markdown file, front matter included.

    Returns:
        dict[str, Any]: The parsed metadata, ready to be stored as JSON.

    Raises:
        ValueError: When the front matter does not describe a post, with the reasons.
    """
    front_matter, body = split_front_matter(text)
    declared = _PostFrontMatter.model_validate(front_matter)

    metadata = _PostMetadata.model_validate(
        {
            **declared.model_dump(),
            "preview": first_paragraph(body),
            "first_image": first_image(body),
        }
    )
    return metadata.model_dump(mode="json")


def post_from_metadata(metadata: dict[str, Any], entry: RepositoryEntry, repository: Repository) -> _NewsPost:
    """Build a post out of the metadata cached for its file.

    Args:
        metadata: The cached metadata.
        entry: The file backing the post.
        repository: The repository holding the file, used to resolve the banner image.

    Returns:
        _NewsPost: The post.

    Raises:
        ValueError: When the cached metadata no longer describes a post.
    """
    parsed = _PostMetadata.model_validate(metadata)
    directory = entry.path.rpartition("/")[0]

    return _NewsPost(
        slug=entry.name[: -len(".md")],
        file_path=entry.path,
        # A post without an identifier of its own numbers itself by the second it was
        # published, which orders like the date and stays the same across deployments.
        id=parsed.id or post_id(parsed.date),
        title=parsed.title,
        author=parsed.author,
        published_at=parsed.date,
        updated_at=parsed.updated or parsed.date,
        series=parsed.series or SERIES_NONE,
        preview=parsed.preview,
        first_image=resolve_image(parsed.first_image, directory, repository),
    )


def post_id(published_at: datetime) -> int:
    """Derive the identifier of a post from its publication time.

    osu-web numbers its posts from a database column; here the number is derived, so
    that it stays the same across restarts and deployments.

    Args:
        published_at: Publication time of the post.

    Returns:
        int: The identifier, which orders like the publication time.
    """
    return int(published_at.strftime("%Y%m%d%H%M%S"))


def _is_published_in(timestamp: datetime, year: int) -> bool:
    """Check whether a timestamp falls in the listing of a year.

    osu-web also shows the last two months of the previous year during the first
    three months of a year, so that the turn of the year does not empty the listing.

    Args:
        timestamp: The publication time to check.
        year: The year being listed.

    Returns:
        bool: Whether the post belongs to that year's listing.
    """
    now = datetime.now(UTC)
    start = datetime(year, 1, 1, tzinfo=UTC)
    if now.year == year and now.month < 4:
        start = datetime(year - 1, 11, 1, tzinfo=UTC)

    return start <= timestamp < datetime(year + 1, 1, 1, tzinfo=UTC)


def decode_cursor(cursor_string: str | None, values: dict[str, str]) -> tuple[datetime, int] | None:
    """Read the position a listing continues from.

    A client either sends `cursor_string` back whole, or splits it into
    `cursor[published_at]` and `cursor[id]`, which is what osu!(lazer) does.

    Args:
        cursor_string: The encoded position, when the client sends it.
        values: The position, split per key, when the client sends it that way.

    Returns:
        tuple[datetime, int] | None: The publication time and identifier to continue
            from, or `None` when no usable position was sent.
    """
    published_at = values.get("published_at")
    post_identifier = values.get("id")

    if (published_at is None or post_identifier is None) and cursor_string:
        try:
            decoded = json.loads(base64.urlsafe_b64decode(f"{cursor_string}{'=' * (-len(cursor_string) % 4)}"))
        except ValueError:
            return None
        if isinstance(decoded, list) and len(decoded) == 2:
            published_at, post_identifier = str(decoded[0]), str(decoded[1])

    if published_at is None or post_identifier is None:
        return None

    timestamp = parse_timestamp(published_at)
    try:
        identifier = int(post_identifier)
    except ValueError:
        return None

    return (timestamp, identifier) if timestamp is not None else None


def encode_cursor(post: _NewsPost) -> tuple[str, dict[str, str | int]]:
    """Build the position a client continues a listing from.

    Args:
        post: The last post of the page.

    Returns:
        tuple[str, dict[str, str | int]]: The encoded position, and the same position
            split per key.
    """
    published_at = timestamp_to_iso(post.published_at)
    payload = json.dumps([published_at, post.id]).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("="), {"published_at": published_at, "id": post.id}


class NewsService:
    """Answers news requests from a snapshot of the news directory.

    Attributes:
        _index: Snapshot of every post's metadata, refreshed behind the requests.
    """

    def __init__(self) -> None:
        self._index: ContentIndex[tuple[_NewsPost, ...]] = ContentIndex(
            self._load_index,
            expire=settings.content_index_expire_seconds,
        )

    @property
    def _root(self) -> str:
        """Repository directory holding the posts."""
        return settings.news_content_root.strip("/")

    async def _list_files(self) -> list[RepositoryEntry]:
        """List every Markdown file of the news directory.

        Returns:
            list[RepositoryEntry]: The files, in no particular order.
        """
        entries = await list_content_directory(self._root)
        files = [entry for entry in entries if entry.kind == "file" and entry.name.endswith(".md")]

        # Posts live in a directory per year, which is the layout osu-wiki uses; a
        # repository that keeps them flat is read the same way.
        directories = [entry for entry in entries if entry.kind == "dir"]
        listings = await gather_limited(
            [list_content_directory(entry.path or join_repo_path(self._root, entry.name)) for entry in directories]
        )
        for listing in listings:
            files.extend(entry for entry in listing if entry.kind == "file" and entry.name.endswith(".md"))

        return files

    async def _load_post(self, entry: RepositoryEntry) -> _NewsPost | None:
        """Read the metadata of a post, from the cache when its content did not change.

        Args:
            entry: The file backing the post.

        Returns:
            _NewsPost | None: The post, or `None` when its file cannot be read or is
                refused, which is reported to the log with the field it broke on.
        """
        repository = content_repository()

        metadata = await read_metadata("news", entry.sha)
        if metadata is None:
            text = await repository.fetch_text(entry.path)
            if text is None:
                logger.warning(f"News post {entry.path} disappeared while it was being read")
                return None

            try:
                metadata = parse_post_document(text)
            except ValueError as error:
                logger.error(f"Refusing {entry.path}: {describe_errors(error)}")
                return None

            await write_metadata("news", entry.sha, metadata)

        try:
            return post_from_metadata(metadata, entry, repository)
        except ValueError as error:
            logger.error(f"Refusing the cached metadata of {entry.path}: {describe_errors(error)}")
            return None

    async def _load_index(self) -> tuple[_NewsPost, ...]:
        """Build the snapshot of every published post.

        Returns:
            tuple[_NewsPost, ...]: The posts, oldest first.
        """
        posts = await gather_limited([self._load_post(entry) for entry in await self._list_files()])

        ordered = sorted((post for post in posts if post is not None), key=lambda post: (post.published_at, post.id))

        # Publication times are precise to the second, which two posts can share; a
        # derived identifier has to stay unique, so the later ones move up.
        seen: set[int] = set()
        unique: list[_NewsPost] = []
        for post in ordered:
            while post.id in seen:
                post = replace(post, id=post.id + 1)
            seen.add(post.id)
            unique.append(post)

        return tuple(unique)

    async def _published(self) -> list[_NewsPost]:
        """Return every post a visitor may see, newest first.

        Returns:
            list[_NewsPost]: The published posts.
        """
        now = datetime.now(UTC)
        return [post for post in reversed(await self._index.get()) if post.published_at <= now]

    def _to_model(
        self,
        post: _NewsPost,
        *,
        preview: bool = True,
        content_markdown: str | None = None,
        navigation: NewsNavigation | None = None,
    ) -> NewsPost:
        """Build the response model of a post.

        Args:
            post: The post to describe.
            preview: Whether to include the opening paragraph.
            content_markdown: The article body, when it was read.
            navigation: Neighbouring posts, when they were resolved.

        Returns:
            NewsPost: The response model.
        """
        return NewsPost(
            id=post.id,
            author=post.author,
            edit_url=(
                f"{settings.content_web_base_url.rstrip('/')}/{settings.content_repository}"
                f"/tree/{settings.content_branch}/{quote_repo_path(post.file_path)}"
            ),
            first_image=post.first_image,
            published_at=post.published_at,
            updated_at=post.updated_at,
            slug=post.slug,
            title=post.title,
            preview=post.preview if preview else None,
            content=None,
            content_markdown=content_markdown,
            navigation=navigation,
        )

    async def get_listing(
        self,
        *,
        limit: int = DEFAULT_LIMIT,
        year: int | None = None,
        cursor: tuple[datetime, int] | None = None,
    ) -> NewsIndex:
        """List the published posts.

        Args:
            limit: Maximum number of posts to return.
            year: Year to list, or every year when omitted.
            cursor: Position to continue from.

        Returns:
            NewsIndex: The page of posts, with the year navigation of the listing.
        """
        limit = max(1, min(limit, MAX_LIMIT))
        posts = await self._published()
        years = sorted({post.published_at.year for post in posts}, reverse=True)

        if year is not None:
            posts = [post for post in posts if _is_published_in(post.published_at, year)]
        if cursor is not None:
            posts = [post for post in posts if (post.published_at, post.id) < cursor]

        page = posts[:limit]
        has_more = len(posts) > limit

        sidebar_year = page[0].published_at.year if page else (year or datetime.now(UTC).year)
        sidebar_posts = [post for post in await self._published() if _is_published_in(post.published_at, sidebar_year)]

        cursor_string: str | None = None
        cursor_values: dict[str, str | int] | None = None
        if has_more:
            cursor_string, cursor_values = encode_cursor(page[-1])

        return NewsIndex(
            news_posts=[self._to_model(post) for post in page],
            news_sidebar=NewsSidebar(
                current_year=sidebar_year,
                news_posts=[self._to_model(post, preview=False) for post in sidebar_posts],
                years=years,
            ),
            search=NewsSearch(limit=limit, year=year),
            cursor_string=cursor_string,
            cursor=cursor_values,
        )

    async def get_post(self, slug: str, *, by_id: bool = False, include_markdown: bool = True) -> NewsPost | None:
        """Read a single post, with its body and its neighbours.

        Args:
            slug: Slug of the post, or its identifier when `by_id` is set.
            by_id: Whether `slug` holds an identifier instead of a slug.
            include_markdown: Whether to include the body.

        Returns:
            NewsPost | None: The post, or `None` when it does not exist or is not
                published yet.
        """
        posts = await self._published()

        target: _NewsPost | None = None
        if by_id:
            try:
                identifier = int(slug)
            except ValueError:
                return None
            target = next((post for post in posts if post.id == identifier), None)
        else:
            target = next((post for post in posts if post.slug == slug), None)

        if target is None:
            return None

        content_markdown: str | None = None
        if include_markdown:
            text = await content_repository().fetch_text(target.file_path)
            if text is None:
                return None
            _, content_markdown = split_front_matter(text)

        return self._to_model(
            target,
            preview=False,
            content_markdown=content_markdown,
            navigation=self._navigation(posts, target),
        )

    def _navigation(self, posts: list[_NewsPost], post: _NewsPost) -> NewsNavigation:
        """Resolve the posts published around a post.

        Args:
            posts: Every published post, newest first.
            post: The post being read.

        Returns:
            NewsNavigation: The neighbouring posts, when they exist.
        """
        position = next((index for index, item in enumerate(posts) if item.id == post.id), None)
        if position is None:
            return NewsNavigation()

        return NewsNavigation(
            newer=self._to_model(posts[position - 1], preview=False) if position > 0 else None,
            older=self._to_model(posts[position + 1], preview=False) if position + 1 < len(posts) else None,
        )

    async def warm(self) -> None:
        """Build the snapshot ahead of the first request.

        Meant to run as a background task, as it waits on the repository.
        """
        await self._index.warm()

    async def close(self) -> None:
        """Stop a refresh that is still running."""
        await self._index.close()
