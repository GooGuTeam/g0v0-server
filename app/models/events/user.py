from ._base import PluginEvent


class UserRegisteredEvent(PluginEvent):
    """Event fired when a user registers an account."""

    user_id: int
    username: str
    country_code: str
