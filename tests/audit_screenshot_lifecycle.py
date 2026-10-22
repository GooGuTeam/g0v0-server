"""Deterministic characterizations of unresolved lifecycle bugs, NOT fixes.

Run: uv run python -m unittest tests.audit_screenshot_lifecycle -v
Passing tests here reproduce the broken outcomes described in the review.
SQLite verifies application sequencing, not MySQL isolation or distributed locks.
"""

# ruff: noqa: PT009

from datetime import timedelta
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from app.database import Screenshot
from app.helpers import utcnow
from app.service.screenshot_service import ScreenshotService, canonical_storage_path

from . import test_screenshot_service as fixtures
from .test_screenshot_service import image_bytes

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession


class LifecycleAudit(IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ScreenshotServiceTests.asyncSetUp
    asyncTearDown = fixtures.ScreenshotServiceTests.asyncTearDown
    rows = fixtures.ScreenshotServiceTests.rows

    async def test_reupload_after_zero_count_leaves_live_row_without_object(self):
        content = image_bytes()
        digest = await ScreenshotService.record_upload(self.db, self.storage, 1, content)
        record_id = (await self.rows())[0].id

        async def remove_after_reupload(path):
            # delete_screenshot has already counted zero references. A second
            # request finishes uploading before the pending object delete.
            async with AsyncSession(self.engine) as other:
                await ScreenshotService.record_upload(other, self.storage, 1, content)
            self.objects.pop(path, None)

        self.storage.delete_file.side_effect = remove_after_reupload
        await ScreenshotService.delete_screenshot(self.db, self.storage, record_id, 1)
        self.db.expire_all()
        self.assertEqual(len(await self.rows()), 1)
        self.assertNotIn(canonical_storage_path(1, digest), self.objects)

    async def test_cleanup_deletes_record_that_got_accessed_after_scan(self):
        await self._cleanup_after_change(accessed=True)

    async def test_cleanup_deletes_record_that_got_refreshed_after_scan(self):
        await self._cleanup_after_change(accessed=False)

    async def _cleanup_after_change(self, *, accessed):
        content = image_bytes()
        await ScreenshotService.record_upload(self.db, self.storage, 1, content)
        record = (await self.rows())[0]
        record_id = record.id
        record.timestamp = utcnow() - timedelta(days=90)
        self.db.add(record)
        await self.db.commit()
        original_exec = self.db.exec
        changed = False

        async def exec_after_change(statement, *args, **kwargs):
            nonlocal changed
            result = await original_exec(statement, *args, **kwargs)
            if not changed:
                changed = True
                # The cleanup SELECT has already materialized its candidates.
                async with AsyncSession(self.engine) as other:
                    if accessed:
                        current = await other.get(Screenshot, record_id)
                        current.hits += 1
                        current.last_access = utcnow()
                        other.add(current)
                        await other.commit()
                    else:
                        await ScreenshotService.record_upload(other, self.storage, 1, content)
            return result

        with patch.object(self.db, "exec", side_effect=exec_after_change):
            result = await ScreenshotService.cleanup_unused_screenshots(self.db, self.storage, 30)
        self.assertEqual(result["rows"], 1)
        self.assertEqual(list(await self.rows()), [])

    async def test_failed_object_delete_has_no_retry_candidate(self):
        await ScreenshotService.record_upload(self.db, self.storage, 1, image_bytes())
        record_id = (await self.rows())[0].id
        self.storage.delete_file.side_effect = RuntimeError("temporary storage failure")
        await ScreenshotService.delete_screenshot(self.db, self.storage, record_id, 1)
        self.assertEqual(list(await self.rows()), [])
        self.assertEqual(len(self.objects), 1)
        self.storage.delete_file.reset_mock()
        result = await ScreenshotService.cleanup_unused_screenshots(self.db, self.storage, 30)
        self.assertEqual(result["rows"], 0)
        self.storage.delete_file.assert_not_awaited()

    async def test_two_uploads_can_both_observe_no_record_and_insert(self):
        content = image_bytes()
        original_exec = self.db.exec
        inserted = False

        async def exec_after_competing_insert(statement, *args, **kwargs):
            nonlocal inserted
            result = await original_exec(statement, *args, **kwargs)
            if not inserted:
                inserted = True
                async with AsyncSession(self.engine) as other:
                    await ScreenshotService.record_upload(other, self.storage, 1, content)
            return result

        with patch.object(self.db, "exec", side_effect=exec_after_competing_insert):
            await ScreenshotService.record_upload(self.db, self.storage, 1, content)
        async with AsyncSession(self.engine) as other:
            records = (await other.exec(select(Screenshot))).all()
        self.assertEqual(len(records), 2)
        self.assertEqual(len(self.objects), 1)
