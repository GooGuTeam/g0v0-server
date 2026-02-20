import hashlib
import json
from typing import Any, Literal, NotRequired, TypedDict

from app.config import settings as app_settings
from app.log import log
from app.path import CONFIG_DIR, STATIC_DIR

from pydantic import ConfigDict, Field, create_model
from pydantic.main import BaseModel


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
FREEMOD = 0
for k, v in API_MOD_TO_LEGACY.items():
    LEGACY_MOD_TO_API_MOD[v] = APIMod(acronym=k, settings={})
    FREEMOD |= v
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


API_MODS: dict[Literal[0, 1, 2, 3], dict[str, Mod]] = {}


def init_mods():
    mods_file = STATIC_DIR / "mods.json"
    raw_mods = json.loads(mods_file.read_text(encoding="utf-8"))
    for ruleset in raw_mods:
        ruleset_mods = {}
        for mod in ruleset["Mods"]:
            ruleset_mods[mod["Acronym"]] = mod
        API_MODS[ruleset["RulesetID"]] = ruleset_mods


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


DEFAULT_RANKED_MODS = {
    0: {
        "EZ": {"retries": {"type": "number", "eq": 2}},
        "NF": {},
        "HT": {"speed_change": {"type": "number", "eq": 0.75}, "adjust_pitch": {"check": False, "type": "boolean"}},
        "DC": {"speed_change": {"type": "number", "eq": 0.75}},
        "HR": {},
        "SD": {
            "fail_on_slider_tail": {"check": False, "type": "boolean"},
            "restart": {"check": False, "type": "boolean"},
        },
        "PF": {"restart": {"check": False, "type": "boolean"}},
        "HD": {"only_fade_approach_circles": {"type": "boolean", "eq": False}},
        "DT": {"speed_change": {"type": "number", "eq": 1.5}, "adjust_pitch": {"check": False, "type": "boolean"}},
        "NC": {"speed_change": {"type": "number", "eq": 1.5}},
        "FL": {
            "follow_delay": {"type": "number", "eq": 1.0},
            "size_multiplier": {"type": "number", "eq": 1.0},
            "combo_based_size": {"type": "boolean", "eq": True},
        },
        "AC": {
            "minimum_accuracy": {"check": False, "type": "number"},
            "accuracy_judge_mode": {"check": False, "type": "string"},
            "restart": {"check": False, "type": "boolean"},
        },
        "MU": {
            "inverse_muting": {"check": False, "type": "boolean"},
            "enable_metronome": {"check": False, "type": "boolean"},
            "mute_combo_count": {"check": False, "type": "number"},
            "affects_hit_sounds": {"check": False, "type": "boolean"},
        },
        "TD": {},
        "BL": {},
        "NS": {"hidden_combo_count": {"check": False, "type": "number"}},
        "SO": {},
        "TC": {},
        "AL": {},
        "SG": {},
    },
    1: {
        "EZ": {},
        "NF": {},
        "HT": {"speed_change": {"type": "number", "eq": 0.75}, "adjust_pitch": {"check": False, "type": "boolean"}},
        "DC": {"speed_change": {"type": "number", "eq": 0.75}},
        "HR": {},
        "SD": {"restart": {"check": False, "type": "boolean"}},
        "PF": {"restart": {"check": False, "type": "boolean"}},
        "HD": {},
        "DT": {"speed_change": {"type": "number", "eq": 1.5}, "adjust_pitch": {"check": False, "type": "boolean"}},
        "NC": {"speed_change": {"type": "number", "eq": 1.5}},
        "FL": {"size_multiplier": {"type": "number", "eq": 1.0}, "combo_based_size": {"type": "boolean", "eq": True}},
        "AC": {
            "minimum_accuracy": {"check": False, "type": "number"},
            "accuracy_judge_mode": {"check": False, "type": "string"},
            "restart": {"check": False, "type": "boolean"},
        },
        "MU": {
            "inverse_muting": {"check": False, "type": "boolean"},
            "enable_metronome": {"check": False, "type": "boolean"},
            "mute_combo_count": {"check": False, "type": "number"},
            "affects_hit_sounds": {"check": False, "type": "boolean"},
        },
        "SG": {},
        "SW": {},
    },
    2: {
        "EZ": {"retries": {"type": "number", "eq": 2}},
        "NF": {},
        "HT": {"speed_change": {"type": "number", "eq": 0.75}, "adjust_pitch": {"check": False, "type": "boolean"}},
        "DC": {"speed_change": {"type": "number", "eq": 0.75}},
        "HR": {},
        "SD": {"restart": {"check": False, "type": "boolean"}},
        "PF": {"restart": {"check": False, "type": "boolean"}},
        "HD": {},
        "DT": {"speed_change": {"type": "number", "eq": 1.5}, "adjust_pitch": {"check": False, "type": "boolean"}},
        "NC": {"speed_change": {"type": "number", "eq": 1.5}},
        "FL": {"size_multiplier": {"type": "number", "eq": 1.0}, "combo_based_size": {"type": "boolean", "eq": True}},
        "AC": {
            "minimum_accuracy": {"check": False, "type": "number"},
            "accuracy_judge_mode": {"check": False, "type": "string"},
            "restart": {"check": False, "type": "boolean"},
        },
        "MU": {
            "inverse_muting": {"check": False, "type": "boolean"},
            "enable_metronome": {"check": False, "type": "boolean"},
            "mute_combo_count": {"check": False, "type": "number"},
            "affects_hit_sounds": {"check": False, "type": "boolean"},
        },
        "NS": {"hidden_combo_count": {"check": False, "type": "number"}},
    },
    3: {
        "EZ": {"retries": {"type": "number", "eq": 2}},
        "NF": {},
        "HT": {"speed_change": {"type": "number", "eq": 0.75}, "adjust_pitch": {"check": False, "type": "boolean"}},
        "DC": {"speed_change": {"type": "number", "eq": 0.75}},
        "SD": {"restart": {"check": False, "type": "boolean"}},
        "PF": {
            "require_perfect_hits": {"check": False, "type": "boolean"},
            "restart": {"check": False, "type": "boolean"},
        },
        "HD": {},
        "DT": {"speed_change": {"type": "number", "eq": 1.5}, "adjust_pitch": {"check": False, "type": "boolean"}},
        "NC": {"speed_change": {"type": "number", "eq": 1.5}},
        "FL": {"size_multiplier": {"type": "number", "eq": 1.0}, "combo_based_size": {"type": "boolean", "eq": False}},
        "AC": {
            "minimum_accuracy": {"check": False, "type": "number"},
            "accuracy_judge_mode": {"check": False, "type": "string"},
            "restart": {"check": False, "type": "boolean"},
        },
        "MU": {
            "inverse_muting": {"check": False, "type": "boolean"},
            "enable_metronome": {"check": False, "type": "boolean"},
            "mute_combo_count": {"check": False, "type": "number"},
            "affects_hit_sounds": {"check": False, "type": "boolean"},
        },
        "MR": {},
        "4K": {},
        "5K": {},
        "6K": {},
        "7K": {},
        "8K": {},
        "9K": {},
    },
}
TYPE_TO_PY = {
    "number": int | float,
    "boolean": bool,
    "string": str,
}

