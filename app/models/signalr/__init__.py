from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pydantic import (
    BaseModel,
    Field,
)


@dataclass
class SignalRMeta:
    member_ignore: bool = False  # implement of IgnoreMember (msgpack) attribute
    json_ignore: bool = False  # implement of JsonIgnore (json) attribute
    use_abbr: bool = True


class SignalRUnionMessage(BaseModel):
    union_type: ClassVar[int]


class Transport(BaseModel):
    transport: str
    transfer_formats: list[str] = Field(default_factory=lambda: ["Binary", "Text"], alias="transferFormats")


class NegotiateResponse(BaseModel):
    connectionId: str
    connectionToken: str
    negotiateVersion: int = 1
    availableTransports: list[Transport]


class UserState(BaseModel):
    connection_id: str
    connection_token: str
