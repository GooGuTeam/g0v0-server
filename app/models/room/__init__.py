from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class RoomCategory(str, Enum):
    NORMAL = "normal"
    SPOTLIGHT = "spotlight"
    FEATURED_ARTIST = "featured_artist"
    DAILY_CHALLENGE = "daily_challenge"
    REALTIME = "realtime"  # INTERNAL USE ONLY, DO NOT USE IN API


class MatchType(str, Enum):
    PLAYLISTS = "playlists"
    HEAD_TO_HEAD = "head_to_head"
    TEAM_VERSUS = "team_versus"


class QueueMode(str, Enum):
    HOST_ONLY = "host_only"
    ALL_PLAYERS = "all_players"
    ALL_PLAYERS_ROUND_ROBIN = "all_players_round_robin"


class RoomAvailability(str, Enum):
    PUBLIC = "public"
    FRIENDS_ONLY = "friends_only"
    INVITE_ONLY = "invite_only"


class RoomStatus(str, Enum):
    IDLE = "idle"
    PLAYING = "playing"


class MultiplayerRoomState(str, Enum):
    OPEN = "open"
    WAITING_FOR_LOAD = "waiting_for_load"
    PLAYING = "playing"
    CLOSED = "closed"


class MultiplayerUserState(str, Enum):
    IDLE = "idle"
    READY = "ready"
    WAITING_FOR_LOAD = "waiting_for_load"
    LOADED = "loaded"
    READY_FOR_GAMEPLAY = "ready_for_gameplay"
    PLAYING = "playing"
    FINISHED_PLAY = "finished_play"
    RESULTS = "results"
    SPECTATING = "spectating"

    @property
    def is_playing(self) -> bool:
        return self in {
            self.WAITING_FOR_LOAD,
            self.PLAYING,
            self.READY_FOR_GAMEPLAY,
            self.LOADED,
        }


class DownloadState(str, Enum):
    UNKNOWN = "unknown"
    NOT_DOWNLOADED = "not_downloaded"
    DOWNLOADING = "downloading"
    IMPORTING = "importing"
    LOCALLY_AVAILABLE = "locally_available"


class RoomPlaylistItemStats(BaseModel):
    count_active: int
    count_total: int
    ruleset_ids: list[int] = []


class RoomDifficultyRange(BaseModel):
    min: float
    max: float


class PlaylistStatus(BaseModel):
    count_active: int
    count_total: int
    ruleset_ids: list[int]
