"""Event hub for plugin event handling.

This module provides an event bus system that allows plugins to subscribe
to and emit events. Events are processed asynchronously in background tasks.
Listeners can declare their interest in specific event types through function annotations
and use dependency injection to receive dependencies they need.

Classes:
    EventHub: Event bus for managing event subscriptions and emissions.

Functions:
    subscribe_event: Subscribe a listener to the global event hub.
    listen: Decorator to subscribe a function to the global event hub.

Variables:
    hub: Global EventHub instance.
"""

from collections.abc import Awaitable, Callable
import contextlib
import inspect
from typing import Annotated, Any

from app.helpers import bg_tasks
from app.models.events import PluginEvent

from fast_depends import Depends, ValidationError, inject


class EventHub:
    """Event bus for managing plugin events.

    Allows plugins to subscribe to events and emit events that will
    be dispatched to all registered listeners.

    Attributes:
        _listeners: List of registered event listener functions.
    """

    def __init__(self):
        """Initialize the event hub."""
        self._listeners = []

    def subscribe_event(self, listener: Callable[..., Awaitable[Any]]) -> None:
        """Subscribe a listener function to the event hub.

        Args:
            listener: An async function that will be called when events are emitted.
        """
        self._listeners.append(listener)

    def listen(self, func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        """Decorator to subscribe a function to the event hub.

        Args:
            func: The async function to subscribe.

        Returns:
            The same function, now subscribed to events.
        """
        self.subscribe_event(func)
        return func

    def emit(self, event: PluginEvent) -> None:
        """Emit an event to all subscribed listeners.

        Events are processed asynchronously in background tasks.
        Each listener that accepts the event type will be called.

        Args:
            event: The event to emit.
        """

        async def _task(listener):
            sig = inspect.signature(listener)
            params = []

            for name, param in sig.parameters.items():
                if (
                    isinstance(param.annotation, type)
                    and issubclass(param.annotation, PluginEvent)
                    and isinstance(event, param.annotation)
                ):
                    # convert to Depends
                    dep = Depends(lambda: event)
                    params.append(param.replace(annotation=Annotated[param.annotation, dep]))
                else:
                    params.append(param)

            listener.__signature__ = sig.replace(parameters=params)

            # Now call inject
            with contextlib.suppress(StopIteration, ValidationError):
                await inject(listener)()

        for listener in self._listeners:
            bg_tasks.add_task(_task, listener)


hub = EventHub()


def subscribe_event(listener: Callable[..., Awaitable[Any]]) -> None:
    """Subscribe a listener to the global event hub.

    Args:
        listener: An async function that will be called when events are emitted.
    """
    hub.subscribe_event(listener)


def listen(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Decorator to subscribe a function to the global event hub.

    Args:
        func: The async function to subscribe.

    Returns:
        The same function, now subscribed to events.
    """
    return hub.listen(func)
