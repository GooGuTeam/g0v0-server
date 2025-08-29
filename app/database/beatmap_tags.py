from __future__ import annotations

from sqlmodel import Field, SQLModel


class BeatmapTagVote(SQLModel):
    __tablename__ = "beatmap_tag_votes"  # pyright: ignore[reportAssignmentType]
    tag_id: int | None = Field(primary_key=True, index=True, default=None)
    beatmap_id: int | None = Field(primary_key=True, index=True, default=None)
