from .achievement import UserAchievement, UserAchievementResp
from .auth import OAuthClient, OAuthToken, V1APIKeys
from .beatmap import (
    Beatmap,
    BeatmapResp,
)
from .beatmap_playcounts import BeatmapPlaycounts, BeatmapPlaycountsResp
from .beatmapset import (
    Beatmapset,
    BeatmapsetResp,
)
from .beatmapset_ratings import BeatmapRating
from .best_score import BestScore
from .chat import (
    ChannelType,
    ChatChannel,
    ChatChannelResp,
    ChatMessage,
    ChatMessageResp,
)
from .counts import (
    CountResp,
    MonthlyPlaycounts,
    ReplayWatchedCount,
)
from .daily_challenge import DailyChallengeStats, DailyChallengeStatsResp
from .email_verification import EmailVerification, LoginSession
from .events import Event
from .favourite_beatmapset import FavouriteBeatmapset
from .lazer_user import (
    User,
    UserResp,
)
from .multiplayer_event import MultiplayerEvent, MultiplayerEventResp
from .notification import Notification, UserNotification
from .password_reset import PasswordReset
from .playlist_attempts import (
    ItemAttemptsCount,
    ItemAttemptsResp,
    PlaylistAggregateScore,
)
from .playlist_best_score import PlaylistBestScore
from .playlists import Playlist, PlaylistResp
from .pp_best_score import PPBestScore
from .rank_history import RankHistory, RankHistoryResp, RankTop
from .relationship import Relationship, RelationshipResp, RelationshipType
from .room import APIUploadedRoom, Room, RoomResp
from .room_participated_user import RoomParticipatedUser
from .score import (
    MultiplayerScores,
    Score,
    ScoreAround,
    ScoreBase,
    ScoreResp,
    ScoreStatistics,
)
from .score_token import ScoreToken, ScoreTokenResp
from .statistics import (
    UserStatistics,
    UserStatisticsResp,
)
from .team import Team, TeamMember, TeamRequest
from .user_account_history import (
    UserAccountHistory,
    UserAccountHistoryResp,
    UserAccountHistoryType,
)
from .user_login_log import UserLoginLog

__all__ = [
    "APIUploadedRoom",
    "Beatmap",
    "BeatmapPlaycounts",
    "BeatmapPlaycountsResp",
    "BeatmapRating",
    "BeatmapResp",
    "Beatmapset",
    "BeatmapsetResp",
    "BestScore",
    "ChannelType",
    "ChatChannel",
    "ChatChannelResp",
    "ChatMessage",
    "ChatMessageResp",
    "CountResp",
    "DailyChallengeStats",
    "DailyChallengeStatsResp",
    "EmailVerification",
    "Event",
    "FavouriteBeatmapset",
    "ItemAttemptsCount",
    "ItemAttemptsResp",
    "LoginSession",
    "MonthlyPlaycounts",
    "MultiplayerEvent",
    "MultiplayerEventResp",
    "MultiplayerScores",
    "Notification",
    "OAuthClient",
    "OAuthToken",
    "PPBestScore",
    "PasswordReset",
    "Playlist",
    "PlaylistAggregateScore",
    "PlaylistBestScore",
    "PlaylistResp",
    "RankHistory",
    "RankHistoryResp",
    "RankTop",
    "Relationship",
    "RelationshipResp",
    "RelationshipType",
    "ReplayWatchedCount",
    "Room",
    "RoomParticipatedUser",
    "RoomResp",
    "Score",
    "ScoreAround",
    "ScoreBase",
    "ScoreResp",
    "ScoreStatistics",
    "ScoreToken",
    "ScoreTokenResp",
    "Team",
    "TeamMember",
    "TeamRequest",
    "User",
    "UserAccountHistory",
    "UserAccountHistoryResp",
    "UserAccountHistoryType",
    "UserAchievement",
    "UserAchievement",
    "UserAchievementResp",
    "UserLoginLog",
    "UserNotification",
    "UserResp",
    "UserStatistics",
    "UserStatisticsResp",
    "V1APIKeys",
]

for i in __all__:
    if i.endswith("Resp"):
        globals()[i].model_rebuild()  # type: ignore[call-arg]
