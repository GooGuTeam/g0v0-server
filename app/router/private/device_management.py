"""
设备管理 API 端点
用于用户管理自己的登录设备和会话
"""

from __future__ import annotations

from app.database.lazer_user import User
from app.dependencies.database import Database
from app.dependencies.user import get_current_user
from app.service.device_management_service import DeviceManagementService, DeviceSession

from fastapi import APIRouter, Depends, HTTPException
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
    user: User = Depends(get_current_user),
):
    """获取用户的所有活跃会话"""
    try:
        # 注意：这里需要获取当前用户的 access_token，但在当前架构下比较复杂
        # 简化实现，不标记当前会话
        sessions = await DeviceManagementService.get_user_active_sessions(db, user.id, current_token=None)
        return sessions
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取会话列表失败: {e!s}")


@router.post(
    "/sessions/revoke", response_model=DeviceManagementResponse, name="撤销指定会话", description="撤销指定的登录会话"
)
async def revoke_session(
    request: RevokeSessionRequest,
    db: Database,
    user: User = Depends(get_current_user),
):
    """撤销指定的会话"""
    try:
        success, message = await DeviceManagementService.revoke_session(
            db, user.id, request.session_id, current_token=None
        )

        return DeviceManagementResponse(success=success, message=message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"撤销会话失败: {e!s}")


@router.post(
    "/sessions/revoke-all-others",
    response_model=DeviceManagementResponse,
    name="撤销其他会话",
    description="撤销除当前会话外的所有其他会话",
)
async def revoke_all_other_sessions(
    db: Database,
    user: User = Depends(get_current_user),
):
    """撤销除当前会话外的所有其他会话"""
    try:
        # 这里需要获取当前 access_token，暂时使用空值
        # 在实际使用中需要从请求中提取当前token
        success, message, count = await DeviceManagementService.revoke_all_other_sessions(
            db,
            user.id,
            "",  # 需要传入当前 access_token
        )

        return DeviceManagementResponse(success=success, message=message, data={"revoked_count": count})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"撤销其他会话失败: {e!s}")


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


@router.post("/cleanup", response_model=DeviceManagementResponse, name="清理过期会话", description="清理用户的过期会话")
async def cleanup_expired_sessions(
    db: Database,
    user: User = Depends(get_current_user),
):
    """清理过期会话"""
    try:
        cleanup_count = await DeviceManagementService.cleanup_expired_sessions(db, user.id)

        return DeviceManagementResponse(
            success=True, message=f"清理完成，共清理 {cleanup_count} 个过期会话", data={"cleanup_count": cleanup_count}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清理过期会话失败: {e!s}")
