"""Screenshot storage and lifecycle service.

Provides the shared storage-key helpers, deduplicated upload persistence, user
initiated deletion and the periodic cleanup of unused screenshots.
"""

from datetime import timedelta
import hashlib

from app.database import Screenshot
from app.helpers import utcnow
from app.log import service_logger
from app.models.error import ErrorType, RequestError
from app.storage import StorageService as OriginStorageService

from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = service_logger("ScreenshotService")

SCREENSHOT_STORAGE_PREFIX = "screenshots/"

DEFAULT_CLEANUP_BATCH_SIZE = 200
MAX_CLEANUP_BATCHES = 50


def canonical_storage_path(user_id: int, sha256_hash: str) -> str:
    """Build the deterministic storage key used by the upload endpoint.

    Args:
        user_id: Owner of the screenshot.
        sha256_hash: SHA-256 hash of the uploaded image.

    Returns:
        The storage key of the screenshot.
    """
    return f"{SCREENSHOT_STORAGE_PREFIX}{user_id}_{sha256_hash}.jpg"


def resolve_storage_path(storage: OriginStorageService, screenshot: Screenshot) -> str:
    """Resolve the storage key of a screenshot record.

    The URL stored in the database may be a presigned URL that cannot be parsed
    back into a storage key, so the parsed value is only trusted when it stays
    inside the screenshot prefix. Otherwise the deterministic key used at upload
    time is returned.

    Args:
        storage: The storage service.
        screenshot: The screenshot record.

    Returns:
        The storage key of the record.
    """
    parsed = storage.get_file_name_by_url(screenshot.url)
    if parsed and parsed.startswith(SCREENSHOT_STORAGE_PREFIX) and ".." not in parsed:
        return parsed
    return canonical_storage_path(screenshot.user_id, screenshot.sha256_hash)


