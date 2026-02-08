from enum import StrEnum

from pydantic import BaseModel


class RoomCategory(StrEnum):
    NORMAL = "normal"
    SPOTLIGHT = "spotlight"
    FEATURED_ARTIST = "featured_artist"
    DAILY_CHALLENGE = "daily_challenge"
    REALTIME = "realtime"  # INTERNAL USE ONLY, DO NOT USE IN API


class MatchType(StrEnum):
    PLAYLISTS = "playlists"
    HEAD_TO_HEAD = "head_to_head"
    TEAM_VERSUS = "team_versus"
    MATCHMAKING = "matchmaking"


class QueueMode(StrEnum):
    HOST_ONLY = "host_only"
    ALL_PLAYERS = "all_players"
    ALL_PLAYERS_ROUND_ROBIN = "all_players_round_robin"


class RoomAvailability(StrEnum):
    PUBLIC = "public"
    FRIENDS_ONLY = "friends_only"
    INVITE_ONLY = "invite_only"


class RoomStatus(StrEnum):
    IDLE = "idle"
    PLAYING = "playing"


class MultiplayerRoomState(StrEnum):
    OPEN = "open"
    WAITING_FOR_LOAD = "waiting_for_load"
    PLAYING = "playing"
    CLOSED = "closed"


class MultiplayerUserState(StrEnum):
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


class DownloadState(StrEnum):
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
