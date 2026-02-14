"""Miscellaneous API endpoints.

This module contains various utility endpoints such as seasonal backgrounds.
"""

from datetime import UTC, datetime

from app.config import settings

from .router import router

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
