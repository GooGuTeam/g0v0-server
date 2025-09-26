from __future__ import annotations

from app.config import settings

from . import (  # noqa: F401
    audio_proxy,
    avatar,
    beatmapset_ratings,
    cover,
    device_management,
    oauth,
    relationship,
    team,
    user_preferences,
    username,
)
from .router import router as private_router

if settings.enable_totp_verification:
    from . import totp  # noqa: F401

__all__ = [
    "private_router",
]
