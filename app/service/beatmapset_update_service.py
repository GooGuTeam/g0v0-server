import datetime
from datetime import timedelta
from enum import Enum
import math
import random
from typing import TYPE_CHECKING, NamedTuple

from app.config import OldScoreProcessingMode, settings
from app.database.beatmap import Beatmap, BeatmapResp
from app.database.beatmap_sync import BeatmapSync, SavedBeatmapMeta
from app.database.beatmapset import Beatmapset, BeatmapsetResp
from app.database.score import Score
from app.dependencies.database import get_redis, with_db
from app.dependencies.storage import get_storage_service
from app.log import logger
from app.models.beatmap import BeatmapRankStatus
from app.utils import bg_tasks, utcnow

from .beatmapset_cache_service import get_beatmapset_cache_service

from httpx import HTTPError, HTTPStatusError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from app.fetcher import Fetcher


class BeatmapChangeType(Enum):
    MAP_UPDATED = "map_updated"
    MAP_DELETED = "map_deleted"
    MAP_ADDED = "map_added"
    STATUS_CHANGED = "status_changed"


class BeatmapsetChangeType(Enum):
    STATUS_CHANGED = "status_changed"
    HYPE_CHANGED = "hype_changed"
    NOMINATIONS_CHANGED = "nominations_changed"
    RANKED_DATE_CHANGED = "ranked_date_changed"
    PLAYCOUNT_CHANGED = "playcount_changed"


class ChangedBeatmap(NamedTuple):
    beatmap_id: int
    type: BeatmapChangeType


BASE = 1200
TAU = 3600
JITTER_MIN = -30
JITTER_MAX = 30
MIN_DELTA = 1200
GROWTH = 2.0
GRAVEYARD_DOUBLING_PERIOD_DAYS = 30
GRAVEYARD_MAX_DAYS = 365
STATUS_FACTOR: dict[BeatmapRankStatus, float] = {
    BeatmapRankStatus.WIP: 0.5,
    BeatmapRankStatus.PENDING: 0.5,
    BeatmapRankStatus.GRAVEYARD: 1,
}
SCHEDULER_INTERVAL_MINUTES = 2


class ProcessingBeatmapset:
    def __init__(self, beatmapset: BeatmapsetResp, record: BeatmapSync) -> None:
        self.beatmapset = beatmapset
        self.status = BeatmapRankStatus(self.beatmapset.ranked)
        self.record = record

    def calculate_next_sync_time(
        self,
    ) -> timedelta | None:
        if self.status.ranked():
            return None

        now = utcnow()
        if self.status == BeatmapRankStatus.QUALIFIED:
            assert self.beatmapset.ranked_date is not None, "ranked_date should not be None for qualified maps"
            time_to_ranked = (self.beatmapset.ranked_date + timedelta(days=7) - now).total_seconds()
            baseline = max(MIN_DELTA, time_to_ranked / 2)
            next_delta = max(MIN_DELTA, baseline)
        elif self.status in {BeatmapRankStatus.WIP, BeatmapRankStatus.PENDING}:
            seconds_since_update = (now - self.beatmapset.last_updated).total_seconds()
            factor_update = max(1.0, seconds_since_update / TAU)
            factor_play = 1.0 + math.log(1.0 + self.beatmapset.play_count)
            status_factor = STATUS_FACTOR[self.status]
            baseline = BASE * factor_play / factor_update * status_factor
            next_delta = max(MIN_DELTA, baseline * (GROWTH ** (self.record.consecutive_no_change + 1)))
        elif self.status == BeatmapRankStatus.GRAVEYARD:
            days_since_update = (now - self.beatmapset.last_updated).days
            doubling_periods = days_since_update / GRAVEYARD_DOUBLING_PERIOD_DAYS
            delta = MIN_DELTA * (2**doubling_periods)
            max_seconds = GRAVEYARD_MAX_DAYS * 86400
            next_delta = min(max_seconds, delta)
        else:
            next_delta = MIN_DELTA

        if next_delta > 86400:
            minor = round(next_delta / 10)
            jitter = timedelta(seconds=random.randint(-minor, minor))
        else:
            jitter = timedelta(minutes=random.randint(JITTER_MIN, JITTER_MAX))
        return timedelta(seconds=next_delta) + jitter

    @property
    def beatmapset_changed(self) -> bool:
        return self.record.beatmap_status != BeatmapRankStatus(self.beatmapset.ranked)

    @property
    def changed_beatmaps(self) -> list[ChangedBeatmap]:
        changed_beatmaps = []
        for bm in self.beatmapset.beatmaps:
            saved = next((s for s in self.record.beatmaps if s["beatmap_id"] == bm.id), None)
            if not saved or saved["is_deleted"]:
                changed_beatmaps.append(ChangedBeatmap(bm.id, BeatmapChangeType.MAP_ADDED))
            elif saved["md5"] != bm.checksum:
                changed_beatmaps.append(ChangedBeatmap(bm.id, BeatmapChangeType.MAP_UPDATED))
            elif saved["beatmap_status"] != BeatmapRankStatus(bm.ranked):
                changed_beatmaps.append(ChangedBeatmap(bm.id, BeatmapChangeType.STATUS_CHANGED))
        for saved in self.record.beatmaps:
            if not any(bm.id == saved["beatmap_id"] for bm in self.beatmapset.beatmaps) and not saved["is_deleted"]:
                changed_beatmaps.append(ChangedBeatmap(saved["beatmap_id"], BeatmapChangeType.MAP_DELETED))
        return changed_beatmaps


