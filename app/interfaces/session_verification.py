"""
会话验证接口

基于osu-web的SessionVerificationInterface实现
用于标准化会话验证行为
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class SessionVerificationInterface(ABC):
    """会话验证接口

    定义了会话验证所需的基本操作，参考osu-web的实现
    """

    @classmethod
    @abstractmethod
    async def find_for_verification(cls, session_id: str) -> SessionVerificationInterface | None:
        """根据会话ID查找会话用于验证

        Args:
            session_id: 会话ID

        Returns:
            会话实例或None
        """
        pass

    @abstractmethod
    def get_key(self) -> str:
        """获取会话密钥/ID"""
        pass

    @abstractmethod
    def get_key_for_event(self) -> str:
        """获取用于事件广播的会话密钥"""
        pass

    @abstractmethod
    def get_verification_method(self) -> str | None:
        """获取当前验证方法

        Returns:
            验证方法 ('totp', 'mail') 或 None
        """
        pass

    @abstractmethod
    def is_verified(self) -> bool:
        """检查会话是否已验证"""
        pass

    @abstractmethod
    async def mark_verified(self) -> None:
        """标记会话为已验证"""
        pass

    @abstractmethod
    async def set_verification_method(self, method: str) -> None:
        """设置验证方法

        Args:
            method: 验证方法 ('totp', 'mail')
        """
        pass

    @abstractmethod
    def user_id(self) -> int | None:
        """获取关联的用户ID"""
        pass
