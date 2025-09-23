from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class OnlineStats(BaseModel):
    """在线统计信息"""

    registered_users: int
    online_users: int
    playing_users: int
    timestamp: datetime


class OnlineHistoryPoint(BaseModel):
    """在线历史数据点"""

    timestamp: datetime
    online_count: int
    playing_count: int


class OnlineHistoryStats(BaseModel):
    """24小时在线历史统计"""

    history: list[OnlineHistoryPoint]
    current_stats: OnlineStats


class ServerStatistics(BaseModel):
    """服务器统计信息"""

    total_users: int
    online_users: int
    playing_users: int
    spectating_users: int
    multiplayer_users: int
    last_updated: datetime
