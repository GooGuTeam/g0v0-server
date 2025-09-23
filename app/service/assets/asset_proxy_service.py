"""
资源文件代理服务
提供URL替换方案：将osu!官方资源URL替换为自定义域名
"""

from __future__ import annotations

import re
from typing import Any

from app.config import settings


class AssetProxyService:
    """资源代理服务 - 仅URL替换模式"""

    def __init__(self):
        # 从配置获取自定义assets域名和前缀
        self.custom_asset_domain = settings.custom_asset_domain
        self.asset_proxy_prefix = settings.asset_proxy_prefix
        self.avatar_proxy_prefix = settings.avatar_proxy_prefix
        self.beatmap_proxy_prefix = settings.beatmap_proxy_prefix
        # 音频代理接口URL
        self.audio_proxy_base_url = f"{settings.server_url}api/private/audio/beatmapset"

    async def replace_asset_urls(self, data: Any) -> Any:
        """
        递归替换数据中的osu!资源URL为自定义域名
        """
        # 处理Pydantic模型
        if hasattr(data, "model_dump"):
            # 转换为字典，处理后再转换回模型
            data_dict = data.model_dump()
            processed_dict = await self.replace_asset_urls(data_dict)
            # 尝试从字典重新创建模型
            try:
                return data.__class__(**processed_dict)
            except Exception:
                # 如果重新创建失败，返回字典
                return processed_dict
        elif isinstance(data, dict):
            result = {}
            for key, value in data.items():
                result[key] = await self.replace_asset_urls(value)
            return result
        elif isinstance(data, list):
            return [await self.replace_asset_urls(item) for item in data]
        elif isinstance(data, str):
            # 替换各种osu!资源域名
            result = data

            # 替换 assets.ppy.sh (用户头像、封面、奖章等)
            result = re.sub(
                r"https://assets\.ppy\.sh/", f"https://{self.asset_proxy_prefix}.{self.custom_asset_domain}/", result
            )

            # 替换 b.ppy.sh 预览音频为我们的音频代理接口
            # 匹配 https://b.ppy.sh/preview/{beatmapset_id}.mp3 格式
            result = re.sub(r"https://b\.ppy\.sh/preview/(\d+)\.mp3", rf"{self.audio_proxy_base_url}/\1", result)

            # 匹配 //b.ppy.sh/preview/{beatmapset_id}.mp3 格式
            result = re.sub(r"//b\.ppy\.sh/preview/(\d+)\.mp3", rf"{self.audio_proxy_base_url}/\1", result)

            # 替换 a.ppy.sh 头像
            result = re.sub(
                r"https://a\.ppy\.sh/", f"https://{self.avatar_proxy_prefix}.{self.custom_asset_domain}/", result
            )

            return result
        else:
            return data


# 全局实例
_asset_proxy_service: AssetProxyService | None = None


def get_asset_proxy_service() -> AssetProxyService:
    """获取资源代理服务实例"""
    global _asset_proxy_service
    if _asset_proxy_service is None:
        _asset_proxy_service = AssetProxyService()
    return _asset_proxy_service
