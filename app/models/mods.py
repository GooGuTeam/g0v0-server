from __future__ import annotations

import json
from typing import NotRequired, TypedDict

from app.config import settings as app_settings
from app.log import logger
from app.path import STATIC_DIR


class APIMod(TypedDict):
    acronym: str
    settings: NotRequired[dict[str, bool | float | str | int]]


# https://github.com/ppy/osu-api/wiki#mods
API_MOD_TO_LEGACY: dict[str, int] = {
    "NF": 1 << 0,  # No Fail
    "EZ": 1 << 1,  # Easy
    "TD": 1 << 2,  # Touch Device
    "HD": 1 << 3,  # Hidden
    "HR": 1 << 4,  # Hard Rock
    "SD": 1 << 5,  # Sudden Death
    "DT": 1 << 6,  # Double Time
    "RX": 1 << 7,  # Relax
    "HT": 1 << 8,  # Half Time
    "NC": 1 << 9,  # Nightcore
    "FL": 1 << 10,  # Flashlight
    "AT": 1 << 11,  # Autoplay
    "SO": 1 << 12,  # Spun Out
    "AP": 1 << 13,  # Auto Pilot
    "PF": 1 << 14,  # Perfect
    "4K": 1 << 15,  # 4K
    "5K": 1 << 16,  # 5K
    "6K": 1 << 17,  # 6K
    "7K": 1 << 18,  # 7K
    "8K": 1 << 19,  # 8K
    "FI": 1 << 20,  # Fade In
    "RD": 1 << 21,  # Random
    "CN": 1 << 22,  # Cinema
    "TP": 1 << 23,  # Target Practice
    "9K": 1 << 24,  # 9K
    "CO": 1 << 25,  # Key Co-op
    "1K": 1 << 26,  # 1K
    "3K": 1 << 27,  # 3K
    "2K": 1 << 28,  # 2K
    "SV2": 1 << 29,  # ScoreV2
    "MR": 1 << 30,  # Mirror
}
LEGACY_MOD_TO_API_MOD = {}
for k, v in API_MOD_TO_LEGACY.items():
    LEGACY_MOD_TO_API_MOD[v] = APIMod(acronym=k, settings={})
API_MOD_TO_LEGACY["NC"] |= API_MOD_TO_LEGACY["DT"]
API_MOD_TO_LEGACY["PF"] |= API_MOD_TO_LEGACY["SD"]


# see static/mods.json
class Settings(TypedDict):
    Name: str
    Type: str
    Label: str
    Description: str


class Mod(TypedDict):
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


API_MODS: dict[int, dict[str, Mod]] = {}
NO_CHECK = "DO_NO_CHECK"
RANKED_MODS: dict[int, dict[str, dict]] = {}


def load_mods() -> None:
    mods_file = STATIC_DIR / "mods.json"
    raw_mods = json.loads(mods_file.read_text(encoding="utf-8"))
    for ruleset in raw_mods:
        ruleset_mods = {}
        for mod in ruleset["Mods"]:
            ruleset_mods[mod["Acronym"]] = mod
        API_MODS[ruleset["RulesetID"]] = ruleset_mods
        logger.success(f"Loaded {len(ruleset_mods)} mods for ruleset {ruleset['RulesetID']}")


def load_extra_rulesets_mods():
    mods_dir = STATIC_DIR / "extra_rulesets"
    for ruleset_file in mods_dir.iterdir():
        if not ruleset_file.is_file():
            continue
        ruleset_mods = {}
        raw_mods = json.loads(ruleset_file.read_text(encoding="utf-8"))
        for mod in raw_mods["Mods"]:
            ruleset_mods[mod["Acronym"]] = mod
        API_MODS[int(ruleset_file.stem)] = ruleset_mods
        logger.success(f"Loaded {len(ruleset_mods)} mods for custom ruleset {ruleset_file.stem}")


def load_ranked_mods():
    mods_file = STATIC_DIR / "ranked_mods.json"
    raw_mods = json.loads(mods_file.read_text(encoding="utf-8"))
    for ruleset_id, mods in raw_mods.items():
        RANKED_MODS[int(ruleset_id)] = mods
        logger.success(f"Loaded {len(mods)} ranked mods for rulesets {ruleset_id}")


