from enum import Enum
from typing import TypedDict, cast

from app.config import settings

from .mods import API_MODS, APIMod

from pydantic import BaseModel, Field, ValidationInfo, field_serializer, field_validator


class GameMode(str, Enum):
    OSU = "osu"
    TAIKO = "taiko"
    FRUITS = "fruits"
    MANIA = "mania"

    OSURX = "osurx"
    OSUAP = "osuap"
    TAIKORX = "taikorx"
    FRUITSRX = "fruitsrx"

    SENTAKKI = "Sentakki"
    TAU = "tau"
    RUSH = "rush"
    HISHIGATA = "hishigata"
    SOYOKAZE = "soyokaze"

    def __int__(self) -> int:
        return {
            GameMode.OSU: 0,
            GameMode.TAIKO: 1,
            GameMode.FRUITS: 2,
            GameMode.MANIA: 3,
            GameMode.OSURX: 0,
            GameMode.OSUAP: 0,
            GameMode.TAIKORX: 1,
            GameMode.FRUITSRX: 2,
            GameMode.SENTAKKI: 10,
            GameMode.TAU: 11,
            GameMode.RUSH: 12,
            GameMode.HISHIGATA: 13,
            GameMode.SOYOKAZE: 14,
        }[self]

    def __str__(self) -> str:
        return self.value

    @classmethod
    def from_int(cls, v: int) -> "GameMode":
        return {
            0: GameMode.OSU,
            1: GameMode.TAIKO,
            2: GameMode.FRUITS,
            3: GameMode.MANIA,
            10: GameMode.SENTAKKI,
            11: GameMode.TAU,
            12: GameMode.RUSH,
            13: GameMode.HISHIGATA,
            14: GameMode.SOYOKAZE,
        }[v]

    @classmethod
    def from_int_extra(cls, v: int) -> "GameMode":
        gamemode = {
            4: GameMode.OSURX,
            5: GameMode.OSUAP,
            6: GameMode.TAIKORX,
            7: GameMode.FRUITSRX,
        }.get(v)
        return gamemode or cls.from_int(v)

    def readable(self) -> str:
        return {
            GameMode.OSU: "osu!",
            GameMode.TAIKO: "osu!taiko",
            GameMode.FRUITS: "osu!catch",
            GameMode.MANIA: "osu!mania",
            GameMode.OSURX: "osu!relax",
            GameMode.OSUAP: "osu!autopilot",
            GameMode.TAIKORX: "taiko relax",
            GameMode.FRUITSRX: "catch relax",
            GameMode.SENTAKKI: "sentakki",
            GameMode.TAU: "tau",
            GameMode.RUSH: "Rush!",
            GameMode.HISHIGATA: "hishigata",
            GameMode.SOYOKAZE: "soyokaze!",
        }[self]

    def is_official(self) -> bool:
        return self in {
            GameMode.OSU,
            GameMode.TAIKO,
            GameMode.FRUITS,
            GameMode.MANIA,
            GameMode.OSURX,
            GameMode.TAIKORX,
            GameMode.FRUITSRX,
        }

    def is_custom_ruleset(self) -> bool:
        return not self.is_official()

    def to_base_ruleset(self) -> "GameMode":
        gamemode = {
            GameMode.OSURX: GameMode.OSU,
            GameMode.OSUAP: GameMode.OSU,
            GameMode.TAIKORX: GameMode.TAIKO,
            GameMode.FRUITSRX: GameMode.FRUITS,
        }.get(self)
        return gamemode or self

    def to_special_mode(self, mods: list[APIMod] | list[str]) -> "GameMode":
        if self not in (GameMode.OSU, GameMode.TAIKO, GameMode.FRUITS):
            return self
        if not settings.enable_rx and not settings.enable_ap:
            return self
        if len(mods) > 0 and isinstance(mods[0], dict):
            mods = [mod["acronym"] for mod in cast(list[APIMod], mods)]
        if "AP" in mods and settings.enable_ap:
            return GameMode.OSUAP
        if "RX" in mods and settings.enable_rx:
            return {
                GameMode.OSU: GameMode.OSURX,
                GameMode.TAIKO: GameMode.TAIKORX,
                GameMode.FRUITS: GameMode.FRUITSRX,
            }[self]
        return self

    @classmethod
    def parse(cls, v: str | int) -> "GameMode | None":
        if isinstance(v, int) or v.isdigit():
            return cls.from_int_extra(int(v))
        v = v.upper()
        try:
            return cls[v]
        except ValueError:
            return None


