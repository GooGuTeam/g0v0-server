"""Performance calculator that uses the osu-performance-server.

References:
  - https://github.com/GooGuTeam/osu-performance-server
"""

import asyncio
import datetime
from typing import TypedDict, cast

from app.models.mods import APIMod
from app.models.performance import (
    DifficultyAttributes,
    DifficultyAttributesUnion,
    PerformanceAttributes,
    PerformanceAttributesUnion,
)
from app.models.score import GameMode, ScoreData

from ._base import (
    AvailableModes,
    CalculateError,
    DifficultyError,
    PerformanceCalculator as BasePerformanceCalculator,
    PerformanceError,
)

from httpx import AsyncClient, HTTPError
from pydantic import TypeAdapter


class AvailableRulesetResp(TypedDict):
    has_performance_calculator: list[str]
    has_difficulty_calculator: list[str]
    loaded_rulesets: list[str]


class PerformanceServerPerformanceCalculator(BasePerformanceCalculator):
    def __init__(self, server_url: str = "http://localhost:5225", **kwargs) -> None:  # noqa: ARG002
        """Initialize the performance server performance calculator.

        Args:
            server_url: The URL of the osu-performance-server instance to use for calculations.
        """
        self.server_url = server_url

        self._available_modes: AvailableModes | None = None
        self._modes_lock = asyncio.Lock()
        self._today = datetime.date.today()

    async def init(self):
        await self.get_available_modes()

    def _process_modes(self, modes: AvailableRulesetResp) -> AvailableModes:
        performance_modes = {
            m for mode in modes["has_performance_calculator"] if (m := GameMode.parse(mode)) is not None
        }
        difficulty_modes = {m for mode in modes["has_difficulty_calculator"] if (m := GameMode.parse(mode)) is not None}
        if GameMode.OSU in performance_modes:
            performance_modes.add(GameMode.OSURX)
            performance_modes.add(GameMode.OSUAP)
        if GameMode.TAIKO in performance_modes:
            performance_modes.add(GameMode.TAIKORX)
        if GameMode.FRUITS in performance_modes:
            performance_modes.add(GameMode.FRUITSRX)

        return AvailableModes(
            has_performance_calculator=performance_modes,
            has_difficulty_calculator=difficulty_modes,
        )

    async def get_available_modes(self) -> AvailableModes:
        # https://github.com/GooGuTeam/osu-performance-server#get-available_rulesets
        if self._available_modes is not None and self._today == datetime.date.today():
            return self._available_modes
        async with self._modes_lock, AsyncClient() as client:
            try:
                resp = await client.get(f"{self.server_url}/available_rulesets")
                if resp.status_code != 200:
                    raise CalculateError(f"Failed to get available modes: {resp.text}")
                modes = cast(AvailableRulesetResp, resp.json())
                result = self._process_modes(modes)

                self._available_modes = result
                self._today = datetime.date.today()
                return result
            except HTTPError as e:
                raise CalculateError(f"Failed to get available modes: {e}") from e
            except CalculateError:
                raise
            except Exception as e:
                raise CalculateError(f"Unknown error: {e}") from e

    async def calculate_performance(self, beatmap_raw: str, score: ScoreData) -> PerformanceAttributes:
        # https://github.com/GooGuTeam/osu-performance-server#post-performance
        hit_result_map = {
            "n300": "great",
            "n100": "ok",
            "n50": "meh",
            "nmiss": "miss",
            "ngeki": "perfect",
            "nkatu": "good",
            "nlarge_tick_hit": "large_tick_hit",
            "nlarge_tick_miss": "large_tick_miss",
            "nsmall_tick_hit": "small_tick_hit",
            "nslider_tail_hit": "slider_tail_hit",
        }

        async with AsyncClient(timeout=15) as client:
            try:
                statistics = {}
                for attr, name in hit_result_map.items():
                    value = getattr(score, attr, None)
                    if value is not None:
                        statistics[name] = value

                resp = await client.post(
                    f"{self.server_url}/performance",
                    json={
                        "beatmap_id": score.beatmap_id,
                        "beatmap_file": beatmap_raw,
                        "checksum": score.map_md5,
                        "accuracy": score.accuracy,
                        "combo": score.max_combo,
                        "mods": score.mods,
                        "statistics": statistics,
                        "ruleset": score.gamemode.to_base_ruleset().value,
                    },
                )
                if resp.status_code != 200:
                    raise PerformanceError(f"Failed to calculate performance: {resp.text}")
                return TypeAdapter(PerformanceAttributesUnion).validate_json(resp.text)
            except HTTPError as e:
                raise PerformanceError(f"Failed to calculate performance: {e}") from e
            except CalculateError:
                raise
            except Exception as e:
                raise CalculateError(f"Unknown error: {e}") from e

    async def calculate_difficulty(
        self, beatmap_raw: str, mods: list[APIMod] | None = None, gamemode: GameMode | None = None
    ) -> DifficultyAttributes:
        # https://github.com/GooGuTeam/osu-performance-server#post-difficulty
        async with AsyncClient(timeout=15) as client:
            try:
                resp = await client.post(
                    f"{self.server_url}/difficulty",
                    json={
                        "beatmap_file": beatmap_raw,
                        "mods": mods or [],
                        "ruleset": gamemode.value if gamemode else None,
                    },
                )
                if resp.status_code != 200:
                    raise DifficultyError(f"Failed to calculate difficulty: {resp.text}")
                return TypeAdapter(DifficultyAttributesUnion).validate_json(resp.text)
            except HTTPError as e:
                raise DifficultyError(f"Failed to calculate difficulty: {e}") from e
            except CalculateError:
                raise
            except Exception as e:
                raise DifficultyError(f"Unknown error: {e}") from e


PerformanceCalculator = PerformanceServerPerformanceCalculator
