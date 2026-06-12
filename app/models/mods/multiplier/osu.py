from app.config import settings
from app.models.mods.definition import get_default_setting

from ._base import ModMultiplierCalculator, ModMultiplierContext, _ModWrapper, combination


class OsuModMultiplierCalculatorV1(ModMultiplierCalculator):
    @staticmethod
    def _rate_adjust_multiplier(speed_change: float) -> float:
        # Round down to the nearest multiple of 0.1, matching C# int cast behaviour.
        value = int(speed_change * 10) / 10.0
        value -= 1

        if speed_change >= 1:
            return 1 + value / 5
        return 0.6 + value

    # Difficulty Reduction
    def ez(self) -> float:
        return 0.5

    def nf(self) -> float:
        return 0.5

    def ht(self) -> float:
        speed_change: float = self.me.speed_change
        return self._rate_adjust_multiplier(speed_change)

    def dc(self) -> float:
        speed_change: float = self.me.speed_change
        return self._rate_adjust_multiplier(speed_change)

    # Difficulty Increase
    def hr(self) -> float:
        return 1.06 if self.me.is_uses_default() else 1

    def dt(self) -> float:
        speed_change: float = self.me.speed_change
        return self._rate_adjust_multiplier(speed_change)

    def nc(self) -> float:
        speed_change: float = self.me.speed_change
        return self._rate_adjust_multiplier(speed_change)

    def hd(self) -> float:
        return 1.06 if self.me.is_uses_default() else 1

    def fl(self) -> float:
        return 1.12 if self.me.is_uses_default() else 1

    def bl(self) -> float:
        return 1.12 if self.me.is_uses_default() else 1

    # Conversion
    def tp(self) -> float:
        return 0.1

    def da(self) -> float:
        return 0.5

    def cl(self) -> float:
        return 0.96

    # Automation
    def rx(self) -> float:
        return 0.1

    def ap(self) -> float:
        return 0.1

    def so(self) -> float:
        return 0.9

    # Fun
    def gr(self) -> float:
        return 0.5

    def df(self) -> float:
        return 0.5

    def ns(self) -> float:
        return 0.5

    def rp(self) -> float:
        return 0.5

    def bu(self) -> float:
        return 0.8


blinds_multiplier = 1.24


