"""Performance calculator implementation using the rosu-pp-py library

References:
  - https://github.com/MaxOhn/rosu-pp-py
"""

from asyncio import get_event_loop
from copy import deepcopy
from typing import TYPE_CHECKING, ClassVar

from app.calculating import clamp
from app.models.mods import APIMod
from app.models.performance import (
    DifficultyAttributes,
    ManiaPerformanceAttributes,
    OsuDifficultyAttributes,
    OsuPerformanceAttributes,
    PerformanceAttributes,
    TaikoDifficultyAttributes,
    TaikoPerformanceAttributes,
)
from app.models.score import GameMode

from ._base import (
    AvailableModes,
    CalculateError,
    ConvertError,
    DifficultyError,
    PerformanceCalculator as BasePerformanceCalculator,
    PerformanceError,
)

if TYPE_CHECKING:
    from app.database.score import Score

try:
    import rosu_pp_py as rosu
except ImportError:
    raise ImportError(
        "rosu-pp-py is not installed. "
        "Please install it.\n"
        "   Official: uv add rosu-pp-py\n"
        "   gu: uv add git+https://github.com/GooGuTeam/gu-pp-py.git"
    )

PERFORMANCE_CLASS = {
    GameMode.OSU: OsuPerformanceAttributes,
    GameMode.TAIKO: TaikoPerformanceAttributes,
    GameMode.MANIA: ManiaPerformanceAttributes,
}
DIFFICULTY_CLASS = {
    GameMode.OSU: OsuDifficultyAttributes,
    GameMode.TAIKO: TaikoDifficultyAttributes,
}

_enum_to_str = {
    0: {
        "MR": {"reflection"},
        "AC": {"accuracy_judge_mode"},
        "BR": {"direction"},
        "AD": {"style"},
    },
    1: {"AC": {"accuracy_judge_mode"}},
    2: {"AC": {"accuracy_judge_mode"}},
    3: {"AC": {"accuracy_judge_mode"}},
}


def _parse_enum_to_str(ruleset_id: int, mods: list[APIMod]):
    for mod in mods:
        if mod["acronym"] in _enum_to_str.get(ruleset_id, {}):
            for setting in mod.get("settings", {}):
                if setting in _enum_to_str[ruleset_id][mod["acronym"]]:
                    mod["settings"][setting] = str(mod["settings"][setting])  # pyright: ignore[reportTypedDictNotRequiredAccess]


