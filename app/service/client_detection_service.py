"""
客户端检测服务
用于识别不同类型的 osu! 客户端和设备
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import ClassVar, Literal

from app.log import logger


@dataclass
class ClientInfo:
    """客户端信息"""

    client_type: Literal["osu_stable", "osu_lazer", "osu_web", "mobile", "unknown"]
    platform: str | None = None
    version: str | None = None
    device_fingerprint: str | None = None
    is_trusted_client: bool = False


class ClientDetectionService:
    """客户端检测服务"""

    # osu! 客户端的 User-Agent 模式
    OSU_CLIENT_PATTERNS: ClassVar[dict[str, list[str]]] = {
        "osu_stable": [
            r"osu!/(\d+(?:\.\d+)*)",  # osu!/20241001 (带版本号的是stable)
        ],
        "osu_lazer": [
            r"osu-lazer/(\d+(?:\.\d+)*)",  # osu-lazer/2024.1009.0
            r"osu!lazer/(\d+(?:\.\d+)*)",  # osu!lazer/2024.1009.0
            r"^osu!$",  # 纯 "osu!" 是 lazer 客户端
        ],
        "osu_web": [
            r"Mozilla.*osu\.ppy\.sh",  # 网页客户端
        ],
        "mobile": [
            r"osu!.*mobile",
            r"osu.*Mobile",
            r"Mobile.*osu",
        ],
    }

    # 受信任的客户端类型（不需要频繁验证）
    TRUSTED_CLIENT_TYPES: ClassVar[set[str]] = {"osu_stable", "osu_lazer"}

    @staticmethod
    def detect_client(
        user_agent: str | None, client_id: int | None = None, api_version: str | None = None
    ) -> ClientInfo:
        """
        检测客户端类型和信息

        Args:
            user_agent: 用户代理字符串
            client_id: OAuth 客户端 ID
            api_version: API版本号 (来自 x-api-version 请求头)

        Returns:
            ClientInfo: 客户端信息
        """
        from app.config import settings  # 导入在函数内部避免循环导入

        if not user_agent:
            return ClientInfo(client_type="unknown")

        # 首先检查 API 版本号来区分 stable 和 lazer
        if api_version:
            if api_version.startswith("b"):
                # API版本号以 'b' 开头的是 osu!stable
                return ClientInfo(
                    client_type="osu_stable",
                    version=api_version,
                    platform=ClientDetectionService._extract_platform(user_agent),
                    device_fingerprint=ClientDetectionService._generate_device_fingerprint(user_agent),
                    is_trusted_client=True,
                )
            else:
                # 检查User-Agent是否为浏览器
                if any(browser in user_agent.lower() for browser in ["chrome", "firefox", "safari", "edge", "mozilla"]):
                    # 浏览器发送的API版本请求，应该是web客户端
                    return ClientInfo(
                        client_type="osu_web",
                        version=api_version,
                        platform=ClientDetectionService._extract_platform(user_agent),
                        device_fingerprint=ClientDetectionService._generate_device_fingerprint(user_agent),
                        is_trusted_client=False,
                    )
                else:
                    # 其他版本号格式通常是 osu!lazer
                    return ClientInfo(
                        client_type="osu_lazer",
                        version=api_version,
                        platform=ClientDetectionService._extract_platform(user_agent),
                        device_fingerprint=ClientDetectionService._generate_device_fingerprint(user_agent),
                        is_trusted_client=True,
                    )

        # 优先通过 client_id 判断客户端类型
        if client_id is not None:
            if client_id == settings.osu_client_id:
                # client_id = 5 是 osu!lazer 客户端
                return ClientInfo(
                    client_type="osu_lazer",
                    platform=ClientDetectionService._extract_platform(user_agent),
                    device_fingerprint=ClientDetectionService._generate_device_fingerprint(user_agent),
                    is_trusted_client=True,
                )
            elif client_id == settings.osu_web_client_id:
                # client_id = 6 是 osu_web 客户端
                return ClientInfo(
                    client_type="osu_web",
                    platform=ClientDetectionService._extract_platform(user_agent),
                    device_fingerprint=ClientDetectionService._generate_device_fingerprint(user_agent),
                    is_trusted_client=False,
                )

        # 回退到基于 User-Agent 的检测，按优先级顺序检测

        # 1. 首先检测浏览器（避免被osu!模式误匹配）
        if any(browser in user_agent.lower() for browser in ["chrome", "firefox", "safari", "edge", "mozilla"]):
            return ClientInfo(
                client_type="osu_web",
                platform=ClientDetectionService._extract_platform(user_agent),
                device_fingerprint=ClientDetectionService._generate_device_fingerprint(user_agent),
                is_trusted_client=False,
            )

        # 2. 按特定顺序检测osu!客户端（从最具体到最通用）
        detection_order = [
            ("osu_stable", ClientDetectionService.OSU_CLIENT_PATTERNS["osu_stable"]),
            ("osu_lazer", ClientDetectionService.OSU_CLIENT_PATTERNS["osu_lazer"]),
            ("mobile", ClientDetectionService.OSU_CLIENT_PATTERNS["mobile"]),
        ]

        for client_type_str, patterns in detection_order:
            for pattern in patterns:
                match = re.search(pattern, user_agent, re.IGNORECASE)
                if match:
                    version = match.group(1) if match.groups() else None
                    platform = ClientDetectionService._extract_platform(user_agent)

                    # 确保 client_type 是正确的 Literal 类型
                    client_type: Literal["osu_stable", "osu_lazer", "osu_web", "mobile", "unknown"] = client_type_str  # type: ignore

                    return ClientInfo(
                        client_type=client_type,
                        platform=platform,
                        version=version,
                        device_fingerprint=ClientDetectionService._generate_device_fingerprint(user_agent),
                        is_trusted_client=client_type in ClientDetectionService.TRUSTED_CLIENT_TYPES,
                    )

        return ClientInfo(
            client_type="unknown",
            device_fingerprint=ClientDetectionService._generate_device_fingerprint(user_agent),
            is_trusted_client=False,
        )

    @staticmethod
    def _extract_platform(user_agent: str) -> str | None:
        """从 User-Agent 中提取平台信息"""
        platforms = {
            "windows": ["windows", "win32", "win64"],
            "macos": ["macintosh", "mac os", "darwin"],
            "linux": ["linux", "ubuntu", "debian"],
            "android": ["android"],
            "ios": ["iphone", "ipad", "ios"],
        }

        user_agent_lower = user_agent.lower()
        for platform, keywords in platforms.items():
            if any(keyword in user_agent_lower for keyword in keywords):
                return platform

        return None

    @staticmethod
    def _generate_device_fingerprint(user_agent: str) -> str:
        """生成设备指纹"""
        # 使用 User-Agent 的哈希值作为简单的设备指纹
        # 在实际应用中可以结合更多信息（IP、屏幕分辨率等）
        return hashlib.sha256(user_agent.encode()).hexdigest()[:16]

    @staticmethod
    def should_skip_email_verification(
        client_info: ClientInfo,
        is_new_location: bool,
        user_id: int,
    ) -> bool:
        """
        判断是否应该跳过邮件验证

        Args:
            client_info: 客户端信息
            is_new_location: 是否为新位置登录
            user_id: 用户 ID

        Returns:
            bool: 是否应该跳过邮件验证
        """
        # 受信任的客户端类型可以减少验证频率
        if client_info.is_trusted_client:
            logger.info(
                f"[Client Detection] Trusted client {client_info.client_type} for user {user_id}, "
                f"reducing verification requirements"
            )
            return True

        # 如果不是新位置，跳过验证
        if not is_new_location:
            return True

        return False

    @staticmethod
    def get_verification_cooldown(client_info: ClientInfo) -> int:
        """
        获取验证冷却时间（秒）

        Args:
            client_info: 客户端信息

        Returns:
            int: 冷却时间（秒）
        """
        # 受信任的客户端有更长的冷却时间
        if client_info.is_trusted_client:
            return 3600  # 1小时

        # 网页客户端较短的冷却时间
        if client_info.client_type == "osu_web":
            return 1800  # 30分钟

        # 未知客户端最短冷却时间
        return 900  # 15分钟

    @staticmethod
    def format_client_display_name(client_info: ClientInfo) -> str:
        """格式化客户端显示名称"""
        display_names = {
            "osu_stable": "osu! (stable)",
            "osu_lazer": "osu!(lazer)",
            "osu_web": "osu! web",
            "mobile": "osu! mobile",
            "unknown": "Unknown client",
        }

        base_name = display_names.get(client_info.client_type, "Unknown client")

        if client_info.version:
            base_name += f" v{client_info.version}"

        if client_info.platform:
            base_name += f" ({client_info.platform})"

        return base_name
