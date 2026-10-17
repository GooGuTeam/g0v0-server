"""Wiki asset router.

Serves the images referenced by the documentation markdown, straight from the
documentation repository (<https://github.com/GooGuTeam/g0v0-docs>).

Two client behaviours shape this route:

* Relative image URLs are first resolved against the wiki page URL
  (`{website}/wiki/{path}/`), which is not necessarily this server, so the wiki
  endpoint rewrites every in-site image to an absolute URL on this route.
* `WikiMarkdownImage` then rewrites the `/wiki/` segment of the image URL into
  `/wiki/images/` before loading it, so the route deliberately does not live
  under `/wiki/...` — that would mangle the path.

Note that `OsuMarkdownImage` wraps every image URL in
`https://osu.ppy.sh/media-url?url=...`, so the client only renders these images
when that proxy can reach `SERVER_URL`; the images cannot be served to an
offline client no matter what this route returns.
"""

from typing import Annotated

from app.config import settings
from app.models.error import ErrorType, RequestError
from app.service.wiki_service import wiki_service

from fastapi import APIRouter, Path
from fastapi.responses import Response

router = APIRouter(prefix="/wiki-images", tags=["Wiki"])

# Images are sometimes replaced under the same name: a week cuts upstream traffic without pinning stale ones.
CACHE_CONTROL = "max-age=604800, public"


@router.get(
    "/{path:path}",
    name="Get wiki image",
    response_class=Response,
    responses={
        200: {"description": "The requested image.", "content": {"image/*": {}}},
        404: {"description": "The image does not exist in the documentation repository."},
    },
    description=(
        "Get an image referenced by the documentation. The path is relative to the "
        "documentation repository (`public/images/example.png`), and paths relative to the "
        "VitePress public directory (`images/example.png`) are accepted as well."
    ),
)
async def get_wiki_image(
    path: Annotated[
        str,
        Path(description="Repository relative path of the image. Example: public/images/api-doc-example.png"),
    ],
) -> Response:
    """Retrieve an image from the documentation repository.

    Args:
        path: Repository relative path of the image.

    Returns:
        Response: The image content.

    Raises:
        RequestError: If the wiki is disabled or the image does not exist.
    """
    if not settings.enable_wiki:
        raise RequestError(ErrorType.NOT_FOUND)

    image = await wiki_service.get_image(path)
    if image is None:
        raise RequestError(ErrorType.NOT_FOUND)

    content, media_type = image
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": CACHE_CONTROL},
    )
