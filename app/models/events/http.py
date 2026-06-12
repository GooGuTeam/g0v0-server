from typing import Any

from ._base import PluginEvent

from pydantic import ConfigDict
from starlette.requests import Request


class RequestReceivedEvent(PluginEvent):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    time: float
    request: Request


class RequestHandledEvent(PluginEvent):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    time: float
    request: Request
    response: Any
