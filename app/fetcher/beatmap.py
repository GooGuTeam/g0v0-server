from app.database.beatmap import BeatmapResp
from app.log import fetcher_logger

from ._base import BaseFetcher

logger = fetcher_logger("BeatmapFetcher")


class BeatmapFetcher(BaseFetcher):
    async def get_beatmap(self, beatmap_id: int | None = None, beatmap_checksum: str | None = None) -> BeatmapResp:
        if beatmap_id:
            params = {"id": beatmap_id}
        elif beatmap_checksum:
            params = {"checksum": beatmap_checksum}
        else:
            raise ValueError("Either beatmap_id or beatmap_checksum must be provided.")
        logger.opt(colors=True).debug(f"get_beatmap: <y>{params}</y>")

        return BeatmapResp.model_validate(
            await self.request_api(
                "https://osu.ppy.sh/api/v2/beatmaps/lookup",
                params=params,
            )
        )
