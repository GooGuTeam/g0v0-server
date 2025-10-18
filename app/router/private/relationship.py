from typing import Annotated

from app.database import Relationship
from app.database.relationship import RelationshipType
from app.dependencies.database import Database
from app.dependencies.user import ClientUser

from .router import router

from fastapi import HTTPException, Path
from pydantic import BaseModel, Field
from sqlmodel import select


class CheckResponse(BaseModel):
    is_followed: bool = Field(..., description="对方是否关注了当前用户")
    is_following: bool = Field(..., description="当前用户是否关注了对方")
    mutual: bool = Field(..., description="当前用户与对方是否互相关注")


@router.get(
    "/relationship/check/{user_id}",
    name="检查关系状态",
    description="检查当前用户与指定用户的关系状态",
    response_model=CheckResponse,
    tags=["用户关系", "g0v0 API"],
)
async def check_user_relationship(
    db: Database,
    user_id: Annotated[int, Path(..., description="目标用户的 ID")],
    current_user: ClientUser,
):
    if user_id == current_user.id:
        raise HTTPException(422, "Cannot check relationship with yourself")

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
