from app.config import settings

from ._base import ModMultiplierCalculator


class CatchModMultiplierCalculator(ModMultiplierCalculator):
    @staticmethod
    def _rate_adjust_multiplier(speed_change: float) -> float:
        value = int(speed_change * 10) / 10.0
        value -= 1

        if speed_change >= 1:
            return 1 + value / 5
        return 0.6 + value

    def ez(self) -> float:
        return 0.5

    def nf(self) -> float:
        return 0.5

    def ht(self) -> float:
        return self._rate_adjust_multiplier(self.me.speed_change)

    def dc(self) -> float:
        return self._rate_adjust_multiplier(self.me.speed_change)

    def hr(self) -> float:
        return 1.12 if self.me.is_uses_default() else 1

    def hd(self) -> float:
        return 1.06 if self.me.is_uses_default() else 1

    def dt(self) -> float:
        return self._rate_adjust_multiplier(self.me.speed_change)

    def nc(self) -> float:
        return self._rate_adjust_multiplier(self.me.speed_change)

    def fl(self) -> float:
        return 1.12 if self.me.is_uses_default() else 1

    def da(self) -> float:
        return 0.5

    def cl(self) -> float:
        return 0.96 if settings.use_old_score_multiplier else 1

    def rx(self) -> float:
        return 0.1

    def wu(self) -> float:
        return 0.5

    def wd(self) -> float:
        return 0.5

    def sy(self) -> float:
        return 0.8