class BeatmapsetUpdateService:
    def __init__(self, fetcher: "Fetcher"):
        self.fetcher = fetcher
        self._adding_missing = False

    async def add_missing_beatmapset(self, beatmapset_id: int, immediate: bool = False) -> bool:
        beatmapset = await self.fetcher.get_beatmapset(beatmapset_id)
        if immediate:
            await self._sync_immediately(beatmapset)
            logger.debug(f"triggered immediate sync for beatmapset {beatmapset_id} ")
            return True
        await self.add(beatmapset)
        logger.debug(f"added missing beatmapset {beatmapset_id} ")
        return True

    async def add_missing_beatmapsets(self):
        if self._adding_missing:
            return
        self._adding_missing = True
        async with with_db() as session:
            missings = await session.exec(
                select(Beatmapset.id)
                .where(
                    col(Beatmapset.beatmap_status).in_(
                        [
                            BeatmapRankStatus.WIP,
                            BeatmapRankStatus.PENDING,
                            BeatmapRankStatus.GRAVEYARD,
                            BeatmapRankStatus.QUALIFIED,
                        ]
                    ),
                    col(Beatmapset.id).notin_(select(BeatmapSync.beatmapset_id)),
                )
                .order_by(col(Beatmapset.last_updated).desc())
            )
            total = 0
            for missing in missings:
                try:
                    if await self.add_missing_beatmapset(missing):
                        total += 1
                except HTTPStatusError as e:
                    if e.response.status_code == 404:
                        logger.opt(colors=True).warning(f"beatmapset {missing} not found (404), skipping")

                        session.add(
                            BeatmapSync(
                                beatmapset_id=missing,
                                beatmap_status=BeatmapRankStatus.GRAVEYARD,
                                next_sync_time=datetime.datetime.max,
                                beatmaps=[],
                            )
                        )
                    else:
                        logger.error(f"failed to add missing beatmapset {missing}: [{e.__class__.__name__}] {e}")
                except Exception as e:
                    logger.error(f"failed to add missing beatmapset {missing}: {e}")
            if total > 0:
                logger.opt(colors=True).info(f"added {total} missing beatmapset")
        self._adding_missing = False

    async def add(self, beatmapset: BeatmapsetResp, calculate_next_sync: bool = True):
        async with with_db() as session:
            sync_record = await session.get(BeatmapSync, beatmapset.id)
            if not sync_record:
                database_beatmapset = await session.get(Beatmapset, beatmapset.id)
                if database_beatmapset:
                    status = BeatmapRankStatus(database_beatmapset.beatmap_status)
                    await database_beatmapset.awaitable_attrs.beatmaps
                    beatmaps = [
                        SavedBeatmapMeta(
                            beatmap_id=bm.id,
                            md5=bm.checksum,
                            is_deleted=False,
                            beatmap_status=BeatmapRankStatus(bm.beatmap_status),
                        )
                        for bm in database_beatmapset.beatmaps
                    ]
                else:
                    status = BeatmapRankStatus(beatmapset.ranked)
                    beatmaps = [
                        SavedBeatmapMeta(
                            beatmap_id=bm.id,
                            md5=bm.checksum,
                            is_deleted=False,
                            beatmap_status=BeatmapRankStatus(bm.ranked),
                        )
                        for bm in beatmapset.beatmaps
                    ]

                sync_record = BeatmapSync(
                    beatmapset_id=beatmapset.id,
                    beatmaps=beatmaps,
                    beatmap_status=status,
                )
                session.add(sync_record)
                await session.commit()
                await session.refresh(sync_record)
            else:
                sync_record.beatmaps = [
                    SavedBeatmapMeta(
                        beatmap_id=bm.id, md5=bm.checksum, is_deleted=False, beatmap_status=BeatmapRankStatus(bm.ranked)
                    )
                    for bm in beatmapset.beatmaps
                ]
                sync_record.beatmap_status = BeatmapRankStatus(beatmapset.ranked)
            if calculate_next_sync:
                processing = ProcessingBeatmapset(beatmapset, sync_record)
                next_time_delta = processing.calculate_next_sync_time()
                if not next_time_delta:
                    # for qualified -> ranked, run immediate sync
                    await BeatmapsetUpdateService._sync_immediately(self, beatmapset)
                    return
                sync_record.next_sync_time = utcnow() + next_time_delta
            logger.opt(colors=True).info(f"<g>[{beatmapset.id}]</g> next sync at {sync_record.next_sync_time}")
            await session.commit()

    async def _sync_immediately(self, beatmapset: BeatmapsetResp) -> None:
        async with with_db() as session:
            record = await session.get(BeatmapSync, beatmapset.id)
            if not record:
                record = BeatmapSync(
                    beatmapset_id=beatmapset.id,
                    beatmaps=[],
                    beatmap_status=BeatmapRankStatus(beatmapset.ranked),
                )
                session.add(record)
                await session.commit()
                await session.refresh(record)
            await self.sync(record, session, beatmapset=beatmapset)
            await session.commit()

    async def sync(
        self,
        record: BeatmapSync,
        session: AsyncSession,
        *,
        beatmapset: BeatmapsetResp | None = None,
    ):
        logger.opt(colors=True).info(f"<g>[{record.beatmapset_id}]</g> syncing...")
        if beatmapset is None:
            try:
                beatmapset = await self.fetcher.get_beatmapset(record.beatmapset_id)
            except Exception as e:
                if isinstance(e, HTTPStatusError) and e.response.status_code == 404:
                    logger.opt(colors=True).warning(
                        f"<g>[{record.beatmapset_id}]</g> beatmapset not found (404), removing from sync list"
                    )
                    await session.delete(record)
                    await session.commit()
                    return
                if isinstance(e, HTTPError):
                    logger.opt(colors=True).warning(
                        f"<g>[{record.beatmapset_id}]</g> "
                        f"failed to fetch beatmapset: [{e.__class__.__name__}] {e}, retrying later"
                    )
                else:
                    logger.opt(colors=True).exception(
                        f"<g>[{record.beatmapset_id}]</g> unexpected error: {e}, retrying later"
                    )
                record.next_sync_time = utcnow() + timedelta(seconds=MIN_DELTA)
                return
        processing = ProcessingBeatmapset(beatmapset, record)
        changed_beatmaps = processing.changed_beatmaps
        changed = processing.beatmapset_changed or changed_beatmaps
        if changed:
            record.beatmaps = [
                SavedBeatmapMeta(
                    beatmap_id=bm.id,
                    md5=bm.checksum,
                    is_deleted=False,
                    beatmap_status=BeatmapRankStatus(bm.ranked),
                )
                for bm in beatmapset.beatmaps
            ]
            record.beatmap_status = BeatmapRankStatus(beatmapset.ranked)
            record.consecutive_no_change = 0

            bg_tasks.add_task(
                self._process_changed_beatmaps,
                changed_beatmaps,
                beatmapset.beatmaps,
            )
            bg_tasks.add_task(
                self._process_changed_beatmapset,
                beatmapset,
            )
        else:
            record.consecutive_no_change += 1

        next_time_delta = processing.calculate_next_sync_time()
        if not next_time_delta:
            logger.opt(colors=True).info(
                f"<yellow>[{beatmapset.id}]</yellow> beatmapset has transformed to ranked or loved,"
                f" removing from sync list"
            )
            await session.delete(record)
        else:
            record.next_sync_time = utcnow() + next_time_delta
            logger.opt(colors=True).info(f"<g>[{record.beatmapset_id}]</g> next sync at {record.next_sync_time}")

    async def _update_beatmaps(self):
        async with with_db() as session:
            logger.info("checking for beatmapset updates...")
            now = utcnow()
            records = await session.exec(
                select(BeatmapSync)
                .where(BeatmapSync.next_sync_time <= now)
                .order_by(col(BeatmapSync.next_sync_time).desc())
            )
            for record in records:
                await self.sync(record, session)
            await session.commit()

    async def _process_changed_beatmapset(self, beatmapset: BeatmapsetResp):
        async with with_db() as session:
            db_beatmapset = await session.get(Beatmapset, beatmapset.id)
            new_beatmapset = await Beatmapset.from_resp_no_save(beatmapset)
            if db_beatmapset:
                await session.merge(new_beatmapset)
            await get_beatmapset_cache_service(get_redis()).invalidate_beatmapset_cache(beatmapset.id)
            await session.commit()

    async def _process_changed_beatmaps(self, changed: list[ChangedBeatmap], beatmaps_list: list[BeatmapResp]):
        storage_service = get_storage_service()
        beatmaps = {bm.id: bm for bm in beatmaps_list}

        async with with_db() as session:

            async def _process_update_or_delete_beatmaps(beatmap_id: int):
                scores = await session.exec(select(Score).where(Score.beatmap_id == beatmap_id))
                total = 0
                for score in scores:
                    if settings.old_score_processing_mode == OldScoreProcessingMode.STRICT:
                        await score.delete(session, storage_service)
                    elif settings.old_score_processing_mode == OldScoreProcessingMode.NORMAL:
                        if await score.awaitable_attrs.best_score:
                            assert score.best_score is not None
                            await score.best_score.delete(session)
                        if await score.awaitable_attrs.ranked_score:
                            assert score.ranked_score is not None
                            await score.ranked_score.delete(session)
                    total += 1
                if total > 0:
                    logger.opt(colors=True).info(f"<g>[beatmap: {beatmap_id}]</g> processed {total} old scores")
                await session.commit()

            for change in changed:
                if change.type == BeatmapChangeType.MAP_ADDED:
                    beatmap = beatmaps.get(change.beatmap_id)
                    if not beatmap:
                        logger.opt(colors=True).warning(
                            f"<g>[beatmap: {change.beatmap_id}]</g> beatmap data not found in beatmapset, skipping"
                        )
                        continue
                    logger.opt(colors=True).info(
                        f"<g>[{beatmap.beatmapset_id}]</g> adding beatmap <blue>{beatmap.id}</blue>"
                    )
                    await Beatmap.from_resp_no_save(session, beatmap)
                else:
                    beatmap = beatmaps.get(change.beatmap_id)
                    if not beatmap:
                        logger.opt(colors=True).warning(
                            f"<g>[beatmap: {change.beatmap_id}]</g> beatmap data not found in beatmapset, skipping"
                        )
                        continue
                    logger.opt(colors=True).info(
                        f"<g>[{beatmap.beatmapset_id}]</g> processing beatmap <blue>{beatmap.id}</blue> "
                        f"change <cyan>{change.type}</cyan>"
                    )
                    new_db_beatmap = await Beatmap.from_resp_no_save(session, beatmap)
                    existing_beatmap = await session.get(Beatmap, change.beatmap_id)
                    if existing_beatmap:
                        await session.merge(new_db_beatmap)
                        await session.commit()
                    if change.type != BeatmapChangeType.STATUS_CHANGED:
                        await _process_update_or_delete_beatmaps(change.beatmap_id)
                await get_beatmapset_cache_service(get_redis()).invalidate_beatmap_lookup_cache(change.beatmap_id)


service: BeatmapsetUpdateService | None = None


def init_beatmapset_update_service(fetcher: "Fetcher") -> BeatmapsetUpdateService:
    global service
    if service is None:
        service = BeatmapsetUpdateService(fetcher)
    if settings.enable_auto_beatmap_sync:
        bg_tasks.add_task(service.add_missing_beatmapsets)
    return service


def get_beatmapset_update_service() -> BeatmapsetUpdateService:
    if service is None:
        raise ValueError("BeatmapsetUpdateService is not initialized")
    assert service is not None, "BeatmapsetUpdateService is not initialized"
    return service
