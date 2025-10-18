from datetime import datetime
from typing import TypedDict

from app.models.beatmap import BeatmapRankStatus
from app.utils import utcnow

from sqlmodel import JSON, Column, DateTime, Field, SQLModel


class SavedBeatmapMeta(TypedDict):
    beatmap_id: int
    md5: str
    is_deleted: bool
    beatmap_status: BeatmapRankStatus


class BeatmapSync(SQLModel, table=True):
    beatmapset_id: int = Field(primary_key=True, foreign_key="beatmapsets.id")
    beatmaps: list[SavedBeatmapMeta] = Field(sa_column=Column(JSON))
    beatmap_status: BeatmapRankStatus = Field(index=True)
    consecutive_no_change: int = Field(default=0)
    next_sync_time: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime, index=True))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime, index=True))
