"""Wiki asset router.

Serves the images referenced by the wiki Markdown, straight from the wiki
repository.

osu-wiki pages link images as `/wiki/shared/...` (absolute) or `img/...` (relative
to the article). The client resolves both against the page URL and then rewrites
the `/wiki/` segment into `/wiki/images/`, which is the route osu-web serves, so
`/wiki/images/{path}` maps onto `{content_root}/{path}` and the Markdown needs no
rewriting at all.
"""

from typing import Annotated

from app.config import settings
from app.models.error import ErrorType, RequestError
from app.service.wiki import wiki_service

from fastapi import APIRouter, Path
from fastapi.responses import Response

router = APIRouter(tags=["Wiki"])

# Images are sometimes replaced under the same name: a week cuts upstream traffic without pinning stale ones.
CACHE_CONTROL = "max-age=604800, public"


@router.get(
    "/wiki/images/{path:path}",
    name="Get wiki image",
    response_class=Response,
    responses={
        200: {"description": "The requested image.", "content": {"image/*": {}}},
        404: {"description": "The image does not exist in the wiki repository."},
    },
    description=(
        "Get an image referenced by the wiki. This is the route the client rewrites `/wiki/` image URLs "
        "into, and the path is relative to the wiki directory of the repository (`shared/mode/osu.png`)."
    ),
)
async def get_wiki_image(
    path: Annotated[
        str,
        Path(description="Wiki relative path of the image. Example: shared/mode/osu.png"),
    ],
) -> Response:
    """Retrieve an image from the wiki repository.

    Args:
        path: Wiki relative path of the image.

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
    return Response(content=content, media_type=media_type, headers={"Cache-Control": CACHE_CONTROL})
