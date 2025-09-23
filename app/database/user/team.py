from datetime import datetime
from typing import TYPE_CHECKING

from app.models.model import UTCBaseModel
from app.utils import utcnow

from sqlalchemy import Column, DateTime
from sqlmodel import BigInteger, Field, ForeignKey, Relationship, SQLModel

if TYPE_CHECKING:
    from .lazer_user import User


class Team(SQLModel, UTCBaseModel, table=True):
    __tablename__: str = "teams"

    id: int = Field(default=None, primary_key=True, index=True)
    name: str = Field(max_length=100)
    short_name: str = Field(max_length=10)
    flag_url: str | None = Field(default=None)
    cover_url: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime))
    leader_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id")))

    leader: "User" = Relationship()
    members: list["TeamMember"] = Relationship(back_populates="team")


class TeamMember(SQLModel, UTCBaseModel, table=True):
    __tablename__: str = "team_members"

    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), primary_key=True))
    team_id: int = Field(foreign_key="teams.id")
    joined_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime))

    user: "User" = Relationship(back_populates="team_membership", sa_relationship_kwargs={"lazy": "joined"})
    team: "Team" = Relationship(back_populates="members", sa_relationship_kwargs={"lazy": "joined"})


class TeamRequest(SQLModel, UTCBaseModel, table=True):
    __tablename__: str = "team_requests"

    user_id: int = Field(sa_column=Column(BigInteger, ForeignKey("lazer_users.id"), primary_key=True))
    team_id: int = Field(foreign_key="teams.id", primary_key=True)
    requested_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime))

    user: "User" = Relationship(sa_relationship_kwargs={"lazy": "joined"})
    team: "Team" = Relationship(sa_relationship_kwargs={"lazy": "joined"})
