from . import beatmap  # noqa: F401
from .beatmapset import BeatmapsetModel

from sqlmodel import SQLModel

SearchBeatmapset = BeatmapsetModel.generate_typeddict(("beatmaps.max_combo", "pack_tags"))


class SearchBeatmapsetsResp(SQLModel):
    beatmapsets: list[SearchBeatmapset]  # pyright: ignore[reportInvalidTypeForm]
    total: int
    cursor: dict[str, int | float | str] | None = None
    cursor_string: str | None = None
