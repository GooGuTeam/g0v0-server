"""
音频代理接口
提供从osu!官方获取beatmapset音频预览的代理服务
"""

from __future__ import annotations

from typing import Annotated

from app.dependencies.database import get_redis
from app.service.assets.audio_proxy_service import AudioProxyService, get_audio_proxy_service

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import Response
from loguru import logger
import redis.asyncio as redis

router = APIRouter(prefix="/audio", tags=["Audio Proxy"])


async def get_audio_proxy_dependency(redis_client: Annotated[redis.Redis, Depends(get_redis)]) -> AudioProxyService:
    """音频代理服务依赖注入"""
    return get_audio_proxy_service(redis_client)


@router.get("/beatmapset/{beatmapset_id}")
async def get_beatmapset_audio(
    beatmapset_id: Annotated[int, Path(description="谱面集ID", ge=1)],
    audio_service: Annotated[AudioProxyService, Depends(get_audio_proxy_dependency)],
):
    """
    获取谱面集的音频预览

    根据谱面集ID获取osu!官方的音频预览文件。
    音频文件会被缓存7天以提高响应速度。

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

    except HTTPException:
        # 重新抛出HTTP异常
        raise
    except Exception as e:
        logger.error(f"Unexpected error getting beatmapset audio: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e
