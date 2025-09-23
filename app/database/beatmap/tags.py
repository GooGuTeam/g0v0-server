from __future__ import annotations

from sqlmodel import Field, SQLModel


class BeatmapTagVote(SQLModel, table=True):
    __tablename__: str = "beatmap_tags"
    tag_id: int = Field(primary_key=True, index=True, default=None)
    beatmap_id: int = Field(primary_key=True, index=True, default=None)
    user_id: int = Field(primary_key=True, index=True, default=None)
