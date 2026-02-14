"""Path constants module.

This module defines commonly used directory paths for static files,
configuration, and achievements.

Attributes:
    STATIC_DIR: Path to the static files directory.
    CONFIG_DIR: Path to the configuration directory.
    ACHIEVEMENTS_DIR: Path to the achievements directory.
"""

from pathlib import Path

STATIC_DIR = Path(__file__).parent.parent / "static"
CONFIG_DIR = Path(__file__).parent.parent / "config"
ACHIEVEMENTS_DIR = Path(__file__).parent / "achievements"
