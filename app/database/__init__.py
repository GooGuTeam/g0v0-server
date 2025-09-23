from __future__ import annotations

from .achievement import UserAchievement, UserAchievementResp
from .auth import OAuthClient, OAuthToken, TotpKeys, V1APIKeys
from .auth.password_reset import PasswordReset
from .auth.verification import EmailVerification, LoginSession
from .beatmap import Beatmap, BeatmapResp
from .beatmap.beatmapset import Beatmapset, BeatmapsetResp, SearchBeatmapsetsResp
from .beatmap.daily_challenge import DailyChallengeStats, DailyChallengeStatsResp
from .beatmap.failtime import FailTime, FailTimeResp
from .beatmap.favourites import FavouriteBeatmapset
from .beatmap.playcounts import BeatmapPlaycounts, BeatmapPlaycountsResp
from .beatmap.ratings import BeatmapRating
from .beatmap.tags import BeatmapTagVote
from .chat import ChannelType, ChatChannel, ChatChannelResp, ChatMessage, ChatMessageResp
from .notification import Notification, UserNotification
from .room import APIUploadedRoom, Room, RoomResp
from .room.events import Event
from .room.multiplayer_event import MultiplayerEvent, MultiplayerEventResp
from .room.participated_user import RoomParticipatedUser
from .room.playlist_attempts import ItemAttemptsCount, ItemAttemptsResp, PlaylistAggregateScore
from .room.playlists import Playlist, PlaylistResp
from .score import MultiplayerScores, Score, ScoreAround, ScoreBase, ScoreResp, ScoreStatistics
from .score.best_score import BestScore
from .score.playlist_best_score import PlaylistBestScore
from .score.pp_best_score import PPBestScore
from .score.token import ScoreToken, ScoreTokenResp
from .user.account_history import (
    UserAccountHistory,
    UserAccountHistoryResp,
    UserAccountHistoryType,
)
from .user.counts import CountResp, MonthlyPlaycounts, ReplayWatchedCount
from .user.lazer_user import MeResp, User, UserResp
from .user.login_log import UserLoginLog
from .user.rank_history import RankHistory, RankHistoryResp, RankTop
from .user.relationship import Relationship, RelationshipResp, RelationshipType
from .user.statistics import UserStatistics, UserStatisticsResp
from .user.team import Team, TeamMember, TeamRequest

__all__ = [
    "APIUploadedRoom",
    "Beatmap",
    "BeatmapPlaycounts",
    "BeatmapPlaycountsResp",
    "BeatmapRating",
    "BeatmapResp",
    "BeatmapTagVote",
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
    "FailTime",
    "FailTimeResp",
    "FavouriteBeatmapset",
    "ItemAttemptsCount",
    "ItemAttemptsResp",
    "LoginSession",
    "MeResp",
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
    "SearchBeatmapsetsResp",
    "Team",
    "TeamMember",
    "TeamRequest",
    "TotpKeys",
    "User",
    "UserAccountHistory",
    "UserAccountHistoryResp",
    "UserAccountHistoryType",
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
