import asyncio
import importlib

from app.config import settings

from ._base import CalculateError, ConvertError, DifficultyError, PerformanceCalculator, PerformanceError

CALCULATOR: PerformanceCalculator | None = None
_init_lock = asyncio.Lock()


async def init_calculator(
    calculator: str | None = None, calculator_config: dict | None = None, set_to_global: bool = True
) -> PerformanceCalculator | None:
    """Initialize the performance calculator.

    Dynamically imports and initializes the configured performance calculator
    backend from app.calculating.calculators.

    Args:
        calculator: The name of the calculator to initialize. If None, uses the value from settings.
        calculator_config: The configuration dictionary for the calculator. If None, uses the value from settings.

    Returns:
        The initialized PerformanceCalculator, or None if initialization fails.

    Raises:
        ImportError: If the calculator module cannot be imported.
    """
    if calculator is None:
        calculator = settings.calculator
    if calculator_config is None:
        calculator_config = settings.calculator_config

    global CALCULATOR
    if set_to_global and CALCULATOR is not None:
        return CALCULATOR

    async with _init_lock:
        try:
            if calculator.startswith("-"):
                from app.plugins import manager

                # Calculator is from a plugin, e.g. "-osu_native_calculator"
                plugin = manager.get_plugin_by_id(calculator[1:])
                if plugin is None:
                    raise ImportError(f"Plugin '{calculator[1:]}' not found for performance calculator")
                module = plugin.module
                if module is None:
                    raise RuntimeError(f"Plugin '{calculator[1:]}' is not loaded.")
            elif "." not in calculator:
                # Built-in calculator, e.g. "performance_server"
                module = importlib.import_module(f".{calculator}", package="app.calculating.calculators")
            else:
                # Absolute package path, e.g. "plugins.osu_native_calculator"
                module = importlib.import_module(calculator)

            calculator_class = module.PerformanceCalculator(**calculator_config)
            if calculator_class is not None:
                await calculator_class.init()
                if set_to_global:
                    CALCULATOR = calculator_class
                else:
                    return calculator_class
        except (ImportError, AttributeError) as e:
            raise ImportError(f"Failed to import performance calculator for {calculator}") from e
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
