"""
邮件验证相关数据库模型
"""

from datetime import datetime
from typing import TYPE_CHECKING, Literal, Optional

from app.models.model import UTCBaseModel
from app.utils import extract_user_agent, utcnow

from sqlalchemy import BigInteger, Column, ForeignKey
from sqlmodel import VARCHAR, DateTime, Field, Integer, Relationship, SQLModel, Text

if TYPE_CHECKING:
    from .auth import OAuthToken


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


class LoginSessionBase(SQLModel):
    """登录会话记录"""

    id: int = Field(default=None, primary_key=True)
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), nullable=False, index=True))
    ip_address: str = Field(sa_column=Column(VARCHAR(45), nullable=False), default="127.0.0.1")
    user_agent: str | None = Field(default=None, sa_column=Column(Text))
    is_verified: bool = Field(default=False)  # 是否已验证
    created_at: datetime = Field(default_factory=lambda: utcnow())
    verified_at: datetime | None = Field(default=None)
    expires_at: datetime = Field()  # 会话过期时间
    device_id: int | None = Field(
        sa_column=Column(BigInteger, ForeignKey("trusted_devices.id", ondelete="SET NULL"), nullable=True, index=True),
        default=None,
    )


class LoginSession(LoginSessionBase, table=True):
    __tablename__: str = "login_sessions"
    token_id: int | None = Field(
        sa_column=Column(Integer, ForeignKey("oauth_tokens.id", ondelete="SET NULL"), nullable=True, index=True),
        exclude=True,
    )
    is_new_device: bool = Field(default=False, exclude=True)  # 是否新位置登录
    web_uuid: str | None = Field(sa_column=Column(VARCHAR(36), nullable=True), default=None, exclude=True)
    verification_method: str | None = Field(default=None, max_length=20, exclude=True)  # 验证方法 (totp/mail)

    device: Optional["TrustedDevice"] = Relationship(back_populates="sessions")
    token: Optional["OAuthToken"] = Relationship(back_populates="login_session")


class LoginSessionResp(UTCBaseModel, LoginSessionBase):
    is_client: bool = False

    @classmethod
    def from_db(cls, obj: LoginSession) -> "LoginSessionResp":
        session = cls.model_validate(obj.model_dump())
        ua = extract_user_agent(session.user_agent)
        session.is_client = ua.is_client
        return session


class TrustedDeviceBase(SQLModel):
    id: int = Field(default=None, primary_key=True)
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), nullable=False, index=True))
    ip_address: str = Field(sa_column=Column(VARCHAR(45), nullable=False), default="127.0.0.1")
    user_agent: str = Field(sa_column=Column(Text, nullable=False))
    client_type: Literal["web", "client"] = Field(sa_column=Column(VARCHAR(10), nullable=False), default="web")
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime = Field(sa_column=Column(DateTime))


class TrustedDevice(TrustedDeviceBase, table=True):
    __tablename__: str = "trusted_devices"
    web_uuid: str | None = Field(sa_column=Column(VARCHAR(36), nullable=True), default=None)

    sessions: list["LoginSession"] = Relationship(back_populates="device", passive_deletes=True)


class TrustedDeviceResp(UTCBaseModel, TrustedDeviceBase):
    @classmethod
    def from_db(cls, device: TrustedDevice) -> "TrustedDeviceResp":
        return cls.model_validate(device.model_dump())
