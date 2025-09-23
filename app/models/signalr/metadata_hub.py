from __future__ import annotations

from enum import IntEnum
from typing import ClassVar, Literal

from app.models.signalr import SignalRUnionMessage, UserState

from pydantic import BaseModel, Field

TOTAL_SCORE_DISTRIBUTION_BINS = 13


class _UserActivity(SignalRUnionMessage): ...


class ChoosingBeatmap(_UserActivity):
    union_type: ClassVar[Literal[11]] = 11


class _InGame(_UserActivity):
    beatmap_id: int
    beatmap_display_title: str
    ruleset_id: int
    ruleset_playing_verb: str


class InSoloGame(_InGame):
    union_type: ClassVar[Literal[12]] = 12


class InMultiplayerGame(_InGame):
    union_type: ClassVar[Literal[23]] = 23


class SpectatingMultiplayerGame(_InGame):
    union_type: ClassVar[Literal[24]] = 24


class InPlaylistGame(_InGame):
    union_type: ClassVar[Literal[31]] = 31


class PlayingDailyChallenge(_InGame):
    union_type: ClassVar[Literal[52]] = 52


class EditingBeatmap(_UserActivity):
    union_type: ClassVar[Literal[41]] = 41
    beatmap_id: int
    beatmap_display_title: str


class TestingBeatmap(EditingBeatmap):
    union_type: ClassVar[Literal[43]] = 43


class ModdingBeatmap(EditingBeatmap):
    union_type: ClassVar[Literal[42]] = 42


class WatchingReplay(_UserActivity):
    union_type: ClassVar[Literal[13]] = 13
    score_id: int
    player_name: str
    beatmap_id: int
    beatmap_display_title: str


class SpectatingUser(WatchingReplay):
    union_type: ClassVar[Literal[14]] = 14


class SearchingForLobby(_UserActivity):
    union_type: ClassVar[Literal[21]] = 21


class InLobby(_UserActivity):
    union_type: ClassVar[Literal[22]] = 22
    room_id: int
    room_name: str


class InDailyChallengeLobby(_UserActivity):
    union_type: ClassVar[Literal[51]] = 51


UserActivity = (
    ChoosingBeatmap
    | InSoloGame
    | WatchingReplay
    | SpectatingUser
    | SearchingForLobby
    | InLobby
    | InMultiplayerGame
    | SpectatingMultiplayerGame
    | InPlaylistGame
    | EditingBeatmap
    | ModdingBeatmap
    | TestingBeatmap
    | InDailyChallengeLobby
    | PlayingDailyChallenge
)


class UserPresence(BaseModel):
    activity: UserActivity | None = None

    status: OnlineStatus | None = None

    @property
    def pushable(self) -> bool:
        return self.status is not None and self.status != OnlineStatus.OFFLINE

    @property
    def for_push(self) -> "UserPresence | None":
        return UserPresence(
            activity=self.activity,
            status=self.status,
        )


class MetadataClientState(UserPresence, UserState): ...


class OnlineStatus(IntEnum):
    OFFLINE = 0  # 隐身
    DO_NOT_DISTURB = 1
    ONLINE = 2


class DailyChallengeInfo(BaseModel):
    room_id: int


class MultiplayerPlaylistItemStats(BaseModel):
    playlist_item_id: int = 0
    total_score_distribution: list[int] = Field(
        default_factory=list,
        min_length=TOTAL_SCORE_DISTRIBUTION_BINS,
        max_length=TOTAL_SCORE_DISTRIBUTION_BINS,
    )
    cumulative_score: int = 0
    last_processed_score_id: int = 0


class MultiplayerRoomStats(BaseModel):
    room_id: int
    playlist_item_stats: dict[int, MultiplayerPlaylistItemStats] = Field(default_factory=dict)


class MultiplayerRoomScoreSetEvent(BaseModel):
    room_id: int
    playlist_item_id: int
    score_id: int
    user_id: int
    total_score: int
    new_rank: int | None = None
