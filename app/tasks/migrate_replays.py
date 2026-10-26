"""Finish replay filename changes queued by the BanchoBot ID migration."""

import asyncio
import json

from app.database import Score
from app.dependencies.database import with_db
from app.dependencies.storage import get_storage_service
from app.log import task_logger
from app.path import CONFIG_DIR
from app.storage.base import StorageService

from sqlmodel import col, select

logger = task_logger("MigrateReplays")


async def _rename_replay(storage: StorageService, source: str, destination: str) -> None:
    if source == destination:
        return
    if await storage.is_exists(destination):
        if await storage.is_exists(source):
            # A failed object-storage deletion can leave both copies behind.
            if await storage.read_file(source) != await storage.read_file(destination):
                raise RuntimeError(f"Replay destination already contains different data: {destination}")
            await storage.delete_file(source)
        return
    await storage.rename_file(source, destination)


async def migrate_replays() -> None:
    """Rename queued replays before accepting requests, retaining the marker on failure."""
    pending = CONFIG_DIR / "new_user_id"
    try:
        raw = await asyncio.to_thread(pending.read_text, encoding="utf-8")
    except FileNotFoundError:
        return
    data = json.loads(raw)
    if not isinstance(data, dict) or not all(
        key.isdecimal() and str(int(key)) == key and int(key) > 0 and type(value) is int and value > 0
        for key, value in data.items()
    ):
        raise ValueError(f"Invalid user ID mapping in {pending}")
    mapping = {int(old): new for old, new in data.items()}
    if len(set(mapping.values())) != len(mapping):
        raise ValueError(f"Duplicate destination user IDs in {pending}")
    previous_ids = {new: old for old, new in mapping.items()}
    storage = get_storage_service()
    migrated = 0
    last_score_id = 0
    logger.info(f"Migrating replay filenames for user ID mappings: {mapping}")
    try:
        async with with_db() as session:
            while previous_ids:
                # Page through scalar fields so large accounts do not load all
                # scores or their relationships into memory at startup.
                scores = (
                    await session.exec(
                        select(Score.id, Score.beatmap_id, Score.user_id, Score.type)
                        .where(
                            col(Score.user_id).in_(previous_ids),
                            col(Score.has_replay).is_(True),
                            Score.id > last_score_id,
                        )
                        .order_by(col(Score.id))
                        .limit(100)
                    )
                ).all()
                if not scores:
                    break
                for score_id, beatmap_id, user_id, score_type in scores:
                    old = previous_ids[user_id]
                    source = f"replays/{score_id}_{beatmap_id}_{old}_{score_type}_replay.osr"
                    destination = f"replays/{score_id}_{beatmap_id}_{user_id}_{score_type}_replay.osr"
                    await _rename_replay(storage, source, destination)
                    migrated += 1
                last_score_id = scores[-1][0]
        await asyncio.to_thread(pending.unlink, missing_ok=True)
    except Exception:
        logger.exception(f"Replay migration failed; keeping {pending} for retry")
        raise
    logger.success(f"Replay migration completed for {migrated} replay(s); removed {pending}")
