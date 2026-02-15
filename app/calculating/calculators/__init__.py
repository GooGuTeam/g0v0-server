import importlib

from app.config import settings
from app.plugins import plugin_manager

from ._base import CalculateError, ConvertError, DifficultyError, PerformanceCalculator, PerformanceError

CALCULATOR: PerformanceCalculator | None = None


async def init_calculator() -> PerformanceCalculator | None:
    """Initialize the performance calculator.

    Dynamically imports and initializes the configured performance calculator
    backend from app.calculating.calculators.

    Returns:
        The initialized PerformanceCalculator, or None if initialization fails.

    Raises:
        ImportError: If the calculator module cannot be imported.
    """
    global CALCULATOR
    try:
        if settings.calculator.startswith("-"):
            # Calculator is from a plugin, e.g. "-osu_native_calculator"
            plugin = plugin_manager.get_plugin_by_id(settings.calculator[1:])
            if plugin is None:
                raise ImportError(f"Plugin '{settings.calculator[1:]}' not found for performance calculator")
            module = plugin.module
            if module is None:
                raise RuntimeError(f"Plugin '{settings.calculator[1:]}' is not loaded.")
        elif "." not in settings.calculator:
            # Built-in calculator, e.g. "performance_server"
            module = importlib.import_module(f".{settings.calculator}", package="app.calculating.calculators")
        else:
            # Absolute package path, e.g. "plugins.osu_native_calculator"
            module = importlib.import_module(settings.calculator)

        CALCULATOR = module.PerformanceCalculator(**settings.calculator_config)
        if CALCULATOR is not None:
            await CALCULATOR.init()
    except (ImportError, AttributeError) as e:
        raise ImportError(f"Failed to import performance calculator for {settings.calculator}") from e
    return CALCULATOR


def get_calculator() -> PerformanceCalculator:
    """Get the initialized performance calculator.

    Returns:
        The PerformanceCalculator instance.

    Raises:
        RuntimeError: If the calculator has not been initialized.
    """
    if CALCULATOR is None:
        raise RuntimeError("Performance calculator is not initialized")
    return CALCULATOR


__all__ = [
    "CalculateError",
    "ConvertError",
    "DifficultyError",
    "PerformanceCalculator",
    "PerformanceError",
    "get_calculator",
    "init_calculator",
]
