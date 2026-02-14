"""Asset proxy helper functions and route decorators.

This module provides utilities for replacing osu! asset URLs with custom
proxied URLs to support asset proxying functionality.

Functions:
    replace_asset_urls: Replace osu! asset URLs in data structures.
    asset_proxy_response: Decorator for replacing asset URLs in responses.
"""

from collections.abc import Awaitable, Callable
from functools import wraps
import re
from typing import Any

from app.config import settings

from fastapi import Response
from pydantic import BaseModel

Handler = Callable[..., Awaitable[Any]]


def _replace_asset_urls_in_string(value: str) -> str:
    """Replace osu! asset URLs in a string with proxied URLs.

    Args:
        value: The string to process.

    Returns:
        The string with replaced URLs.
    """
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
    """Recursively replace osu! asset URLs in data structures.

    Args:
        data: The data structure to process (str, list, tuple, dict, etc.).

    Returns:
        The data with replaced URLs.
    """
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
    """Replace osu! asset URLs in data structures.

    Processes the data and replaces all osu! asset URLs with custom
    proxied URLs based on the application settings.

    Args:
        data: The data to process. Can be a dict, list, tuple, string,
            or Pydantic model.

    Returns:
        The data with all asset URLs replaced.
    """

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
    """Decorator to replace asset URLs in responses.

    This decorator wraps an async handler function and replaces all
    osu! asset URLs in the response with proxied URLs.

    Args:
        func: The async handler function to wrap.

    Returns:
        The wrapped handler function.
    """

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
