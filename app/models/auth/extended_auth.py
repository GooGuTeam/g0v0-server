"""
扩展的 OAuth 响应模型，支持二次验证
"""

from __future__ import annotations

from pydantic import BaseModel


class ExtendedTokenResponse(BaseModel):
    """扩展的令牌响应，支持二次验证状态"""

    access_token: str | None = None
    token_type: str = "Bearer"
    expires_in: int | None = None
    refresh_token: str | None = None
    scope: str | None = None

    # 二次验证相关字段
    requires_second_factor: bool = False
    verification_message: str | None = None
    user_id: int | None = None  # 用于二次验证的用户ID


class SessionState(BaseModel):
    """会话状态"""

    user_id: int
    username: str
    email: str
    requires_verification: bool
    session_token: str | None = None
    verification_sent: bool = False
