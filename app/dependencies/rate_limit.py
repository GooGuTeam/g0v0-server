from app.config import settings

from fastapi import Depends
from fastapi_limiter.depends import RateLimiter
from pyrate_limiter import Duration, Limiter, Rate

if settings.enable_rate_limit:
    LIMITERS = [
        Depends(RateLimiter(limiter=Limiter(Rate(1200, Duration.MINUTE)))),
        Depends(RateLimiter(limiter=Limiter(Rate(200, Duration.SECOND)))),
    ]
else:
    LIMITERS = []
