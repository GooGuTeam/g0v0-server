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
