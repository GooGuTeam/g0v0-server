"""V1 API 用户相关模型"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PlayerStatsHistory(BaseModel):
    """玩家 PP 历史数据"""

    pp: list[float] = Field(default_factory=list)


class PlayerModeStats(BaseModel):
    """单个模式的玩家统计数据"""

    id: int
    mode: int
    tscore: int  # total_score
    rscore: int  # ranked_score
    pp: float
    plays: int  # play_count
    playtime: int  # play_time
    acc: float  # accuracy
    max_combo: int  # maximum_combo
    total_hits: int
    replay_views: int  # replays_watched_by_others
    xh_count: int  # grade_ssh
    x_count: int  # grade_ss
    sh_count: int  # grade_sh
    s_count: int  # grade_s
    a_count: int  # grade_a
    level: int
    level_progress: int
    rank: int
    country_rank: int
    history: PlayerStatsHistory


class PlayerStatsResponse(BaseModel):
    """玩家统计信息响应 - 包含所有模式"""

    stats: dict[str, PlayerModeStats] = Field(default_factory=dict)


class PlayerEventItem(BaseModel):
    """玩家事件项目"""

    userId: int
    name: str
    mapId: int | None = None
    setId: int | None = None
    artist: str | None = None
    title: str | None = None
    version: str | None = None
    mode: int | None = None
    rank: int | None = None
    grade: str | None = None
    event: str | None = None
    time: str | None = None


class PlayerEventsResponse(BaseModel):
    """玩家事件响应"""

    events: list[PlayerEventItem] = Field(default_factory=list)


class PlayerInfo(BaseModel):
    """玩家基本信息"""

    id: int
    name: str
    safe_name: str
    priv: int
    country: str
    silence_end: int
    donor_end: int
    creation_time: int
    latest_activity: int
    clan_id: int
    clan_priv: int
    preferred_mode: int
    preferred_type: int
    play_style: int
    custom_badge_enabled: int
    custom_badge_name: str
    custom_badge_icon: str
    custom_badge_color: str
    userpage_content: str
    recentFailed: int
    social_discord: str | None = None
    social_youtube: str | None = None
    social_twitter: str | None = None
    social_twitch: str | None = None
    social_github: str | None = None
    social_osu: str | None = None
    username_history: list[str] = Field(default_factory=list)


class PlayerInfoResponse(BaseModel):
    """玩家信息响应"""

    info: PlayerInfo


class PlayerAllResponse(BaseModel):
    """玩家完整信息响应 - 包含所有数据"""

    info: PlayerInfo
    stats: dict[str, PlayerModeStats] = Field(default_factory=dict)
    events: list[PlayerEventItem] = Field(default_factory=list)


class GetPlayerInfoResponse(BaseModel):
    """get_player_info 接口响应"""

    status: str = "success"
    player: PlayerStatsResponse | PlayerEventsResponse | PlayerInfoResponse | PlayerAllResponse


class PlayerCountData(BaseModel):
    """玩家数量数据"""

    online: int
    total: int


class GetPlayerCountResponse(BaseModel):
    """get_player_count 接口响应"""

    status: str = "success"
    counts: PlayerCountData
