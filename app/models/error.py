from enum import Enum

from fastapi import HTTPException


class ErrorEntry:
    """
    Records basic information of an error.

    Args:
        msg_key (str): The key of the error message.
        status_code (int): The status code should be responded, defaults to 422 to match osu!api's behavior.
    """

    def __init__(self, msg_key: str, status_code: int = 422):
        self.msg_key = msg_key
        self.status_code = status_code


class ErrorType(Enum):
    """
    All possible error types that could be passed to the client.

    Each entry is a tuple of the message key and status code, which should match the arguments of ErrorEntry.
    """

    UNKNOWN = ("unknown", 500)

    def get_instance(self):
        """
        Get an instance of ErrorEntry from an ErrorType.
        """

        return ErrorEntry(*self.value)


class RequestError(HTTPException):
    """
    A wrapper for major API errors to simplify response composition.
    """

    def __init__(self, error_type: ErrorType):
        self.error_entry = error_type.get_instance()
        super().__init__(self.error_entry.status_code, detail={"error": self.error_entry.msg_key})
