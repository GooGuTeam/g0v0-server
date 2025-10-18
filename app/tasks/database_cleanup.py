from app.dependencies.database import with_db
from app.dependencies.scheduler import get_scheduler
from app.log import logger
from app.service.database_cleanup_service import DatabaseCleanupService


@get_scheduler().scheduled_job(
    "interval",
    id="cleanup_database",
    hours=1,
)
async def scheduled_cleanup_job():
    async with with_db() as session:
        logger.info("Starting database cleanup...")
        results = await DatabaseCleanupService.run_full_cleanup(session)
        total = sum(results.values())
        if total > 0:
            logger.success(f"Cleanup completed, total records cleaned: {total}")
        return results
