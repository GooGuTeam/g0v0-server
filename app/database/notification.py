"""Notification system database models.

This module handles user notifications including storage,
delivery tracking, and read status.
"""

from datetime import datetime
from typing import Any

from app.helpers import utcnow
from app.models.notification import NotificationDetail, NotificationName

from sqlmodel import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Field,
    ForeignKey,
    Relationship,
    SQLModel,
)
from sqlmodel.ext.asyncio.session import AsyncSession


class Notification(SQLModel, table=True):
    """Database table for notifications."""

    __tablename__: str = "notifications"

    id: int = Field(primary_key=True, index=True, default=None)
    name: NotificationName = Field(index=True)
    category: str = Field(max_length=255, index=True)
    created_at: datetime = Field(sa_column=Column(DateTime))
    object_type: str = Field(index=True)
    object_id: int = Field(sa_column=Column(BigInteger, index=True))
    source_user_id: int = Field(index=True)
    details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class UserNotification(SQLModel, table=True):
    """Database table linking notifications to users with read status."""

    __tablename__: str = "user_notifications"
    id: int = Field(
        sa_column=Column(
            BigInteger,
            primary_key=True,
            index=True,
        ),
        default=None,
    )
    notification_id: int = Field(index=True, foreign_key="notifications.id")
    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), index=True))
    is_read: bool = Field(index=True)

    notification: Notification = Relationship(sa_relationship_kwargs={"lazy": "joined"})


async def insert_notification(session: AsyncSession, detail: NotificationDetail):
    """Insert a notification and create user notification records.

    Args:
        session: Database session.
        detail: Notification detail containing content and receivers.

    Returns:
        The ID of the created notification.
    """
    notification = Notification(
        name=detail.name,
        category=detail.name.category,
        object_type=detail.object_type,
        object_id=detail.object_id,
        source_user_id=detail.source_user_id,
        details=detail.model_dump(),
        created_at=utcnow(),
    )
    session.add(notification)
    await session.commit()
    await session.refresh(notification)
    id_ = notification.id
    for receiver in await detail.get_receivers(session):
        user_notification = UserNotification(
            notification_id=id_,
            user_id=receiver,
            is_read=False,
        )
        session.add(user_notification)
    await session.commit()
    return id_
