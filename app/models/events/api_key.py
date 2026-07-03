from ._base import PluginEvent


class APIKeyCreatedEvent(PluginEvent):
    """Event fired when a v1 API key is created."""

    user_id: int
    key_id: int
    name: str


class APIKeyUpdatedEvent(PluginEvent):
    """Event fired when a v1 API key is renamed."""

    user_id: int
    key_id: int
    name: str


class APIKeyDeletedEvent(PluginEvent):
    """Event fired when a v1 API key is deleted."""

    user_id: int
    key_id: int
    name: str


class APIKeyRegeneratedEvent(PluginEvent):
    """Event fired when a v1 API key is regenerated."""

    user_id: int
    key_id: int
    name: str
