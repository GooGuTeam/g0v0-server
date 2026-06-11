"""Utilities for mod definitions, legacy conversion, and PP-ranked mod validation.

This package intentionally excludes multiplier-specific logic, which lives in
the dedicated multiplier subpackage.
"""

from .definition import (
    API_MODS,
    APIMod,
    Mod,
    Settings,
    get_available_mods,
    get_speed_rate,
    init_mods,
    mod_to_save,
)
from .legacy import (
    API_MOD_TO_LEGACY,
    FREEMOD,
    LEGACY_MOD_TO_API_MOD,
    int_to_mods,
    mods_to_int,
)
from .performance import (
    RANKED_MODS,
    RankedMods,
    RulesetRankedMods,
    check_settings,
    generate_ranked_mod_settings,
    init_ranked_mods,
    mods_can_get_pp,
    mods_can_get_pp_vanilla,
)

__all__ = [
    "API_MODS",
    "API_MOD_TO_LEGACY",
    "FREEMOD",
    "LEGACY_MOD_TO_API_MOD",
    "RANKED_MODS",
    "APIMod",
    "Mod",
    "RankedMods",
    "RulesetRankedMods",
    "Settings",
    "check_settings",
    "generate_ranked_mod_settings",
    "get_available_mods",
    "get_speed_rate",
    "init_mods",
    "init_ranked_mods",
    "int_to_mods",
    "mod_to_save",
    "mods_can_get_pp",
    "mods_can_get_pp_vanilla",
    "mods_to_int",
]