RulesetRankedMods = dict[str, dict[str, Any]]
RankedMods = dict[int, RulesetRankedMods]
RANKED_MODS: RankedMods = {}


class _LegacyModSettings(BaseModel):
    enable_all_mods_pp: bool = False


def _get_mods_file_checksum() -> str:
    current_mods_file = STATIC_DIR / "mods.json"
    if not current_mods_file.exists():
        return ""
    return hashlib.md5(current_mods_file.read_bytes(), usedforsecurity=False).hexdigest()


def generate_ranked_mod_settings(enable_all: bool = False):
    ranked_mods_file = CONFIG_DIR / "ranked_mods.json"
    checksum = _get_mods_file_checksum()
    legacy_setting = _LegacyModSettings.model_validate(app_settings.model_dump())
    if not legacy_setting.enable_all_mods_pp and not enable_all:
        result = DEFAULT_RANKED_MODS
    else:
        result = {}
        for ruleset_id, ruleset_mods in API_MODS.items():
            result[ruleset_id] = {}
            for mod_acronym in ruleset_mods:
                result[ruleset_id][mod_acronym] = {}
        if not enable_all:
            log("Mod").info("ENABLE_ALL_MODS_PP is deprecated, transformed to config/ranked_mods.json")
    result["$mods_checksum"] = checksum  # pyright: ignore[reportArgumentType]
    ranked_mods_file.write_text(json.dumps(result, indent=4))


