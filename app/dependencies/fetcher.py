from typing import Annotated

from app.config import settings
from app.dependencies.database import get_redis
from app.fetcher import Fetcher as OriginFetcher

from fastapi import Depends

fetcher: OriginFetcher | None = None


async def get_fetcher() -> OriginFetcher:
    global fetcher
    if fetcher is None:
        fetcher = OriginFetcher(
            settings.fetcher_client_id,
            settings.fetcher_client_secret,
        )
        redis = get_redis()
        access_token = await redis.get(f"fetcher:access_token:{fetcher.client_id}")
        expire_at = await redis.get(f"fetcher:expire_at:{fetcher.client_id}")
        if expire_at:
            fetcher.token_expiry = int(float(expire_at))
        if access_token:
            fetcher.access_token = str(access_token)
        # Always ensure the access token is valid, regardless of initial state
        await fetcher.ensure_valid_access_token()
    return fetcher


Fetcher = Annotated[OriginFetcher, Depends(get_fetcher)]
