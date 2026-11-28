"""Serialize same-user presence notices that arrive on concurrent background tasks."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from app.helpers.time import utcnow
from app.log import log

logger = log("UserPresenceMailbox")

ApplyPresence = Callable[[int, bool | None, datetime], Awaitable[None]]

_presence_queues: dict[int, asyncio.Queue[tuple[bool | None, datetime, ApplyPresence]]] = {}
_presence_workers: dict[int, asyncio.Task[None]] = {}
_presence_worker_lock = asyncio.Lock()


def parse_presence_event_time(raw: object) -> datetime:
    """Parse a presence event timestamp, falling back to now when it is missing or invalid."""
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=UTC)
        return raw.astimezone(UTC)
    if isinstance(raw, int | float):
        return datetime.fromtimestamp(float(raw), tz=UTC)
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return utcnow()
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return utcnow()


def is_stale_presence(existing_last_visit: datetime | None, event_time: datetime) -> bool:
    """Return whether a presence event is older than the last persisted last_visit."""
    if existing_last_visit is None:
        return False
    existing = (
        existing_last_visit if existing_last_visit.tzinfo is not None else existing_last_visit.replace(tzinfo=UTC)
    )
    return event_time < existing


def reset_presence_mailbox_for_tests() -> None:
    """Clear mailbox state between tests. Cancels any leftover workers."""
    for worker in _presence_workers.values():
        worker.cancel()
    _presence_workers.clear()
    _presence_queues.clear()


async def enqueue_user_presence(
    user_id: int,
    is_online: bool | None,
    event_time: datetime,
    apply: ApplyPresence,
) -> None:
    """Queue a presence notice so same-user updates are applied in receive order."""
    async with _presence_worker_lock:
        queue = _presence_queues.get(user_id)
        if queue is None:
            queue = asyncio.Queue()
            _presence_queues[user_id] = queue
        queue.put_nowait((is_online, event_time, apply))
        worker = _presence_workers.get(user_id)
        if worker is None or worker.done():
            _presence_workers[user_id] = asyncio.create_task(_run_presence_worker(user_id))


async def _run_presence_worker(user_id: int) -> None:
    try:
        while True:
            async with _presence_worker_lock:
                queue = _presence_queues.get(user_id)
                if queue is None or queue.empty():
                    _presence_workers.pop(user_id, None)
                    _presence_queues.pop(user_id, None)
                    return
                is_online, event_time, apply = queue.get_nowait()
            try:
                await apply(user_id, is_online, event_time)
            except Exception:
                logger.exception("Error applying presence update for user_id: {}", user_id)
    except Exception:
        logger.exception("Presence worker failed for user_id: {}", user_id)
        async with _presence_worker_lock:
            _presence_workers.pop(user_id, None)
            queue = _presence_queues.get(user_id)
            if queue is not None and not queue.empty():
                _presence_workers[user_id] = asyncio.create_task(_run_presence_worker(user_id))
