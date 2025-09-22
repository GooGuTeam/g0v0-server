"""
邮件验证相关数据库模型
"""

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from app.utils import utcnow

from sqlalchemy import BigInteger, Column, ForeignKey
from sqlmodel import Field, Integer, Relationship, SQLModel

if TYPE_CHECKING:
    from app.database.auth import OAuthToken


class EmailVerification(SQLModel, table=True):
    """邮件验证记录"""

    __tablename__: str = "email_verifications"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), nullable=False, index=True))
    email: str = Field(index=True)
    verification_code: str = Field(max_length=8)  # 8位验证码
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime = Field()  # 验证码过期时间
    is_used: bool = Field(default=False)  # 是否已使用
    used_at: datetime | None = Field(default=None)
    ip_address: str | None = Field(default=None)  # 请求IP
    user_agent: str | None = Field(default=None)  # 用户代理


class LoginSession(SQLModel, table=True):
    """登录会话记录"""

    __tablename__: str = "login_sessions"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), nullable=False, index=True))
    token_id: int | None = Field(
        sa_column=Column(Integer, ForeignKey("oauth_tokens.id", ondelete="SET NULL"), nullable=True, index=True)
    )
    ip_address: str = Field()  # 登录IP
    user_agent: str | None = Field(default=None, max_length=250)
    country_code: str | None = Field(default=None)
    is_verified: bool = Field(default=False)  # 是否已验证
    created_at: datetime = Field(default_factory=lambda: utcnow())
    verified_at: datetime | None = Field(default=None)
    expires_at: datetime = Field()  # 会话过期时间
    is_new_location: bool = Field(default=False)  # 是否新位置登录

    token: Optional["OAuthToken"] = Relationship(back_populates="login_session")
