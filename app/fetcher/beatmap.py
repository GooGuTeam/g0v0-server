from app.database.beatmap import BeatmapDict, BeatmapModel
from app.log import fetcher_logger

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
    async def get_beatmap(self, beatmap_id: int | None = None, beatmap_checksum: str | None = None) -> BeatmapDict:
        if beatmap_id:
            params = {"id": beatmap_id}
        elif beatmap_checksum:
            params = {"checksum": beatmap_checksum}
        else:
            raise ValueError("Either beatmap_id or beatmap_checksum must be provided.")
        logger.opt(colors=True).debug(f"get_beatmap: <y>{params}</y>")

        return adapter.validate_python(  # pyright: ignore[reportReturnType]
            await self.request_api(
                "https://osu.ppy.sh/api/v2/beatmaps/lookup",
                params=params,
            )
        )
