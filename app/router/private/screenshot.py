"""Screenshot management endpoints.

Provides endpoints for users to manage the screenshots they submitted through
g0v0! or web client.
"""

from datetime import datetime
from typing import Annotated

from app.config import settings
from app.database import Screenshot
from app.dependencies.database import Database
from app.dependencies.storage import StorageService
from app.dependencies.user import ClientUser
from app.log import log
from app.models.model import UTCBaseModel
from app.storage import StorageService as OriginStorageService

from .router import router

from fastapi import Query
from pydantic import Field
from sqlmodel import col, func, select

logger = log("Screenshot")

SCREENSHOT_STORAGE_PREFIX = "screenshots/"


class ScreenshotInfo(UTCBaseModel):
    """A screenshot submitted by the current user.

    Attributes:
        id: Screenshot record ID.
        sha256_hash: SHA-256 hash of the uploaded image.
        url: Stable share link served by this server.
        direct_url: Direct storage link, regenerated on every request. Maybe
            a short-lived presigned URL when the object storage backend has no
            public URL base configured.
        hits: Number of times the share link has been opened.
        timestamp: Upload time.
        last_access: Last time the share link was opened.
    """

    id: int = Field(description="Screenshot record ID")
    sha256_hash: str = Field(description="SHA-256 hash of the uploaded image")
    url: str = Field(description="Stable share link of the screenshot")
    direct_url: str | None = Field(default=None, description="Direct link of the stored image")
    hits: int = Field(description="Number of times the share link has been opened")
    timestamp: datetime = Field(description="Upload time")
    last_access: datetime = Field(description="Last access time")


class ScreenshotsResp(UTCBaseModel):
    """Paginated list of the current user's screenshots.

    Attributes:
        total: Total number of screenshots owned by the current user.
        items: Screenshots on the current page, newest first.
    """

    total: int = Field(description="Total number of screenshots owned by the current user")
    items: list[ScreenshotInfo] = Field(description="Screenshots on the current page, newest first")


def _canonical_storage_path(screenshot: Screenshot) -> str:
    """Build the deterministic storage path used by the upload endpoint.

    Args:
        screenshot: The screenshot record.

    Returns:
        The storage path of the record.
    """
    return f"{SCREENSHOT_STORAGE_PREFIX}{screenshot.user_id}_{screenshot.sha256_hash}.jpg"


def _resolve_storage_path(storage: OriginStorageService, screenshot: Screenshot) -> str:
    """Resolve the storage path of a screenshot record.

    The URL stored in the database may be a presigned URL that cannot be
    parsed back into a storage key, so the parsed value is only trusted when
    it stays inside the screenshot prefix. Otherwise the deterministic path
    used at upload time is returned.

    Args:
        storage: The storage service.
        screenshot: The screenshot record.

    Returns:
        The storage path of the record.
    """
    parsed = storage.get_file_name_by_url(screenshot.url)
    if parsed and parsed.startswith(SCREENSHOT_STORAGE_PREFIX) and ".." not in parsed:
        return parsed
    return _canonical_storage_path(screenshot)


async def _build_info(storage: OriginStorageService, screenshot: Screenshot) -> ScreenshotInfo:
    """Convert a screenshot record into an API response item.

    Args:
        storage: The storage service used to build the direct link.
        screenshot: The screenshot record.

    Returns:
        The response item of the record.
    """
    try:
        direct_url: str | None = await storage.get_file_url(_resolve_storage_path(storage, screenshot))
    except RuntimeError as e:
        logger.warning(f"Failed to build direct URL for screenshot {screenshot.id}: {e}")
        direct_url = None

    return ScreenshotInfo(
        id=screenshot.id,
        sha256_hash=screenshot.sha256_hash,
        url=f"{settings.server_url}ss/{screenshot.sha256_hash}",
        direct_url=direct_url,
        hits=screenshot.hits,
        timestamp=screenshot.timestamp,
        last_access=screenshot.last_access,
    )


@router.get(
    "/screenshots",
    name="List own screenshots",
    description="Get the screenshots submitted by the current user, newest first.",
    tags=["Screenshot", "g0v0 API"],
    response_model=ScreenshotsResp,
)
async def list_screenshots(
    session: Database,
    current_user: ClientUser,
    storage: StorageService,
    limit: Annotated[int, Query(ge=1, le=100, description="Maximum number of screenshots to return (1-100)")] = 20,
    offset: Annotated[int, Query(ge=0, description="Number of screenshots to skip")] = 0,
) -> ScreenshotsResp:
    """List the screenshots owned by the current user.

    Args:
        session: Database session.
        current_user: The authenticated user.
        storage: Storage service used to build direct links.
        limit: Maximum number of screenshots to return.
        offset: Number of screenshots to skip.

    Returns:
        The total number of screenshots and the requested page.
    """
    total = (
        await session.exec(select(func.count()).select_from(Screenshot).where(Screenshot.user_id == current_user.id))
    ).one()

    screenshots = (
        await session.exec(
            select(Screenshot)
            .where(Screenshot.user_id == current_user.id)
            .order_by(col(Screenshot.timestamp).desc(), col(Screenshot.id).desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    return ScreenshotsResp(
        total=total,
        items=[await _build_info(storage, screenshot) for screenshot in screenshots],
    )