class RosuPerformanceCalculator(BasePerformanceCalculator):
    SUPPORT_MODES: ClassVar[set[GameMode]] = {
        GameMode.OSU,
        GameMode.TAIKO,
        GameMode.FRUITS,
        GameMode.MANIA,
        GameMode.OSURX,
        GameMode.OSUAP,
        GameMode.TAIKORX,
        GameMode.FRUITSRX,
    }

    @classmethod
    def _to_rosu_mode(cls, mode: GameMode) -> rosu.GameMode:
        return {
            GameMode.OSU: rosu.GameMode.Osu,
            GameMode.TAIKO: rosu.GameMode.Taiko,
            GameMode.FRUITS: rosu.GameMode.Catch,
            GameMode.MANIA: rosu.GameMode.Mania,
            GameMode.OSURX: rosu.GameMode.Osu,
            GameMode.OSUAP: rosu.GameMode.Osu,
            GameMode.TAIKORX: rosu.GameMode.Taiko,
            GameMode.FRUITSRX: rosu.GameMode.Catch,
        }[mode]

    @classmethod
    def _from_rosu_mode(cls, mode: rosu.GameMode) -> GameMode:
        return {
            rosu.GameMode.Osu: GameMode.OSU,
            rosu.GameMode.Taiko: GameMode.TAIKO,
            rosu.GameMode.Catch: GameMode.FRUITS,
            rosu.GameMode.Mania: GameMode.MANIA,
        }[mode]

    async def get_available_modes(self) -> AvailableModes:
        return AvailableModes(
            has_performance_calculator=self.SUPPORT_MODES,
            has_difficulty_calculator=self.SUPPORT_MODES,
        )

    @classmethod
    def _perf_attr_to_model(cls, attr: rosu.PerformanceAttributes, gamemode: GameMode) -> PerformanceAttributes:
        attr_class = PERFORMANCE_CLASS.get(gamemode, PerformanceAttributes)

        if attr_class is OsuPerformanceAttributes:
            return OsuPerformanceAttributes(
                pp=attr.pp,
                aim=attr.pp_aim or 0,
                speed=attr.pp_speed or 0,
                accuracy=attr.pp_accuracy or 0,
                flashlight=attr.pp_flashlight or 0,
                effective_miss_count=attr.effective_miss_count or 0,
                speed_deviation=attr.speed_deviation,
                combo_based_estimated_miss_count=0,
                score_based_estimated_miss_count=0,
                aim_estimated_slider_breaks=0,
                speed_estimated_slider_breaks=0,
            )
        elif attr_class is TaikoPerformanceAttributes:
            return TaikoPerformanceAttributes(
                pp=attr.pp,
                difficulty=attr.pp_difficulty or 0,
                accuracy=attr.pp_accuracy or 0,
                estimated_unstable_rate=attr.estimated_unstable_rate,
            )
        elif attr_class is ManiaPerformanceAttributes:
            return ManiaPerformanceAttributes(
                pp=attr.pp,
                difficulty=attr.pp_difficulty or 0,
            )
        else:
            return PerformanceAttributes(pp=attr.pp)

    async def calculate_performance(self, beatmap_raw: str, score: "Score") -> PerformanceAttributes:
        try:
            map = rosu.Beatmap(content=beatmap_raw)
            mods = deepcopy(score.mods.copy())
            _parse_enum_to_str(int(score.gamemode), mods)
            map.convert(self._to_rosu_mode(score.gamemode), mods)  # pyright: ignore[reportArgumentType]
            perf = rosu.Performance(
                mods=mods,
                lazer=True,
                accuracy=clamp(score.accuracy * 100, 0, 100),
                combo=score.max_combo,
                large_tick_hits=score.nlarge_tick_hit or 0,
                slider_end_hits=score.nslider_tail_hit or 0,
                small_tick_hits=score.nsmall_tick_hit or 0,
                n_geki=score.ngeki,
                n_katu=score.nkatu,
                n300=score.n300,
                n100=score.n100,
                n50=score.n50,
                misses=score.nmiss,
            )
            attr = await get_event_loop().run_in_executor(None, perf.calculate, map)
            return self._perf_attr_to_model(attr, score.gamemode.to_base_ruleset())
        except rosu.ParseError as e:  # pyright: ignore[reportAttributeAccessIssue]
            raise PerformanceError(f"Beatmap parse error: {e}")
        except Exception as e:
            raise CalculateError(f"Unknown error: {e}") from e

    @classmethod
    def _diff_attr_to_model(cls, diff: rosu.DifficultyAttributes, gamemode: GameMode) -> DifficultyAttributes:
        attr_class = DIFFICULTY_CLASS.get(gamemode, DifficultyAttributes)

        if attr_class is OsuDifficultyAttributes:
            return OsuDifficultyAttributes(
                star_rating=diff.stars,
                max_combo=diff.max_combo,
                aim_difficulty=diff.aim or 0,
                aim_difficult_slider_count=diff.aim_difficult_slider_count or 0,
                speed_difficulty=diff.speed or 0,
                speed_note_count=diff.speed_note_count or 0,
                slider_factor=diff.slider_factor or 0,
                aim_difficult_strain_count=diff.aim_difficult_strain_count or 0,
                speed_difficult_strain_count=diff.speed_difficult_strain_count or 0,
                flashlight_difficulty=diff.flashlight or 0,
                aim_top_weighted_slider_factor=0,
                speed_top_weighted_slider_factor=0,
                nested_score_per_object=0,
                legacy_score_base_multiplier=0,
                maximum_legacy_combo_score=0,
            )
        elif attr_class is TaikoDifficultyAttributes:
            return TaikoDifficultyAttributes(
                star_rating=diff.stars,
                max_combo=diff.max_combo,
                rhythm_difficulty=diff.rhythm or 0,
                mono_stamina_factor=diff.stamina or 0,
                consistency_factor=0,
            )
        else:
            return DifficultyAttributes(
                star_rating=diff.stars,
                max_combo=diff.max_combo,
            )

    async def calculate_difficulty(
        self, beatmap_raw: str, mods: list[APIMod] | None = None, gamemode: GameMode | None = None
    ) -> DifficultyAttributes:
        try:
            map = rosu.Beatmap(content=beatmap_raw)
            if gamemode is not None:
                map.convert(self._to_rosu_mode(gamemode), mods)  # pyright: ignore[reportArgumentType]
            diff_calculator = rosu.Difficulty(mods=mods)
            diff = await get_event_loop().run_in_executor(None, diff_calculator.calculate, map)
            return self._diff_attr_to_model(
                diff, gamemode.to_base_ruleset() if gamemode else self._from_rosu_mode(diff.mode)
            )
        except rosu.ConvertError as e:  # pyright: ignore[reportAttributeAccessIssue]
            raise ConvertError(f"Beatmap convert error: {e}")
        except rosu.ParseError as e:  # pyright: ignore[reportAttributeAccessIssue]
            raise DifficultyError(f"Beatmap parse error: {e}")
        except Exception as e:
            raise CalculateError(f"Unknown error: {e}") from e


PerformanceCalculator = RosuPerformanceCalculator