def init_ranked_mods():
    ranked_mods_file = CONFIG_DIR / "ranked_mods.json"
    if ranked_mods_file.exists():
        raw_ranked_mods = json.loads(ranked_mods_file.read_text(encoding="utf-8"))
        mods_file_checksum = raw_ranked_mods.pop("$mods_checksum", None)
        if mods_file_checksum is not None and mods_file_checksum != (current_checksum := _get_mods_file_checksum()):
            raise RuntimeError(
                f"Mods file has changed, please modify ranked_mods.json or delete it to regenerate\n"
                f"Current mods checksum: {current_checksum}"
            )
        for ruleset_id_str, mods in raw_ranked_mods.items():
            ruleset_id = int(ruleset_id_str)
            RANKED_MODS[ruleset_id] = mods
    else:
        generate_ranked_mod_settings()
        init_ranked_mods()


def _generate_model(settings: dict[str, dict[str, Any]]) -> type[BaseModel]:
    fields = {}
    for setting, validation in settings.items():
        type_ = validation.get("type")
        if type_ is None:
            raise ValueError("Type is required")
        py_type = TYPE_TO_PY.get(type_)
        if py_type is None:
            raise ValueError(f"Unknown type: {type_}")

        if validation.get("check", True) is False:
            fields[setting] = (Any, None)
        elif (const_value := validation.get("eq")) is not None:
            fields[setting] = (Literal[const_value], const_value)
        elif (some_values := validation.get("in")) is not None:
            if not isinstance(some_values, list) or len(some_values) == 0:
                raise ValueError("In must be a non-empty list")
            fields[setting] = (Literal[*some_values], some_values[0])
        else:
            copy = validation.copy()
            copy.pop("type", None)
            fields[setting] = (py_type | None, Field(default=None, **copy))
    if not fields:
        raise ValueError("No fields")
    return create_model("ModSettingsValidator", __config__=ConfigDict(extra="forbid"), **fields)


def check_settings(mod: APIMod, ranked_mods: RulesetRankedMods) -> bool:
    if (settings := ranked_mods.get(mod["acronym"])) is None:
        return False
    if settings == {}:
        return True
    model = _generate_model(settings)
    try:
        model.model_validate(mod.get("settings", {}))
        return True
    except ValueError:
        return False


def _mods_can_get_pp(ruleset_id: int, mods: list[APIMod], ranked_mods: RankedMods) -> bool:
    for mod in mods:
        if app_settings.enable_rx and mod["acronym"] == "RX" and ruleset_id in {0, 1, 2}:
            continue
        if app_settings.enable_ap and mod["acronym"] == "AP" and ruleset_id == 0:
            continue
        check_settings_result = check_settings(mod, ranked_mods.get(ruleset_id, {}))
        if not check_settings_result:
            return False
    return True


def mods_can_get_pp_vanilla(ruleset_id: int, mods: list[APIMod]) -> bool:
    return _mods_can_get_pp(ruleset_id, mods, DEFAULT_RANKED_MODS)


def mods_can_get_pp(ruleset_id: int, mods: list[APIMod]) -> bool:
    return _mods_can_get_pp(ruleset_id, mods, RANKED_MODS)


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
