"""Client version update scheduled task.

Provides periodic updates of valid client version lists used
for client verification during authentication.
"""

from app.config import settings
from app.dependencies.scheduler import get_scheduler
from app.log import logger
from app.service.client_verification_service import get_client_verification_service

if settings.check_client_version:

    @get_scheduler().scheduled_job("interval", id="update_client_version", hours=2)
    async def update_client_version() -> None:
        """Update client version lists from remote sources.

        Runs every 2 hours to fetch latest valid client versions
        and reload them into memory.
        """
        logger.info("Updating client version lists...")
        client_verification_service = get_client_verification_service()
        await client_verification_service.refresh()
        await client_verification_service.load_from_disk()
