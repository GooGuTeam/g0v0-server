import importlib

from app.config import settings

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
        module = importlib.import_module(f".{settings.calculator}", package="app.calculating.calculators")
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
