"""Achievement loading startup task.

Loads achievement medal definitions from Python modules in the
achievements directory during application startup.
"""

import importlib

from app.log import logger
from app.models.achievement import MEDALS, Medals
from app.path import ACHIEVEMENTS_DIR


def load_achievements() -> Medals:
    """Load all achievement definitions from the achievements directory.

    Iterates through Python files in the achievements directory and
    imports their MEDALS dictionaries into the global registry.

    Returns:
        The updated global MEDALS dictionary containing all loaded achievements.
    """
    for module in ACHIEVEMENTS_DIR.iterdir():
        if module.is_file() and module.suffix == ".py":
            module_name = module.stem
            module_achievements = importlib.import_module(f"app.achievements.{module_name}")
            medals = getattr(module_achievements, "MEDALS", {})
            MEDALS.update(medals)
            logger.success(f"Successfully loaded {len(medals)} achievements from {module_name}.py")
    return MEDALS
