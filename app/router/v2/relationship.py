"""User relationship API endpoints.

This module provides endpoints for managing user relationships including
friends (follows) and blocks.
"""

from typing import Annotated, Any

from app.database import Relationship, RelationshipType, User
from app.database.relationship import RelationshipModel
from app.database.user import UserModel
from app.dependencies.api_version import APIVersion
from app.dependencies.database import Database
from app.dependencies.user import ClientUser, get_current_user
from app.helpers import api_doc
from app.models.error import ErrorType, RequestError

from .router import router

from fastapi import Path, Query, Request, Security
from sqlmodel import col, exists, select


@router.get(
    "/friends",
    tags=["Relationships"],
    responses={
        200: api_doc(
            "Friends list\n\nIf `x-api-version < 20241022`, returns `User` list, otherwise `Relationship` list.",
            list[RelationshipModel] | list[UserModel],
            [f"target.{inc}" for inc in User.LIST_INCLUDES],
        )
    },
    name="Get friends list",
    description="Get the current user's friends list.",
)
@router.get(
    "/blocks",
    tags=["Relationships"],
    response_model=list[dict[str, Any]],
    name="Get block list",
    description="Get the current user's blocked users list.",
)
async def get_relationship(
    db: Database,
    request: Request,
    api_version: APIVersion,
    current_user: Annotated[User, Security(get_current_user, scopes=["friends.read"])],
):
    """Get the current user's relationships (friends or blocks).

    Args:
        db: Database session dependency.
        request: The FastAPI request object.
        api_version: API version for response format selection.
        current_user: The authenticated user.

    Returns:
        List of relationships or users depending on endpoint and API version.
    """
    relationship_type = RelationshipType.FOLLOW if request.url.path.endswith("/friends") else RelationshipType.BLOCK
    relationships = await db.exec(
        select(Relationship).where(
            Relationship.user_id == current_user.id,
            Relationship.type == relationship_type,
            ~User.is_restricted_query(col(Relationship.target_id)),
        )
    )
    if api_version >= 20241022 or relationship_type == RelationshipType.BLOCK:
        return [
            await RelationshipModel.transform(
                rel,
                includes=[f"target.{inc}" for inc in User.LIST_INCLUDES],
                ruleset=current_user.playmode,
            )
            for rel in relationships.unique()
        ]
    else:
        return [
            await UserModel.transform(
                rel.target,
                ruleset=current_user.playmode,
                includes=User.LIST_INCLUDES,
            )
            for rel in relationships.unique()
        ]


@router.post(
    "/friends",
    tags=["Relationships"],
    responses={
        200: api_doc(
            "Friend relationship",
            {"user_relation": RelationshipModel},
            name="UserRelationshipResponse",
        )
    },
    name="Add or update friend relationship",
    description="\nAdd or update a friend relationship with the target user.",
)
@router.post(
    "/blocks",
    tags=["Relationships"],
    name="Add or update block relationship",
    description="\nAdd or update a block relationship with the target user.",
)
async def add_relationship(
    db: Database,
    request: Request,
    target: Annotated[int, Query(description="Target user ID")],
    current_user: ClientUser,
):
    """Add or update a relationship with a target user.

    Args:
        db: Database session dependency.
        request: The FastAPI request object.
        target: Target user ID.
        current_user: The authenticated user.

    Returns:
        dict: The created/updated relationship (for friends).

    Raises:
        RequestError: If user is restricted, target not found, or adding self.
    """
    if await current_user.is_restricted(db):
        raise RequestError(ErrorType.ACCOUNT_RESTRICTED)
    if not (
        await db.exec(select(exists()).where((User.id == target) & ~User.is_restricted_query(col(User.id))))
    ).first():
        raise RequestError(ErrorType.TARGET_USER_NOT_FOUND)

    relationship_type = RelationshipType.FOLLOW if request.url.path.endswith("/friends") else RelationshipType.BLOCK
    if target == current_user.id:
        raise RequestError(ErrorType.CANNOT_ADD_RELATIONSHIP_TO_SELF)
    relationship = (
        await db.exec(
            select(Relationship).where(
                Relationship.user_id == current_user.id,
                Relationship.target_id == target,
            )
        )
    ).first()
    if relationship:
        relationship.type = relationship_type
        # Original behavior: if it was block, it would also change to follow
        # Keeping consistent with ppy/osu-web behavior
    else:
        relationship = Relationship(
            user_id=current_user.id,
            target_id=target,
            type=relationship_type,
        )
        db.add(relationship)
    origin_type = relationship.type
    if origin_type == RelationshipType.BLOCK:
        target_relationship = (
            await db.exec(
                select(Relationship).where(
                    Relationship.user_id == target,
                    Relationship.target_id == current_user.id,
                )
            )
        ).first()
        if target_relationship and target_relationship.type == RelationshipType.FOLLOW:
            await db.delete(target_relationship)
    current_user_id = current_user.id
    current_gamemode = current_user.playmode
    await db.commit()
    if origin_type == RelationshipType.FOLLOW:
        relationship = (
            await db.exec(
                select(Relationship).where(
                    Relationship.user_id == current_user_id,
                    Relationship.target_id == target,
                )
            )
        ).one()
        return {
            "user_relation": await RelationshipModel.transform(
                relationship,
                includes=[],
                ruleset=current_gamemode,
            )
        }


@router.delete(
    "/friends/{target}",
    tags=["Relationships"],
    name="Remove friend",
    description="\nRemove a friend relationship with the target user.",
)
@router.delete(
    "/blocks/{target}",
    tags=["Relationships"],
    name="Remove block",
    description="\nRemove a block relationship with the target user.",
)
async def delete_relationship(
    db: Database,
    request: Request,
    target: Annotated[int, Path(..., description="Target user ID")],
    current_user: ClientUser,
) -> None:
    """Delete a relationship with a target user.

    Args:
        db: Database session dependency.
        request: The FastAPI request object.
        target: Target user ID.
        current_user: The authenticated user.

    Raises:
        RequestError: If user is restricted, target not found, relationship not found,
                     or relationship type mismatch.
    """
    if await current_user.is_restricted(db):
        raise RequestError(ErrorType.ACCOUNT_RESTRICTED)
    if not (
        await db.exec(select(exists()).where((User.id == target) & ~User.is_restricted_query(col(User.id))))
    ).first():
        raise RequestError(ErrorType.TARGET_USER_NOT_FOUND)

    relationship_type = RelationshipType.BLOCK if "/blocks/" in request.url.path else RelationshipType.FOLLOW
    relationship = (
        await db.exec(
            select(Relationship).where(
                Relationship.user_id == current_user.id,
                Relationship.target_id == target,
            )
        )
    ).first()
    if not relationship:
        raise RequestError(ErrorType.RELATIONSHIP_NOT_FOUND)
    if relationship.type != relationship_type:
        raise RequestError(ErrorType.RELATIONSHIP_TYPE_MISMATCH)
    await db.delete(relationship)
    await db.commit()
