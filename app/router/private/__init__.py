from __future__ import annotations

from . import avatar, cover, oauth, relationship, team, username  # noqa: F401
from .router import router as private_router

__all__ = [
    "private_router",
]
