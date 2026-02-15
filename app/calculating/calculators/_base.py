"""Base class for performance calculators.

This module defines the base class for performance calculators, which are responsible for calculating the performance
attributes of a score based on the beatmap and mods used. It also defines custom exceptions for error handling during
the calculation process.
"""

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
    """The available game modes for performance and difficulty calculation."""

    has_performance_calculator: set[GameMode]
    has_difficulty_calculator: set[GameMode]


class PerformanceCalculator(abc.ABC):
    """Base class for performance calculators."""

    def __init__(self, **kwargs) -> None:
        pass

    @abc.abstractmethod
    async def get_available_modes(self) -> AvailableModes:
        """Get the available game modes for performance and difficulty calculation.

        Returns:
            AvailableModes: The available game modes for performance and difficulty calculation.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def calculate_performance(self, beatmap_raw: str, score: "Score") -> PerformanceAttributes:
        """Calculate the performance attributes of a score.

        Args:
            beatmap_raw: The raw beatmap file (`.osu`).
            score: The score for which to calculate the performance attributes.

        Returns:
            PerformanceAttributes: The calculated performance attributes.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def calculate_difficulty(
        self, beatmap_raw: str, mods: list[APIMod] | None = None, gamemode: GameMode | None = None
    ) -> DifficultyAttributes:
        """Calculate the difficulty attributes of a beatmap.

        Args:
            beatmap_raw: The raw beatmap file (`.osu`).
            mods: The mods used for the calculation (optional).
            gamemode: The game mode for the calculation (optional).

        Returns:
            DifficultyAttributes: The calculated difficulty attributes.
        """
        raise NotImplementedError

    async def can_calculate_performance(self, gamemode: GameMode) -> bool:
        """Check if the calculator can calculate performance for the given game mode.

        Args:
            gamemode: The game mode to check.

        Returns:
            bool: True if the calculator can calculate performance for the given game mode, False otherwise.
        """
        modes = await self.get_available_modes()
        return gamemode in modes.has_performance_calculator

    async def can_calculate_difficulty(self, gamemode: GameMode) -> bool:
        """Check if the calculator can calculate difficulty for the given game mode.

        Args:
            gamemode: The game mode to check.

        Returns:
            bool: True if the calculator can calculate difficulty for the given game mode, False otherwise.
        """
        modes = await self.get_available_modes()
        return gamemode in modes.has_difficulty_calculator

    async def init(self) -> None:
        """Initialize the calculator (if needed)."""
        pass
