"""
资源代理辅助函数和中间件
"""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.service.assets.asset_proxy_service import get_asset_proxy_service

from fastapi import Request


async def process_response_assets(data: Any, request: Request) -> Any:
    """
    根据配置处理响应数据中的资源URL

    Args:
        data: API响应数据
        request: FastAPI请求对象

    Returns:
        处理后的数据
    """
    if not settings.enable_asset_proxy:
        return data

    asset_service = get_asset_proxy_service()

    # 仅URL替换模式
    return await asset_service.replace_asset_urls(data)


def should_process_asset_proxy(path: str) -> bool:
    """
    判断路径是否需要处理资源代理
    """
    # 只对特定的API端点处理资源代理
    asset_proxy_endpoints = [
        "/api/v1/users/",
        "/api/v2/users/",
        "/api/v1/me/",
        "/api/v2/me/",
        "/api/v2/beatmapsets/search",
        "/api/v2/beatmapsets/lookup",
        "/api/v2/beatmaps/",
        "/api/v1/beatmaps/",
        "/api/v2/beatmapsets/",
        # 可以根据需要添加更多端点
    ]

    return any(path.startswith(endpoint) for endpoint in asset_proxy_endpoints)


# 响应处理装饰器
def asset_proxy_response(func):
    """
    装饰器：自动处理响应中的资源URL
    """

    async def wrapper(*args, **kwargs):
        # 获取request对象
        request = None
        for arg in args:
            if isinstance(arg, Request):
                request = arg
                break

        # 执行原函数
        result = await func(*args, **kwargs)

        # 如果有request对象且启用了资源代理，则处理响应
        if request and settings.enable_asset_proxy and should_process_asset_proxy(request.url.path):
            result = await process_response_assets(result, request)

        return result

    return wrapper
