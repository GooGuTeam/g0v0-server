"""Room participation tracking database models.

This module tracks users who have joined/left multiplayer rooms.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from app.helpers import utcnow

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import Mapped
from sqlmodel import Column, DateTime, Field, ForeignKey, Index, Integer, Relationship, SQLModel

if TYPE_CHECKING:
    from .room import Room
    from .user import User


class RoomParticipatedUser(AsyncAttrs, SQLModel, table=True):
    """Tracks user participation history in multiplayer rooms."""

    __tablename__: str = "room_participated_users"
    __table_args__ = (Index("ix_room_participated_users_room_left_at", "room_id", "left_at"),)

    room_id: int = Field(sa_column=Column(ForeignKey("rooms.id"), nullable=False, primary_key=True))
    user_id: int = Field(sa_column=Column(Integer, ForeignKey("lazer_users.id"), nullable=False, primary_key=True))
    joined_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=utcnow,
    )
    left_at: datetime | None = Field(sa_column=Column(DateTime(timezone=True), nullable=True), default=None)

    room: Mapped["Room"] = Relationship()
    user: Mapped["User"] = Relationship()
