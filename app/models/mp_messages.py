import uuid

from pydantic import BaseModel, Field


class MultiplayerCountdownEnvelope(BaseModel):
    seconds: int


class MultiplayerRoomStateEnvelope(BaseModel):
    locked: bool | None = None


class MultiplayerUserStateEnvelope(BaseModel):
    team_id: int | None = None
    slot_id: int | None = None


class MultiplayerRoomSettingsEnvelope(BaseModel):
    name: str | None = None
    password: str | None = None
    type: int | None = None
    max_participants: int | None = None


class MultiplayerRoomEventEnvelope(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str
    target: int | None = None
    by: int | None = None
    countdown: MultiplayerCountdownEnvelope | None = None
    room_state: MultiplayerRoomStateEnvelope | None = None
    user_state: MultiplayerUserStateEnvelope | None = None
    room_settings: MultiplayerRoomSettingsEnvelope | None = None


class MultiplayerCallbackDetails(BaseModel):
    referee_ids: set[int] | None = None


class MultiplayerCallbackMessage(BaseModel):
    id: str
    type: str
    success: bool
    message: str | None = None
    details: MultiplayerCallbackDetails
