"""
用户偏好设置服务
处理用户偏好设置相关的业务逻辑，包括客户端类型检测和模式转换
"""

from __future__ import annotations

from app.database.auth import OAuthToken
from app.dependencies.database import Database
from app.models.score import GameMode


async def get_client_type_from_token(session: Database, token_id: int) -> str:
    """通过 token_id 从 oauth_tokens 表获取客户端类型"""
    token = await session.get(OAuthToken, token_id)
    if token and token.device_type:
        # 根据 device_type 映射到标准客户端类型
        device_type_mapping = {
            "osu_stable": "osu_stable",
            "osu_lazer": "osu_lazer",
            "osu_web": "osu_web",
            "web": "osu_web",
            "lazer": "osu_lazer",
            "stable": "osu_stable",
        }
        return device_type_mapping.get(token.device_type.lower(), "unknown")
    return "unknown"


def convert_mode_for_client(mode: GameMode, client_type: str) -> GameMode:
    """
    根据客户端类型转换游戏模式

    Args:
        mode: 原始游戏模式
        client_type: 客户端类型

    Returns:
        GameMode: 转换后的游戏模式
    """
    # 如果是 osu_lazer 客户端，需要转换特殊模式为标准模式
    if client_type == "osu_lazer":
        conversion_map = {
            GameMode.OSURX: GameMode.OSU,  # osurx -> osu
            GameMode.OSUAP: GameMode.OSU,  # osuap -> osu
            GameMode.TAIKORX: GameMode.TAIKO,  # taikorx -> taiko
            GameMode.FRUITSRX: GameMode.FRUITS,  # fruitsrx -> fruits
        }
        return conversion_map.get(mode, mode)

    # 其他客户端（osu_web等）保持原样
    return mode


def get_available_modes(client_type: str) -> list[str]:
    """获取客户端支持的游戏模式列表"""
    if client_type == "osu_web":
        # osu_web 支持所有模式
        return ["osu", "osurx", "osuap", "taiko", "taikorx", "fruits", "fruitsrx", "mania"]
    else:
        # osu_lazer 只支持标准模式
        return ["osu", "taiko", "fruits", "mania"]
