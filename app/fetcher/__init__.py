from .beatmap import BeatmapFetcher
from .beatmap_raw import BeatmapRawFetcher
from .beatmapset import BeatmapsetFetcher


class Fetcher(BeatmapFetcher, BeatmapsetFetcher, BeatmapRawFetcher):
    """A class that combines all fetchers for easy access."""

    pass
