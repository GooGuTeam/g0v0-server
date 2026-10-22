"""Run with: uv run python -m unittest tests.test_screenshot_service -v."""

# Use the standard-library runner; the repository has no pytest dependency.
# ruff: noqa: PT009, PT027

from datetime import timedelta
import hashlib
from io import BytesIO
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock

from app.database import Screenshot
from app.helpers import utcnow
from app.models.error import ErrorType, RequestError
from app.service.screenshot_service import ScreenshotService, canonical_storage_path
from app.storage import StorageService

from PIL import Image
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

MAX_BYTES = 10 * 1024 * 1024


def image_bytes(image_format: str = "JPEG") -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format=image_format)
    return output.getvalue()


class ScreenshotServiceTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite://")
        async with self.engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.tables["screenshots"].create(sync_connection)
            )
        # Keep the application's default expire_on_commit=True.
        self.db = AsyncSession(self.engine)
        self.storage = MagicMock(spec=StorageService)
        self.objects = {}

        async def write(path, content, content_type):
            self.assertEqual(content_type, "image/jpeg")
            self.objects[path] = content

        async def remove(path):
            self.objects.pop(path, None)

        self.storage.write_file.side_effect = write
        self.storage.delete_file.side_effect = remove
        self.storage.get_file_url.side_effect = lambda path: f"https://storage.example/{path}"

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def rows(self):
        return (await self.db.exec(select(Screenshot))).all()

    async def assert_rejected(self, content, error_type):
        with self.assertRaises(RequestError) as raised:
            await ScreenshotService.record_upload(self.db, self.storage, 1, content)
        self.assertEqual(raised.exception.msg_key, error_type.value[0])
        self.storage.write_file.assert_not_awaited()
        self.storage.get_file_url.assert_not_awaited()
        self.assertEqual(list(await self.rows()), [])

    async def test_direct_service_accepts_jpeg_and_hashes_exact_stored_bytes(self):
        content = image_bytes()
        digest = await ScreenshotService.record_upload(self.db, self.storage, 1, content)
        self.assertEqual(digest, hashlib.sha256(content).hexdigest())
        self.assertEqual(self.objects, {canonical_storage_path(1, digest): content})
        records = await self.rows()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].sha256_hash, digest)

    async def test_direct_service_rejects_png_content(self):
        await self.assert_rejected(image_bytes("PNG"), ErrorType.INVALID_IMAGE_FORMAT)

    async def test_direct_service_rejects_non_image_content(self):
        await self.assert_rejected(b"not a JPEG", ErrorType.ERROR_PROCESSING_IMAGE)

    async def test_direct_service_rejects_empty_content(self):
        await self.assert_rejected(b"", ErrorType.FILE_EMPTY)

    async def test_size_boundaries(self):
        jpeg = image_bytes()
        # Pillow accepts JPEGs with trailing padding; size counts every byte.
        for size in (MAX_BYTES - 1, MAX_BYTES):
            with self.subTest(size=size):
                content = jpeg + b"\0" * (size - len(jpeg))
                digest = await ScreenshotService.record_upload(self.db, self.storage, 1, content)
                self.assertEqual(self.objects[canonical_storage_path(1, digest)], content)
        before = len(await self.rows())
        self.storage.write_file.reset_mock()
        self.storage.get_file_url.reset_mock()
        with self.assertRaises(RequestError) as raised:
            await ScreenshotService.record_upload(self.db, self.storage, 1, jpeg + b"\0" * (MAX_BYTES + 1 - len(jpeg)))
        self.assertEqual(raised.exception.msg_key, ErrorType.FILE_SIZE_EXCEEDS_LIMIT.value[0])
        self.storage.write_file.assert_not_awaited()
        self.storage.get_file_url.assert_not_awaited()
        self.assertEqual(len(await self.rows()), before)

    async def test_sequential_duplicate_refreshes_one_row_preserving_hits(self):
        content = image_bytes()
        digest = await ScreenshotService.record_upload(self.db, self.storage, 1, content)
        record = (await self.rows())[0]
        record_id = record.id
        old = utcnow() - timedelta(days=90)
        record.hits = 7
        record.timestamp = old
        record.last_access = old
        self.db.add(record)
        await self.db.commit()
        self.storage.get_file_url.return_value = "https://storage.example/renewed"
        self.storage.get_file_url.side_effect = None
        self.assertEqual(await ScreenshotService.record_upload(self.db, self.storage, 1, content), digest)
        records = await self.rows()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, record_id)
        self.assertEqual(records[0].hits, 7)
        self.assertGreater(records[0].timestamp.replace(tzinfo=old.tzinfo), old)
        self.assertEqual(records[0].last_access.replace(tzinfo=old.tzinfo), old)
        self.assertEqual(records[0].url, "https://storage.example/renewed")

    async def test_same_bytes_from_different_owners_have_distinct_objects(self):
        content = image_bytes()
        digest = await ScreenshotService.record_upload(self.db, self.storage, 1, content)
        await ScreenshotService.record_upload(self.db, self.storage, 2, content)
        self.assertEqual(len(await self.rows()), 2)
        self.assertEqual(set(self.objects), {canonical_storage_path(1, digest), canonical_storage_path(2, digest)})

    async def test_delete_other_owners_record_is_not_found(self):
        await ScreenshotService.record_upload(self.db, self.storage, 1, image_bytes())
        record_id = (await self.rows())[0].id
        with self.assertRaises(RequestError) as raised:
            await ScreenshotService.delete_screenshot(self.db, self.storage, record_id, 2)
        self.assertEqual(raised.exception.msg_key, ErrorType.SCREENSHOT_NOT_FOUND.value[0])
        self.storage.delete_file.assert_not_awaited()
        self.assertEqual(len(await self.rows()), 1)

    async def test_legacy_duplicate_keeps_object_until_last_reference(self):
        digest = await ScreenshotService.record_upload(self.db, self.storage, 1, image_bytes())
        first = (await self.rows())[0]
        first_id = first.id
        self.db.add(
            Screenshot(
                user_id=1,
                sha256_hash=digest,
                url="https://untrusted.example/other-object",
                timestamp=utcnow(),
                last_access=utcnow(),
            )
        )
        await self.db.commit()
        await ScreenshotService.delete_screenshot(self.db, self.storage, first_id, 1)
        self.storage.delete_file.assert_not_awaited()
        remaining = await self.rows()
        self.assertEqual(len(remaining), 1)
        await ScreenshotService.delete_screenshot(self.db, self.storage, remaining[0].id, 1)
        self.storage.delete_file.assert_awaited_once_with(canonical_storage_path(1, digest))
        self.assertEqual(self.objects, {})

    async def test_cleanup_keeps_accessed_recent_and_null_timestamp_records(self):
        old = utcnow() - timedelta(days=90)
        for index, (hits, timestamp) in enumerate(((0, old), (1, old), (0, utcnow()), (0, None))):
            self.db.add(
                Screenshot(
                    user_id=1,
                    sha256_hash=f"{index:064x}",
                    url="unused",
                    hits=hits,
                    timestamp=timestamp,  # pyright: ignore[reportArgumentType] -- legacy nullable DB column
                    last_access=old,
                )
            )
        await self.db.commit()
        result = await ScreenshotService.cleanup_unused_screenshots(self.db, self.storage, 30, batch_size=1)
        self.assertEqual(result, {"rows": 1, "objects": 1, "object_failures": 0})
        self.assertEqual(len(await self.rows()), 3)
