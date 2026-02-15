"""User online status subscriber.

Handles Redis pub/sub events for user online status updates.
"""

from app.dependencies.database import get_redis
from app.log import logger
from app.service.user_cache_service import get_user_cache_service

from .base import RedisSubscriber

KEY = "user:online_status"


class UserOnlineSubscriber(RedisSubscriber):
    """User online status subscriber.

    Invalidates user cache when online status changes.
    """

    async def start_subscribe(self):
        """Start subscribing to user online status channel."""
        await self.subscribe(KEY)
        self.add_handler(KEY, self.on)
        self.start()

    async def on(self, c: str, s: str):  # noqa: ARG002
        """Handle user online status update."""
        user_id = int(s)
        logger.info(f"Received user online status update for user_id: {s}")
        await get_user_cache_service(get_redis()).invalidate_user_cache(user_id)


user_online_subscriber = UserOnlineSubscriber()
