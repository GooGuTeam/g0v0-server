"""Base router configuration for osu! API v2.

This module defines the base APIRouter with the /api/v2 prefix and rate limiting
dependencies applied to all endpoints.
"""

from app.dependencies.rate_limit import LIMITERS

from fastapi import APIRouter

router = APIRouter(prefix="/api/v2", dependencies=LIMITERS)
