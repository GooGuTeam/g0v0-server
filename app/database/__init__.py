"""Database models and ORM entities for the osu! API server.

This module exports all database models, TypedDict types, and response classes
used throughout the application for database operations and API responses.
"""

from .achievement import UserAchievement, UserAchievementResp
from .auth import OAuthClient, OAuthToken, TotpKeys, V1APIKeys
from .beatmap import (
    Beatmap,
    BeatmapDict,
    BeatmapModel,
)
from .beatmap_playcounts import (
    BeatmapPlaycounts,
    BeatmapPlaycountsDict,
    BeatmapPlaycountsModel,
)
from .beatmap_sync import BeatmapSync
from .beatmap_tags import BeatmapTagVote
from .beatmapset import (
    Beatmapset,
    BeatmapsetDict,
    BeatmapsetModel,
)
from .beatmapset_ratings import BeatmapRating
from .best_scores import BestScore
from .chat import (
    ChannelType,
    ChatChannel,
    ChatChannelDict,
    ChatChannelModel,
    ChatMessage,
    ChatMessageDict,
    ChatMessageModel,
)
from .counts import (
    CountResp,
    MonthlyPlaycounts,
    ReplayWatchedCount,
)
from .daily_challenge import DailyChallengeStats, DailyChallengeStatsResp
from .events import Event
from .favourite_beatmapset import FavouriteBeatmapset
from .item_attempts_count import (
    ItemAttemptsCount,
    ItemAttemptsCountDict,
    ItemAttemptsCountModel,
)
from .matchmaking import (
    MatchmakingPool,
    MatchmakingPoolBeatmap,
    MatchmakingUserStats,
)
from .multiplayer_event import MultiplayerEvent, MultiplayerEventResp
from .notification import Notification, UserNotification
from .password_reset import PasswordReset
from .playlist_best_score import PlaylistBestScore
from .playlists import Playlist, PlaylistDict, PlaylistModel
from .rank_history import RankHistory, RankHistoryResp, RankTop
from .relationship import Relationship, RelationshipDict, RelationshipModel, RelationshipType
from .room import APIUploadedRoom, Room, RoomDict, RoomModel
from .room_participated_user import RoomParticipatedUser
from .score import (
    MultiplayerScores,
    Score,
    ScoreAround,
    ScoreDict,
    ScoreModel,
    ScoreStatistics,
)
from .score_token import ScoreToken, ScoreTokenResp
from .search_beatmapset import SearchBeatmapsetsResp
from .statistics import (
    UserStatistics,
    UserStatisticsDict,
    UserStatisticsModel,
)
from .team import Team, TeamMember, TeamRequest, TeamResp
from .total_score_best_scores import TotalScoreBestScore
from .user import (
    User,
    UserDict,
    UserModel,
)
from .user_account_history import (
    UserAccountHistory,
    UserAccountHistoryResp,
    UserAccountHistoryType,
)
from .user_login_log import UserLoginLog
from .user_preference import UserPreference
from .verification import EmailVerification, LoginSession, LoginSessionResp, TrustedDevice, TrustedDeviceResp

__all__ = [
    "APIUploadedRoom",
    "Beatmap",
    "BeatmapDict",
    "BeatmapModel",
    "BeatmapPlaycounts",
    "BeatmapPlaycountsDict",
    "BeatmapPlaycountsModel",
    "BeatmapRating",
    "BeatmapSync",
    "BeatmapTagVote",
    "Beatmapset",
    "BeatmapsetDict",
    "BeatmapsetModel",
    "BestScore",
    "ChannelType",
    "ChatChannel",
    "ChatChannelDict",
    "ChatChannelModel",
    "ChatMessage",
    "ChatMessageDict",
    "ChatMessageModel",
    "CountResp",
    "DailyChallengeStats",
    "DailyChallengeStatsResp",
    "EmailVerification",
    "Event",
    "FavouriteBeatmapset",
    "ItemAttemptsCount",
    "ItemAttemptsCountDict",
    "ItemAttemptsCountModel",
    "LoginSession",
    "LoginSessionResp",
    "MatchmakingPool",
    "MatchmakingPoolBeatmap",
    "MatchmakingUserStats",
    "MonthlyPlaycounts",
    "MultiplayerEvent",
    "MultiplayerEventResp",
    "MultiplayerScores",
    "Notification",
    "OAuthClient",
    "OAuthToken",
    "PasswordReset",
    "Playlist",
    "PlaylistBestScore",
    "PlaylistDict",
    "PlaylistModel",
    "RankHistory",
    "RankHistoryResp",
    "RankTop",
    "Relationship",
    "RelationshipDict",
    "RelationshipModel",
    "RelationshipType",
    "ReplayWatchedCount",
    "Room",
    "RoomDict",
    "RoomModel",
    "RoomParticipatedUser",
    "Score",
    "ScoreAround",
    "ScoreDict",
    "ScoreModel",
    "ScoreStatistics",
    "ScoreToken",
    "ScoreTokenResp",
    "SearchBeatmapsetsResp",
    "Team",
    "TeamMember",
    "TeamRequest",
    "TeamResp",
    "TotalScoreBestScore",
    "TotpKeys",
    "TrustedDevice",
    "TrustedDeviceResp",
    "User",
    "UserAccountHistory",
    "UserAccountHistoryResp",
    "UserAccountHistoryType",
    "UserAchievement",
    "UserAchievementResp",
    "UserDict",
    "UserLoginLog",
    "UserModel",
    "UserNotification",
    "UserPreference",
    "UserStatistics",
    "UserStatisticsDict",
    "UserStatisticsModel",
    "V1APIKeys",
]

for i in __all__:
    if i.endswith("Model") or i.endswith("Resp"):
        globals()[i].model_rebuild()
