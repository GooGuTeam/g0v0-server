"""Email verification and session management database models.

This module handles email verification, login sessions, and trusted devices
for user authentication and security.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Literal, Optional

from app.helpers.geoip_helper import GeoIPHelper
from app.models.model import UserAgentInfo, UTCBaseModel
from app.utils import extract_user_agent, utcnow

from pydantic import BaseModel
from sqlalchemy import BigInteger, Column, ForeignKey
from sqlmodel import VARCHAR, DateTime, Field, Integer, Relationship, SQLModel, Text

if TYPE_CHECKING:
    from .auth import OAuthToken


class Location(BaseModel):
    """Geographic location data."""

    country: str = ""
    city: str = ""
    country_code: str = ""


class EmailVerification(SQLModel, table=True):
    """Database table for email verification records."""

    __tablename__: str = "email_verifications"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), nullable=False, index=True))
    email: str = Field(index=True)
    verification_code: str = Field(max_length=8)  # 8-character verification code
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime = Field()  # Code expiration time
    is_used: bool = Field(default=False)  # Whether the code has been used
    used_at: datetime | None = Field(default=None)
    ip_address: str | None = Field(default=None)  # Request IP
    user_agent: str | None = Field(default=None)  # User agent


class LoginSessionBase(SQLModel):
    """Base fields for login sessions."""

    id: int = Field(default=None, primary_key=True)
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), nullable=False, index=True))
    ip_address: str = Field(sa_column=Column(VARCHAR(45), nullable=False), default="127.0.0.1", exclude=True)
    user_agent: str | None = Field(default=None, sa_column=Column(Text))
    is_verified: bool = Field(default=False)  # Whether the session is verified
    created_at: datetime = Field(default_factory=lambda: utcnow())
    verified_at: datetime | None = Field(default=None)
    expires_at: datetime = Field()  # Session expiration time
    device_id: int | None = Field(
        sa_column=Column(BigInteger, ForeignKey("trusted_devices.id", ondelete="SET NULL"), nullable=True, index=True),
        default=None,
    )


class LoginSession(LoginSessionBase, table=True):
    """Database table for login sessions."""

    __tablename__: str = "login_sessions"
    token_id: int | None = Field(
        sa_column=Column(Integer, ForeignKey("oauth_tokens.id", ondelete="SET NULL"), nullable=True, index=True),
        exclude=True,
    )
    is_new_device: bool = Field(default=False, exclude=True)  # New device login
    web_uuid: str | None = Field(sa_column=Column(VARCHAR(36), nullable=True), default=None, exclude=True)
    verification_method: str | None = Field(default=None, max_length=20, exclude=True)  # totp/mail

    device: Optional["TrustedDevice"] = Relationship(back_populates="sessions")
    token: Optional["OAuthToken"] = Relationship(back_populates="login_session")


class LoginSessionResp(UTCBaseModel, LoginSessionBase):
    """Response model for login sessions."""

    user_agent_info: UserAgentInfo | None = None
    location: Location | None = None

    @classmethod
    def from_db(cls, obj: LoginSession, get_geoip_helper: GeoIPHelper) -> "LoginSessionResp":
        session = cls.model_validate(obj.model_dump())
        session.user_agent_info = extract_user_agent(session.user_agent)
        if obj.ip_address:
            loc = get_geoip_helper.lookup(obj.ip_address)
            session.location = Location(
                country=loc.get("country_name", ""),
                city=loc.get("city_name", ""),
                country_code=loc.get("country_code", ""),
            )
        else:
            session.location = None
        return session


class TrustedDeviceBase(SQLModel):
    """Base fields for trusted devices."""

    id: int = Field(default=None, primary_key=True)
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), nullable=False, index=True))
    ip_address: str = Field(sa_column=Column(VARCHAR(45), nullable=False), default="127.0.0.1", exclude=True)
    user_agent: str = Field(sa_column=Column(Text, nullable=False))
    client_type: Literal["web", "client"] = Field(sa_column=Column(VARCHAR(10), nullable=False), default="web")
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime = Field(sa_column=Column(DateTime))


class TrustedDevice(TrustedDeviceBase, table=True):
    """Database table for trusted devices."""

    __tablename__: str = "trusted_devices"
    web_uuid: str | None = Field(sa_column=Column(VARCHAR(36), nullable=True), default=None)

    sessions: list["LoginSession"] = Relationship(back_populates="device", passive_deletes=True)


class TrustedDeviceResp(UTCBaseModel, TrustedDeviceBase):
    """Response model for trusted devices."""

    user_agent_info: UserAgentInfo | None = None
    location: Location | None = None

    @classmethod
    def from_db(cls, device: TrustedDevice, get_geoip_helper: GeoIPHelper) -> "TrustedDeviceResp":
        device_ = cls.model_validate(device.model_dump())
        device_.user_agent_info = extract_user_agent(device_.user_agent)
        if device.ip_address:
            loc = get_geoip_helper.lookup(device.ip_address)
            device_.location = Location(
                country=loc.get("country_name", ""),
                city=loc.get("city_name", ""),
                country_code=loc.get("country_code", ""),
            )
        else:
            device_.location = None
        return device_
