import abc
from typing import TYPE_CHECKING, NamedTuple

from app.models.mods import APIMod
from app.models.performance import DifficultyAttributes, PerformanceAttributes
from app.models.score import GameMode

if TYPE_CHECKING:
    from app.database.score import Score


class CalculateError(Exception):
    """An error occurred during performance calculation."""


class DifficultyError(CalculateError):
    """The difficulty could not be calculated."""


class ConvertError(DifficultyError):
    """A beatmap cannot be converted to the specified game mode."""


class PerformanceError(CalculateError):
    """The performance could not be calculated."""


class AvailableModes(NamedTuple):
    has_performance_calculator: set[GameMode]
    has_difficulty_calculator: set[GameMode]


class PerformanceCalculator(abc.ABC):
    def __init__(self, **kwargs) -> None:
        pass

    @abc.abstractmethod
    async def get_available_modes(self) -> AvailableModes:
        raise NotImplementedError

    @abc.abstractmethod
    async def calculate_performance(self, beatmap_raw: str, score: "Score") -> PerformanceAttributes:
        raise NotImplementedError

    @abc.abstractmethod
    async def calculate_difficulty(
        self, beatmap_raw: str, mods: list[APIMod] | None = None, gamemode: GameMode | None = None
    ) -> DifficultyAttributes:
        raise NotImplementedError

    async def can_calculate_performance(self, gamemode: GameMode) -> bool:
        modes = await self.get_available_modes()
        return gamemode in modes.has_performance_calculator

    async def can_calculate_difficulty(self, gamemode: GameMode) -> bool:
        modes = await self.get_available_modes()
        return gamemode in modes.has_difficulty_calculator

    async def init(self) -> None:
        """Initialize the calculator (if needed)."""
        pass
