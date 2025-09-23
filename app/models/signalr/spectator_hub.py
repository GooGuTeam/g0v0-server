from __future__ import annotations

import datetime
from enum import IntEnum
from typing import Annotated, Any

from app.models.beatmap import BeatmapRankStatus
from app.models.beatmap.mods import APIMod
from app.models.score import (
    ScoreStatistics,
)
from app.models.signalr import SignalRMeta, UserState

from pydantic import BaseModel, Field, field_validator


class SpectatedUserState(IntEnum):
    Idle = 0
    Playing = 1
    Paused = 2
    Passed = 3
    Failed = 4
    Quit = 5


class SpectatorState(BaseModel):
    beatmap_id: int | None = None
    ruleset_id: int | None = None  # 0,1,2,3
    mods: list[APIMod] = Field(default_factory=list)
    state: SpectatedUserState
    maximum_statistics: ScoreStatistics = Field(default_factory=dict)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SpectatorState):
            return False
        return (
            self.beatmap_id == other.beatmap_id
            and self.ruleset_id == other.ruleset_id
            and self.mods == other.mods
            and self.state == other.state
        )


class ScoreProcessorStatistics(BaseModel):
    base_score: float
    maximum_base_score: float
    accuracy_judgement_count: int
    combo_portion: float
    bonus_portion: float


class FrameHeader(BaseModel):
    total_score: int
    accuracy: float
    combo: int
    max_combo: int
    statistics: ScoreStatistics = Field(default_factory=dict)
    score_processor_statistics: ScoreProcessorStatistics
    received_time: datetime.datetime
    mods: list[APIMod] = Field(default_factory=list)

    @field_validator("received_time", mode="before")
    @classmethod
    def validate_timestamp(cls, v: Any) -> datetime.datetime:
        if isinstance(v, list):
            return v[0]
        if isinstance(v, datetime.datetime):
            return v
        if isinstance(v, int | float):
            return datetime.datetime.fromtimestamp(v, tz=datetime.UTC)
        if isinstance(v, str):
            return datetime.datetime.fromisoformat(v)
        raise ValueError(f"Cannot convert {type(v)} to datetime")


# class ReplayButtonState(IntEnum):
#     NONE = 0
#     LEFT1 = 1
#     RIGHT1 = 2
#     LEFT2 = 4
#     RIGHT2 = 8
#     SMOKE = 16


class LegacyReplayFrame(BaseModel):
    time: float  # from ReplayFrame,the parent of LegacyReplayFrame
    mouse_x: float | None = None
    mouse_y: float | None = None
    button_state: int

    header: Annotated[FrameHeader | None, Field(default=None), SignalRMeta(member_ignore=True)]


class FrameDataBundle(BaseModel):
    header: FrameHeader
    frames: list[LegacyReplayFrame]


# Use for server
class APIUser(BaseModel):
    id: int
    name: str


class ScoreInfo(BaseModel):
    mods: list[APIMod]
    user: APIUser
    ruleset: int
    maximum_statistics: ScoreStatistics
    id: int | None = None
    total_score: int | None = None
    accuracy: float | None = None
    max_combo: int | None = None
    combo: int | None = None
    statistics: ScoreStatistics = Field(default_factory=dict)


class StoreScore(BaseModel):
    score_info: ScoreInfo
    replay_frames: list[LegacyReplayFrame] = Field(default_factory=list)


class StoreClientState(UserState):
    state: SpectatorState | None = None
    beatmap_status: BeatmapRankStatus | None = None
    checksum: str | None = None
    ruleset_id: int | None = None
    score_token: int | None = None
    watched_user: set[int] = Field(default_factory=set)
    score: StoreScore | None = None
