from typing import Literal

from ._base import PluginEvent


class UserRelationshipChangedEvent(PluginEvent):
    """Event fired when a user relationship is added, updated, or removed."""

    user_id: int
    target_user_id: int
    relationship_type: Literal["friend", "block"]
    action: Literal["add", "update", "delete"]
