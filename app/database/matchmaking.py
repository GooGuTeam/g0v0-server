"""Matchmaking system database models.

This module provides models for matchmaking pools, user stats,
and pool beatmap configurations.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from app.models.model import UTCBaseModel
from app.models.mods import APIMod

from sqlalchemy import Column, DateTime, ForeignKey, Index, SmallInteger
from sqlmodel import (
    JSON,
    BigInteger,
    Field,
    Relationship,
    SQLModel,
    func,
)

if TYPE_CHECKING:
    from .beatmap import Beatmap
    from .user import User


class MatchmakingUserStatsBase(SQLModel, UTCBaseModel):
    """Base fields for matchmaking user statistics."""

    user_id: int = Field(
        default=None,
        sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), primary_key=True),
    )
    pool_id: int = Field(
        default=None,
        sa_column=Column(ForeignKey("matchmaking_pools.id"), primary_key=True, nullable=True),
    )
    first_placements: int = Field(default=0, ge=0)
    total_points: int = Field(default=0, ge=0)
    elo_data: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now()),
    )


class MatchmakingUserStats(MatchmakingUserStatsBase, table=True):
    """Database table for matchmaking user statistics."""

    __tablename__: str = "matchmaking_user_stats"
    __table_args__ = (
        Index("matchmaking_user_stats_pool_first_idx", "pool_id", "first_placements"),
        Index("matchmaking_user_stats_pool_points_idx", "pool_id", "total_points"),
    )

    user: "User" = Relationship(back_populates="matchmaking_stats", sa_relationship_kwargs={"lazy": "joined"})
    pool: "MatchmakingPool" = Relationship()


class MatchmakingPoolBase(SQLModel, UTCBaseModel):
    """Base fields for matchmaking pools."""

    id: int | None = Field(default=None, primary_key=True)
    ruleset_id: int = Field(
        default=0,
        sa_column=Column(SmallInteger, nullable=False),
    )
    name: str = Field(max_length=255)
    active: bool = Field(default=True)
    lobby_size: int = Field(default=8)
    rating_search_radius: int = Field(default=20)
    rating_search_radius_exp: int = Field(default=15)
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now()),
    )


class MatchmakingPool(MatchmakingPoolBase, table=True):
    """Database table for matchmaking pools."""

    __tablename__: str = "matchmaking_pools"
    __table_args__ = (Index("matchmaking_pools_ruleset_active_idx", "ruleset_id", "active"),)

    beatmaps: list["MatchmakingPoolBeatmap"] = Relationship(
        back_populates="pool",
        # sa_relationship_kwargs={
        #     "lazy": "selectin",
        # },
    )


class MatchmakingPoolBeatmapBase(SQLModel, UTCBaseModel):
    """Base fields for beatmaps in matchmaking pools."""

    id: int | None = Field(default=None, primary_key=True)
    pool_id: int = Field(
        default=None,
        sa_column=Column(ForeignKey("matchmaking_pools.id"), nullable=False, index=True),
    )
    beatmap_id: int = Field(
        default=None,
        sa_column=Column(ForeignKey("beatmaps.id"), nullable=False),
    )
    mods: list[APIMod] | None = Field(default=None, sa_column=Column(JSON))
    rating: int | None = Field(default=1500)
    selection_count: int = Field(default=0)


class MatchmakingPoolBeatmap(MatchmakingPoolBeatmapBase, table=True):
    __tablename__: str = "matchmaking_pool_beatmaps"

    pool: MatchmakingPool = Relationship(back_populates="beatmaps")
    beatmap: Optional["Beatmap"] = Relationship(
        # sa_relationship_kwargs={"lazy": "joined"},
    )