class ScreenshotService:
    """Business logic for screenshot records and their stored objects."""

    @staticmethod
    async def record_upload(
        db: AsyncSession,
        storage: OriginStorageService,
        user_id: int,
        content: bytes,
    ) -> str:
        """Store a submitted screenshot, reusing an existing record.

        Uploading the same image twice by the same user shares one storage
        object, so the existing record is refreshed instead of duplicated: the
        URL is renewed (presigned links may have expired) and the upload time is
        reset, while the hit counters are preserved.

        The checksum is derived from the exact bytes being stored, so the
        record can never reference a key that does not match its content.

        Args:
            db: Database session.
            storage: Storage service.
            user_id: ID of the uploading user.
            content: Raw JPEG bytes.

        Returns:
            The SHA-256 hex digest of the stored image.
        """
        sha256_hash = hashlib.sha256(content).hexdigest()
        file_path = canonical_storage_path(user_id, sha256_hash)
        await storage.write_file(file_path, content, "image/jpeg")
        url = await storage.get_file_url(file_path)
        now = utcnow()

        existing = (
            await db.exec(
                select(Screenshot)
                .where(col(Screenshot.user_id) == user_id, col(Screenshot.sha256_hash) == sha256_hash)
                .order_by(col(Screenshot.id).asc())
                .limit(1)
            )
        ).first()

        if existing is not None:
            existing.url = url
            existing.timestamp = now
            db.add(existing)
        else:
            db.add(
                Screenshot(
                    sha256_hash=sha256_hash,
                    user_id=user_id,
                    timestamp=now,
                    last_access=now,
                    url=url,
                )
            )
        await db.commit()
        return sha256_hash

    @staticmethod
    async def delete_screenshot(
        db: AsyncSession,
        storage: OriginStorageService,
        screenshot_id: int,
        user_id: int,
    ) -> None:
        """Delete one screenshot owned by the requesting user.

        Records owned by other users are reported as not found so that IDs
        cannot be enumerated. The record is removed before its object: a leaked
        object is harmless, while a record pointing to a missing object is a
        broken share link.

        Args:
            db: Database session.
            storage: Storage service.
            screenshot_id: Screenshot record ID.
            user_id: ID of the requesting user.

        Raises:
            RequestError: If the record does not exist or belongs to another user.
        """
        screenshot = await db.get(Screenshot, screenshot_id)
        if screenshot is None or screenshot.user_id != user_id:
            raise RequestError(ErrorType.SCREENSHOT_NOT_FOUND)

        owner_id = screenshot.user_id
        sha256_hash = screenshot.sha256_hash
        await db.delete(screenshot)
        await db.commit()

        # Destructive operations never trust the URL stored in the database, it
        # may be a presigned link. Only the deterministic, user scoped key is
        # ever deleted.
        if not sha256_hash:
            logger.warning(f"Screenshot {screenshot_id} has no hash, skipping object deletion")
            return

        remaining = await ScreenshotService._count_references(db, {(owner_id, sha256_hash)})
        if remaining.get((owner_id, sha256_hash), 0) > 0:
            return

        await ScreenshotService._delete_object(storage, canonical_storage_path(owner_id, sha256_hash), screenshot_id)

    @staticmethod
    async def cleanup_unused_screenshots(
        db: AsyncSession,
        storage: OriginStorageService,
        retention_days: int,
        batch_size: int = DEFAULT_CLEANUP_BATCH_SIZE,
        max_batches: int = MAX_CLEANUP_BATCHES,
    ) -> dict[str, int]:
        """Delete screenshots that were never accessed and outlived the retention window.

        A record is eligible when it has no hits and its upload time is older
        than ``now - retention_days``. Records without an upload time are kept
        because they cannot be proven expired. Objects are removed once no
        record references the same ``(user_id, sha256_hash)`` key anymore.

        Args:
            db: Database session.
            storage: Storage service.
            retention_days: Retention window in days.
            batch_size: Number of records deleted per transaction.
            max_batches: Upper bound of batches per run.

        Returns:
            Counters of deleted records, deleted objects and failed deletions.
        """
        cutoff = utcnow() - timedelta(days=retention_days)
        deleted_rows = 0
        deleted_objects = 0
        failed_objects = 0

        for _ in range(max_batches):
            records = (
                await db.exec(
                    select(Screenshot)
                    .where(
                        col(Screenshot.hits) == 0,
                        col(Screenshot.timestamp).is_not(None),
                        col(Screenshot.timestamp) < cutoff,
                    )
                    .order_by(col(Screenshot.timestamp).asc(), col(Screenshot.id).asc())
                    .limit(batch_size)
                )
            ).all()
            if not records:
                break

            candidates = {(record.user_id, record.sha256_hash) for record in records if record.sha256_hash}
            for record in records:
                await db.delete(record)
            await db.commit()
            deleted_rows += len(records)

            remaining = await ScreenshotService._count_references(db, candidates)
            for owner_id, sha256_hash in candidates:
                if remaining.get((owner_id, sha256_hash), 0) > 0:
                    continue
                path = canonical_storage_path(owner_id, sha256_hash)
                if await ScreenshotService._delete_object(storage, path, None):
                    deleted_objects += 1
                else:
                    failed_objects += 1

            if len(records) < batch_size:
                break

        if deleted_rows or failed_objects:
            logger.info(
                f"Unused screenshot cleanup removed {deleted_rows} records and "
                f"{deleted_objects} objects ({failed_objects} object failures)"
            )
        return {"rows": deleted_rows, "objects": deleted_objects, "object_failures": failed_objects}

    @staticmethod
    async def _count_references(
        db: AsyncSession,
        keys: set[tuple[int, str]],
    ) -> dict[tuple[int, str], int]:
        """Count remaining records per screenshot storage key.

        Args:
            db: Database session.
            keys: ``(user_id, sha256_hash)`` keys to check.

        Returns:
            Mapping of storage key to the number of remaining records.
        """
        if not keys:
            return {}
        user_ids = {key[0] for key in keys}
        hashes = {key[1] for key in keys}
        rows = (
            await db.exec(
                select(Screenshot.user_id, Screenshot.sha256_hash, func.count())
                .where(col(Screenshot.user_id).in_(user_ids), col(Screenshot.sha256_hash).in_(hashes))
                .group_by(col(Screenshot.user_id), col(Screenshot.sha256_hash))
            )
        ).all()
        return {(row[0], row[1]): int(row[2]) for row in rows}

    @staticmethod
    async def _delete_object(
        storage: OriginStorageService,
        path: str,
        screenshot_id: int | None,
    ) -> bool:
        """Delete a stored screenshot object, logging failures instead of raising.

        Args:
            storage: Storage service.
            path: Storage key of the object.
            screenshot_id: Record ID the deletion originates from, if any.

        Returns:
            Whether the object was deleted without error.
        """
        try:
            await storage.delete_file(path)
        except Exception as e:
            source = f"record {screenshot_id}" if screenshot_id is not None else "cleanup"
            logger.error(f"Failed to delete screenshot object {path} ({source}): {e!s}")
            return False
        return True
