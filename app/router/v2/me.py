from typing import Annotated

from app.database import MeResp
from app.dependencies.database import Database
from app.dependencies.user import UserAndToken, get_current_user_and_token
from app.models.score import GameMode

from .router import router

from fastapi import Path, Security
from fastapi.responses import RedirectResponse


@router.get(
    "/me/{ruleset}",
    response_model=MeResp,
    name="获取当前用户信息 (指定 ruleset)",
    description="获取当前登录用户信息 （含指定 ruleset 统计）。",
    tags=["用户"],
)
async def get_user_info_with_ruleset(
    session: Database,
    ruleset: Annotated[GameMode, Path(description="指定 ruleset")],
    user_and_token: Annotated[UserAndToken, Security(get_current_user_and_token, scopes=["identify"])],
):
    user_resp = await MeResp.from_db(user_and_token[0], session, ruleset, token_id=user_and_token[1].id)
    return user_resp


@router.get(
    "/me/",
    response_model=MeResp,
    name="获取当前用户信息",
    description="获取当前登录用户信息。",
    tags=["用户"],
)
async def get_user_info_default(
    session: Database,
    user_and_token: Annotated[UserAndToken, Security(get_current_user_and_token, scopes=["identify"])],
):
    user_resp = await MeResp.from_db(user_and_token[0], session, None, token_id=user_and_token[1].id)
    return user_resp


@router.put("/users/{user_id}/page", include_in_schema=False)
async def update_userpage():
    return RedirectResponse(url="/api/private/user/page", status_code=307)


@router.post("/me/validate-bbcode", include_in_schema=False)
async def validate_bbcode():
    return RedirectResponse(url="/api/private/user/validate-bbcode", status_code=307)
