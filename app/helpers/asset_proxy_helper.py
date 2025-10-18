"""资源代理辅助方法与路由装饰器。"""

from collections.abc import Awaitable, Callable
from functools import wraps
import re
from typing import Any

from app.config import settings

from fastapi import Response
from pydantic import BaseModel

Handler = Callable[..., Awaitable[Any]]


def _replace_asset_urls_in_string(value: str) -> str:
    result = value
    custom_domain = settings.custom_asset_domain
    asset_prefix = settings.asset_proxy_prefix
    avatar_prefix = settings.avatar_proxy_prefix
    beatmap_prefix = settings.beatmap_proxy_prefix
    audio_proxy_base_url = f"{settings.server_url}api/private/audio/beatmapset"

    result = re.sub(
        r"^https://assets\.ppy\.sh/",
        f"https://{asset_prefix}.{custom_domain}/",
        result,
    )

    result = re.sub(
        r"^https://b\.ppy\.sh/preview/(\d+)\\.mp3",
        rf"{audio_proxy_base_url}/\1",
        result,
    )

    result = re.sub(
        r"^//b\.ppy\.sh/preview/(\d+)\\.mp3",
        rf"{audio_proxy_base_url}/\1",
        result,
    )

    result = re.sub(
        r"^https://a\.ppy\.sh/",
        f"https://{avatar_prefix}.{custom_domain}/",
        result,
    )

    result = re.sub(
        r"https://b\.ppy\.sh/",
        f"https://{beatmap_prefix}.{custom_domain}/",
        result,
    )
    return result


def _replace_asset_urls_in_data(data: Any) -> Any:
    if isinstance(data, str):
        return _replace_asset_urls_in_string(data)
    if isinstance(data, list):
        return [_replace_asset_urls_in_data(item) for item in data]
    if isinstance(data, tuple):
        return tuple(_replace_asset_urls_in_data(item) for item in data)
    if isinstance(data, dict):
        return {key: _replace_asset_urls_in_data(value) for key, value in data.items()}
    return data


async def replace_asset_urls(data: Any) -> Any:
    """替换数据中的 osu! 资源 URL。"""

    if not settings.enable_asset_proxy:
        return data

    if hasattr(data, "model_dump"):
        raw = data.model_dump()
        processed = _replace_asset_urls_in_data(raw)
        try:
            return data.__class__(**processed)
        except Exception:
            return processed

    if isinstance(data, (dict, list, tuple, str)):
        return _replace_asset_urls_in_data(data)

    return data


def asset_proxy_response(func: Handler) -> Handler:
    """装饰器：在返回响应前替换资源 URL。"""

    @wraps(func)
    async def wrapper(*args, **kwargs):
        result = await func(*args, **kwargs)

        if not settings.enable_asset_proxy:
            return result

        if isinstance(result, Response):
            return result

        if isinstance(result, BaseModel):
            result = result.model_dump()

        return _replace_asset_urls_in_data(result)

    return wrapper  # type: ignore[return-value]
