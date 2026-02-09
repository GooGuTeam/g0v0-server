"""
音频代理接口
提供从osu!官方获取beatmapset音频预览的代理服务
"""

from typing import Annotated

from app.dependencies.database import get_redis, get_redis_binary
from app.models.error import ErrorType, RequestError
from app.service.audio_proxy_service import AudioProxyService, get_audio_proxy_service

from fastapi import APIRouter, Depends, Path
from fastapi.responses import Response
from fastapi_limiter.depends import RateLimiter
from loguru import logger
from pyrate_limiter import Duration, Limiter, Rate
import redis.asyncio as redis

router = APIRouter(prefix="/audio", tags=["Audio Proxy"])


async def get_audio_proxy_dependency(
    redis_binary_client: Annotated[redis.Redis, Depends(get_redis_binary)],
    redis_text_client: Annotated[redis.Redis, Depends(get_redis)],
) -> AudioProxyService:
    """音频代理服务依赖注入"""
    return get_audio_proxy_service(redis_binary_client, redis_text_client)


@router.get(
    "/beatmapset/{beatmapset_id}",
    dependencies=[
        Depends(RateLimiter(limiter=Limiter(Rate(30, Duration.MINUTE)))),  # 每分钟最多30次请求
        Depends(RateLimiter(limiter=Limiter(Rate(5, Duration.SECOND * 10)))),  # 每10秒最多5次请求
    ],
)
async def get_beatmapset_audio(
    beatmapset_id: Annotated[int, Path(description="谱面集ID", ge=1)],
    audio_service: Annotated[AudioProxyService, Depends(get_audio_proxy_dependency)],
):
    """
    获取谱面集的音频预览

    根据谱面集ID获取osu!官方的音频预览文件。
    音频文件会被缓存7天以提高响应速度。

    速率限制:
    - 每分钟最多30次请求
    - 每10秒最多5次请求

    参数:
    - beatmapset_id: 谱面集ID

    返回:
    - 音频文件的二进制数据，Content-Type为audio/mpeg
    """
    try:
        # 获取谱面集音频数据
        audio_data, content_type = await audio_service.get_beatmapset_audio(beatmapset_id)

        # 返回音频响应
        return Response(
            content=audio_data,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=604800",  # 7天缓存
                "Content-Length": str(len(audio_data)),
                "Content-Disposition": f'inline; filename="{beatmapset_id}.mp3"',
            },
        )

    except RequestError:
        # 重新抛出 API 级错误
        raise
    except Exception as e:
        logger.error(f"Unexpected error getting beatmapset audio: {e}")
        raise RequestError(ErrorType.INTERNAL_ERROR_FETCHING_AUDIO) from e
