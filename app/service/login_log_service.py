"""User login logging service.

Records user login attempts with IP and geolocation information.
"""

import asyncio

from app.database.user_login_log import UserLoginLog
from app.dependencies.geoip import get_client_ip, get_geoip_helper, normalize_ip
from app.helpers import utcnow
from app.log import logger

from fastapi import Request
from sqlmodel.ext.asyncio.session import AsyncSession


class LoginLogService:
    """User login logging service.

    Provides methods to record successful and failed login attempts
    with geolocation information.
    """

    @staticmethod
    async def record_login(
        db: AsyncSession,
        user_id: int,
        request: Request,
        user_agent: str | None = None,
        login_success: bool = True,
        login_method: str = "password",
        notes: str | None = None,
    ) -> UserLoginLog:
        """
        Record a user login attempt.

        Args:
            db: Database session.
            user_id: ID of the user who attempted to log in.
            request: HTTP request object.
            login_success: Whether login was successful.
            login_method: Login method.
            notes: Additional notes.

        Returns:
            UserLoginLog: Login log object.
        """
        # Get client IP and normalize format
        raw_ip = get_client_ip(request)
        ip_address = normalize_ip(raw_ip)

        # Create basic login record
        login_log = UserLoginLog(
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            login_time=utcnow(),
            login_success=login_success,
            login_method=login_method,
            notes=notes,
        )

        # Async get GeoIP info
        try:
            geoip = get_geoip_helper()

            # Run GeoIP query in background thread (avoid blocking)
            loop = asyncio.get_event_loop()
            geo_info = await loop.run_in_executor(None, lambda: geoip.lookup(ip_address))

            if geo_info:
                login_log.country_code = geo_info.get("country_iso", "")
                login_log.country_name = geo_info.get("country_name", "")
                login_log.city_name = geo_info.get("city_name", "")
                login_log.latitude = geo_info.get("latitude", "")
                login_log.longitude = geo_info.get("longitude", "")
                login_log.time_zone = geo_info.get("time_zone", "")

                # Handle ASN (may be string, needs conversion to int)
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

        # Save to database
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
        user_agent: str | None = None,
    ) -> UserLoginLog:
        """Record failed login attempt.

        Args:
            db: Database session.
            request: HTTP request object.
            attempted_username: Username that was attempted.
            login_method: Login method.
            notes: Additional notes.

        Returns:
            UserLoginLog: Login log object.
        """
        # For failed logins, use user_id=0 for unknown user
        return await LoginLogService.record_login(
            db=db,
            user_id=0,  # 0 indicates unknown/failed login
            request=request,
            login_success=False,
            login_method=login_method,
            user_agent=user_agent,
            notes=(
                f"Failed login attempt on user {attempted_username}: {notes}"
                if attempted_username
                else "Failed login attempt"
            ),
        )


def get_request_info(request: Request) -> dict:
    """
    Extract request information for logging.

    Args:
        request: HTTP request object.

    Returns:
        dict: Dictionary containing IP, user agent, referer, accept language, and other headers.
    """
    return {
        "ip": get_client_ip(request),
        "user_agent": request.headers.get("User-Agent", ""),
        "referer": request.headers.get("Referer", ""),
        "accept_language": request.headers.get("Accept-Language", ""),
        "x_forwarded_for": request.headers.get("X-Forwarded-For", ""),
        "x_real_ip": request.headers.get("X-Real-IP", ""),
    }