def load_extra_ranked_mods():
    mods_dir = STATIC_DIR / "extra_rulesets" / "ranked"
    for mods_file in mods_dir.iterdir():
        raw_mods = json.loads(mods_file.read_text(encoding="utf-8"))
        ruleset_id = mods_file.stem
        mods = raw_mods[ruleset_id]
        RANKED_MODS[int(ruleset_id)] = mods
        logger.success(f"Loaded {len(mods)} ranked mods for custom rulesets {ruleset_id}")


load_mods()
load_extra_rulesets_mods()
load_ranked_mods()
load_extra_ranked_mods()


def int_to_mods(mods: int) -> list[APIMod]:
    mod_list = []
    for mod in range(31):
        if mods & (1 << mod):
            mod_list.append(LEGACY_MOD_TO_API_MOD[(1 << mod)])
    if mods & (1 << 14):
        mod_list.remove(LEGACY_MOD_TO_API_MOD[(1 << 5)])
    if mods & (1 << 9):
        mod_list.remove(LEGACY_MOD_TO_API_MOD[(1 << 6)])
    return mod_list


def mods_to_int(mods: list[APIMod]) -> int:
    sum_ = 0
    for mod in mods:
        sum_ |= API_MOD_TO_LEGACY.get(mod["acronym"], 0)
    return sum_


def mods_can_get_pp_vanilla(ruleset_id: int, mods: list[APIMod]) -> bool:
    ranked_mods = RANKED_MODS[ruleset_id]
    for mod in mods:
        mod["settings"] = mod.get("settings", {})
        if (settings := ranked_mods.get(mod["acronym"])) is None:
            return False
        if settings == {}:
            continue
        for setting, value in mod["settings"].items():
            if (expected_value := settings.get(setting)) is None:
                return False
            if expected_value != NO_CHECK and value != expected_value:
                return False
    return True


def mods_can_get_pp(ruleset_id: int, mods: list[APIMod]) -> bool:
    if app_settings.enable_all_mods_pp:
        return True
    ranked_mods = RANKED_MODS[ruleset_id]
    for mod in mods:
        if app_settings.enable_rx and mod["acronym"] == "RX" and ruleset_id in {0, 1, 2}:
            continue
        if app_settings.enable_ap and mod["acronym"] == "AP" and ruleset_id == 0:
            continue

        mod["settings"] = mod.get("settings", {})
        if (settings := ranked_mods.get(mod["acronym"])) is None:
            return False
        if settings == {}:
            continue
        for setting, value in mod["settings"].items():
            if (expected_value := settings.get(setting)) is None:
                return False
            if expected_value != NO_CHECK and value != expected_value:
                return False
    return True


ENUM_TO_STR = {
    0: {
        "MR": {"reflection"},
        "AC": {"accuracy_judge_mode"},
        "BR": {"direction"},
        "AD": {"style"},
    },
    1: {"AC": {"accuracy_judge_mode"}},
    2: {"AC": {"accuracy_judge_mode"}},
    3: {"AC": {"accuracy_judge_mode"}},
    10: {
        "AC": {"accuracy_judge_mode"},
        "C": {"live_setting"},
        "HR": {"judgement_mode", "minimum_valid_result"},
        "BR": {"direction"},
    },
}


def parse_enum_to_str(ruleset_id: int, mods: list[APIMod]):
    for mod in mods:
        if mod["acronym"] in ENUM_TO_STR.get(ruleset_id, {}):
            for setting in mod.get("settings", {}):
                if setting in ENUM_TO_STR[ruleset_id][mod["acronym"]]:
                    mod["settings"][setting] = str(mod["settings"][setting])  # pyright: ignore[reportTypedDictNotRequiredAccess]


def mod_to_save(mods: list[APIMod]) -> list[str]:
    s = list({mod["acronym"] for mod in mods})
    s.sort()
    return s


def get_speed_rate(mods: list[APIMod]):
    rate = 1.0
    for mod in mods:
        if mod["acronym"] in {"DT", "NC", "HT", "DC"}:
            rate *= mod.get("settings", {}).get("speed_change", 1.0)  # pyright: ignore[reportOperatorIssue]
    return rate


def get_available_mods(ruleset_id: int, required_mods: list[APIMod]) -> list[APIMod]:
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
