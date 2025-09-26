"""
设备管理服务
用于管理用户的登录设备和会话
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.database.auth import OAuthToken
from app.dependencies.geoip import get_geoip_helper
from app.log import logger
from app.service.client_detection_service import ClientDetectionService
from app.utils import utcnow

from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession


class DeviceSession(BaseModel):
    """设备会话信息"""

    id: int
    device_type: str
    device_fingerprint: str | None
    user_agent: str | None
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    is_current: bool = False
    location: str | None = None
    client_display_name: str


class DeviceManagementService:
    """设备管理服务"""

    @staticmethod
    async def get_user_active_sessions(
        db: AsyncSession, user_id: int, current_token: str | None = None
    ) -> list[DeviceSession]:
        """获取用户的所有活跃会话"""
        try:
            # 查询用户所有未过期的令牌
            statement = (
                select(OAuthToken)
                .where(OAuthToken.user_id == user_id, OAuthToken.expires_at > utcnow())
                .order_by(OAuthToken.last_used_at.desc(), OAuthToken.created_at.desc())
            )

            tokens = (await db.exec(statement)).all()
            sessions = []

            for token in tokens:
                # 生成客户端显示名称
                if token.device_type and token.user_agent:
                    from app.service.client_detection_service import ClientInfo

                    client_info = ClientInfo(
                        client_type=token.device_type,  # type: ignore
                        device_fingerprint=token.device_fingerprint,
                    )
                    client_display_name = ClientDetectionService.format_client_display_name(client_info)
                else:
                    client_display_name = token.device_type or "Unknown Device"

                # 获取地理位置信息
                location = None
                if token.ip_address:
                    try:
                        geoip = get_geoip_helper()
                        geo_info = geoip.lookup(token.ip_address)

                        if geo_info:
                            country_name = geo_info.get("country_name", "")
                            country_iso = geo_info.get("country_iso", "")
                            city_name = geo_info.get("city_name", "")

                            if country_name:
                                if city_name:
                                    location = f"{city_name}, {country_name}"
                                else:
                                    location = country_name
                            elif country_iso:
                                location = country_iso
                            else:
                                location = "Unknown Location"
                        else:
                            location = "Unknown Location"
                    except Exception as e:
                        logger.warning(f"Failed to get geo info for IP {token.ip_address}: {e}")
                        location = "Unknown Location"

                session = DeviceSession(
                    id=token.id,
                    device_type=token.device_type or "unknown",
                    device_fingerprint=token.device_fingerprint,
                    user_agent=token.user_agent,
                    created_at=token.created_at,
                    last_used_at=token.last_used_at,
                    expires_at=token.expires_at,
                    is_current=(token.access_token == current_token),
                    location=location,
                    client_display_name=client_display_name,
                )
                sessions.append(session)

            return sessions

        except Exception as e:
            logger.error(f"[Device Management] Error getting user sessions: {e}")
            return []

    @staticmethod
    async def revoke_session(
        db: AsyncSession, user_id: int, session_id: int, current_token: str | None = None
    ) -> tuple[bool, str]:
        """撤销指定的会话"""
        try:
            # 查找要撤销的令牌
            statement = select(OAuthToken).where(
                OAuthToken.id == session_id, OAuthToken.user_id == user_id, OAuthToken.expires_at > utcnow()
            )

            token = (await db.exec(statement)).first()
            if not token:
                return False, "会话不存在或已过期"

            # 防止用户撤销当前会话（除非明确允许）
            if token.access_token == current_token:
                return False, "不能撤销当前会话"

            # 删除令牌
            await db.delete(token)
            await db.commit()

            logger.info(f"[Device Management] User {user_id} revoked session {session_id}")
            return True, "会话已成功撤销"

        except Exception as e:
            logger.error(f"[Device Management] Error revoking session: {e}")
            await db.rollback()
            return False, "撤销会话时发生错误"

    @staticmethod
    async def get_device_type_summary(db: AsyncSession, user_id: int) -> dict[str, int]:
        """获取用户各设备类型的会话数量统计"""
        try:
            statement = select(OAuthToken).where(OAuthToken.user_id == user_id, OAuthToken.expires_at > utcnow())

            tokens = (await db.exec(statement)).all()
            summary = {}

            for token in tokens:
                device_type = token.device_type or "unknown"
                summary[device_type] = summary.get(device_type, 0) + 1

            return summary

        except Exception as e:
            logger.error(f"[Device Management] Error getting device summary: {e}")
            return {}

    @staticmethod
    async def get_session_activity_stats(db: AsyncSession, user_id: int, days: int = 30) -> dict:
        """获取用户会话活动统计"""
        try:
            since_date = utcnow() - timedelta(days=days)

            statement = select(OAuthToken).where(OAuthToken.user_id == user_id, OAuthToken.created_at >= since_date)

            tokens = (await db.exec(statement)).all()

            stats = {
                "total_sessions": len(tokens),
                "active_sessions": len([t for t in tokens if t.expires_at > utcnow()]),
                "device_types": {},
                "recent_ips": set(),
            }

            for token in tokens:
                device_type = token.device_type or "unknown"
                stats["device_types"][device_type] = stats["device_types"].get(device_type, 0) + 1

                if token.ip_address:
                    stats["recent_ips"].add(token.ip_address)

            stats["recent_ips"] = list(stats["recent_ips"])

            return stats

        except Exception as e:
            logger.error(f"[Device Management] Error getting session stats: {e}")
            return {}
