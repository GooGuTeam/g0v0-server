"""osu! API v1 router module.

This module provides endpoints compatible with the legacy osu! API v1 specification.
See: https://github.com/ppy/osu-api/wiki

Exports:
    api_v1_public_router: Public API router (no authentication required).
    api_v1_router: Authenticated API router.
"""

from . import beatmap, public_user, replay, score, user  # noqa: F401
from .public_router import public_router as api_v1_public_router
from .router import router as api_v1_router

__all__ = ["api_v1_public_router", "api_v1_router"]
