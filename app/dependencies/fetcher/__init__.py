from __future__ import annotations

from app.config import settings
from app.dependencies.database import get_redis
from app.fetcher import Fetcher
from app.log import logger

fetcher: Fetcher | None = None


async def get_fetcher() -> Fetcher:
    global fetcher
    if fetcher is None:
        fetcher = Fetcher(
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
            logger.opt(colors=True).info(f"Login to initialize fetcher: <y>{fetcher.authorize_url}</y>")
    return fetcher
