import asyncio
from collections.abc import Awaitable, Callable
from fnmatch import fnmatch
from typing import Any

from app.dependencies.database import get_redis_pubsub


class RedisSubscriber:
    def __init__(self):
        self.pubsub = get_redis_pubsub()
        self.handlers: dict[str, list[Callable[[str, str], Awaitable[Any]]]] = {}
        self.task: asyncio.Task | None = None

    async def subscribe(self, channel: str):
        await self.pubsub.subscribe(channel)
        if channel not in self.handlers:
            self.handlers[channel] = []

    async def unsubscribe(self, channel: str):
        if channel in self.handlers:
            del self.handlers[channel]
        await self.pubsub.unsubscribe(channel)

    def add_handler(self, channel: str, handler: Callable[[str, str], Awaitable[Any]]):
        if channel not in self.handlers:
            self.handlers[channel] = []
        self.handlers[channel].append(handler)

    async def listen(self):
        while True:
            message = await self.pubsub.get_message(ignore_subscribe_messages=True, timeout=None)
            if message is not None and message["type"] == "message":
                matched_handlers: list[Callable[[str, str], Awaitable[Any]]] = []

                if message["channel"] in self.handlers:
                    matched_handlers.extend(self.handlers[message["channel"]])

                chan = message["channel"]
                for pattern, handlers in self.handlers.items():
                    if pattern == chan:
                        continue
                    if not any(ch in pattern for ch in "*?[]"):
                        continue
                    if fnmatch(chan, pattern):
                        for h in handlers:
                            if h not in matched_handlers:
                                matched_handlers.append(h)

                if matched_handlers:
                    await asyncio.gather(
                        *[handler(message["channel"], message["data"]) for handler in matched_handlers]
                    )

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.listen())

    def stop(self):
        if self.task is not None and not self.task.done():
            self.task.cancel()
            self.task = None
