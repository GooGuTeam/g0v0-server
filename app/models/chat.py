from typing import Any

from pydantic import BaseModel


class ChatEvent(BaseModel):
    event: str
    data: dict[str, Any] | None = None
