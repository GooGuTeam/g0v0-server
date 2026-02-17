from enum import StrEnum
from typing import Any, TypedDict


class MessageType(StrEnum):
    """Types of chat messages."""

    ACTION = "action"
    MARKDOWN = "markdown"
    PLAIN = "plain"


class ChatEvent(TypedDict):
    event: str
    data: dict[str, Any] | None
