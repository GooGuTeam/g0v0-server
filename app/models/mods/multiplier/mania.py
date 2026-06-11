from datetime import datetime

from app.config import settings

from ._base import ModMultiplierCalculator


class ManiaModMultiplierCalculator(ModMultiplierCalculator):
    @staticmethod
    def _rate_adjust_multiplier(speed_change: float) -> float:
        value = int(speed_change * 10) / 10.0
        value -= 1

        if speed_change >= 1:
            return 1 + value / 5
        return 0.6 + value

    @staticmethod
    def _key_mod_multiplier_from_context(client_version: str, date: datetime) -> float:
        if client_version:
            pieces = client_version.split(".")
            if len(pieces) >= 2:
                try:
                    year = int(pieces[0])
                    month_day = int(pieces[1])
                except ValueError:
                    pass
                else:
                    if year < 2025 or (year == 2025 and month_day < 718):
                        return 1
                    return 0.9

        cutoff = datetime(2025, 7, 18, tzinfo=date.tzinfo)
        if date < cutoff:
            return 1
        return 0.9

    def ez(self) -> float:
        return 0.5

    def nf(self) -> float:
        return 0.5

    def ht(self) -> float:
        return self._rate_adjust_multiplier(self.me.speed_change)

    def dc(self) -> float:
        return self._rate_adjust_multiplier(self.me.speed_change)

    def nr(self) -> float:
        return 0.9

    def da(self) -> float:
        return 0.5

    def cl(self) -> float:
        return 0.96 if settings.use_old_score_multiplier else 1

    def cs(self) -> float:
        return 0.9

    def ho(self) -> float:
        return 0.9

    def k1(self) -> float:
        return self._key_mod_multiplier_from_context(self.context.client_version, self.context.date)

    def k2(self) -> float:
        return self._key_mod_multiplier_from_context(self.context.client_version, self.context.date)

    def k3(self) -> float:
        return self._key_mod_multiplier_from_context(self.context.client_version, self.context.date)

    def k4(self) -> float:
        return self._key_mod_multiplier_from_context(self.context.client_version, self.context.date)

    def k5(self) -> float:
        return self._key_mod_multiplier_from_context(self.context.client_version, self.context.date)

    def k6(self) -> float:
        return self._key_mod_multiplier_from_context(self.context.client_version, self.context.date)

    def k7(self) -> float:
        return self._key_mod_multiplier_from_context(self.context.client_version, self.context.date)

    def k8(self) -> float:
        return self._key_mod_multiplier_from_context(self.context.client_version, self.context.date)

    def k9(self) -> float:
        return self._key_mod_multiplier_from_context(self.context.client_version, self.context.date)

    def k10(self) -> float:
        return self._key_mod_multiplier_from_context(self.context.client_version, self.context.date)

    def wu(self) -> float:
        return 0.5

    def wd(self) -> float:
        return 0.5

    def mu(self) -> float:
        return 0.5

    def as_(self) -> float:
        return 0.5
