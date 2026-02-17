"""Room participation tracking database models.

This module tracks users who have joined/left multiplayer rooms.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from app.helpers import utcnow

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlmodel import (
    BigInteger,
    Column,
    DateTime,
    Field,
    ForeignKey,
    Relationship,
    SQLModel,
)

if TYPE_CHECKING:
    from .room import Room
    from .user import User


class RoomParticipatedUser(AsyncAttrs, SQLModel, table=True):
    """Tracks user participation history in multiplayer rooms."""

    __tablename__: str = "room_participated_users"

    id: int | None = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))
    room_id: int = Field(sa_column=Column(ForeignKey("rooms.id"), nullable=False))
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), nullable=False))
    joined_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=utcnow,
    )
    left_at: datetime | None = Field(sa_column=Column(DateTime(timezone=True), nullable=True), default=None)

    room: "Room" = Relationship()
    user: "User" = Relationship()
