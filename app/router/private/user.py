from typing import Annotated

from app.auth import validate_username
from app.config import settings
from app.database import User
from app.database.events import Event, EventType
from app.dependencies.database import Database
from app.dependencies.user import ClientUser
from app.models.user import Page
from app.models.userpage import (
    UpdateUserpageRequest,
    UpdateUserpageResponse,
    UserpageError,
    ValidateBBCodeRequest,
    ValidateBBCodeResponse,
)
from app.service.bbcode_service import bbcode_service
from app.utils import utcnow

from .router import router

from fastapi import Body, HTTPException
from sqlmodel import exists, select


@router.post("/rename", name="修改用户名", tags=["用户", "g0v0 API"])
async def user_rename(
    session: Database,
    new_name: Annotated[str, Body(..., description="新的用户名")],
    current_user: ClientUser,
):
    """修改用户名

    为指定用户修改用户名，并将原用户名添加到历史用户名列表中

    错误情况:
    - 404: 找不到指定用户
    - 409: 新用户名已被占用

    返回:
    - 成功: None
    """
    if await current_user.is_restricted(session):
        # https://github.com/ppy/osu-web/blob/cae2fdf03cfb8c30c8e332cfb142e03188ceffef/app/Libraries/ChangeUsername.php#L48-L49
        raise HTTPException(403, "Your account is restricted and cannot perform this action.")

    samename_user = (await session.exec(select(exists()).where(User.username == new_name))).first()
    if samename_user:
        raise HTTPException(409, "Username Exists")
    errors = validate_username(new_name)
    if errors:
        raise HTTPException(403, "\n".join(errors))
    previous_username = []
    previous_username.extend(current_user.previous_usernames)
    previous_username.append(current_user.username)
    current_user.username = new_name
    current_user.previous_usernames = previous_username
    rename_event = Event(
        created_at=utcnow(),
        type=EventType.USERNAME_CHANGE,
        user_id=current_user.id,
        user=current_user,
    )
    rename_event.event_payload["user"] = {
        "username": new_name,
        "url": settings.web_url + "users/" + str(current_user.id),
        "previous_username": current_user.previous_usernames[-1],
    }
    session.add(rename_event)
    await session.commit()
    return None


@router.put(
    "/user/page",
    response_model=UpdateUserpageResponse,
    name="更新用户页面",
    description="更新指定用户的个人页面内容（支持BBCode）。匹配官方osu-web API格式。",
    tags=["用户", "g0v0 API"],
)
async def update_userpage(
    request: UpdateUserpageRequest,
    session: Database,
    current_user: ClientUser,
):
    """更新用户页面内容"""
    if await current_user.is_restricted(session):
        raise HTTPException(403, "Your account is restricted and cannot perform this action.")

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
    "/user/validate-bbcode",
    response_model=ValidateBBCodeResponse,
    name="验证BBCode",
    description="验证BBCode语法并返回预览。",
    tags=["用户", "g0v0 API"],
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
