from typing import Literal

from app.models.score import GameMode

from ._base import PluginEvent


class TeamCreatedEvent(PluginEvent):
    """Event fired when a team is created."""

    team_id: int
    leader_id: int
    name: str
    short_name: str
    playmode: GameMode


class TeamUpdatedEvent(PluginEvent):
    """Event fired when a team is updated."""

    team_id: int
    actor_user_id: int
    updated_fields: list[str]


class TeamDeletedEvent(PluginEvent):
    """Event fired when a team is deleted."""

    team_id: int
    actor_user_id: int
    name: str
    short_name: str


class TeamJoinRequestedEvent(PluginEvent):
    """Event fired when a user requests to join a team."""

    team_id: int
    user_id: int


class TeamJoinRequestHandledEvent(PluginEvent):
    """Event fired when a team join request is accepted or rejected."""

    team_id: int
    user_id: int
    actor_user_id: int
    action: Literal["accepted", "rejected"]


class TeamMemberRemovedEvent(PluginEvent):
    """Event fired when a team member leaves or is removed."""

    team_id: int
    user_id: int
    actor_user_id: int
