from __future__ import annotations

from app.auth import validate_username
from app.config import settings
from app.database.events import Event, EventType
from app.database.lazer_user import User
from app.dependencies.database import Database
from app.dependencies.user import get_client_user
from app.utils import utcnow

from .router import router

from fastapi import Body, HTTPException, Security
from sqlmodel import exists, select


@router.post(
    "/rename",
    name="修改用户名",
)
async def user_rename(
    session: Database,
    new_name: str = Body(..., description="新的用户名"),
    current_user: User = Security(get_client_user),
):
    """修改用户名

    为指定用户修改用户名，并将原用户名添加到历史用户名列表中

    错误情况:
    - 404: 找不到指定用户
    - 409: 新用户名已被占用

    返回:
    - 成功: None
    """
    samename_user = (await session.exec(select(exists()).where(User.username == new_name))).first()
    if samename_user:
        raise HTTPException(409, "Username Exisits")
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
