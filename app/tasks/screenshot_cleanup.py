"""Screenshot cleanup scheduled task.

Removes screenshots that were never accessed and outlived the retention window.
"""

from app.config import settings
from app.dependencies.database import with_db
from app.dependencies.scheduler import get_scheduler
from app.dependencies.storage import get_storage_service
from app.service.screenshot_service import ScreenshotService

if settings.enable_screenshot_cleanup:

    @get_scheduler().scheduled_job(
        "interval",
        id="cleanup_screenshots",
        hours=1,
    )
    async def screenshot_cleanup_job() -> dict[str, int]:
        """Delete screenshots that were never accessed and outlived the retention window.

        Returns:
            Counters of deleted records, deleted objects and failed deletions.
        """
        storage = get_storage_service()
        async with with_db() as session:
            return await ScreenshotService.cleanup_unused_screenshots(
                session,
                storage,
                settings.screenshot_retention_days,
                settings.screenshot_cleanup_batch_size,
            )
