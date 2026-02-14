"""Multiplayer room event database models.

This module tracks events that occur in multiplayer rooms
such as player joins, game starts, and score submissions.
"""

from datetime import datetime
from typing import Any

from app.helpers import utcnow
from app.models.model import UTCBaseModel

from sqlmodel import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Field,
    ForeignKey,
    SQLModel,
)


class MultiplayerEventBase(SQLModel, UTCBaseModel):
    """Base fields for multiplayer events."""

    playlist_item_id: int | None = None
    user_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True),
    )
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
        ),
        default_factory=utcnow,
    )
    event_type: str = Field(index=True)


class MultiplayerEvent(MultiplayerEventBase, table=True):
    """Database table for multiplayer room events."""

    __tablename__: str = "multiplayer_events"
    id: int = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True, index=True),
    )
    room_id: int = Field(foreign_key="rooms.id", index=True)
    updated_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
        ),
        default_factory=utcnow,
    )
    event_detail: dict[str, Any] | None = Field(
        sa_column=Column(JSON),
        default_factory=dict,
    )


class MultiplayerEventResp(MultiplayerEventBase):
    """Response model for multiplayer events."""

    id: int

    @classmethod
    def from_db(cls, event: MultiplayerEvent) -> "MultiplayerEventResp":
        return cls.model_validate(event)
