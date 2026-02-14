"""osu! API v2 router module.

This module aggregates all v2 API endpoints and exposes the main router.
All endpoints in this module must remain compatible with the official osu! API v2 specification.
"""

from . import (  # noqa: F401
    beatmap,
    beatmapset,
    me,
    misc,
    ranking,
    relationship,
    room,
    score,
    session_verify,
    tags,
    user,
)
from .router import router as api_v2_router

__all__ = [
    "api_v2_router",
]
