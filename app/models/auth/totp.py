from __future__ import annotations

from enum import Enum
from typing import TypedDict


class StartCreateTotpKeyResp(TypedDict):
    secret: str
    uri: str


class FinishStatus(str, Enum):
    INVALID = "invalid"
    SUCCESS = "success"
    FAILED = "failed"
    TOO_MANY_ATTEMPTS = "too_many_attempts"
