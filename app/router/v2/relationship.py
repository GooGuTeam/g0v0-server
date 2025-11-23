from typing import Annotated, Any

from app.database import Relationship, RelationshipType, User
from app.database.relationship import RelationshipModel
from app.database.user import UserModel
from app.dependencies.api_version import APIVersion
from app.dependencies.database import Database
from app.dependencies.user import ClientUser, get_current_user
from app.utils import api_doc

from .router import router

from fastapi import HTTPException, Path, Query, Request, Security
from sqlmodel import col, exists, select


@router.get(
    "/friends",
    tags=["用户关系"],
    responses={
        200: api_doc(
            "好友列表\n\n如果 `x-api-version < 20241022`，返回值为 `User` 列表，否则为 `Relationship` 列表。",
            list[RelationshipModel] | list[UserModel],
            [f"target.{inc}" for inc in User.LIST_INCLUDES],
        )
    },
    name="获取好友列表",
    description="获取当前用户的好友列表。",
)
@router.get(
    "/blocks",
    tags=["用户关系"],
    response_model=list[dict[str, Any]],
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
    tags=["用户关系"],
    responses={200: api_doc("好友关系", {"user_relation": RelationshipModel}, name="UserRelationshipResponse")},
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
        return {
            "user_relation": await RelationshipModel.transform(
                relationship,
                includes=[],
                ruleset=current_user.playmode,
            )
        }


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
