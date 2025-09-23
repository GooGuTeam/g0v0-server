"""
API 状态管理 - 模拟 osu! 的 APIState 和会话管理
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class APIState(str, Enum):
    """API 连接状态，对应 osu! 的 APIState"""

    OFFLINE = "offline"
    CONNECTING = "connecting"
    REQUIRES_SECOND_FACTOR_AUTH = "requires_second_factor_auth"  # 需要二次验证
    ONLINE = "online"
    FAILING = "failing"


class UserSession(BaseModel):
    """用户会话信息"""

    user_id: int
    username: str
    email: str
    session_token: str | None = None
    state: APIState = APIState.OFFLINE
    requires_verification: bool = False
    verification_sent: bool = False
    last_verification_attempt: datetime | None = None
    failed_attempts: int = 0
    ip_address: str | None = None
    country_code: str | None = None
    is_new_location: bool = False


class SessionManager:
    """会话管理器"""

    def __init__(self):
        self._sessions: dict[str, UserSession] = {}

    def create_session(
        self,
        user_id: int,
        username: str,
        email: str,
        ip_address: str,
        country_code: str | None = None,
        is_new_location: bool = False,
    ) -> UserSession:
        """创建新的用户会话"""
        import secrets

        session_token = secrets.token_urlsafe(32)

        # 根据是否为新位置决定初始状态
        if is_new_location:
            state = APIState.REQUIRES_SECOND_FACTOR_AUTH
        else:
            state = APIState.ONLINE

        session = UserSession(
            user_id=user_id,
            username=username,
            email=email,
            session_token=session_token,
            state=state,
            requires_verification=is_new_location,
            ip_address=ip_address,
            country_code=country_code,
            is_new_location=is_new_location,
        )

        self._sessions[session_token] = session
        return session

    def get_session(self, session_token: str) -> UserSession | None:
        """获取会话"""
        return self._sessions.get(session_token)

    def update_session_state(self, session_token: str, state: APIState):
        """更新会话状态"""
        if session_token in self._sessions:
            self._sessions[session_token].state = state

    def mark_verification_sent(self, session_token: str):
        """标记验证邮件已发送"""
        if session_token in self._sessions:
            session = self._sessions[session_token]
            session.verification_sent = True
            session.last_verification_attempt = datetime.now()

    def increment_failed_attempts(self, session_token: str):
        """增加失败尝试次数"""
        if session_token in self._sessions:
            self._sessions[session_token].failed_attempts += 1

    def verify_session(self, session_token: str) -> bool:
        """验证会话成功"""
        if session_token in self._sessions:
            session = self._sessions[session_token]
            session.state = APIState.ONLINE
            session.requires_verification = False
            return True
        return False

    def remove_session(self, session_token: str):
        """移除会话"""
        self._sessions.pop(session_token, None)

    def cleanup_expired_sessions(self):
        """清理过期会话"""
        # 这里可以实现清理逻辑
        pass


# 全局会话管理器
session_manager = SessionManager()
