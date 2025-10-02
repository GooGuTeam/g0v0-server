from datetime import datetime
import secrets
from typing import TYPE_CHECKING

from app.models.model import UTCBaseModel
from app.utils import utcnow

from .verification import LoginSession

from sqlalchemy import Column, DateTime
from sqlmodel import (
    JSON,
    BigInteger,
    Field,
    ForeignKey,
    Relationship,
    SQLModel,
    Text,
)

if TYPE_CHECKING:
    from .lazer_user import User


class OAuthToken(UTCBaseModel, SQLModel, table=True):
    __tablename__: str = "oauth_tokens"

    id: int = Field(default=None, primary_key=True, index=True)
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    client_id: int = Field(index=True)
    access_token: str = Field(max_length=500, unique=True)
    refresh_token: str = Field(max_length=500, unique=True)
    token_type: str = Field(default="Bearer", max_length=20)
    scope: str = Field(default="*", max_length=100)
    expires_at: datetime = Field(sa_column=Column(DateTime, index=True))
    refresh_token_expires_at: datetime = Field(sa_column=Column(DateTime, index=True))
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime))

    user: "User" = Relationship()
    login_session: LoginSession | None = Relationship(back_populates="token", passive_deletes=True)


class OAuthClient(SQLModel, table=True):
    __tablename__: str = "oauth_clients"
    name: str = Field(max_length=100, index=True)
    description: str = Field(sa_column=Column(Text), default="")
    client_id: int | None = Field(default=None, primary_key=True, index=True)
    client_secret: str = Field(default_factory=secrets.token_hex, index=True)
    redirect_uris: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    owner_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))


class V1APIKeys(SQLModel, table=True):
    __tablename__: str = "v1_api_keys"
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=100, index=True)
    key: str = Field(default_factory=secrets.token_hex, index=True)
    owner_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))


class TotpKeys(SQLModel, table=True):
    __tablename__: str = "totp_keys"
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), primary_key=True))
    secret: str = Field(max_length=100)
    backup_keys: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime))

    user: "User" = Relationship(back_populates="totp_key")
