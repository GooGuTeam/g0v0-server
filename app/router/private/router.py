"""Private API router configuration.

Defines the main router for private API endpoints with rate limiting.
"""

from app.dependencies.rate_limit import LIMITERS

from fastapi import APIRouter

router = APIRouter(prefix="/api/private", dependencies=LIMITERS)

# Import and include sub-routers
from .audio_proxy import router as audio_proxy_router

router.include_router(audio_proxy_router)
