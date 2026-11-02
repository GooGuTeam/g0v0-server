"""Content asset router.

Serves the images from the changelog and news repository.
"""

from typing import Annotated

from app.config import settings
from app.models.error import ErrorType, RequestError
from app.service.content.base import content_repository
from app.service.repository import join_repo_path

from fastapi import APIRouter, Path
from fastapi.responses import Response

router = APIRouter(tags=["Content"])

# Repository directory the images are read from, below the one this route serves.
CONTENT_ASSET_ROOT = "assets"

# Images are sometimes replaced under the same name.
CACHE_CONTROL = "max-age=604800, public"


@router.get(
    "/content/images/{path:path}",
    name="Get content image",
    response_class=Response,
    responses={
        200: {"description": "The requested image.", "content": {"image/*": {}}},
        404: {"description": "The content does not ship this image."},
    },
    description=(
        "Get an image the changelog or the news links. The path is relative to the `assets` directory of "
        "the content repository (`changelog/2026.1101.0/banner.png`), which is what a Markdown image "
        "written as `/content/images/{path}` resolves to."
    ),
)
async def get_content_image(
    path: Annotated[
        str,
        Path(description="Repository relative path of the image. Example: changelog/2026.1101.0/banner.png"),
    ],
) -> Response:
    """Retrieve an image from the content repository.

    Args:
        path: Repository relative path of the image, below the `assets` directory.

    Returns:
        Response: The image content.

    Raises:
        RequestError: If content images are disabled, or the repository does not ship
            the image, or what it ships there is not an image.
    """
    if not settings.enable_content_images:
        raise RequestError(ErrorType.NOT_FOUND)

    image = await content_repository().fetch_binary(join_repo_path(CONTENT_ASSET_ROOT, path))
    if image is None:
        raise RequestError(ErrorType.NOT_FOUND)

    content, media_type = image
    return Response(content=content, media_type=media_type, headers={"Cache-Control": CACHE_CONTROL})
