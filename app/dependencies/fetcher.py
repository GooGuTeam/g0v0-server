from typing import Annotated

from app.config import settings
from app.dependencies.database import get_redis
from app.fetcher import Fetcher as OriginFetcher
from app.log import fetcher_logger

from fastapi import Depends

fetcher: OriginFetcher | None = None


async def get_fetcher() -> OriginFetcher:
    global fetcher
    if fetcher is None:
        fetcher = OriginFetcher(
            settings.fetcher_client_id,
            settings.fetcher_client_secret,
            settings.fetcher_scopes,
            settings.fetcher_callback_url,
        )
        redis = get_redis()
        access_token = await redis.get(f"fetcher:access_token:{fetcher.client_id}")
        if access_token:
            fetcher.access_token = str(access_token)
        refresh_token = await redis.get(f"fetcher:refresh_token:{fetcher.client_id}")
        if refresh_token:
            fetcher.refresh_token = str(refresh_token)
        if not fetcher.access_token or not fetcher.refresh_token:
            fetcher_logger("Fetcher").opt(colors=True).info(
                f"Login to initialize fetcher: <y>{fetcher.authorize_url}</y>"
            )
    return fetcher


Fetcher = Annotated[OriginFetcher, Depends(get_fetcher)]
