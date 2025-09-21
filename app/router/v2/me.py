from __future__ import annotations

from app.database import MeResp, User
from app.database.lazer_user import ALL_INCLUDED
from app.dependencies import get_current_user
from app.dependencies.database import Database
from app.dependencies.user import UserAndToken, get_current_user_and_token
from app.exceptions.userpage import UserpageError
from app.models.score import GameMode
from app.models.user import Page
from app.models.userpage import (
    UpdateUserpageRequest,
    UpdateUserpageResponse,
    ValidateBBCodeRequest,
    ValidateBBCodeResponse,
)
from app.service.bbcode_service import bbcode_service

from .router import router

from fastapi import HTTPException, Path, Security


@router.get(
    "/me/{ruleset}",
    response_model=MeResp,
    name="获取当前用户信息 (指定 ruleset)",
    description="获取当前登录用户信息 （含指定 ruleset 统计）。",
    tags=["用户"],
)
async def get_user_info_with_ruleset(
    session: Database,
    ruleset: GameMode = Path(description="指定 ruleset"),
    user_and_token: UserAndToken = Security(get_current_user_and_token, scopes=["identify"]),
):
    user_resp = await MeResp.from_db(user_and_token[0], session, ALL_INCLUDED, ruleset, token_id=user_and_token[1].id)
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
    user_and_token: UserAndToken = Security(get_current_user_and_token, scopes=["identify"]),
):
    user_resp = await MeResp.from_db(user_and_token[0], session, ALL_INCLUDED, None, token_id=user_and_token[1].id)
    return user_resp


# @router.get(
#     "/users/{user_id}/page",
#     response_model=UserpageResponse,
#     name="获取用户页面",
#     description="获取指定用户的个人页面内容。匹配官方osu-web API格式。",
#     tags=["用户"],
# )
# async def get_userpage(
#     session: Database,
#     user_id: int = Path(description="用户ID"),
# ):
#     """获取用户页面内容"""
#     # 查找用户
#     user = await session.get(User, user_id)
#     if not user:
#         raise HTTPException(status_code=404, detail={"error": "User not found"})

#     # 返回页面内容
#     if user.page:
#         return UserpageResponse(html=user.page.get("html", ""), raw=user.page.get("raw", ""))
#     else:
#         return UserpageResponse(html="", raw="")


@router.put(
    "/users/{user_id}/page",
    response_model=UpdateUserpageResponse,
    name="更新用户页面",
    description="更新指定用户的个人页面内容（支持BBCode）。匹配官方osu-web API格式。",
    tags=["用户"],
)
async def update_userpage(
    request: UpdateUserpageRequest,
    session: Database,
    user_id: int = Path(description="用户ID"),
    current_user: User = Security(get_current_user, scopes=["edit"]),
):
    """更新用户页面内容（匹配官方osu-web实现）"""
    # 检查权限：只能编辑自己的页面（除非是管理员）
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail={"error": "Access denied"})

    try:
        # 处理BBCode内容
        processed_page = bbcode_service.process_userpage_content(request.body)

        # 更新数据库 - 直接更新用户对象
        current_user.page = Page(html=processed_page["html"], raw=processed_page["raw"])
        session.add(current_user)
        await session.commit()
        await session.refresh(current_user)

        # 返回官方格式的响应：只包含html
        return UpdateUserpageResponse(html=processed_page["html"])

    except UserpageError as e:
        # 使用官方格式的错误响应：{'error': message}
        raise HTTPException(status_code=422, detail={"error": e.message})
    except Exception:
        raise HTTPException(status_code=500, detail={"error": "Failed to update user page"})


@router.post(
    "/me/validate-bbcode",
    response_model=ValidateBBCodeResponse,
    name="验证BBCode",
    description="验证BBCode语法并返回预览。",
    tags=["用户"],
)
async def validate_bbcode(
    request: ValidateBBCodeRequest,
):
    """验证BBCode语法"""
    try:
        # 验证BBCode语法
        errors = bbcode_service.validate_bbcode(request.content)

        # 生成预览（如果没有严重错误）
        if len(errors) == 0:
            preview = bbcode_service.process_userpage_content(request.content)
        else:
            preview = {"raw": request.content, "html": ""}

        return ValidateBBCodeResponse(valid=len(errors) == 0, errors=errors, preview=preview)

    except UserpageError as e:
        return ValidateBBCodeResponse(valid=False, errors=[e.message], preview={"raw": request.content, "html": ""})
    except Exception:
        raise HTTPException(status_code=500, detail={"error": "Failed to validate BBCode"})
