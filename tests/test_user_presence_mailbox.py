"""Tests for same-user presence mailbox ordering."""

import asyncio
from datetime import UTC, datetime
import unittest

from app.service import user_presence_mailbox as presence_mailbox
from app.service.user_presence_mailbox import (
    enqueue_user_presence,
    is_stale_presence,
    parse_presence_event_time,
    reset_presence_mailbox_for_tests,
)


class PresenceMailboxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        reset_presence_mailbox_for_tests()

    def tearDown(self) -> None:
        reset_presence_mailbox_for_tests()

    def test_is_stale_presence_rejects_older_event(self) -> None:
        older = datetime(2026, 11, 25, 11, 0, tzinfo=UTC)
        newer = datetime(2026, 11, 25, 11, 1, tzinfo=UTC)
        self.assertTrue(is_stale_presence(newer, older))
        self.assertFalse(is_stale_presence(older, newer))
        self.assertFalse(is_stale_presence(None, older))

    def test_parse_presence_event_time_accepts_iso_strings(self) -> None:
        parsed = parse_presence_event_time("2026-11-25T19:00:00+00:00")
        self.assertEqual(parsed, datetime(2026, 11, 25, 19, 0, tzinfo=UTC))

    async def test_worker_applies_online_last_when_offline_apply_is_delayed(self) -> None:
        applied: list[bool | None] = []
        offline_started = asyncio.Event()
        release_offline = asyncio.Event()

        async def fake_apply(user_id: int, is_online: bool | None, event_time: datetime) -> None:
            if is_online is False:
                offline_started.set()
                await release_offline.wait()
            applied.append(is_online)

        t0 = datetime(2026, 11, 25, 11, 0, tzinfo=UTC)
        t1 = datetime(2026, 11, 25, 11, 1, tzinfo=UTC)

        await enqueue_user_presence(1, False, t0, fake_apply)
        await offline_started.wait()
        await enqueue_user_presence(1, True, t1, fake_apply)
        release_offline.set()
        worker = presence_mailbox._presence_workers.get(1)
        if worker is not None and not worker.done():
            async with asyncio.timeout(2):
                await worker

        self.assertEqual(applied, [False, True])


if __name__ == "__main__":
    unittest.main()
