"""
密码重置相关数据库模型
"""

from __future__ import annotations

from datetime import datetime

from app.utils import utcnow

from sqlalchemy import BigInteger, Column, ForeignKey
from sqlmodel import Field, SQLModel


class PasswordReset(SQLModel, table=True):
    """密码重置记录"""

    __tablename__: str = "password_resets"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), nullable=False, index=True))
    email: str = Field(index=True)
    reset_code: str = Field(max_length=8)  # 8位重置验证码
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime = Field()  # 验证码过期时间
    is_used: bool = Field(default=False)  # 是否已使用
    used_at: datetime | None = Field(default=None)
    ip_address: str | None = Field(default=None)  # 请求IP
    user_agent: str | None = Field(default=None)  # 用户代理
