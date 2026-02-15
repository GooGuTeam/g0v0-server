"""Fetchers for osu! data.

This module contains classes that fetch data from the osu! API and other sources.
Each class is responsible for fetching a specific type of data, such as beatmaps, beatmapsets, or raw beatmap data.
The Fetcher class combines all of these fetchers for easy access.

Fetcher needs osu! v2 API credentials to function, which can be set in the config file or as environment variables.

- References:
    - osu! API documentation: https://osu.ppy.sh/docs/index.html
    - Configuration: https://docs.g0v0.top/lazer/reference/configurations.html#fetcher-%E8%AE%BE%E7%BD%AE
"""

from .beatmap import BeatmapFetcher
from .beatmap_raw import BeatmapRawFetcher
from .beatmapset import BeatmapsetFetcher


class Fetcher(BeatmapFetcher, BeatmapsetFetcher, BeatmapRawFetcher):
    """A class that combines all fetchers for easy access."""

    pass
