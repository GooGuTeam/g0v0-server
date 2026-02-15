"""Beatmap fetcher for osu! API.

This module provides a fetcher class for retrieving beatmap information from
the osu! API v2. It supports looking up beatmaps by ID or checksum.

Classes:
    BeatmapFetcher: Fetcher for retrieving individual beatmap data.
"""

from app.database.beatmap import BeatmapDict, BeatmapModel
from app.log import fetcher_logger
from app.models.events.fetcher import BeatmapFetchedEvent, FetchingBeatmapEvent
from app.plugins import event_hub

from ._base import BaseFetcher

from pydantic import TypeAdapter

logger = fetcher_logger("BeatmapFetcher")
adapter = TypeAdapter(
    BeatmapModel.generate_typeddict(
        (
            "checksum",
            "accuracy",
            "ar",
            "bpm",
            "convert",
            "count_circles",
            "count_sliders",
            "count_spinners",
            "cs",
            "deleted_at",
            "drain",
            "hit_length",
            "is_scoreable",
            "last_updated",
            "mode_int",
            "ranked",
            "url",
            "max_combo",
            "beatmapset",
        )
    )
)


class BeatmapFetcher(BaseFetcher):
    """Fetcher for retrieving beatmap data from the osu! API.

    Inherits from BaseFetcher to utilize OAuth and rate limiting functionality.
    """

    async def get_beatmap(self, beatmap_id: int | None = None, beatmap_checksum: str | None = None) -> BeatmapDict:
        """Fetch a beatmap by ID or checksum.

        Retrieves beatmap information from the osu! API v2 beatmaps/lookup endpoint.
        At least one of beatmap_id or beatmap_checksum must be provided.

        Args:
            beatmap_id: The beatmap ID to look up. Defaults to None.
            beatmap_checksum: The MD5 checksum of the beatmap to look up.
                Defaults to None.

        Returns:
            A dictionary containing the beatmap data with extended information
            including checksum, difficulty settings, and beatmapset data.

        Raises:
            ValueError: If neither beatmap_id nor beatmap_checksum is provided.
        """
        event_hub.emit(FetchingBeatmapEvent(beatmap_id=beatmap_id, beatmap_checksum=beatmap_checksum))

        if beatmap_id:
            params = {"id": beatmap_id}
        elif beatmap_checksum:
            params = {"checksum": beatmap_checksum}
        else:
            raise ValueError("Either beatmap_id or beatmap_checksum must be provided.")
        logger.opt(colors=True).debug(f"get_beatmap: <y>{params}</y>")

        beatmap = adapter.validate_python(
            await self.request_api(
                "https://osu.ppy.sh/api/v2/beatmaps/lookup",
                params=params,
            )
        )
        event_hub.emit(BeatmapFetchedEvent(beatmap_id=beatmap["id"], beatmap_data=beatmap))  # pyright: ignore[reportArgumentType]
        return beatmap  # pyright: ignore[reportReturnType]
