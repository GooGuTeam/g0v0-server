from app.models.room import RoomCategory

from ._base import PluginEvent


class RoomCreatedEvent(PluginEvent):
    """Event fired when a playlist room is created."""

    room_id: int
    host_user_id: int
    name: str
    category: RoomCategory


class RoomEndedEvent(PluginEvent):
    """Event fired when a playlist room is ended."""

    room_id: int
    actor_user_id: int


class RoomUserJoinedEvent(PluginEvent):
    """Event fired when a user joins a playlist room."""

    room_id: int
    user_id: int


class RoomUserLeftEvent(PluginEvent):
    """Event fired when a user leaves a playlist room."""

    room_id: int
    user_id: int
