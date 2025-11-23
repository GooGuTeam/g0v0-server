from typing import Annotated

from app.database import FavouriteBeatmapset, User
from app.database.user import UserModel
from app.dependencies.database import Database
from app.dependencies.user import UserAndToken, get_current_user, get_current_user_and_token
from app.models.score import GameMode
from app.utils import api_doc

from .router import router

from fastapi import Path, Security
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlmodel import select

ME_INCLUDES = [*User.USER_INCLUDES, "session_verified", "session_verification_method"]


class BeatmapsetIds(BaseModel):
    beatmapset_ids: list[int]


@router.get(
    "/me/beatmapset-favourites",
    response_model=BeatmapsetIds,
    name="获取当前用户收藏的谱面集 ID 列表",
    description="获取当前登录用户收藏的谱面集 ID 列表。",
    tags=["用户", "谱面集"],
)
async def get_user_beatmapset_favourites(
    session: Database,
    current_user: Annotated[User, Security(get_current_user, scopes=["identify"])],
):
    beatmapset_ids = await session.exec(
        select(FavouriteBeatmapset.beatmapset_id).where(FavouriteBeatmapset.user_id == current_user.id)
    )
    return BeatmapsetIds(beatmapset_ids=list(beatmapset_ids.all()))


@router.get(
    "/me/{ruleset}",
    responses={200: api_doc("当前用户信息（含指定 ruleset 统计）", UserModel, ME_INCLUDES)},
    name="获取当前用户信息 (指定 ruleset)",
    description="获取当前登录用户信息 （含指定 ruleset 统计）。",
    tags=["用户"],
)
async def get_user_info_with_ruleset(
    ruleset: Annotated[GameMode, Path(description="指定 ruleset")],
    user_and_token: Annotated[UserAndToken, Security(get_current_user_and_token, scopes=["identify"])],
):
    user_resp = await UserModel.transform(
        user_and_token[0], ruleset=ruleset, token_id=user_and_token[1].id, includes=ME_INCLUDES
    )
    return user_resp


@router.get(
    "/me/",
    responses={200: api_doc("当前用户信息", UserModel, ME_INCLUDES)},
    name="获取当前用户信息",
    description="获取当前登录用户信息。",
    tags=["用户"],
)
async def get_user_info_default(
    user_and_token: Annotated[UserAndToken, Security(get_current_user_and_token, scopes=["identify"])],
):
    user_resp = await UserModel.transform(
        user_and_token[0], ruleset=None, token_id=user_and_token[1].id, includes=ME_INCLUDES
    )
    return user_resp


@router.put("/users/{user_id}/page", include_in_schema=False)
async def update_userpage():
    return RedirectResponse(url="/api/private/user/page", status_code=307)


@router.post("/me/validate-bbcode", include_in_schema=False)
async def validate_bbcode():
    return RedirectResponse(url="/api/private/user/validate-bbcode", status_code=307)
