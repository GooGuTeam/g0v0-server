from enum import Enum

from app.models.score import GameMode, SoloScoreSubmissionInfo

from ._base import PluginEvent


class ScoreType(Enum):
    SOLO = "solo"
    MULTIPLAYER = "multiplayer"


class ScoreCreatedEvent(PluginEvent):
    """Event fired when a new score is created."""

    user_id: int
    beatmap_id: int
    beatmap_hash: str
    gamemode: GameMode
    score_token: int
    score_type: ScoreType


class SoloScoreCreatedEvent(ScoreCreatedEvent):
    """Event fired when a new solo score is created."""

    score_type: ScoreType = ScoreType.SOLO


class MultiplayerScoreCreatedEvent(ScoreCreatedEvent):
    """Event fired when a new multiplayer score is created."""

    score_type: ScoreType = ScoreType.MULTIPLAYER
    room_id: int
    playlist_id: int


class ScoreSubmittedEvent(PluginEvent):
    """Event fired when a score is submitted for processing."""

    user_id: int
    submission_info: SoloScoreSubmissionInfo
    score_type: ScoreType


class SoloScoreSubmittedEvent(ScoreSubmittedEvent):
    """Event fired when a solo score is submitted for processing."""

    score_type: ScoreType = ScoreType.SOLO


class MultiplayerScoreSubmittedEvent(ScoreSubmittedEvent):
    """Event fired when a multiplayer score is submitted for processing."""

    score_type: ScoreType = ScoreType.MULTIPLAYER
    room_id: int
    playlist_id: int


class ScoreProcessedEvent(PluginEvent):
    """Event fired when a score has been processed."""

    score_id: int


class ScoreDeletedEvent(PluginEvent):
    """Event fired when a score is deleted."""

    score_id: int


class ReplayUploadedEvent(PluginEvent):
    """Event fired when a replay is uploaded."""

    score_id: int
    uploader_user_id: int
    file_path: str
    replay_data: bytes


class ReplayDownloadedEvent(PluginEvent):
    """Event fired when a replay is downloaded."""

    score_id: int
    owner_user_id: int
    downloader_user_id: int | None = None
