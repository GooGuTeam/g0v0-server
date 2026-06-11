"""Core mod schema and helpers backed by static/mods.json."""

import json
from typing import Literal, NotRequired, TypedDict

from app.path import STATIC_DIR


class APIMod(TypedDict):
    """Runtime mod payload used by API requests and score data."""

    acronym: str
    settings: NotRequired[dict[str, bool | float | str | int]]


# see static/mods.json
class Settings(TypedDict):
    """A single configurable setting descriptor from static mod metadata."""

    Name: str
    Type: str
    Label: str
    Description: str
    DefaultValue: bool | float | str | int | None


class Mod(TypedDict):
    """Static metadata entry for one mod in one ruleset."""

    Acronym: str
    Name: str
    Description: str
    Type: str
    Settings: list[Settings]
    IncompatibleMods: list[str]
    RequiresConfiguration: bool
    UserPlayable: bool
    ValidForMultiplayer: bool
    ValidForFreestyleAsRequiredMod: bool
    ValidForMultiplayerAsFreeMod: bool
    AlwaysValidForSubmission: bool


API_MODS: dict[Literal[0, 1, 2, 3], dict[str, Mod]] = {}


def init_mods():
    """Load static mod definitions into API_MODS, grouped by ruleset id."""

    mods_file = STATIC_DIR / "mods.json"
    raw_mods = json.loads(mods_file.read_text(encoding="utf-8"))
    for ruleset in raw_mods:
        ruleset_mods = {}
        for mod in ruleset["Mods"]:
            ruleset_mods[mod["Acronym"]] = mod
        API_MODS[ruleset["RulesetID"]] = ruleset_mods


def get_available_mods(ruleset_id: int, required_mods: list[APIMod]) -> list[APIMod]:
    """Return user-playable mods that are compatible with required mods."""

    if ruleset_id not in API_MODS:
        return []

    ruleset_mods = API_MODS[ruleset_id]
    required_mod_acronyms = {mod["acronym"] for mod in required_mods}

    incompatible_mods = set()
    for mod_acronym in required_mod_acronyms:
        if mod_acronym in ruleset_mods:
            incompatible_mods.update(ruleset_mods[mod_acronym]["IncompatibleMods"])

    available_mods = []
    for mod_acronym, mod_data in ruleset_mods.items():
        if mod_acronym in required_mod_acronyms:
            continue

        if mod_acronym in incompatible_mods:
            continue

        if any(required_acronym in mod_data["IncompatibleMods"] for required_acronym in required_mod_acronyms):
            continue

        if mod_data.get("UserPlayable", False):
            available_mods.append(mod_acronym)

    return [APIMod(acronym=acronym) for acronym in available_mods]


def mod_to_save(mods: list[APIMod]) -> list[str]:
    """Normalize mods to a sorted, de-duplicated acronym list for storage."""

    s = list({mod["acronym"] for mod in mods})
    s.sort()
    return s


def get_speed_rate(mods: list[APIMod]):
    """Calculate effective playback speed multiplier from speed-changing mods."""

    rate = 1.0
    for mod in mods:
        if mod["acronym"] in {"DT", "NC", "HT", "DC"}:
            mod_rate = mod.get("settings", {}).get("speed_change", 1.0)
            if mod_rate == 0:
                continue
            rate *= mod_rate  # pyright: ignore[reportOperatorIssue]
    return rate


def get_default_setting(mod: APIMod, setting_name: str) -> bool | float | str | int | None:
    """Helper to get a setting's default value from a mod's static metadata."""

    ruleset_mods = API_MODS.get(0, {})
    mod_data = ruleset_mods.get(mod["acronym"])
    if not mod_data:
        return None

    for setting in mod_data["Settings"]:
        if setting["Name"] == setting_name:
            return setting["DefaultValue"]

    return None
