from enum import StrEnum
from typing import TypedDict


class StartCreateTotpKeyResp(TypedDict):
    secret: str
    uri: str


class FinishStatus(StrEnum):
    INVALID = "invalid"
    SUCCESS = "success"
    FAILED = "failed"
    TOO_MANY_ATTEMPTS = "too_many_attempts"
