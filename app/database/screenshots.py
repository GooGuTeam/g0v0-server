"""Screenshot database models."""

from datetime import datetime

from sqlmodel import VARCHAR, Column, DateTime, Field, SQLModel


class Screenshot(SQLModel, table=True):
    """Database table for submitted screenshots."""

    __tablename__: str = "screenshots"

    id: int = Field(default=None, primary_key=True)
    sha256_hash: str = Field(sa_column=Column(VARCHAR(64), index=True))
    url: str
    user_id: int = Field(index=True)
    timestamp: datetime = Field(sa_column=Column(DateTime))
    hits: int = Field(default=0)
    last_access: datetime = Field(sa_column=Column(DateTime))
