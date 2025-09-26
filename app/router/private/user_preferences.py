from __future__ import annotations

from app.dependencies.database import Database
from app.dependencies.user import UserAndToken, get_current_user_and_token
from app.models.score import GameMode
from app.service.user_preferences_service import (
    convert_mode_for_client,
    get_available_modes,
    get_client_type_from_token,
)

from .router import router

from fastapi import Security
from pydantic import BaseModel


class SetDefaultModeRequest(BaseModel):
    mode: GameMode


class SetDefaultModeResponse(BaseModel):
    success: bool
    original_mode: str
    stored_mode: str
    client_type: str
    message: str


class GetUserPreferencesResponse(BaseModel):
    default_mode: str
    client_type: str
    available_modes: list[str]


@router.post(
    "/user-preferences/default-mode",
    response_model=SetDefaultModeResponse,
    name="设置默认游戏模式",
    description="设置用户的默认游戏模式。根据客户端类型自动转换模式。",
    tags=["g0v0 API", "用户偏好设置"],
)
async def set_default_mode(
    request: SetDefaultModeRequest,
    session: Database,
    user_and_token: UserAndToken = Security(get_current_user_and_token, scopes=["edit"]),
):
    """设置用户默认游戏模式，根据客户端类型进行转换"""
    user, token = user_and_token

    # 通过 token 获取客户端类型
    client_type = await get_client_type_from_token(session, token.id)

    # 根据客户端类型转换模式
    converted_mode = convert_mode_for_client(request.mode, client_type)

    # 更新用户默认模式
    user.playmode = converted_mode
    session.add(user)
    await session.commit()
    await session.refresh(user)

    return SetDefaultModeResponse(
        success=True,
        original_mode=request.mode.value,
        stored_mode=converted_mode.value,
        client_type=client_type,
        message=f"Default playmode set to {converted_mode.value}",
    )


@router.get(
    "/user-preferences",
    response_model=GetUserPreferencesResponse,
    name="获取用户偏好设置",
    description="获取用户的偏好设置，包括默认游戏模式等。",
    tags=["g0v0 API", "用户偏好设置"],
)
async def get_user_preferences(
    session: Database,
    user_and_token: UserAndToken = Security(get_current_user_and_token, scopes=["identify"]),
):
    """获取用户偏好设置"""
    user, token = user_and_token

    # 通过 token 获取客户端类型
    client_type = await get_client_type_from_token(session, token.id)

    # 根据客户端类型转换显示的默认模式
    display_mode = user.playmode
    if client_type == "osu_web":
        # osu_web 显示原始存储的模式
        display_mode = user.playmode
    elif client_type == "osu_lazer":
        # osu_lazer 显示转换后的标准模式
        display_mode = convert_mode_for_client(user.playmode, "osu_lazer")

    # 获取客户端支持的模式列表
    available_modes = get_available_modes(client_type)

    return GetUserPreferencesResponse(
        default_mode=display_mode.value, client_type=client_type, available_modes=available_modes
    )
