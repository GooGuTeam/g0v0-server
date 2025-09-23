from __future__ import annotations

from app.fetcher.base import BaseFetcher

from httpx import AsyncClient, HTTPError
from httpx._models import Response
from loguru import logger
import redis.asyncio as redis

urls = [
    "https://osu.ppy.sh/osu/{beatmap_id}",
    "https://osu.direct/api/osu/{beatmap_id}",
    "https://catboy.best/osu/{beatmap_id}",
]


class BeatmapRawFetcher(BaseFetcher):
    async def get_beatmap_raw(self, beatmap_id: int) -> str:
        for url in urls:
            req_url = url.format(beatmap_id=beatmap_id)
            logger.opt(colors=True).debug(f"<blue>[BeatmapRawFetcher]</blue> get_beatmap_raw: <y>{req_url}</y>")
            resp = await self._request(req_url)
            if resp.status_code >= 400:
                continue
            return resp.text
        raise HTTPError("Failed to fetch beatmap")

    async def _request(self, url: str) -> Response:
        async with AsyncClient() as client:
            response = await client.get(
                url,
            )
            return response

    async def get_or_fetch_beatmap_raw(self, redis: redis.Redis, beatmap_id: int) -> str:
        from app.config import settings

        cache_key = f"beatmap:{beatmap_id}:raw"
        cache_expire = settings.beatmap_cache_expire_hours * 60 * 60

        # 检查缓存
        if await redis.exists(cache_key):
            content = await redis.get(cache_key)
            if content:
                # 延长缓存时间
                await redis.expire(cache_key, cache_expire)
                return content

        # 获取并缓存
        raw = await self.get_beatmap_raw(beatmap_id)
        await redis.set(cache_key, raw, ex=cache_expire)
        return raw
