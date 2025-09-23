"""
[GeoIP] Initialization Service
Initialize the GeoIP database when the application starts
"""

from __future__ import annotations

import asyncio

from app.dependencies.geoip import get_geoip_helper
from app.log import logger


async def init_geoip():
    """
    Asynchronously initialize the GeoIP database
    """
    try:
        geoip = get_geoip_helper()
        logger.info("[GeoIP] Initializing GeoIP database...")

        # Run the synchronous update method in a background thread
        # force=False means only download if files don't exist or are expired
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: geoip.update(force=False))

        logger.info("[GeoIP] GeoIP database initialization completed")
    except Exception as e:
        logger.error(f"[GeoIP] GeoIP database initialization failed: {e}")
        # Do not raise an exception to avoid blocking application startup
