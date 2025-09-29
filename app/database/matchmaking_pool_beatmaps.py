from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.models.model import UTCBaseModel

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlmodel import (
    JSON,
    Column,
    Field,
    ForeignKey,
    Relationship,
    SQLModel,
    select,
)
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from .beatmap import Beatmap
    from .matchmaking_pools import MatchmakingPools


class MatchmakingPoolBeatmapsBase(SQLModel, UTCBaseModel):
    """匹配池谱面关联基础模型"""

    pool_id: int = Field(
        sa_column=Column(ForeignKey("matchmaking_pools.id", ondelete="CASCADE"), primary_key=True),
        description="匹配池ID",
    )
    beatmap_id: int = Field(
        sa_column=Column(ForeignKey("beatmaps.id", ondelete="CASCADE"), primary_key=True), description="谱面ID"
    )
    mods: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON), description="MOD配置 (JSON格式)")


class MatchmakingPoolBeatmaps(AsyncAttrs, MatchmakingPoolBeatmapsBase, table=True):
    """匹配池谱面关联表"""

    __tablename__: str = "matchmaking_pool_beatmaps"

    # 关联到匹配池表
    pool: "MatchmakingPools" = Relationship(back_populates="pool_beatmaps", sa_relationship_kwargs={"lazy": "selectin"})

    # 关联到谱面表 (假设存在beatmap表)
    beatmap: "Beatmap" = Relationship(sa_relationship_kwargs={"lazy": "selectin"})

    @classmethod
    async def get_pool_beatmaps(cls, session: AsyncSession, pool_id: int) -> list["MatchmakingPoolBeatmaps"]:
        """获取匹配池中的所有谱面"""
        stmt = select(cls).where(cls.pool_id == pool_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def add_beatmap_to_pool(
        cls, session: AsyncSession, pool_id: int, beatmap_id: int, mods: dict[str, Any] | None = None
    ) -> "MatchmakingPoolBeatmaps":
        """将谱面添加到匹配池"""
        pool_beatmap = cls(pool_id=pool_id, beatmap_id=beatmap_id, mods=mods)
        session.add(pool_beatmap)
        await session.commit()
        await session.refresh(pool_beatmap)
        return pool_beatmap

    @classmethod
    async def remove_beatmap_from_pool(cls, session: AsyncSession, pool_id: int, beatmap_id: int) -> bool:
        """从匹配池中移除谱面"""
        stmt = select(cls).where(cls.pool_id == pool_id, cls.beatmap_id == beatmap_id)
        result = await session.execute(stmt)
        pool_beatmap = result.scalar_one_or_none()

        if pool_beatmap:
            await session.delete(pool_beatmap)
            await session.commit()
            return True
        return False

    @classmethod
    async def get_beatmap_in_pool(
        cls, session: AsyncSession, pool_id: int, beatmap_id: int
    ) -> "MatchmakingPoolBeatmaps | None":
        """检查谱面是否在指定匹配池中"""
        stmt = select(cls).where(cls.pool_id == pool_id, cls.beatmap_id == beatmap_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @classmethod
    async def update_beatmap_mods(
        cls, session: AsyncSession, pool_id: int, beatmap_id: int, mods: dict[str, Any] | None = None
    ) -> "MatchmakingPoolBeatmaps | None":
        """更新匹配池中谱面的MOD配置"""
        pool_beatmap = await cls.get_beatmap_in_pool(session, pool_id, beatmap_id)

        if pool_beatmap:
            pool_beatmap.mods = mods
            await session.commit()
            await session.refresh(pool_beatmap)

        return pool_beatmap


class MatchmakingPoolBeatmapsResp(MatchmakingPoolBeatmapsBase):
    """匹配池谱面关联响应模型"""
