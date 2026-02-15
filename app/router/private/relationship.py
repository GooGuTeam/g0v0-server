"""User relationship check endpoint.

Provides API to check follow/mutual relationship status between users.
"""

from typing import Annotated

from app.database import Relationship
from app.database.relationship import RelationshipType
from app.dependencies.database import Database
from app.dependencies.user import ClientUser
from app.models.error import ErrorType, RequestError

from .router import router

from fastapi import Path
from pydantic import BaseModel, Field
from sqlmodel import select


class CheckResponse(BaseModel):
    """Relationship check response.

    Attributes:
        is_followed: Whether the target user follows the current user.
        is_following: Whether the current user follows the target user.
        mutual: Whether both users follow each other.
    """

    is_followed: bool = Field(..., description="Whether the target user follows the current user")
    is_following: bool = Field(..., description="Whether the current user follows the target user")
    mutual: bool = Field(..., description="Whether both users follow each other")


@router.get(
    "/relationship/check/{user_id}",
    name="Check relationship status",
    description="Check the relationship status between current user and target user",
    response_model=CheckResponse,
    tags=["User Relationship", "g0v0 API"],
)
async def check_user_relationship(
    db: Database,
    user_id: Annotated[int, Path(..., description="Target user ID")],
    current_user: ClientUser,
):
    if user_id == current_user.id:
        raise RequestError(ErrorType.CANNOT_CHECK_RELATIONSHIP_WITH_SELF)

    my_relationship = (
        await db.exec(
            select(Relationship).where(
                Relationship.user_id == current_user.id,
                Relationship.target_id == user_id,
            )
        )
    ).first()

    target_relationship = (
        await db.exec(
            select(Relationship).where(
                Relationship.user_id == user_id,
                Relationship.target_id == current_user.id,
            )
        )
    ).first()

    is_followed = bool(target_relationship and target_relationship.type == RelationshipType.FOLLOW)
    is_following = bool(my_relationship and my_relationship.type == RelationshipType.FOLLOW)

    return CheckResponse(
        is_followed=is_followed,
        is_following=is_following,
        mutual=is_followed and is_following,
    )
