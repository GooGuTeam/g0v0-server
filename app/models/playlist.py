from datetime import datetime

from app.models.mods import APIMod

from pydantic import BaseModel, Field


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