class OsuModMultiplierCalculatorV2(ModMultiplierCalculator):
    @staticmethod
    def _easy_multiplier(easy: _ModWrapper) -> float:
        retries = easy.retries
        default_retries = get_default_setting(0, easy.mod, "retries")

        if retries is None or default_retries is None:
            return 0.8

        retries = float(retries)
        default_retries = float(default_retries)

        value = 0.8 - max(0, 0.1 * (retries - default_retries))
        return max(0.4, value)

    @staticmethod
    def _half_time_multiplier(speed_change: float) -> float:
        # 0.2x at 0.5x speed, +0.07x per 0.05x speed increment.
        # Default HT (0.75x) = 0.55
        return (int(speed_change * 20) / 20.0) * 1.4 - 0.5

    @staticmethod
    def _double_time_multiplier(speed_change: float) -> float:
        # Floor to the nearest multiple of 0.1.
        value = int(speed_change * 10) / 10.0

        # 0.01 penalty for non-default rates.
        penalty = 0.01 if value != 1.5 and value != 1.0 else 0.0

        # Linear from 1.0 to 1.46, minus the penalty.
        # Default DT (1.5x) = 1.23
        return (value - 1) * 0.46 + 1 - penalty

    @staticmethod
    def _hidden_multiplier(hidden: _ModWrapper, other_mods_provide_timing_info: bool) -> float:
        value = 1.04

        if hidden.only_fade_approach_circles:
            value -= 0.02

        if other_mods_provide_timing_info:
            value -= 0.02

        return value

    @staticmethod
    def _flashlight_multiplier(flashlight: _ModWrapper) -> float:
        size_multiplier = flashlight.size_multiplier
        combo_based_size = flashlight.combo_based_size

        if size_multiplier is None:
            size_multiplier = 1.0

        size_multiplier = float(size_multiplier)

        value = max(1.02, min(1.2, 1.2 - 0.2 * (size_multiplier - 1)))

        if not combo_based_size:
            value = 1 + (value - 1) / 5

        return value

    @staticmethod
    def _difficulty_adjust_multiplier(difficulty_adjust: _ModWrapper, context: ModMultiplierContext) -> float:
        selected_circle_size = difficulty_adjust.circle_size
        selected_drain_rate = difficulty_adjust.drain_rate
        selected_overall_difficulty = difficulty_adjust.overall_difficulty
        selected_approach_rate = difficulty_adjust.approach_rate

        if selected_circle_size is None:
            selected_circle_size = context.cs
        if selected_drain_rate is None:
            selected_drain_rate = context.hp
        if selected_overall_difficulty is None:
            selected_overall_difficulty = context.od
        if selected_approach_rate is None:
            selected_approach_rate = context.ar

        selected_circle_size = float(selected_circle_size)
        selected_drain_rate = float(selected_drain_rate)
        selected_overall_difficulty = float(selected_overall_difficulty)
        selected_approach_rate = float(selected_approach_rate)

        cs_difference = abs(selected_circle_size - float(context.cs))
        hp_difference = abs(selected_drain_rate - float(context.hp))
        od_difference = abs(selected_overall_difficulty - float(context.od))
        ar_difference = abs(selected_approach_rate - float(context.ar))

        # Per parameter, reduce multiplier by 0.05x per 0.1 change.
        cs_multiplier = max(0.1, 1.0 - cs_difference * 0.5)
        hp_multiplier = max(0.1, 1.0 - hp_difference * 0.5)
        od_multiplier = max(0.1, 1.0 - od_difference * 0.5)
        ar_multiplier = max(0.1, 1.0 - ar_difference * 0.5)

        return max(0.1, cs_multiplier * hp_multiplier * od_multiplier * ar_multiplier)

    @staticmethod
    def _time_ramp_multiplier(time_ramp: _ModWrapper) -> float:
        initial_rate = time_ramp.initial_rate
        final_rate = time_ramp.final_rate

        if initial_rate is None:
            initial_rate = 1.0
        if final_rate is None:
            final_rate = 1.0

        initial_rate = float(initial_rate)
        final_rate = float(final_rate)

        min_speed = min(initial_rate, final_rate)
        max_speed = max(initial_rate, final_rate)

        min_multiplier = (
            OsuModMultiplierCalculatorV2._half_time_multiplier(min_speed)
            if min_speed < 1
            else OsuModMultiplierCalculatorV2._double_time_multiplier(min_speed)
        )
        max_multiplier = (
            OsuModMultiplierCalculatorV2._half_time_multiplier(max_speed)
            if max_speed < 1
            else OsuModMultiplierCalculatorV2._double_time_multiplier(max_speed)
        )

        return 0.8 * min_multiplier + 0.2 * max_multiplier

    @staticmethod
    def _deflate_multiplier(deflate: _ModWrapper) -> float:
        start_scale = deflate.start_scale
        default_start_scale = get_default_setting(0, deflate.mod, "start_scale")

        if start_scale is None or default_start_scale is None:
            return 1.0

        return 1.0 - max(0, 0.02 * (float(start_scale) - float(default_start_scale)))

    # Difficulty Reduction
    def ez(self) -> float:
        return self._easy_multiplier(self.me)

    def nf(self) -> float:
        return 0.5

    def ht(self) -> float:
        speed_change: float = self.me.speed_change
        return self._half_time_multiplier(speed_change)

    def dc(self) -> float:
        speed_change: float = self.me.speed_change
        return self._half_time_multiplier(speed_change)

    # Difficulty Increase
    def hr(self) -> float:
        return 1.09

    def dt(self) -> float:
        speed_change: float = self.me.speed_change
        return self._double_time_multiplier(speed_change)

    def nc(self) -> float:
        speed_change: float = self.me.speed_change
        return self._double_time_multiplier(speed_change)

    @combination("HD", "BL")
    def hd_bl(self) -> float:
        return blinds_multiplier

    @combination("HD", "WG")
    def hd_wg(self) -> float:
        hidden = self.mod("HD")
        if hidden is None:
            raise ValueError("HD mod not found in context")

        return self._hidden_multiplier(hidden, other_mods_provide_timing_info=True)

    @combination("HD", "GR")
    def hd_gr(self) -> float:
        hidden = self.mod("HD")
        if hidden is None:
            raise ValueError("HD mod not found in context")

        return self._hidden_multiplier(hidden, other_mods_provide_timing_info=True)

    @combination("HD", "DF")
    def hd_df(self) -> float:
        hidden = self.mod("HD")
        deflate = self.mod("DF")
        if hidden is None:
            raise ValueError("HD mod not found in context")
        if deflate is None:
            raise ValueError("DF mod not found in context")

        return self._hidden_multiplier(hidden, other_mods_provide_timing_info=True) * self._deflate_multiplier(deflate)

    @combination("HD", "RP")
    def hd_rp(self) -> float:
        hidden = self.mod("HD")
        if hidden is None:
            raise ValueError("HD mod not found in context")

        return self._hidden_multiplier(hidden, other_mods_provide_timing_info=True)

    @combination("HD", "DP")
    def hd_dp(self) -> float:
        hidden = self.mod("HD")
        if hidden is None:
            raise ValueError("HD mod not found in context")

        return self._hidden_multiplier(hidden, other_mods_provide_timing_info=True)

    def hd(self) -> float:
        return self._hidden_multiplier(self.me, other_mods_provide_timing_info=False)

    @combination("TC", "BL")
    def tc_bl(self) -> float:
        return blinds_multiplier

    def tc(self) -> float:
        return 1.02

    @combination("FL", "FR")
    def fl_fr(self) -> float:
        flashlight = self.mod("FL")
        if flashlight is None:
            raise ValueError("FL mod not found in context")

        return 1 + (self._flashlight_multiplier(flashlight) - 1) / 2

    def fl(self) -> float:
        flashlight = self.mod("FL")
        if flashlight is None:
            raise ValueError("FL mod not found in context")

        return self._flashlight_multiplier(flashlight)

    def bl(self) -> float:
        return blinds_multiplier

    # Conversion
    def tp(self) -> float:
        return 0.01

    def da(self) -> float:
        return self._difficulty_adjust_multiplier(self.me, self.context)

    def cl(self) -> float:
        return 0.985 if self.me.classic_note_lock else 0.96

    def rd(self) -> float:
        return 0.7

    # Automation
    def rx(self) -> float:
        return 0.1

    def ap(self) -> float:
        return 0.1

    def so(self) -> float:
        return 0.95

    # Fun
    def wu(self) -> float:
        return self._time_ramp_multiplier(self.me)

    def wd(self) -> float:
        return self._time_ramp_multiplier(self.me)

    def ad(self) -> float:
        return 0.7

    def mg(self) -> float:
        return 0.7 - self.me.attraction_strength * 0.6

    def as_(self) -> float:
        return 0.1

    def sy(self) -> float:
        return 0.99


OsuModMultiplierCalculator = (
    OsuModMultiplierCalculatorV1 if settings.use_old_score_multiplier else OsuModMultiplierCalculatorV2
)
