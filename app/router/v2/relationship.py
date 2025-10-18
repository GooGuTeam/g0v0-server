from typing import Annotated

from app.database import Relationship, RelationshipResp, RelationshipType, User
from app.database.user import UserResp
from app.dependencies.api_version import APIVersion
from app.dependencies.database import Database
from app.dependencies.user import ClientUser, get_current_user

from .router import router

from fastapi import HTTPException, Path, Query, Request, Security
from pydantic import BaseModel
from sqlmodel import col, exists, select


@router.get(
    "/friends",
    tags=["用户关系"],
    responses={
        200: {
            "description": "好友列表",
            "content": {
                "application/json": {
                    "schema": {
                        "oneOf": [
                            {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/RelationshipResp"},
                                "description": "好友列表",
                            },
                            {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/UserResp"},
                                "description": "好友列表 (`x-api-version < 20241022`)",
                            },
                        ]
                    }
                }
            },
        }
    },
    name="获取好友列表",
    description=(
        "获取当前用户的好友列表。\n\n"
        "如果 `x-api-version < 20241022`，返回值为 `UserResp` 列表，否则为 `RelationshipResp` 列表。"
    ),
)
@router.get(
    "/blocks",
    tags=["用户关系"],
    response_model=list[RelationshipResp],
    name="获取屏蔽列表",
    description="获取当前用户的屏蔽用户列表。",
)
async def get_relationship(
    db: Database,
    request: Request,
    api_version: APIVersion,
    current_user: Annotated[User, Security(get_current_user, scopes=["friends.read"])],
):
    relationship_type = RelationshipType.FOLLOW if request.url.path.endswith("/friends") else RelationshipType.BLOCK
    relationships = await db.exec(
        select(Relationship).where(
            Relationship.user_id == current_user.id,
            Relationship.type == relationship_type,
            ~User.is_restricted_query(col(Relationship.target_id)),
        )
    )
    if api_version >= 20241022 or relationship_type == RelationshipType.BLOCK:
        return [await RelationshipResp.from_db(db, rel) for rel in relationships.unique()]
    else:
        return [
            await UserResp.from_db(
                rel.target,
                db,
                include=[
                    "team",
                    "daily_challenge_user_stats",
                    "statistics",
                    "statistics_rulesets",
                ],
            )
            for rel in relationships.unique()
        ]


class AddFriendResp(BaseModel):
    """添加好友/屏蔽 返回模型。

    - user_relation: 新的或更新后的关系对象。"""

    user_relation: RelationshipResp


@router.post(
    "/friends",
    tags=["用户关系"],
    response_model=AddFriendResp,
    name="添加或更新好友关系",
    description="\n添加或更新与目标用户的好友关系。",
)
@router.post(
    "/blocks",
    tags=["用户关系"],
    name="添加或更新屏蔽关系",
    description="\n添加或更新与目标用户的屏蔽关系。",
)
async def add_relationship(
    db: Database,
    request: Request,
    target: Annotated[int, Query(description="目标用户 ID")],
    current_user: ClientUser,
):
    if await current_user.is_restricted(db):
        raise HTTPException(403, "Your account is restricted and cannot perform this action.")
    if not (
        await db.exec(select(exists()).where((User.id == target) & ~User.is_restricted_query(col(User.id))))
    ).first():
        raise HTTPException(404, "Target user not found")

    relationship_type = RelationshipType.FOLLOW if request.url.path.endswith("/friends") else RelationshipType.BLOCK
    if target == current_user.id:
        raise HTTPException(422, "Cannot add relationship to yourself")
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
        # 这里原来如何是 block 也会修改为 follow
        # 与 ppy/osu-web 的行为保持一致
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
        return AddFriendResp(user_relation=await RelationshipResp.from_db(db, relationship))


@router.delete(
    "/friends/{target}",
    tags=["用户关系"],
    name="取消好友关系",
    description="\n删除与目标用户的好友关系。",
)
@router.delete(
    "/blocks/{target}",
    tags=["用户关系"],
    name="取消屏蔽关系",
    description="\n删除与目标用户的屏蔽关系。",
)
async def delete_relationship(
    db: Database,
    request: Request,
    target: Annotated[int, Path(..., description="目标用户 ID")],
    current_user: ClientUser,
):
    if await current_user.is_restricted(db):
        raise HTTPException(403, "Your account is restricted and cannot perform this action.")
    if not (
        await db.exec(select(exists()).where((User.id == target) & ~User.is_restricted_query(col(User.id))))
    ).first():
        raise HTTPException(404, "Target user not found")

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
        raise HTTPException(404, "Relationship not found")
    if relationship.type != relationship_type:
        raise HTTPException(422, "Relationship type mismatch")
    await db.delete(relationship)
    await db.commit()
