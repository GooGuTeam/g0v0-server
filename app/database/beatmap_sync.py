"""Beatmap synchronization tracking database models.

This module tracks the synchronization state of beatmapsets with the
official osu! servers, including sync timing and change detection.
"""

from datetime import datetime
from typing import TypedDict

from app.helpers import utcnow
from app.models.beatmap import BeatmapRankStatus

from sqlmodel import JSON, Column, DateTime, Field, SQLModel


class SavedBeatmapMeta(TypedDict):
    """Metadata for a saved beatmap in sync records."""

    beatmap_id: int
    md5: str
    is_deleted: bool
    beatmap_status: BeatmapRankStatus


class BeatmapSync(SQLModel, table=True):
    """Tracks beatmapset synchronization state with the official osu! servers."""

    beatmapset_id: int = Field(primary_key=True, foreign_key="beatmapsets.id")
    beatmaps: list[SavedBeatmapMeta] = Field(sa_column=Column(JSON))
    beatmap_status: BeatmapRankStatus = Field(index=True)
    consecutive_no_change: int = Field(default=0)
    next_sync_time: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime, index=True))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime, index=True))
