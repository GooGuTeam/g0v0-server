from dataclasses import dataclass
from enum import Enum
from typing import Any

from fastapi import HTTPException


class ErrorType(Enum):
    """
    All possible error types that could be passed to the client.

    Each entry is a tuple of the message key, status code and fallback message, which should match the arguments of ErrorEntry.

    Remember to keep this enum in sync with frontend l10n implementation.
    """

    UNKNOWN = ("unknown", 500)

    def get_instance(self):
        """
        Get an instance of ErrorEntry from an ErrorType.
        """

        return ErrorEntry(*self.value)


@dataclass
class RequestError(HTTPException):
    """
    A wrapper for major API errors to simplify response composition.

    Attributes:
        msg_key (str): The key of the error message for localization.
        status_code (int): The status code should be responded, defaults to 422 to match osu!api's behavior.
        fallback_msg (str): The fallback message for clients without localization support.

    Args:
        error_type (ErrorType): The error type to initialize from.
        extra (dict[str, Any] | None): Details to include in the response.
        status_code (int): Overrides the default one given by the error type.
        headers (dict[str, str] | None): Will be attached to the response header.
    """

    msg_key: str
    status_code: int = 422
    fallback_msg: str = None

    def __init__(
        self,
        error_type: ErrorType,
        extra: dict[str, Any] | None = None,
        *,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.msg_key, self.status_code, self.fallback_msg = error_type

        # Optional details
        detail = {"error": self.msg_key}
        if extra:
            detail.update(extra)

        # Fallback message
        if self.fallback_msg:
            detail.update({"message": self.fallback_msg})

        final_status = status_code if status_code is not None else self.status_code
        super().__init__(final_status, detail=detail, headers=headers)
