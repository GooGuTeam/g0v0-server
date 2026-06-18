"""Conversions between lazer API mods and legacy bitwise mod flags."""

from .definition import APIMod

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


def int_to_mods(mods: int) -> list[APIMod]:
    """Convert legacy bit flags to API mod payloads."""

    mod_list = []
    for mod in range(31):
        if mods & (1 << mod):
            mod_list.append(LEGACY_MOD_TO_API_MOD[(1 << mod)])
    if mods & (1 << 14):  # PF
        mod_list.remove(LEGACY_MOD_TO_API_MOD[(1 << 5)])  # SD
    if mods & (1 << 9):  # NC
        mod_list.remove(LEGACY_MOD_TO_API_MOD[(1 << 6)])  # DT
    return mod_list


def mods_to_int(mods: list[APIMod]) -> int:
    """Convert API mod payloads to a legacy bitwise integer."""

    sum_ = 0
    for mod in mods:
        sum_ |= API_MOD_TO_LEGACY.get(mod["acronym"], 0)
        if mod["acronym"] == "PF":
            sum_ |= API_MOD_TO_LEGACY["SD"]
        elif mod["acronym"] == "NC":
            sum_ |= API_MOD_TO_LEGACY["DT"]
    return sum_
