from __future__ import annotations

from datetime import datetime
from typing import Any

from app.models.model import UTCBaseModel
from app.utils import utcnow

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlmodel import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Field,
    ForeignKey,
    SQLModel,
    select,
)
from sqlmodel.ext.asyncio.session import AsyncSession


class MatchmakingUserStatsBase(SQLModel, UTCBaseModel):
    """用户匹配统计基础模型"""

    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), primary_key=True))
    ruleset_id: int = Field(primary_key=True, description="游戏模式ID (0:osu!, 1:taiko, 2:catch, 3:mania)")
    first_placements: int = Field(default=0, description="首次定级赛次数")
    total_points: int = Field(default=0, description="总积分")
    elo_data: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON), description="ELO评级数据 (JSON格式)")
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(DateTime(timezone=True)), description="创建时间"
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(DateTime(timezone=True)), description="更新时间"
    )


class MatchmakingUserStats(AsyncAttrs, MatchmakingUserStatsBase, table=True):
    """用户匹配统计表"""

    __tablename__: str = "matchmaking_user_stats"

    @classmethod
    async def get_user_stats(
        cls, session: AsyncSession, user_id: int, ruleset_id: int
    ) -> "MatchmakingUserStats | None":
        """获取用户在特定模式下的匹配统计"""
        stmt = select(cls).where(cls.user_id == user_id, cls.ruleset_id == ruleset_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @classmethod
    async def create_or_update_stats(
        cls, session: AsyncSession, user_id: int, ruleset_id: int, **kwargs
    ) -> "MatchmakingUserStats":
        """创建或更新用户匹配统计"""
        stats = await cls.get_user_stats(session, user_id, ruleset_id)

        if stats is None:
            # 创建新记录
            stats = cls(user_id=user_id, ruleset_id=ruleset_id, **kwargs)
            session.add(stats)
        else:
            # 更新现有记录
            for key, value in kwargs.items():
                setattr(stats, key, value)
            stats.updated_at = utcnow()

        await session.commit()
        await session.refresh(stats)
        return stats


class MatchmakingUserStatsResp(MatchmakingUserStatsBase):
    """用户匹配统计响应模型"""
