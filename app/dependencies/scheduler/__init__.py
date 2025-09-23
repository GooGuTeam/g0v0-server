from __future__ import annotations

from datetime import UTC
from typing import cast

from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler: AsyncIOScheduler | None = None


def init_scheduler():
    global scheduler
    scheduler = AsyncIOScheduler(timezone=UTC)


def get_scheduler() -> AsyncIOScheduler:
    global scheduler
    if scheduler is None:
        init_scheduler()
    return cast(AsyncIOScheduler, scheduler)


def start_scheduler():
    global scheduler
    if scheduler is not None:
        scheduler.start()


def stop_scheduler():
    global scheduler
    if scheduler:
        scheduler.shutdown()
