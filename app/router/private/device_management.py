"""
设备管理 API 端点
用于用户管理自己的登录设备和会话
"""

from __future__ import annotations

from app.database.lazer_user import User
from app.dependencies.database import Database
from app.dependencies.user import UserAndToken, get_current_user, get_current_user_and_token
from app.service.device_management_service import DeviceManagementService, DeviceSession

from fastapi import APIRouter, Depends, HTTPException, Security
from pydantic import BaseModel


class RevokeSessionRequest(BaseModel):
    session_id: int


class DeviceManagementResponse(BaseModel):
    success: bool
    message: str
    data: dict | None = None


router = APIRouter(tags=["设备管理"], prefix="/device")


@router.get(
    "/sessions", response_model=list[DeviceSession], name="获取活跃会话", description="获取当前用户的所有活跃登录会话"
)
async def get_active_sessions(
    db: Database,
    user_and_token: UserAndToken = Security(get_current_user_and_token),
):
    """获取用户的所有活跃会话"""
    try:
        user = user_and_token[0]
        current_token = user_and_token[1]

        # 传递当前token来正确标记当前会话
        sessions = await DeviceManagementService.get_user_active_sessions(
            db, user.id, current_token=current_token.access_token
        )
        return sessions
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取会话列表失败: {e!s}")


@router.post(
    "/sessions/revoke", response_model=DeviceManagementResponse, name="撤销指定会话", description="撤销指定的登录会话"
)
async def revoke_session(
    request: RevokeSessionRequest,
    db: Database,
    user_and_token: UserAndToken = Security(get_current_user_and_token),
):
    """撤销指定的会话"""
    try:
        user = user_and_token[0]
        current_token = user_and_token[1]

        success, message = await DeviceManagementService.revoke_session(
            db, user.id, request.session_id, current_token=current_token.access_token
        )

        return DeviceManagementResponse(success=success, message=message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"撤销会话失败: {e!s}")


@router.get(
    "/summary", response_model=DeviceManagementResponse, name="设备会话统计", description="获取用户设备会话统计信息"
)
async def get_device_summary(
    db: Database,
    user: User = Depends(get_current_user),
):
    """获取设备会话统计"""
    try:
        device_summary = await DeviceManagementService.get_device_type_summary(db, user.id)
        activity_stats = await DeviceManagementService.get_session_activity_stats(db, user.id)

        return DeviceManagementResponse(
            success=True,
            message="获取统计信息成功",
            data={
                "device_summary": device_summary,
                "activity_stats": activity_stats,
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取统计信息失败: {e!s}")
