"""Screenshot database models."""

from datetime import datetime

from sqlalchemy import Index
from sqlmodel import VARCHAR, Column, DateTime, Field, SQLModel


class Screenshot(SQLModel, table=True):
    """Database table for submitted screenshots."""

    __tablename__: str = "screenshots"
    __table_args__ = (
        # Deduplication and reference counting: WHERE user_id = ? AND sha256_hash = ?
        Index("ix_screenshots_user_hash", "user_id", "sha256_hash"),
        # Cleanup scan: WHERE hits = 0 AND timestamp < ? ORDER BY timestamp
        Index("ix_screenshots_hits_timestamp", "hits", "timestamp"),
    )

    id: int = Field(default=None, primary_key=True)
    sha256_hash: str = Field(sa_column=Column(VARCHAR(64), index=True))
    url: str
    user_id: int = Field(index=True)
    timestamp: datetime = Field(sa_column=Column(DateTime))
    hits: int = Field(default=0)
    last_access: datetime = Field(sa_column=Column(DateTime))
