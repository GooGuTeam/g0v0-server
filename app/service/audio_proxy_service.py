"""
音频代理服务
提供从osu!官方获取beatmapset音频预览并缓存的功能
"""

from app.log import logger
from app.models.error import ErrorType, RequestError

import httpx
import redis.asyncio as redis


class AudioProxyService:
    """音频代理服务"""

    def __init__(self, redis_binary_client: redis.Redis, redis_text_client: redis.Redis):
        self.redis_binary = redis_binary_client
        self.redis_text = redis_text_client
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self._cache_ttl = 7 * 24 * 60 * 60

    async def close(self):
        """关闭HTTP客户端"""
        await self.http_client.aclose()

    def _get_beatmapset_cache_key(self, beatmapset_id: int) -> str:
        """生成beatmapset音频缓存键"""
        return f"beatmapset_audio:{beatmapset_id}"

    def _get_beatmapset_metadata_key(self, beatmapset_id: int) -> str:
        """生成beatmapset音频元数据缓存键"""
        return f"beatmapset_audio_meta:{beatmapset_id}"

    async def get_beatmapset_audio_from_cache(self, beatmapset_id: int) -> tuple[bytes, str] | None:
        """从缓存获取beatmapset音频数据和内容类型"""
        try:
            cache_key = self._get_beatmapset_cache_key(beatmapset_id)
            metadata_key = self._get_beatmapset_metadata_key(beatmapset_id)

            # 获取音频数据（二进制）和元数据（文本）
            audio_data = await self.redis_binary.get(cache_key)
            metadata = await self.redis_text.get(metadata_key)

            if audio_data and metadata:
                logger.debug(f"Beatmapset audio cache hit for ID: {beatmapset_id}")
                # audio_data 已经是 bytes 类型，metadata 是 str 类型
                return audio_data, metadata
            return None
        except (redis.RedisError, redis.ConnectionError) as e:
            logger.error(f"Error getting beatmapset audio from cache: {e}")
            return None

    async def cache_beatmapset_audio(self, beatmapset_id: int, audio_data: bytes, content_type: str):
        """缓存beatmapset音频数据"""
        try:
            cache_key = self._get_beatmapset_cache_key(beatmapset_id)
            metadata_key = self._get_beatmapset_metadata_key(beatmapset_id)

            # 缓存音频数据（二进制）和元数据（文本）
            await self.redis_binary.setex(cache_key, self._cache_ttl, audio_data)
            await self.redis_text.setex(metadata_key, self._cache_ttl, content_type)

            logger.debug(f"Cached beatmapset audio for ID: {beatmapset_id}, size: {len(audio_data)} bytes")
        except (redis.RedisError, redis.ConnectionError) as e:
            logger.error(f"Error caching beatmapset audio: {e}")

    async def fetch_beatmapset_audio(self, beatmapset_id: int) -> tuple[bytes, str]:
        """从osu!官方获取beatmapset音频预览"""
        try:
            # 构建 osu! 官方预览音频 URL
            preview_url = f"https://b.ppy.sh/preview/{beatmapset_id}.mp3"
            logger.info(f"Fetching beatmapset audio from: {preview_url}")

            response = await self.http_client.get(preview_url)
            response.raise_for_status()

            # osu!预览音频通常为mp3格式
            content_type = response.headers.get("content-type", "audio/mpeg")
            audio_data = response.content

            # 检查文件大小限制（10MB，预览音频通常不会太大）
            max_size = 10 * 1024 * 1024  # 10MB
            if len(audio_data) > max_size:
                raise RequestError(
                    ErrorType.AUDIO_FILE_TOO_LARGE,
                    {"size": len(audio_data), "max_size": max_size},
                )

            if len(audio_data) == 0:
                raise RequestError(ErrorType.AUDIO_PREVIEW_NOT_AVAILABLE)

            logger.info(f"Successfully fetched beatmapset audio: {len(audio_data)} bytes, type: {content_type}")
            return audio_data, content_type

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching beatmapset audio for ID {beatmapset_id}: {e}")
            if e.response.status_code == 404:
                raise RequestError(ErrorType.AUDIO_PREVIEW_NOT_FOUND) from e
            else:
                raise RequestError(
                    ErrorType.INTERNAL_ERROR_FETCHING_AUDIO,
                    {"status_code": e.response.status_code},
                    status_code=e.response.status_code,
                ) from e
        except httpx.RequestError as e:
            logger.error(f"Request error fetching beatmapset audio for ID {beatmapset_id}: {e}")
            raise RequestError(ErrorType.FAILED_CONNECT_OSU_SERVERS) from e
        except Exception as e:
            logger.error(f"Unexpected error fetching beatmapset audio for ID {beatmapset_id}: {e}")
            raise RequestError(ErrorType.INTERNAL_ERROR_FETCHING_AUDIO) from e

    async def get_beatmapset_audio(self, beatmapset_id: int) -> tuple[bytes, str]:
        """根据 beatmapset_id 获取音频预览"""
        # 先尝试从缓存获取
        cached_result = await self.get_beatmapset_audio_from_cache(beatmapset_id)
        if cached_result:
            return cached_result

        # 缓存未命中，从osu!官方获取
        audio_data, content_type = await self.fetch_beatmapset_audio(beatmapset_id)

        # 缓存新获取的音频数据
        await self.cache_beatmapset_audio(beatmapset_id, audio_data, content_type)

        return audio_data, content_type


def get_audio_proxy_service(redis_binary_client: redis.Redis, redis_text_client: redis.Redis) -> AudioProxyService:
    """获取音频代理服务实例"""
    # 每次创建新实例，避免全局状态
    return AudioProxyService(redis_binary_client, redis_text_client)
