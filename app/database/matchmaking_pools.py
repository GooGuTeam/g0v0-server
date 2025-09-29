from datetime import datetime
from typing import TYPE_CHECKING

from app.models.model import UTCBaseModel
from app.utils import utcnow

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import Mapped
from sqlmodel import Column, DateTime, Field, Relationship, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    # 仅用于类型检查，避免循环导入
    from .matchmaking_pool_beatmaps import MatchmakingPoolBeatmaps


class MatchmakingPoolsBase(SQLModel, UTCBaseModel):
    """匹配池基础模型"""

    ruleset_id: int = Field(index=True, description="游戏模式ID (0:osu!, 1:taiko, 2:catch, 3:mania)")
    variant_id: int = Field(default=0, index=True, description="变体ID")
    name: str = Field(max_length=255, description="匹配池名称")
    active: bool = Field(default=True, index=True, description="是否激活")
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(DateTime(timezone=True)), description="创建时间"
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(DateTime(timezone=True)), description="更新时间"
    )


class MatchmakingPools(AsyncAttrs, MatchmakingPoolsBase, table=True):
    """匹配池表"""

    __tablename__: str = "matchmaking_pools"

    id: int | None = Field(default=None, primary_key=True, index=True)

    pool_beatmaps: Mapped[list["MatchmakingPoolBeatmaps"]] = Relationship(
        back_populates="pool",
        sa_relationship_kwargs={
            "lazy": "selectin",
            "cascade": "all, delete-orphan",
        },
    )

    @classmethod
    async def get_active_pools(
        cls,
        session: AsyncSession,
        ruleset_id: int | None = None,
        variant_id: int | None = None,
    ) -> list["MatchmakingPools"]:
        """获取激活的匹配池"""
        stmt = select(cls).where(cls.active)

        if ruleset_id is not None:
            stmt = stmt.where(cls.ruleset_id == ruleset_id)
        if variant_id is not None:
            stmt = stmt.where(cls.variant_id == variant_id)

        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def get_pool_by_id(cls, session: AsyncSession, pool_id: int) -> "MatchmakingPools | None":
        """根据ID获取匹配池"""
        stmt = select(cls).where(cls.id == pool_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @classmethod
    async def create_pool(
        cls,
        session: AsyncSession,
        name: str,
        ruleset_id: int,
        variant_id: int = 0,
        active: bool = True,
    ) -> "MatchmakingPools":
        """创建新的匹配池"""
        pool = cls(name=name, ruleset_id=ruleset_id, variant_id=variant_id, active=active)
        session.add(pool)
        await session.commit()
        await session.refresh(pool)
        return pool

    async def update_status(self, session: AsyncSession, active: bool) -> None:
        """更新匹配池状态"""
        self.active = active
        self.updated_at = utcnow()
        await session.commit()


class MatchmakingPoolsResp(MatchmakingPoolsBase):
    """匹配池响应模型"""

    id: int
    beatmap_count: int = 0
