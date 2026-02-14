"""Password reset database models.

This module handles password reset requests and verification codes.
"""

from datetime import datetime

from app.utils import utcnow

from sqlalchemy import BigInteger, Column, ForeignKey
from sqlmodel import Field, SQLModel


class PasswordReset(SQLModel, table=True):
    """Database table for password reset requests."""

    __tablename__: str = "password_resets"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), nullable=False, index=True))
    email: str = Field(index=True)
    reset_code: str = Field(max_length=8)  # 8-character reset code
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime = Field()  # Code expiration time
    is_used: bool = Field(default=False)  # Whether the code has been used
    used_at: datetime | None = Field(default=None)
    ip_address: str | None = Field(default=None)  # Request IP
    user_agent: str | None = Field(default=None)  # User agent
