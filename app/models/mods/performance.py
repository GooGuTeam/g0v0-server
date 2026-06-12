"""Ranked-mod configuration and validation for PP eligibility checks."""

import hashlib
import json
from typing import Any, Literal

from app.config import settings as app_settings
from app.log import log
from app.path import CONFIG_DIR, STATIC_DIR

from .definition import API_MODS, APIMod

from pydantic import ConfigDict, Field, create_model
from pydantic.main import BaseModel

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
    """Backward-compatible settings view for legacy env flags."""

    enable_all_mods_pp: bool = False


def _get_mods_file_checksum() -> str:
    """Return md5 checksum for static mods metadata used by ranked config."""

    current_mods_file = STATIC_DIR / "mods.json"
    if not current_mods_file.exists():
        return ""
    return hashlib.md5(current_mods_file.read_bytes(), usedforsecurity=False).hexdigest()


def generate_ranked_mod_settings(enable_all: bool = False):
    """Generate config/ranked_mods.json from defaults or all discovered mods."""

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
    """Load ranked mod rules from config and validate against mods checksum."""

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
    """Build a runtime Pydantic model from ranked-mod setting constraints."""

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
    """Validate one mod settings payload against ranked-mod constraints."""

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
    """Check if a mod list is PP-eligible under a given ranked-mod ruleset."""

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
    """Validate PP eligibility against built-in default ranked-mod rules."""

    return _mods_can_get_pp(ruleset_id, mods, DEFAULT_RANKED_MODS)


def mods_can_get_pp(ruleset_id: int, mods: list[APIMod]) -> bool:
    """Validate PP eligibility against loaded runtime ranked-mod rules."""

    return _mods_can_get_pp(ruleset_id, mods, RANKED_MODS)
