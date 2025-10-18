import importlib

from app.log import logger
from app.models.achievement import MEDALS, Medals
from app.path import ACHIEVEMENTS_DIR


def load_achievements() -> Medals:
    for module in ACHIEVEMENTS_DIR.iterdir():
        if module.is_file() and module.suffix == ".py":
            module_name = module.stem
            module_achievements = importlib.import_module(f"app.achievements.{module_name}")
            medals = getattr(module_achievements, "MEDALS", {})
            MEDALS.update(medals)
            logger.success(f"Successfully loaded {len(medals)} achievements from {module_name}.py")
    return MEDALS
