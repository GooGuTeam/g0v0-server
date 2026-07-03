from datetime import datetime
from enum import IntEnum

from app.models.mods import APIMod

from pydantic import BaseModel, Field


class WinCondition(IntEnum):
    """Enumeration for win conditions in a playlist item."""

    SCORE = 0
    ACCURACY = 1
    COMBO = 2
    PP = 3


class PlaylistItem(BaseModel):
    id: int = Field(default=0, ge=-1)
    owner_id: int
    beatmap_id: int
    beatmap_checksum: str = ""
    ruleset_id: int = 0
    required_mods: list[APIMod] = Field(default_factory=list)
    allowed_mods: list[APIMod] = Field(default_factory=list)
    expired: bool = False
    playlist_order: int = 0
    played_at: datetime | None = None
    star_rating: float = 0.0
    freestyle: bool = False
    win_condition: WinCondition | None = None
