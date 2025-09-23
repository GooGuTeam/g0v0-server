"""调度器包入口。"""

from __future__ import annotations

from app.scheduler.cache import start_cache_scheduler, stop_cache_scheduler
from app.scheduler.maintenance import (
    run_manual_database_cleanup,
    start_database_cleanup_scheduler,
    stop_database_cleanup_scheduler,
)
from app.scheduler.user import (
    schedule_user_cache_cleanup_task,
    schedule_user_cache_preload_task,
    schedule_user_cache_warmup_task,
)

__all__ = [
    "run_manual_database_cleanup",
    "schedule_user_cache_cleanup_task",
    "schedule_user_cache_preload_task",
    "schedule_user_cache_warmup_task",
    "start_cache_scheduler",
    "start_database_cleanup_scheduler",
    "stop_cache_scheduler",
    "stop_database_cleanup_scheduler",
]
