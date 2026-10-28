"""Scheduled refresh of trusted proxy ranges.

CDNs rotate their edge addresses, so ranges published through
``TRUSTED_PROXY_SOURCES`` are reloaded on a fixed interval instead of only once
at startup. A failed refresh keeps the last good snapshot.
"""

from app.config import settings
from app.dependencies.geoip import refresh_trusted_proxies
from app.dependencies.scheduler import get_scheduler
from app.log import task_logger

logger = task_logger("TrustedProxies")


@get_scheduler().scheduled_job(
    "interval",
    minutes=max(1, settings.trusted_proxy_refresh_minutes),
    id="trusted_proxy_refresh",
    name="Refresh trusted proxy ranges",
)
async def refresh_trusted_proxy_job() -> None:
    """Reload the published proxy ranges."""
    try:
        await refresh_trusted_proxies()
    except Exception as error:
        logger.error(f"Scheduled trusted proxy refresh failed: {error}")
