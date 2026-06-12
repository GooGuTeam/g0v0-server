"""osu! API v1 router module.

This module provides endpoints compatible with the legacy osu! API v1 specification.
See: https://github.com/ppy/osu-api/wiki

Exports:
    api_v1_router: Authenticated API router.
"""

from . import beatmap, replay, score, user  # noqa: F401
from .router import router as api_v1_router

__all__ = ["api_v1_router"]
