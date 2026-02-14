"""Private API router module.

This module exports the private router and conditionally imports sub-modules
based on application settings (e.g., TOTP verification).
"""

from app.config import settings

from . import (  # noqa: F401
    admin,
    audio_proxy,
    avatar,
    beatmapset,
    cover,
    gamemodes,
    oauth,
    password,
    relationship,
    score,
    team,
    user,
)
from .router import router as private_router

if settings.enable_totp_verification:
    from . import totp  # noqa: F401

__all__ = [
    "private_router",
]
