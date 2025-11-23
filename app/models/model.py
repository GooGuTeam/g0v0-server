from dataclasses import dataclass
from datetime import UTC, datetime

from app.models.score import GameMode

from pydantic import BaseModel, FieldSerializationInfo, field_serializer


class UTCBaseModel(BaseModel):
    @field_serializer("*", when_used="always")
    def serialize_datetime(self, v, _info: FieldSerializationInfo):
        if isinstance(v, datetime):
            if v.tzinfo is None:
                v = v.replace(tzinfo=UTC)
            return v.astimezone(UTC)
        return v


Cursor = dict[str, int | float]


class RespWithCursor(BaseModel):
    cursor: Cursor | None = None


class PinAttributes(BaseModel):
    is_pinned: bool
    score_id: int


class CurrentUserAttributes(BaseModel):
    can_beatmap_update_owner: bool | None = None
    can_delete: bool | None = None
    can_edit_metadata: bool | None = None
    can_edit_tags: bool | None = None
    can_hype: bool | None = None
    can_hype_reason: str | None = None
    can_love: bool | None = None
    can_remove_from_loved: bool | None = None
    is_watching: bool | None = None
    new_hype_time: datetime | None = None
    nomination_modes: list[GameMode] | None = None
    remaining_hype: int | None = None
    can_destroy: bool | None = None
    can_reopen: bool | None = None
    can_moderate_kudosu: bool | None = None
    can_resolve: bool | None = None
    vote_score: int | None = None
    can_message: bool | None = None
    can_message_error: str | None = None
    last_read_id: int | None = None
    can_new_comment: bool | None = None
    can_new_comment_reason: str | None = None
    pin: PinAttributes | None = None


@dataclass
class UserAgentInfo:
    raw_ua: str = ""
    browser: str | None = None
    version: str | None = None
    os: str | None = None
    platform: str | None = None
    is_mobile: bool = False
    is_tablet: bool = False
    is_pc: bool = False
    is_client: bool = False

    @property
    def displayed_name(self) -> str:
        parts = []
        if self.browser:
            parts.append(self.browser)
            if self.version:
                parts.append(self.version)
        if self.os:
            if parts:
                parts.append(f"on {self.os}")
            else:
                parts.append(self.os)
        return " ".join(parts) if parts else "Unknown"

    def __str__(self) -> str:
        return self.displayed_name
