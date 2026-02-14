"""Beatmap tag voting database models.

This module handles user votes on beatmap tags/labels.
"""

from sqlmodel import Field, SQLModel


class BeatmapTagVote(SQLModel, table=True):
    """Records user votes on beatmap tags."""

    __tablename__: str = "beatmap_tags"
    tag_id: int = Field(primary_key=True, index=True, default=None)
    beatmap_id: int = Field(primary_key=True, index=True, default=None)
    user_id: int = Field(primary_key=True, index=True, default=None)
