"""Audio proxy API endpoints.

Provides proxy service for fetching beatmapset audio previews from osu! official servers.
"""

from typing import Annotated

from app.dependencies.database import get_redis, get_redis_binary
from app.dependencies.rate_limit import create_rate_limiter
from app.log import log
from app.models.error import ErrorType, RequestError
from app.service.audio_proxy_service import AudioProxyService, get_audio_proxy_service

from fastapi import APIRouter, Depends, Path
from fastapi.responses import Response
from pyrate_limiter import Duration, Rate
import redis.asyncio as redis

router = APIRouter(prefix="/audio", tags=["Audio Proxy"])
logger = log("AudioProxy")


async def get_audio_proxy_dependency(
    redis_binary_client: Annotated[redis.Redis, Depends(get_redis_binary)],
    redis_text_client: Annotated[redis.Redis, Depends(get_redis)],
) -> AudioProxyService:
    """Dependency injection for audio proxy service.

    Args:
        redis_binary_client: Redis client for binary data.
        redis_text_client: Redis client for text data.

    Returns:
        AudioProxyService instance.
    """
    return get_audio_proxy_service(redis_binary_client, redis_text_client)


@router.get(
    "/beatmapset/{beatmapset_id}",
    dependencies=[
        Depends(create_rate_limiter(Rate(30, Duration.MINUTE), bucket_key="rate-limit:private:audio:minute")),
        Depends(create_rate_limiter(Rate(5, Duration.SECOND * 10), bucket_key="rate-limit:private:audio:burst")),
    ],
    description="Get audio preview for a beatmapset.",
)
async def get_beatmapset_audio(
    beatmapset_id: Annotated[int, Path(description="Beatmapset ID", ge=1)],
    audio_service: Annotated[AudioProxyService, Depends(get_audio_proxy_dependency)],
):
    try:
        # Get beatmapset audio data
        audio_data, content_type = await audio_service.get_beatmapset_audio(beatmapset_id)
        logger.debug(f"Served proxied audio for beatmapset {beatmapset_id}; size={len(audio_data)} bytes")

        # Return audio response
        return Response(
            content=audio_data,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=604800",  # 7 days cache
                "Content-Length": str(len(audio_data)),
                "Content-Disposition": f'inline; filename="{beatmapset_id}.mp3"',
            },
        )

    except RequestError:
        # Re-raise API-level errors
        raise
    except Exception as e:
        logger.error(f"Unexpected error getting beatmapset audio: {e}")
        raise RequestError(ErrorType.INTERNAL_ERROR_FETCHING_AUDIO) from e
