from collections.abc import Awaitable, Callable
import contextlib
import inspect
from typing import Annotated, Any

from app.models.events import PluginEvent
from app.utils import bg_tasks

from fast_depends import Depends, ValidationError, inject


class EventHub:
    def __init__(self):
        self._listeners = []

    def subscribe_event(self, listener: Callable[..., Awaitable[Any]]) -> None:
        self._listeners.append(listener)

    def listen(self, func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        self.subscribe_event(func)
        return func

    def emit(self, event: PluginEvent) -> None:
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
    hub.subscribe_event(listener)


def listen(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    return hub.listen(func)
