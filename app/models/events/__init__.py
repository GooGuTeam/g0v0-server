from ._base import PluginEvent
from .api_key import APIKeyCreatedEvent, APIKeyDeletedEvent, APIKeyRegeneratedEvent, APIKeyUpdatedEvent
from .beatmapset import (
    BeatmapsetFavouriteChangedEvent,
    BeatmapsetRatedEvent,
    BeatmapsetSyncRequestedEvent,
    BeatmapTagVoteChangedEvent,
)
from .calculating import AfterCalculatingPPEvent, BeforeCalculatingPPEvent
from .chat import JoinChannelEvent, LeaveChannelEvent, MessageSentEvent
from .fetcher import (
    BeatmapFetchedEvent,
    BeatmapRawFetchedEvent,
    BeatmapsetFetchedEvent,
    FetchingBeatmapEvent,
    FetchingBeatmapRawEvent,
    FetchingBeatmapsetEvent,
)
from .http import RequestHandledEvent, RequestReceivedEvent
from .relationship import UserRelationshipChangedEvent
from .room import RoomCreatedEvent, RoomEndedEvent, RoomUserJoinedEvent, RoomUserLeftEvent
from .score import (
    MultiplayerScoreCreatedEvent,
    MultiplayerScoreSubmittedEvent,
    ReplayDownloadedEvent,
    ReplayUploadedEvent,
    ScoreCreatedEvent,
    ScoreDeletedEvent,
    ScoreProcessedEvent,
    ScoreSubmittedEvent,
    SoloScoreCreatedEvent,
    SoloScoreSubmittedEvent,
)
from .team import (
    TeamCreatedEvent,
    TeamDeletedEvent,
    TeamJoinRequestedEvent,
    TeamJoinRequestHandledEvent,
    TeamMemberRemovedEvent,
    TeamUpdatedEvent,
)
from .user import (
    UserLoginEvent,
    UserPageUpdatedEvent,
    UserPreferencesUpdatedEvent,
    UserRegisteredEvent,
    UserRenamedEvent,
)

__all__ = [
    "APIKeyCreatedEvent",
    "APIKeyDeletedEvent",
    "APIKeyRegeneratedEvent",
    "APIKeyUpdatedEvent",
    "AfterCalculatingPPEvent",
    "BeatmapFetchedEvent",
    "BeatmapRawFetchedEvent",
    "BeatmapTagVoteChangedEvent",
    "BeatmapsetFavouriteChangedEvent",
    "BeatmapsetFetchedEvent",
    "BeatmapsetRatedEvent",
    "BeatmapsetSyncRequestedEvent",
    "BeforeCalculatingPPEvent",
    "FetchingBeatmapEvent",
    "FetchingBeatmapRawEvent",
    "FetchingBeatmapsetEvent",
    "JoinChannelEvent",
    "LeaveChannelEvent",
    "MessageSentEvent",
    "MultiplayerScoreCreatedEvent",
    "MultiplayerScoreSubmittedEvent",
    "PluginEvent",
    "ReplayDownloadedEvent",
    "ReplayUploadedEvent",
    "RequestHandledEvent",
    "RequestReceivedEvent",
    "RoomCreatedEvent",
    "RoomEndedEvent",
    "RoomUserJoinedEvent",
    "RoomUserLeftEvent",
    "ScoreCreatedEvent",
    "ScoreDeletedEvent",
    "ScoreProcessedEvent",
    "ScoreSubmittedEvent",
    "SoloScoreCreatedEvent",
    "SoloScoreSubmittedEvent",
    "TeamCreatedEvent",
    "TeamDeletedEvent",
    "TeamJoinRequestHandledEvent",
    "TeamJoinRequestedEvent",
    "TeamMemberRemovedEvent",
    "TeamUpdatedEvent",
    "UserLoginEvent",
    "UserPageUpdatedEvent",
    "UserPreferencesUpdatedEvent",
    "UserRegisteredEvent",
    "UserRelationshipChangedEvent",
    "UserRenamedEvent",
]
