"""Unit and integration tests for WeeklySchedulerService and HybridNotificationOutboxAdapter (H3)."""

from datetime import datetime, timedelta
import os
import shutil
import tempfile
import unittest
from zoneinfo import ZoneInfo

from agente_gestion.adapters.outbox.hybrid_outbox import HybridNotificationOutboxAdapter
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.application.weekly_scheduler import (
    WeeklySchedulerService,
    get_default_schedule,
)
from agente_gestion.domain.notification_outbox import OutboxMessage
from agente_gestion.domain.snapshot import WeeklySnapshot, SnapshotCapture
from agente_gestion.domain.tasks import TaskCard, TaskState

DEFAULT_TZ = ZoneInfo("America/Guayaquil")


class TestWeeklyScheduler(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.outbox_dir = os.path.join(self.temp_dir, "outbox")
        self.storage = SQLiteStorageAdapter(self.db_path)
        self.outbox = HybridNotificationOutboxAdapter(self.storage, outbox_dir=self.outbox_dir)
        self.scheduler = WeeklySchedulerService(self.storage, self.outbox)

        # Seed sample task snapshot
        self.card = TaskCard(
            id="card-1",
            title="Tarea de prueba semanal",
            list_id="l-1",
            state=TaskState.in_progress,
            is_included=True,
            due_date=datetime(2026, 10, 16, 17, 0, tzinfo=DEFAULT_TZ),
        )
        self.snapshot = WeeklySnapshot(
            id="snap-test",
            project_id="PROJ-TEST",
            cutoff_datetime=datetime(2026, 10, 16, 17, 0, tzinfo=DEFAULT_TZ),
            captured_at=datetime(2026, 10, 16, 17, 0, tzinfo=DEFAULT_TZ),
            tasks=[self.card],
        )
        raw_json = self.snapshot.model_dump_json()
        import hashlib
        c_hash = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        self.capture = SnapshotCapture(
            capture_id=c_hash,
            content_hash=c_hash,
            snapshot=self.snapshot,
        )
        conn = self.storage._get_connection()
        try:
            with conn:
                self.storage._insert_capture(conn, raw_json)
        finally:
            conn.close()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_schedule_check_status_boundary_evaluation(self):
        # Default schedule: Friday (4) at 17:00
        schedule = get_default_schedule("PROJ-TEST", weekday=4, local_time="17:00")

        # Thursday (weekday 3) 12:00 -> not due yet for the current week's Friday cut
        thursday = datetime(2026, 10, 15, 12, 0, tzinfo=DEFAULT_TZ)
        status_thursday = self.scheduler.check_schedule("PROJ-TEST", schedule, eval_time=thursday)
        self.assertFalse(status_thursday.is_dispatched)
        self.assertIsNotNone(status_thursday.window)

        # Friday (weekday 4) 18:00 -> passed cutoff
        friday_after = datetime(2026, 10, 16, 18, 0, tzinfo=DEFAULT_TZ)
        status_friday = self.scheduler.check_schedule("PROJ-TEST", schedule, eval_time=friday_after)
        self.assertTrue(status_friday.is_due)
        self.assertTrue(status_friday.can_run_scheduled)

    def test_run_scheduled_cycle_requires_force_when_not_due(self):
        schedule = get_default_schedule("PROJ-TEST", weekday=4, local_time="17:00")
        thursday = datetime(2026, 10, 15, 12, 0, tzinfo=DEFAULT_TZ)

        # Without force -> returns SKIPPED_NOT_DUE
        res_skipped = self.scheduler.run_scheduled_cycle(
            "PROJ-TEST", schedule, eval_time=thursday, force=False
        )
        self.assertEqual(res_skipped.status, "SKIPPED_NOT_DUE")
        self.assertIn("--force", res_skipped.reason)

        # With force -> executes immediately without waiting for Friday!
        res_forced = self.scheduler.run_scheduled_cycle(
            "PROJ-TEST", schedule, eval_time=thursday, force=True
        )
        self.assertEqual(res_forced.status, "EXECUTED")
        self.assertIsNotNone(res_forced.receipt)
        self.assertEqual(res_forced.receipt.status, "DELIVERED")

        # Verify receipt was recorded in SQLite
        receipts = self.storage.list_outbox_receipts("PROJ-TEST")
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0].status, "DELIVERED")

        # Verify artifacts written to outbox_dir
        files = os.listdir(self.outbox_dir)
        self.assertTrue(any(f.endswith(".json") for f in files))
        self.assertTrue(any(f.endswith(".md") for f in files))

    def test_idempotency_prevents_duplicate_dispatch_unless_force(self):
        schedule = get_default_schedule("PROJ-TEST", weekday=4, local_time="17:00")
        eval_time = datetime(2026, 10, 16, 18, 0, tzinfo=DEFAULT_TZ)

        # First run -> EXECUTED
        res1 = self.scheduler.run_scheduled_cycle(
            "PROJ-TEST", schedule, eval_time=eval_time, force=False
        )
        self.assertEqual(res1.status, "EXECUTED")

        # Second run without force -> SKIPPED_ALREADY_DISPATCHED
        res2 = self.scheduler.run_scheduled_cycle(
            "PROJ-TEST", schedule, eval_time=eval_time, force=False
        )
        self.assertEqual(res2.status, "SKIPPED_ALREADY_DISPATCHED")

        # Third run WITH force -> re-dispatched
        res3 = self.scheduler.run_scheduled_cycle(
            "PROJ-TEST", schedule, eval_time=eval_time, force=True
        )
        self.assertEqual(res3.status, "EXECUTED")
        self.assertEqual(res3.receipt.status, "DELIVERED")


if __name__ == "__main__":
    unittest.main()
