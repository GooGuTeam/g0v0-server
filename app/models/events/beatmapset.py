from typing import Literal

from ._base import PluginEvent


class BeatmapsetFavouriteChangedEvent(PluginEvent):
    """Event fired when a user favourites or unfavourites a beatmapset."""

    user_id: int
    beatmapset_id: int
    action: Literal["favourite", "unfavourite"]


class BeatmapsetRatedEvent(PluginEvent):
    """Event fired when a user rates a beatmapset."""

    user_id: int
    beatmapset_id: int
    rating: int


class BeatmapsetSyncRequestedEvent(PluginEvent):
    """Event fired when a user requests a beatmapset sync."""

    user_id: int
    beatmapset_id: int
    immediate: bool


class BeatmapTagVoteChangedEvent(PluginEvent):
    """Event fired when a user adds or removes a beatmap tag vote."""

    user_id: int
    beatmap_id: int
    tag_id: int
    action: Literal["vote", "unvote"]
