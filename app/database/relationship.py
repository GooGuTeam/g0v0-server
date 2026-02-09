from enum import StrEnum
from typing import TYPE_CHECKING, NotRequired, TypedDict

from app.models.score import GameMode

from ._base import DatabaseModel, included, ondemand

from sqlmodel import (
    BigInteger,
    Column,
    Field,
    ForeignKey,
    Relationship as SQLRelationship,
    select,
)
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from .user import User, UserDict


class RelationshipType(StrEnum):
    FOLLOW = "friend"
    BLOCK = "block"


class RelationshipDict(TypedDict):
    target_id: int | None
    type: RelationshipType
    id: NotRequired[int | None]
    user_id: NotRequired[int | None]
    mutual: NotRequired[bool]
    target: NotRequired["UserDict"]


class RelationshipModel(DatabaseModel[RelationshipDict]):
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
        exclude=True,
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

    @included
    @staticmethod
    async def mutual(session: AsyncSession, relationship: "Relationship") -> bool:
        target_relationship = (
            await session.exec(
                select(Relationship).where(
                    Relationship.user_id == relationship.target_id,
                    Relationship.target_id == relationship.user_id,
                )
            )
        ).first()
        return bool(
            target_relationship is not None
            and relationship.type == RelationshipType.FOLLOW
            and target_relationship.type == RelationshipType.FOLLOW
        )

    @ondemand
    @staticmethod
    async def target(
        _session: AsyncSession,
        relationship: "Relationship",
        ruleset: GameMode | None = None,
        includes: list[str] | None = None,
    ) -> "UserDict":
        from .user import UserModel

        return await UserModel.transform(relationship.target, ruleset=ruleset, includes=includes)


class Relationship(RelationshipModel, table=True):
    target: "User" = SQLRelationship(
        sa_relationship_kwargs={
            "foreign_keys": "[Relationship.target_id]",
            "lazy": "selectin",
        }
    )