class Rank(str, Enum):
    X = "X"
    XH = "XH"
    S = "S"
    SH = "SH"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"

    @property
    def in_statisctics(self):
        return self in {
            Rank.X,
            Rank.XH,
            Rank.S,
            Rank.SH,
            Rank.A,
        }


# https://github.com/ppy/osu/blob/master/osu.Game/Rulesets/Scoring/HitResult.cs
class HitResult(str, Enum):
    NONE = "none"  # [Order(15)]

    MISS = "miss"  # [Order(5)]
    MEH = "meh"  # [Order(4)]
    OK = "ok"  # [Order(3)]
    GOOD = "good"  # [Order(2)]
    GREAT = "great"  # [Order(1)]
    PERFECT = "perfect"  # [Order(0)]

    SMALL_TICK_MISS = "small_tick_miss"  # [Order(12)]
    SMALL_TICK_HIT = "small_tick_hit"  # [Order(7)]
    LARGE_TICK_MISS = "large_tick_miss"  # [Order(11)]
    LARGE_TICK_HIT = "large_tick_hit"  # [Order(6)]

    SMALL_BONUS = "small_bonus"  # [Order(10)]
    LARGE_BONUS = "large_bonus"  # [Order(9)]

    IGNORE_MISS = "ignore_miss"  # [Order(14)]
    IGNORE_HIT = "ignore_hit"  # [Order(13)]

    COMBO_BREAK = "combo_break"  # [Order(16)]

    SLIDER_TAIL_HIT = "slider_tail_hit"  # [Order(8)]

    LEGACY_COMBO_INCREASE = "legacy_combo_increase"  # [Order(99)] @deprecated

    def is_hit(self) -> bool:
        return self not in (
            HitResult.NONE,
            HitResult.IGNORE_MISS,
            HitResult.COMBO_BREAK,
            HitResult.LARGE_TICK_MISS,
            HitResult.SMALL_TICK_MISS,
            HitResult.MISS,
        )

    def is_scorable(self) -> bool:
        return self not in (
            HitResult.NONE,
            HitResult.IGNORE_HIT,
            HitResult.IGNORE_MISS,
        )


class LeaderboardType(Enum):
    GLOBAL = "global"
    FRIENDS = "friend"
    COUNTRY = "country"
    TEAM = "team"


ScoreStatistics = dict[HitResult, int]


class SoloScoreSubmissionInfo(BaseModel):
    rank: Rank
    total_score: int = Field(ge=0, le=2**31 - 1)
    total_score_without_mods: int = Field(ge=0, le=2**31 - 1)
    accuracy: float = Field(ge=0, le=1)
    pp: float = Field(default=0, ge=0, le=2**31 - 1)
    max_combo: int = 0
    ruleset_id: int
    passed: bool = False
    mods: list[APIMod] = Field(default_factory=list)
    statistics: ScoreStatistics = Field(default_factory=dict)
    maximum_statistics: ScoreStatistics = Field(default_factory=dict)

    @field_validator("mods", mode="after")
    @classmethod
    def validate_mods(cls, mods: list[APIMod], info: ValidationInfo):
        incompatible_mods = set()
        # check incompatible mods
        for mod in mods:
            if mod["acronym"] in incompatible_mods:
                raise ValueError(f"Mod {mod['acronym']} is incompatible with other mods")
            setting_mods = API_MODS[info.data["ruleset_id"]].get(mod["acronym"])
            if not setting_mods:
                raise ValueError(f"Invalid mod: {mod['acronym']}")
            incompatible_mods.update(setting_mods["IncompatibleMods"])
        return mods

    @field_serializer("statistics", "maximum_statistics", when_used="json")
    def serialize_statistics(self, v):
        """序列化统计字段，确保枚举值正确转换为字符串"""
        if isinstance(v, dict):
            serialized = {}
            for key, value in v.items():
                if hasattr(key, "value"):
                    # 如果是枚举，使用其值
                    serialized[key.value] = value
                else:
                    # 否则直接使用键
                    serialized[str(key)] = value
            return serialized
        return v

    @field_serializer("rank", when_used="json")
    def serialize_rank(self, v):
        """序列化等级，确保枚举值正确转换为字符串"""
        if hasattr(v, "value"):
            return v.value
        return str(v)


class LegacyReplaySoloScoreInfo(TypedDict):
    online_id: int
    mods: list[APIMod]
    statistics: ScoreStatistics
    maximum_statistics: ScoreStatistics
    client_version: str
    rank: Rank
    user_id: int
    total_score_without_mods: int
