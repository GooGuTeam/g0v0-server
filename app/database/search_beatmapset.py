"""Beatmapset search response models.

This module provides response models for beatmapset search results.
"""

from . import beatmap  # noqa: F401
from .beatmapset import BeatmapsetModel

from sqlmodel import SQLModel

SearchBeatmapset = BeatmapsetModel.generate_typeddict(("beatmaps.max_combo", "pack_tags"))
"""Generated TypedDict for search result beatmapsets."""


class SearchBeatmapsetsResp(SQLModel):
    """Response model for beatmapset search results."""

    beatmapsets: list[SearchBeatmapset]  # pyright: ignore[reportInvalidTypeForm]
    total: int
    cursor: dict[str, int | float | str] | None = None
    cursor_string: str | None = None
