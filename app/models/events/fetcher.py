from ._base import PluginEvent


class FetchingBeatmapRawEvent(PluginEvent):
    """Event fired when fetching raw beatmap content (.osu file)."""

    beatmap_id: int


class BeatmapRawFetchedEvent(PluginEvent):
    """Event fired after successfully fetching raw beatmap content."""

    beatmap_id: int
    beatmap_raw: str


class FetchingBeatmapEvent(PluginEvent):
    """Event fired when fetching beatmap data from the API."""

    beatmap_id: int | None = None
    beatmap_checksum: str | None = None


class BeatmapFetchedEvent(PluginEvent):
    """Event fired after successfully fetching beatmap data from the API."""

    beatmap_id: int
    beatmap_data: dict


class FetchingBeatmapsetEvent(PluginEvent):
    """Event fired when fetching beatmapset data from the API."""

    beatmapset_id: int


class BeatmapsetFetchedEvent(PluginEvent):
    """Event fired after successfully fetching beatmapset data from the API."""

    beatmapset_id: int
    beatmapset_data: dict
