"""Chat subscriber service.

Handles Redis pub/sub events for chat room joins, leaves, and notifications.
"""

from typing import TYPE_CHECKING

from app.log import log
from app.models.notification import NotificationDetails

from .base import RedisSubscriber

from pydantic import TypeAdapter

if TYPE_CHECKING:
    from app.router.notification.server import ChatServer


JOIN_CHANNEL = "chat:room:joined"
EXIT_CHANNEL = "chat:room:left"
ON_NOTIFICATION = "chat:notification"

logger = log("Chat")


class ChatSubscriber(RedisSubscriber):
    """Chat room event subscriber.

    Handles room join, leave, and notification events via Redis pub/sub.
    """

    def __init__(self):
        super().__init__()
        self.room_subscriber: dict[int, list[int]] = {}
        self.chat_server: "ChatServer | None" = None

    async def start_subscribe(self):
        """Start subscribing to chat channels."""
        await self.subscribe(JOIN_CHANNEL)
        self.add_handler(JOIN_CHANNEL, self.on_join_room)
        await self.subscribe(EXIT_CHANNEL)
        self.add_handler(EXIT_CHANNEL, self.on_leave_room)
        await self.subscribe(ON_NOTIFICATION)
        self.add_handler(ON_NOTIFICATION, self.on_notification)
        self.start()

    async def on_join_room(self, c: str, s: str):  # noqa: ARG002
        """Handle room join event."""
        channel_id, user_id = s.split(":")
        if self.chat_server is None:
            return
        await self.chat_server.join_room_channel(int(channel_id), int(user_id))

    async def on_leave_room(self, c: str, s: str):  # noqa: ARG002
        """Handle room leave event."""
        channel_id, user_id = s.split(":")
        if self.chat_server is None:
            return
        await self.chat_server.leave_room_channel(int(channel_id), int(user_id))

    async def on_notification(self, c: str, s: str):  # noqa: ARG002
        """Handle notification event."""
        try:
            detail = TypeAdapter(NotificationDetails).validate_json(s)
        except ValueError:
            logger.exception("Failed to parse notification detail")
            return
        except Exception:
            logger.exception("Failed to parse notification detail")
            return
        if self.chat_server is None:
            return
        await self.chat_server.new_private_notification(detail)
