"""Beatmapset update scheduled task.

Provides automatic beatmapset synchronization with upstream osu! API.
Runs periodically to fetch and update beatmap data.
"""

from datetime import datetime, timedelta

from app.config import settings
from app.dependencies.scheduler import get_scheduler
from app.helpers import bg_tasks
from app.service.beatmapset_update_service import get_beatmapset_update_service

if settings.enable_auto_beatmap_sync:

    @get_scheduler().scheduled_job(
        "interval",
        id="update_beatmaps",
        minutes=settings.beatmap_sync_interval_minutes,
        next_run_time=datetime.now() + timedelta(minutes=1),
    )
    async def beatmapset_update_job() -> None:
        """Scheduled job to update beatmaps from upstream.

        Adds missing beatmapsets as a background task and
        synchronously updates existing beatmaps.
        """
        service = get_beatmapset_update_service()
        bg_tasks.add_task(service.add_missing_beatmapsets)
        await service._update_beatmaps()
