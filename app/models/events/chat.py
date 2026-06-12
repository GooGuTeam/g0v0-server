from datetime import datetime

from app.models.chat import MessageType

from ._base import PluginEvent


class MessageSentEvent(PluginEvent):
    """Event fired when a user sends a message in chat."""

    sender_id: int
    channel_id: int
    message_content: str
    timestamp: datetime
    type: MessageType
    is_bot_command: bool


class JoinChannelEvent(PluginEvent):
    """Event fired when a user joins a chat channel."""

    user_id: int
    channel_id: int


class LeaveChannelEvent(PluginEvent):
    """Event fired when a user leaves a chat channel."""

    user_id: int
    channel_id: int
