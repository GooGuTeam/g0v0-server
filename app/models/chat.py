from typing import Any, TypedDict


class ChatEvent(TypedDict):
    event: str
    data: dict[str, Any] | None
