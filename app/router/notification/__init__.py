"""Notification router module.

This module provides endpoints for managing user notifications,
including fetching unread notifications and marking them as read.
"""

from app.config import settings
from app.database.notification import Notification, UserNotification
from app.database.user import User
from app.dependencies.database import Database
from app.dependencies.user import get_client_user
from app.models.chat import ChatEvent
from app.router.v2 import api_v2_router as router
from app.utils import utcnow

from . import channel, message  # noqa: F401
from .server import (
    chat_router as chat_router,
    server,
)

from fastapi import Body, Query, Security
from pydantic import BaseModel
from sqlmodel import col, func, select

__all__ = ["chat_router"]


class NotificationResp(BaseModel):
    """Response model for notification listing.

    Attributes:
        has_more: Whether there are more notifications to fetch.
        notifications: List of unread notifications.
        unread_count: Total count of unread notifications.
        notification_endpoint: WebSocket endpoint URL for real-time notifications.
    """

    has_more: bool
    notifications: list[Notification]
    unread_count: int
    notification_endpoint: str


@router.get(
    "/notifications",
    tags=["Notification", "Chat"],
    name="Get Notifications",
    description=(
        "Get unread notifications for the current user, sorted by ID. Also returns the notification server endpoint."
    ),
    response_model=NotificationResp,
)
async def get_notifications(
    session: Database,
    max_id: int | None = Query(None, description="Get notifications with ID less than this value"),
    current_user: User = Security(get_client_user),
):
    """Get unread notifications for the current user.

    Args:
        session: Database session.
        max_id: Optional maximum notification ID for pagination.
        current_user: The authenticated user.

    Returns:
        NotificationResp containing unread notifications and metadata.
    """
    if settings.server_url is not None:
        notification_endpoint = f"{settings.server_url}notification-server".replace("http://", "ws://").replace(
            "https://", "wss://"
        )
    else:
        notification_endpoint = "/notification-server"
    query = select(UserNotification).where(
        UserNotification.user_id == current_user.id,
        col(UserNotification.is_read).is_(False),
    )
    if max_id is not None:
        query = query.where(UserNotification.notification_id < max_id)
    notifications = (await session.exec(query)).all()
    total_count = (
        await session.exec(
            select(func.count())
            .select_from(UserNotification)
            .where(
                UserNotification.user_id == current_user.id,
                col(UserNotification.is_read).is_(False),
            )
        )
    ).one()
    unread_count = len(notifications)

    return NotificationResp(
        has_more=unread_count < total_count,
        notifications=[notification.notification for notification in notifications],
        unread_count=unread_count,
        notification_endpoint=notification_endpoint,
    )


class _IdentityReq(BaseModel):
    """Request model for identifying notifications.

    Attributes:
        category: Notification category filter.
        id: Specific notification ID.
        object_id: Related object ID.
        object_type: Related object type.
    """

    category: str | None = None
    id: int | None = None
    object_id: int | None = None
    object_type: int | None = None


async def _get_notifications(
    session: Database, current_user: User, identities: list[_IdentityReq]
) -> list[UserNotification]:
    """Fetch notifications matching the given identity filters.

    Args:
        session: Database session.
        current_user: The authenticated user.
        identities: List of identity filters to match notifications.

    Returns:
        List of matching UserNotification objects.
    """
    result: dict[int, UserNotification] = {}
    base_query = select(UserNotification).where(
        UserNotification.user_id == current_user.id,
        col(UserNotification.is_read).is_(False),
    )
    for identity in identities:
        query = base_query
        if identity.id is not None:
            query = base_query.where(UserNotification.notification_id == identity.id)
        if identity.object_id is not None:
            query = base_query.where(
                col(UserNotification.notification).has(col(Notification.object_id) == identity.object_id)
            )
        if identity.object_type is not None:
            query = base_query.where(
                col(UserNotification.notification).has(col(Notification.object_type) == identity.object_type)
            )
        if identity.category is not None:
            query = base_query.where(
                col(UserNotification.notification).has(col(Notification.category) == identity.category)
            )
        result.update({n.notification_id: n for n in await session.exec(query)})
    return list(result.values())


@router.post(
    "/notifications/mark-read",
    tags=["Notification", "Chat"],
    name="Mark Notifications as Read",
    description="Mark the current user's notifications as read.",
    status_code=204,
)
async def mark_notifications_as_read(
    session: Database,
    identities: list[_IdentityReq] = Body(default_factory=list),
    notifications: list[_IdentityReq] = Body(default_factory=list),
    current_user: User = Security(get_client_user),
):
    """Mark specified notifications as read.

    Args:
        session: Database session.
        identities: List of notification identity filters.
        notifications: Additional notification identity filters.
        current_user: The authenticated user.
    """
    identities.extend(notifications)
    user_notifications = await _get_notifications(session, current_user, identities)
    for user_notification in user_notifications:
        user_notification.is_read = True

    await server.send_event(
        current_user.id,
        ChatEvent(
            event="read",
            data={
                "notifications": [i.model_dump() for i in identities],
                "read_count": len(user_notifications),
                "timestamp": utcnow().isoformat(),
            },
        ),
    )
    await session.commit()
