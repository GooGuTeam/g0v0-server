"""
[GeoIP] Scheduled Update Service
Periodically update the MaxMind GeoIP database
"""

from __future__ import annotations

import asyncio

from app.config import settings
from app.dependencies.geoip import get_geoip_helper
from app.dependencies.scheduler import get_scheduler
from app.log import logger


async def update_geoip_database():
    """
    Asynchronous task to update the GeoIP database
    """
    try:
        logger.info("[GeoIP] Starting scheduled GeoIP database update...")
        geoip = get_geoip_helper()

        # Run the synchronous update method in a background thread
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: geoip.update(force=False))

        logger.info("[GeoIP] Scheduled GeoIP database update completed successfully")
    except Exception as e:
        logger.error(f"[GeoIP] Scheduled GeoIP database update failed: {e}")


def schedule_geoip_updates():
    """
    Schedule the GeoIP database update task
    """
    scheduler = get_scheduler()

    # Use settings to configure the update time: update once a week
    scheduler.add_job(
        update_geoip_database,
        "cron",
        day_of_week=settings.geoip_update_day,
        hour=settings.geoip_update_hour,
        minute=0,
        id="geoip_weekly_update",
        name="Weekly GeoIP database update",
        replace_existing=True,
    )

    logger.info(
        f"[GeoIP] Scheduled update task registered: "
        f"every week on day {settings.geoip_update_day} "
        f"at {settings.geoip_update_hour}:00"
    )
