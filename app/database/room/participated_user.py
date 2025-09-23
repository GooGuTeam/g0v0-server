from datetime import datetime
from typing import TYPE_CHECKING

from app.utils import utcnow

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
    from app.database.room import Room
    from app.database.user.lazer_user import User


class RoomParticipatedUser(AsyncAttrs, SQLModel, table=True):
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
