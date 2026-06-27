from datetime import UTC
from typing import cast

from app.log import system_logger

from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler: AsyncIOScheduler | None = None
logger = system_logger("Scheduler")


def init_scheduler():
    global scheduler
    scheduler = AsyncIOScheduler(timezone=UTC)
    logger.info("AsyncIO scheduler initialized")


def get_scheduler() -> AsyncIOScheduler:
    global scheduler
    if scheduler is None:
        init_scheduler()
    return cast(AsyncIOScheduler, scheduler)


def start_scheduler():
    global scheduler
    if scheduler is not None:
        scheduler.start()
        logger.info(f"AsyncIO scheduler started with {len(scheduler.get_jobs())} job(s)")
    else:
        logger.warning("Scheduler start requested before initialization")


def stop_scheduler():
    global scheduler
    if scheduler:
        scheduler.shutdown()
        logger.info("AsyncIO scheduler stopped")
