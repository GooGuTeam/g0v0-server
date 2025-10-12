from typing import TYPE_CHECKING

from app.models.mods import APIMod
from app.models.performance import (
    DIFFICULTY_CLASS,
    PERFORMANCE_CLASS,
    BeatmapAttributes,
    PerformanceAttributes,
)
from app.models.score import GameMode

from ._base import (
    CalculateError,
    DifficultyError,
    PerformanceCalculator as BasePerformanceCalculator,
    PerformanceError,
)

from httpx import AsyncClient, HTTPError

if TYPE_CHECKING:
    from app.database.score import Score


class PerformanceCalculator(BasePerformanceCalculator):
    def __init__(self, server_url: str = "http://localhost:5225") -> None:
        self.server_url = server_url

    async def calculate_performance(self, beatmap_raw: str, score: "Score") -> PerformanceAttributes:
        # https://github.com/GooGuTeam/osu-performance-server#post-performance
        async with AsyncClient() as client:
            try:
                resp = await client.post(
                    f"{self.server_url}/performance",
                    json={
                        "beatmap_id": score.beatmap_id,
                        "beatmap_file": beatmap_raw,
                        "checksum": score.map_md5,
                        "accuracy": score.accuracy,
                        "combo": score.max_combo,
                        "mods": score.mods,
                        "statistics": {
                            "great": score.n300,
                            "ok": score.n100,
                            "meh": score.n50,
                            "miss": score.nmiss,
                            "perfect": score.ngeki,
                            "good": score.nkatu,
                            "large_tick_hit": score.nlarge_tick_hit or 0,
                            "large_tick_miss": score.nlarge_tick_miss or 0,
                            "small_tick_hit": score.nsmall_tick_hit or 0,
                            "slider_tail_hit": score.nslider_tail_hit or 0,
                        },
                    },
                )
                if resp.status_code != 200:
                    raise PerformanceError(f"Failed to calculate performance: {resp.text}")
                data = resp.json()
                return PERFORMANCE_CLASS.get(score.gamemode, PerformanceAttributes).model_validate(data)
            except HTTPError as e:
                raise PerformanceError(f"Failed to calculate performance: {e}") from e
            except Exception as e:
                raise CalculateError(f"Unknown error: {e}") from e

    async def calculate_difficulty(
        self, beatmap_raw: str, mods: list[APIMod] | None = None, gamemode: GameMode | None = None
    ) -> BeatmapAttributes:
        # https://github.com/GooGuTeam/osu-performance-server#post-difficulty
        async with AsyncClient() as client:
            try:
                resp = await client.post(
                    f"{self.server_url}/difficulty",
                    json={
                        "beatmap_file": beatmap_raw,
                        "mods": mods or [],
                        "ruleset": int(gamemode) if gamemode else None,
                    },
                )
                if resp.status_code != 200:
                    raise DifficultyError(f"Failed to calculate difficulty: {resp.text}")
                data = resp.json()
                ruleset_id = data.pop("ruleset_id", 0)
                return DIFFICULTY_CLASS.get(GameMode.from_int(ruleset_id), BeatmapAttributes).model_validate(data)
            except HTTPError as e:
                raise DifficultyError(f"Failed to calculate difficulty: {e}") from e
            except Exception as e:
                raise DifficultyError(f"Unknown error: {e}") from e
