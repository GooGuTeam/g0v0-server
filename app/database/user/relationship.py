from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel
from sqlmodel import (
    BigInteger,
    Column,
    Field,
    ForeignKey,
    Relationship as SQLRelationship,
    SQLModel,
    select,
)
from sqlmodel.ext.asyncio.session import AsyncSession


class RelationshipType(str, Enum):
    FOLLOW = "Friend"
    BLOCK = "Block"


class Relationship(SQLModel, table=True):
    __tablename__: str = "relationship"
    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, autoincrement=True, primary_key=True),
        exclude=True,
    )
    user_id: int = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("lazer_users.id"),
            index=True,
        ),
    )
    target_id: int = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey("lazer_users.id"),
            index=True,
        ),
    )
    type: RelationshipType = Field(default=RelationshipType.FOLLOW, nullable=False)
    target: "User" = SQLRelationship(
        sa_relationship_kwargs={
            "foreign_keys": "[Relationship.target_id]",
            "lazy": "selectin",
        }
    )


class RelationshipResp(BaseModel):
    target_id: int
    target: "UserResp"
    mutual: bool = False
    type: RelationshipType

    @classmethod
    async def from_db(cls, session: AsyncSession, relationship: Relationship) -> "RelationshipResp":
        target_relationship = (
            await session.exec(
                select(Relationship).where(
                    Relationship.user_id == relationship.target_id,
                    Relationship.target_id == relationship.user_id,
                )
            )
        ).first()
        mutual = bool(
            target_relationship is not None
            and relationship.type == RelationshipType.FOLLOW
            and target_relationship.type == RelationshipType.FOLLOW
        )
        from app.database.user.lazer_user import UserResp

        return cls(
            target_id=relationship.target_id,
            target=await UserResp.from_db(
                relationship.target,
                session,
                include=[
                    "team",
                    "daily_challenge_user_stats",
                    "statistics",
                    "statistics_rulesets",
                ],
            ),
            mutual=mutual,
            type=relationship.type,
        )


if TYPE_CHECKING:
    from app.database.user.lazer_user import User, UserResp
