"""Pieces shared by the wiki service.

The wiki API answers with the osu-web page shape, built from an osu-wiki
repository (`ppy/osu-wiki` and its clones): one Markdown file per language,
`{path}/{locale}.md`. This module holds what is specific to a wiki: resolving a
locale and a path, the metadata an article declares in its front matter, and the
images its Markdown links to. Reading the repository itself lives in
`app.service.repository`, as the news and changelog read their content the same way.
"""

from dataclasses import dataclass
import posixpath
import re
from typing import Protocol
from urllib.parse import unquote

from app.models.wiki import WikiPage, WikiPageLayout
from app.service.repository import is_safe_repo_path, split_front_matter

# Extensions the image route is willing to serve.
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".avif", ".bmp"})

_HEADING_PATTERN = re.compile(r"^#[ \t]+(?P<title>\S.*?)[ \t]*#*[ \t]*$", re.MULTILINE)
_MARKDOWN_LINK_PATTERN = re.compile(r"\[(?P<text>[^\]]*)\]\([^)]*\)")


@dataclass(frozen=True)
class WikiSource:
    """A page resolved inside the repository.

    Attributes:
        locale: Language code of the served content.
        path: Locale-agnostic wiki path of the page.
        file_path: Repository relative path of the Markdown file.
        markdown: Raw Markdown of the page.
    """

    locale: str
    path: str
    file_path: str
    markdown: str


class WikiBackend(Protocol):
    """A repository layout that can answer wiki requests."""

    async def get_page(self, locale: str | None, path: str) -> WikiPage | None:
        """Resolve a wiki page.

        Args:
            locale: The requested language code.
            path: The requested wiki path.

        Returns:
            WikiPage | None: The page, or `None` when it is not documented.
        """
        ...

    async def get_image(self, path: str) -> tuple[bytes, str] | None:
        """Fetch an image referenced by the wiki.

        Args:
            path: The requested image path.

        Returns:
            tuple[bytes, str] | None: The image content and its media type, or `None`
                when it cannot be found.
        """
        ...

    async def warm(self) -> None:
        """Load anything the backend keeps in memory, ahead of the first request."""
        ...

    async def close(self) -> None:
        """Release the resources held by the backend."""
        ...


def normalize_locale_code(code: str) -> str:
    """Normalise a language code to lowercase, hyphen separated form.

    Args:
        code: The language code to normalise, e.g. `zh_CN`.

    Returns:
        str: The normalised code, e.g. `zh-cn`.
    """
    return code.strip().lower().replace("_", "-")


def locale_candidates(locale: str | None, fallback: str) -> list[str]:
    """List the language codes to try for a page, in order.

    A client asks with the culture code of its interface language, while
    repositories store the files under a language subtag, so the requested code is
    tried first, then its primary subtag, then the configured fallback.

    Args:
        locale: The requested language code, e.g. `zh-CN`.
        fallback: The language code to fall back to, e.g. `en`.

    Returns:
        list[str]: The codes to try, without duplicates.
    """
    requested = normalize_locale_code(locale or "") or normalize_locale_code(fallback)
    candidates = [requested] if requested else []
    primary = requested.split("-", 1)[0]
    if primary and primary not in candidates:
        candidates.append(primary)
    fallback_code = normalize_locale_code(fallback)
    if fallback_code and fallback_code not in candidates:
        candidates.append(fallback_code)
    return candidates


def normalize_path(path: str, default: str) -> str | None:
    """Normalise a wiki path.

    Accepts anything the client or a Markdown link can produce (`/Gameplay/Accuracy`,
    `./Accuracy.md`, ...) and returns the path itself, without slashes or extension.

    Args:
        path: The path to normalise.
        default: Path to return when the request carries none.

    Returns:
        str | None: The normalised path, or `None` when it escapes the repository root.
    """
    path = unquote(path or "")
    path = path.split("#", 1)[0].split("?", 1)[0].replace("\\", "/")
    path = re.sub(r"/{2,}", "/", path).strip("/")
    path = posixpath.normpath(path) if path else ""
    path = path.strip("/")

    if path in ("", "."):
        return default
    # Once `normpath` has resolved inner `..` segments, a leading `..` means the path escaped the root.
    if path.startswith(".."):
        return None

    return re.sub(r"\.(?:md|html?)$", "", path, flags=re.IGNORECASE).strip("/") or default


def normalize_asset_path(path: str) -> str | None:
    """Normalise a repository relative asset path.

    Args:
        path: The encoded path from the request.

    Returns:
        str | None: The normalised path, or `None` when it is not a usable image.
    """
    path = unquote(path or "").replace("\\", "/").split("#", 1)[0].split("?", 1)[0]
    path = re.sub(r"/{2,}", "/", path).strip("/")
    path = posixpath.normpath(path) if path else ""

    if not is_safe_repo_path(path) or not path.lower().endswith(tuple(IMAGE_EXTENSIONS)):
        return None

    return path


def title_from_path(path: str) -> str:
    """Derive a page title from its wiki path.

    Args:
        path: The locale-agnostic wiki path.

    Returns:
        str: The derived title.
    """
    return path.rpartition("/")[2].replace("-", " ").replace("_", " ")


def title_from_markdown(front_matter: dict[str, object], body: str, path: str) -> str:
    """Resolve the title of a page from its front matter, first heading or path.

    Args:
        front_matter: The parsed front matter of the page.
        body: The Markdown body of the page.
        path: The locale-agnostic wiki path.

    Returns:
        str: The resolved title.
    """
    title = front_matter.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()

    heading = _HEADING_PATTERN.search(body)
    if heading is not None:
        # Headings may carry a link (`# [Game client](/wiki/Client)`); the link text is the title.
        return _MARKDOWN_LINK_PATTERN.sub(r"\g<text>", heading.group("title")).strip()

    return title_from_path(path)


def page_title(markdown: str, path: str) -> str:
    """Resolve the title of a page.

    Args:
        markdown: The Markdown of the page.
        path: The locale-agnostic wiki path.

    Returns:
        str: The resolved title.
    """
    front_matter, body = split_front_matter(markdown)
    return title_from_markdown(front_matter, body, path)


def page_tags(front_matter: dict[str, object]) -> list[str]:
    """Resolve the tags declared by a page.

    Args:
        front_matter: The parsed front matter of the page.

    Returns:
        list[str]: The declared tags.
    """
    tags = front_matter.get("tags")
    if isinstance(tags, list):
        return [str(tag) for tag in tags]
    if isinstance(tags, str) and tags.strip():
        return [tags.strip()]
    return []


def page_layout(front_matter: dict[str, object]) -> WikiPageLayout:
    """Resolve the layout the client should render a page with.

    Args:
        front_matter: The parsed front matter of the page.

    Returns:
        WikiPageLayout: `main_page` only when the page asks for it, otherwise a
            regular Markdown article.
    """
    layout = front_matter.get("layout")
    if isinstance(layout, str) and layout.strip().lower() == WikiPageLayout.MAIN_PAGE.value:
        return WikiPageLayout.MAIN_PAGE
    return WikiPageLayout.MARKDOWN_PAGE
