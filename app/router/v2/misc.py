"""Miscellaneous API endpoints.

This module contains various utility endpoints such as seasonal backgrounds and screenshots.
"""

from datetime import UTC, datetime
import hashlib
from typing import Annotated

from app.config import settings
from app.database import Screenshot
from app.dependencies.database import Database
from app.dependencies.storage import StorageService
from app.dependencies.user import ClientUser
from app.helpers import utcnow
from app.helpers.http import check_image

from .router import router

from fastapi import File
from pydantic import BaseModel


class Background(BaseModel):
    """Single seasonal background item.

    Attributes:
        url: The URL of the background image.
    """

    url: str


class BackgroundsResp(BaseModel):
    """Response model for seasonal backgrounds.

    Attributes:
        ends_at: End time of the seasonal event (far future indicates permanent availability).
        backgrounds: List of background images.
    """

    ends_at: datetime = datetime(year=9999, month=12, day=31, tzinfo=UTC)
    backgrounds: list[Background]


@router.get(
    "/seasonal-backgrounds",
    response_model=BackgroundsResp,
    tags=["Miscellaneous"],
    name="Get seasonal backgrounds",
    description="Get the list of current seasonal background images.",
)
async def get_seasonal_backgrounds() -> BackgroundsResp:
    """Retrieve the list of seasonal background images.

    Returns:
        BackgroundsResp: The seasonal backgrounds response containing image URLs.
    """
    return BackgroundsResp(backgrounds=[Background(url=url) for url in settings.seasonal_backgrounds])


class ScreenshotResp(BaseModel):
    """Response model for submitted screenshots.

    Attributes:
        url: The URL where the submitted screenshot can be accessed.
    """

    url: str


@router.post(
    "/screenshots",
    tags=["Miscellaneous"],
    name="Submit screenshot",
    description="Submit a screenshot for sharing.",
    response_model=ScreenshotResp,
)
async def submit_screenshot(
    session: Database,
    current_user: ClientUser,
    storage_service: StorageService,
    screenshot: Annotated[bytes, File(..., description="The screenshot file to be submitted. Max size: 10MB")],
) -> ScreenshotResp:
    check_image(screenshot, size=10 * 1024 * 1024, allow_formats=["JPEG"])
    sha256_hash = hashlib.sha256(screenshot).hexdigest()
    filepath = f"screenshots/{current_user.id}_{sha256_hash}.jpg"
    await storage_service.write_file(filepath, screenshot, "image/jpeg")
    url = await storage_service.get_file_url(filepath)
    screenshot_record = Screenshot(
        sha256_hash=sha256_hash,
        user_id=current_user.id,
        timestamp=utcnow(),
        last_access=utcnow(),
        url=url,
    )
    session.add(screenshot_record)
    await session.commit()
    return ScreenshotResp(url=f"{settings.server_url}ss/{sha256_hash}")
