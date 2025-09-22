"""
用户登录记录服务
"""

from __future__ import annotations

import asyncio

from app.database.user.login_log import UserLoginLog
from app.dependencies.geoip import get_client_ip, get_geoip_helper, normalize_ip
from app.log import logger
from app.utils import simplify_user_agent, utcnow

from fastapi import Request
from sqlmodel.ext.asyncio.session import AsyncSession


class LoginLogService:
    """用户登录记录服务"""

    @staticmethod
    async def record_login(
        db: AsyncSession,
        user_id: int,
        request: Request,
        login_success: bool = True,
        login_method: str = "password",
        notes: str | None = None,
    ) -> UserLoginLog:
        """
        记录用户登录信息

        Args:
            db: 数据库会话
            user_id: 用户ID
            request: HTTP请求对象
            login_success: 登录是否成功
            login_method: 登录方式
            notes: 备注信息

        Returns:
            UserLoginLog: 登录记录对象
        """
        # 获取客户端IP并标准化格式
        raw_ip = get_client_ip(request)
        ip_address = normalize_ip(raw_ip)

        raw_user_agent = request.headers.get("User-Agent", "")
        user_agent = simplify_user_agent(raw_user_agent, max_length=500)

        # 创建基本的登录记录
        login_log = UserLoginLog(
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            login_time=utcnow(),
            login_success=login_success,
            login_method=login_method,
            notes=notes,
        )

        # 异步获取GeoIP信息
        try:
            geoip = get_geoip_helper()

            # 在后台线程中运行GeoIP查询（避免阻塞）
            loop = asyncio.get_event_loop()
            geo_info = await loop.run_in_executor(None, lambda: geoip.lookup(ip_address))

            if geo_info:
                login_log.country_code = geo_info.get("country_iso", "")
                login_log.country_name = geo_info.get("country_name", "")
                login_log.city_name = geo_info.get("city_name", "")
                login_log.latitude = geo_info.get("latitude", "")
                login_log.longitude = geo_info.get("longitude", "")
                login_log.time_zone = geo_info.get("time_zone", "")

                # 处理 ASN（可能是字符串，需要转换为整数）
                asn_value = geo_info.get("asn")
                if asn_value is not None:
                    try:
                        login_log.asn = int(asn_value)
                    except (ValueError, TypeError):
                        login_log.asn = None

                login_log.organization = geo_info.get("organization", "")

                logger.debug(f"GeoIP lookup for {ip_address}: {geo_info.get('country_name', 'Unknown')}")
            else:
                logger.warning(f"GeoIP lookup failed for {ip_address}")

        except Exception as e:
            logger.warning(f"GeoIP lookup error for {ip_address}: {e}")

        # 保存到数据库
        db.add(login_log)
        await db.commit()
        await db.refresh(login_log)

        logger.info(f"Login recorded for user {user_id} from {ip_address} ({login_method})")
        return login_log

    @staticmethod
    async def record_failed_login(
        db: AsyncSession,
        request: Request,
        attempted_username: str | None = None,
        login_method: str = "password",
        notes: str | None = None,
    ) -> UserLoginLog:
        """
        记录失败的登录尝试

        Args:
            db: 数据库会话
            request: HTTP请求对象
            attempted_username: 尝试登录的用户名
            login_method: 登录方式
            notes: 备注信息

        Returns:
            UserLoginLog: 登录记录对象
        """
        # 对于失败的登录，使用user_id=0表示未知用户
        return await LoginLogService.record_login(
            db=db,
            user_id=0,  # 0表示未知/失败的登录
            request=request,
            login_success=False,
            login_method=login_method,
            notes=f"Failed login attempt: {attempted_username}" if attempted_username else "Failed login attempt",
        )


def get_request_info(request: Request) -> dict:
    """
    提取请求的详细信息

    Args:
        request: HTTP请求对象

    Returns:
        dict: 包含请求信息的字典
    """
    return {
        "ip": get_client_ip(request),
        "user_agent": request.headers.get("User-Agent", ""),
        "referer": request.headers.get("Referer", ""),
        "accept_language": request.headers.get("Accept-Language", ""),
        "x_forwarded_for": request.headers.get("X-Forwarded-For", ""),
        "x_real_ip": request.headers.get("X-Real-IP", ""),
    }
