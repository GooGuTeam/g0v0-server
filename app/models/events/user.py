from typing import Literal

from ._base import PluginEvent


class UserRegisteredEvent(PluginEvent):
    """Event fired when a user registers an account."""

    user_id: int
    username: str
    country_code: str


class UserLoginEvent(PluginEvent):
    """Event fired after a user successfully logs in and a login session is created."""

    user_id: int
    token_id: int
    client_id: int
    scopes: list[str]
    ip_address: str
    user_agent: str | None
    country_code: str
    trusted_device: bool
    verification_method: Literal["totp", "mail"] | None
    session_id: int
    session_verified: bool


class UserRenamedEvent(PluginEvent):
    """Event fired when a user changes their username."""

    user_id: int
    old_username: str
    new_username: str


class UserPageUpdatedEvent(PluginEvent):
    """Event fired when a user updates their profile page."""

    user_id: int
    raw_length: int
    html_length: int


class UserPreferencesUpdatedEvent(PluginEvent):
    """Event fired when a user updates or clears their preferences/profile fields."""

    user_id: int
    action: Literal["update", "overwrite", "clear"]
    updated_fields: list[str]
