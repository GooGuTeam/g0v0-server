from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import NotRequired, TypedDict

from app.models.model import UTCBaseModel

from pydantic import BaseModel


class PlayStyle(str, Enum):
    MOUSE = "mouse"
    KEYBOARD = "keyboard"
    TABLET = "tablet"
    TOUCH = "touch"


class Country(BaseModel):
    code: str
    name: str


class Cover(BaseModel):
    custom_url: str | None = None
    url: str
    id: int | None = None


class Level(BaseModel):
    current: int
    progress: int


class GradeCounts(BaseModel):
    ss: int = 0
    ssh: int = 0
    s: int = 0
    sh: int = 0
    a: int = 0


class Statistics(BaseModel):
    count_100: int = 0
    count_300: int = 0
    count_50: int = 0
    count_miss: int = 0
    level: Level
    global_rank: int | None = None
    global_rank_exp: int | None = None
    pp: float = 0.0
    pp_exp: float = 0.0
    ranked_score: int = 0
    hit_accuracy: float = 0.0
    play_count: int = 0
    play_time: int = 0
    total_score: int = 0
    total_hits: int = 0
    maximum_combo: int = 0
    replays_watched_by_others: int = 0
    is_ranked: bool = False
    grade_counts: GradeCounts
    country_rank: int | None = None
    rank: dict | None = None


class Kudosu(BaseModel):
    available: int = 0
    total: int = 0


class MonthlyPlaycount(BaseModel):
    start_date: str
    count: int


class RankHighest(UTCBaseModel):
    rank: int
    updated_at: datetime


class RankHistory(BaseModel):
    mode: str
    data: list[int]


class Page(TypedDict):
    html: NotRequired[str]
    raw: NotRequired[str]


class BeatmapsetType(str, Enum):
    FAVOURITE = "favourite"
    GRAVEYARD = "graveyard"
    GUEST = "guest"
    LOVED = "loved"
    MOST_PLAYED = "most_played"
    NOMINATED = "nominated"
    PENDING = "pending"
    RANKED = "ranked"
