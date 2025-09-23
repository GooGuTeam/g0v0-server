from __future__ import annotations

from app.config import settings

from fastapi import Depends
from fastapi_limiter.depends import RateLimiter

if settings.enable_rate_limit:
    LIMITERS = [
        Depends(RateLimiter(times=1200, minutes=1)),
        Depends(RateLimiter(times=200, seconds=1)),
    ]
else:
    LIMITERS = []
