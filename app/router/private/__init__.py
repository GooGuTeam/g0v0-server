from __future__ import annotations

from app.config import settings

from . import audio_proxy, avatar, beatmapset, cover, oauth, relationship, score, team, username  # noqa: F401
from .router import router as private_router

if settings.enable_totp_verification:
    from . import totp  # noqa: F401

__all__ = [
    "private_router",
]
