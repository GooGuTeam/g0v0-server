from app.models.score import GameMode

from pydantic import BaseModel


class PerformanceAttributes(BaseModel):
    pp: float


# https://github.com/ppy/osu/blob/9ebc5b0a35452e50bd408af1db62cfc22a57b1f4/osu.Game.Rulesets.Osu/Difficulty/OsuPerformanceAttributes.cs
class OsuPerformanceAttributes(PerformanceAttributes):
    aim: float
    speed: float
    accuracy: float
    flashlight: float
    effective_miss_count: float
    speed_deviation: float | None = None

    # 2025 Q3 update
    # combo_based_estimated_miss_count: int
    # score_based_estimated_miss_count: int | None = None
    # aim_estimated_slider_breaks: int
    # speed_estimated_slider_breaks: int


# https://github.com/ppy/osu/blob/9ebc5b0a35452e50bd408af1db62cfc22a57b1f4/osu.Game.Rulesets.Taiko/Difficulty/TaikoPerformanceAttributes.cs
class TaikoPerformanceAttributes(PerformanceAttributes):
    difficulty: float
    accuracy: float
    estimated_unstable_rate: float | None = None


# https://github.com/ppy/osu/blob/9ebc5b0a35452e50bd408af1db62cfc22a57b1f4/osu.Game.Rulesets.Mania/Difficulty/ManiaPerformanceAttributes.cs
class ManiaPerformanceAttributes(PerformanceAttributes):
    difficulty: float


PERFORMANCE_CLASS: dict[GameMode, type[PerformanceAttributes]] = {
    GameMode.OSU: OsuPerformanceAttributes,
    GameMode.MANIA: ManiaPerformanceAttributes,
    GameMode.TAIKO: TaikoPerformanceAttributes,
}


class BeatmapAttributes(BaseModel):
    star_rating: float
    max_combo: int


# https://github.com/ppy/osu/blob/9ebc5b0a35452e50bd408af1db62cfc22a57b1f4/osu.Game.Rulesets.Osu/Difficulty/OsuDifficultyAttributes.cs
class OsuBeatmapAttributes(BeatmapAttributes):
    aim_difficulty: float
    aim_difficult_slider_count: float
    speed_difficulty: float
    speed_note_count: float
    flashlight_difficulty: float | None = None
    slider_factor: float
    aim_difficult_strain_count: float
    speed_difficult_strain_count: float

    # 2025 Q3 update
    # aim_top_weighted_slider_factor: float
    # speed_top_weighted_slider_factor: float
    # nested_score_per_object: float
    # legacy_score_base_multiplier: float
    # maximum_legacy_combo_score: float


# https://github.com/ppy/osu/blob/9ebc5b0a35452e50bd408af1db62cfc22a57b1f4/osu.Game.Rulesets.Taiko/Difficulty/TaikoDifficultyAttributes.cs
class TaikoBeatmapAttributes(BeatmapAttributes):
    rhythm_difficulty: float
    mono_stamina_factor: float

    # 2025 Q3 update
    # consistency_factor: float


DIFFICULTY_CLASS: dict[GameMode, type[BeatmapAttributes]] = {
    GameMode.OSU: OsuBeatmapAttributes,
    GameMode.TAIKO: TaikoBeatmapAttributes,
}
