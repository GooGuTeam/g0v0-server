from __future__ import annotations

from . import avatar, beatmapset_ratings, cover, oauth, relationship, team, username  # noqa: F401
from .router import router as private_router

__all__ = [
    "private_router",
]
