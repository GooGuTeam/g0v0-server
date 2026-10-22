"""Media URL endpoint.

osu!(lazer) never loads a Markdown image directly: `OsuMarkdownImage` wraps every
image URL into `{server}/media-url?url={url}` and loads that wrapper instead. On
osu-web the endpoint answers with a redirect, either to the image proxy (camo) when
one is configured or to the URL itself, and the client follows it because
`WebRequest` keeps `SocketsHttpHandler`'s redirects enabled.

This endpoint implements the redirect form only: beyond masking the visitor's
address from arbitrary hosts, a proxy would add bandwidth and a request-forgery
surface here.

It is not a wiki endpoint either: every Markdown the client renders (wiki pages,
beatmap descriptions, comments) loads its images through here, so it stays
available even when the wiki is turned off.
"""

from typing import Annotated
from urllib.parse import urlsplit

from app.models.error import ErrorType, RequestError

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse

router = APIRouter(tags=["Wiki"])

# Let the client keep the redirect for a day, as osu-web does.
CACHE_CONTROL = "max-age=86400, public"

# Headers that only a browser sends. A browser arriving here is a redirect handed to
# a visitor rather than the game client, and letting it through would turn this into
# an open redirect for phishing.
BROWSER_ONLY_HEADERS = ("origin", "referer", "sec-fetch-site")

ALLOWED_SCHEMES = ("http", "https")

# Wiki images are served by this server while the Markdown linking them is resolved
# against the website, which is a different host for a private server.
WIKI_PATH = "/wiki/"
WIKI_IMAGE_PATH = "/wiki/images/"


def local_wiki_image_path(path: str) -> str | None:
    """Map a wiki image path onto the route this server serves it from.

    Args:
        path: The path of the wrapped URL.

    Returns:
        str | None: The local path of the image, or `None` when the path is not a
            wiki image.
    """
    if path.startswith(WIKI_IMAGE_PATH):
        return path
    if path.startswith(WIKI_PATH):
        return f"{WIKI_IMAGE_PATH}{path.removeprefix(WIKI_PATH)}"
    return None


def resolve_media_url(url: str) -> str:
    """Turn the wrapped URL into the location to redirect to.

    The client wraps the URL it resolved for the image, so it is either absolute
    (`https://assets.ppy.sh/...`, `https://host/wiki/shared/...`) or root relative
    (`/wiki/images/...`, when the Markdown itself used a root relative link).

    Wiki images are redirected to this server rather than to the host they were
    resolved against, since the website of a private server is a separate host and
    serves no wiki images.

    Args:
        url: The `url` parameter of the request.

    Returns:
        str: The location to redirect to.

    Raises:
        RequestError: If the URL is missing or cannot be redirected to.
    """
    url = url.strip()
    if not url:
        raise RequestError(ErrorType.INVALID_REQUEST, status_code=422)

    # `//host/path` is another host in disguise, not a path on this one.
    if url.startswith("//"):
        raise RequestError(ErrorType.INVALID_REQUEST)

    parts = urlsplit(url)

    wiki_image_path = local_wiki_image_path(parts.path)
    if wiki_image_path is not None:
        return f"{wiki_image_path}?{parts.query}" if parts.query else wiki_image_path

    scheme = parts.scheme.lower()
    if scheme:
        if scheme not in ALLOWED_SCHEMES:
            raise RequestError(ErrorType.INVALID_REQUEST)
        return url

    return f"/{url.lstrip('/')}"


@router.get(
    "/media-url",
    name="Redirect media URL",
    response_class=RedirectResponse,
    responses={
        302: {"description": "Redirect to the wrapped URL."},
        403: {"description": "The request came from a browser rather than the game client."},
        422: {"description": "The `url` parameter is missing or not redirectable."},
    },
    description=(
        "Redirect to the URL wrapped by the client's Markdown image renderer. Every Markdown the client "
        "renders asks for its images through this endpoint instead of loading them directly."
    ),
)
async def get_media_url(
    request: Request,
    url: Annotated[
        str,
        Query(description="The URL to redirect to, wrapped by the client. Example: /wiki/images/shared/mode/osu.png"),
    ],
) -> RedirectResponse:
    """Redirect to the URL the client wrapped for an image.

    Args:
        request: The incoming request, used to tell a browser from the game client.
        url: The wrapped URL.

    Returns:
        RedirectResponse: A redirect to the wrapped URL.

    Raises:
        RequestError: If the request comes from a browser, or the URL is missing or
            not redirectable.
    """
    if any(header in request.headers for header in BROWSER_ONLY_HEADERS):
        raise RequestError(ErrorType.INVALID_REQUEST, status_code=403)

    return RedirectResponse(resolve_media_url(url), status_code=302, headers={"Cache-Control": CACHE_CONTROL})
