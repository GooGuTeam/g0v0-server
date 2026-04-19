"""Team endpoints for osu! API v2.

This module provides endpoints for teams. Only getting team here.
Other APIs please refer to g0v0 API.
"""

from datetime import datetime
from typing import Annotated

from app.database.team import Team, TeamMember, TeamStatistics
from app.database.user import User, UserModel
from app.dependencies.database import Database
from app.models.error import ErrorType, RequestError
from app.models.model import UTCBaseModel
from app.models.score import GameMode

from .router import router

from fastapi import Path
from pydantic import Field
from sqlmodel import col, select


class V2Team(UTCBaseModel):
    flag_url: str | None = Field(..., description="URL to an image containing the team's flag/profile picture")
    id: int = Field(..., description="unique identifier of the team")
    name: str = Field(..., description="team's display name")
    short_name: str = Field(..., description="team's unique short identifier")
    empty_slots: int = Field(
        default=999, description="amount of available free slots in the team"
    )  # This server is no limit so we set a fixed value.
    leader: dict | None = Field(..., description="the current owner of the team")
    members: list[dict] = Field(..., description="list of members (excluding the leader) belonging to this team")
    statistics: TeamStatistics = Field(..., description="the team's gameplay statistics for a given ruleset")
    cover_url: str | None = Field(None, description="URL to the team's cover image")
    description: str | None = Field(None, description="the team's description")
    default_ruleset_id: int = Field(..., description="the team's default ruleset ID")
    created_at: datetime = Field(..., description="the team's creation date in ISO 8601 format")
    is_open: bool = Field(
        default=True, description="Whether the team is currently accepting member applications"
    )  # We currently don't have a way to close team application, so we set it to always open.


async def determine_team(session: Database, team_id: int, ruleset: GameMode | None) -> V2Team:
    team = await session.get(Team, team_id)
    if team is None:
        raise RequestError(ErrorType.TEAM_NOT_FOUND)
    members = (
        await session.exec(
            select(TeamMember).where(
                TeamMember.team_id == team_id,
                ~User.is_restricted_query(col(TeamMember.user_id)),
            )
        )
    ).all()

    converted_members = await UserModel.transform_many([m.user for m in members], includes=UserModel.CARD_INCLUDES)
    leader = None
    for i in range(len(converted_members)):
        if converted_members[i]["id"] == team.leader_id:
            leader = converted_members.pop(i)
            break

    return V2Team(
        flag_url=team.flag_url,
        id=team.id,
        name=team.name,
        short_name=team.short_name,
        leader=leader,  # pyright: ignore[reportArgumentType]
        members=converted_members,  # pyright: ignore[reportArgumentType]
        statistics=await TeamStatistics.compute_statistics(session, team, ruleset),
        cover_url=team.cover_url,
        description=team.description,
        default_ruleset_id=int(team.playmode),
        created_at=team.created_at,
    )


@router.get(
    "/team/{team_id}/",
    name="Get team",
    tags=["Team"],
    response_model=V2Team,
    description="Get team information.",
)
async def get_team(
    session: Database,
    team_id: Annotated[int, Path(..., description="Team ID")],
):
    return await determine_team(session, team_id, None)


@router.get(
    "/team/{team_id}/{ruleset}",
    name="Get team",
    tags=["Team"],
    response_model=V2Team,
    description="Get team information with a specific ruleset.",
)
async def get_team_with_ruleset(
    session: Database,
    team_id: Annotated[int, Path(..., description="Team ID")],
    ruleset: Annotated[GameMode | None, Path(description="Ruleset ID")],
):
    return await determine_team(session, team_id, ruleset)
