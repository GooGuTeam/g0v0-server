"""Team database models.

This module provides models for teams, team members,
and team join requests.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from app.helpers import utcnow
from app.models.model import UTCBaseModel
from app.models.score import GameMode

from sqlalchemy import Column, DateTime
from sqlmodel import BigInteger, Field, ForeignKey, Relationship, SQLModel, Text, col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from .user import User


class TeamBase(SQLModel, UTCBaseModel):
    """Base fields for teams."""

    id: int = Field(default=None, primary_key=True, index=True)
    name: str = Field(max_length=100)
    short_name: str = Field(max_length=10)
    flag_url: str | None = Field(default=None)
    cover_url: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime))
    leader_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id")))
    description: str | None = Field(default=None, sa_column=Column(Text))
    playmode: GameMode = Field(default=GameMode.OSU)
    website: str | None = Field(default=None, sa_column=Column(Text))


class Team(TeamBase, table=True):
    """Database table for teams."""

    __tablename__: str = "teams"

    leader: "User" = Relationship()
    members: list["TeamMember"] = Relationship(back_populates="team")


class TeamResp(TeamBase):
    """Response model for teams with computed statistics."""

    rank: int = 0
    pp: float = 0.0
    ranked_score: int = 0
    total_play_count: int = 0
    member_count: int = 0

    @classmethod
    async def from_db(cls, team: Team, session: AsyncSession, gamemode: GameMode | None = None) -> "TeamResp":
        from .statistics import UserStatistics
        from .user import User

        playmode = gamemode or team.playmode

        pp_expr = func.coalesce(func.sum(col(UserStatistics.pp)), 0.0)
        ranked_score_expr = func.coalesce(func.sum(col(UserStatistics.ranked_score)), 0)
        play_count_expr = func.coalesce(func.sum(col(UserStatistics.play_count)), 0)
        member_count_expr = func.count(func.distinct(col(UserStatistics.user_id)))

        team_stats_stmt = (
            select(pp_expr, ranked_score_expr, play_count_expr, member_count_expr)
            .select_from(UserStatistics)
            .join(TeamMember, col(TeamMember.user_id) == col(UserStatistics.user_id))
            .join(User, col(User.id) == col(UserStatistics.user_id))
            .join(Team, col(Team.id) == col(TeamMember.team_id))
            .where(
                col(Team.id) == team.id,
                col(Team.playmode) == playmode,
                col(UserStatistics.mode) == playmode,
                col(UserStatistics.pp) > 0,
                col(UserStatistics.is_ranked).is_(True),
                ~User.is_restricted_query(col(UserStatistics.user_id)),
            )
        )

        team_stats_result = await session.exec(team_stats_stmt)
        stats_row = team_stats_result.one_or_none()
        if stats_row is None:
            total_pp = 0.0
            total_ranked_score = 0
            total_play_count = 0
            active_member_count = 0
        else:
            total_pp, total_ranked_score, total_play_count, active_member_count = stats_row
            total_pp = float(total_pp or 0.0)
            total_ranked_score = int(total_ranked_score or 0)
            total_play_count = int(total_play_count or 0)
            active_member_count = int(active_member_count or 0)

        total_pp_ranking_expr = func.coalesce(func.sum(col(UserStatistics.pp)), 0.0)
        ranking_stmt = (
            select(Team.id, total_pp_ranking_expr)
            .select_from(Team)
            .join(TeamMember, col(TeamMember.team_id) == col(Team.id))
            .join(UserStatistics, col(UserStatistics.user_id) == col(TeamMember.user_id))
            .join(User, col(User.id) == col(TeamMember.user_id))
            .where(
                col(Team.playmode) == playmode,
                col(UserStatistics.mode) == playmode,
                col(UserStatistics.pp) > 0,
                col(UserStatistics.is_ranked).is_(True),
                ~User.is_restricted_query(col(UserStatistics.user_id)),
            )
            .group_by(col(Team.id))
            .order_by(total_pp_ranking_expr.desc())
        )

        ranking_result = await session.exec(ranking_stmt)
        ranking_rows = ranking_result.all()
        rank = 0
        for index, (team_id, _) in enumerate(ranking_rows, start=1):
            if team_id == team.id:
                rank = index
                break

        data = team.model_dump()
        data.update(
            {
                "pp": total_pp,
                "ranked_score": total_ranked_score,
                "total_play_count": total_play_count,
                "member_count": active_member_count,
                "rank": rank,
            }
        )

        return cls.model_validate(data)


class TeamMember(SQLModel, UTCBaseModel, table=True):
    """Database table for team membership."""

    __tablename__: str = "team_members"

    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), primary_key=True))
    team_id: int = Field(foreign_key="teams.id")
    joined_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime))

    user: "User" = Relationship(back_populates="team_membership", sa_relationship_kwargs={"lazy": "joined"})
    team: "Team" = Relationship(back_populates="members", sa_relationship_kwargs={"lazy": "joined"})


class TeamRequest(SQLModel, UTCBaseModel, table=True):
    """Database table for team join requests."""

    __tablename__: str = "team_requests"

    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), primary_key=True))
    team_id: int = Field(foreign_key="teams.id", primary_key=True)
    requested_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime))

    user: "User" = Relationship(sa_relationship_kwargs={"lazy": "joined"})
    team: "Team" = Relationship(sa_relationship_kwargs={"lazy": "joined"})
