from typing import Annotated

from app.config import settings
from app.dependencies.database import get_redis
from app.fetcher import Fetcher as OriginFetcher
from app.fetcher._base import TokenAuthError
from app.log import fetcher_logger

from fast_depends import Depends as FastDepends
from fastapi import Depends

fetcher: OriginFetcher | None = None
logger = fetcher_logger("FetcherDependency")


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
        try:
            await fetcher.ensure_valid_access_token()
        except TokenAuthError as exc:
            logger.warning(f"Failed to refresh fetcher access token during startup: {exc}. Will retry on demand.")
        except Exception as exc:
            logger.exception("Unexpected error while initializing fetcher access token", exc_info=exc)
    return fetcher


Fetcher = Annotated[OriginFetcher, Depends(get_fetcher), FastDepends(get_fetcher)]
