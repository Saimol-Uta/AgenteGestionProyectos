"""Manager clarity coverage: readable task names and reference dates on deterministic risks.

The read model stays additive: the identity and traceability of every G2/G3 risk
(``risk_id``, ``kind``, ``task_ids``, ``capture_id``, ``source``) are untouched. The
projection only adds what a manager needs to read a risk: the readable title of each
affected task and the reference date of the capture the risk was computed against.
"""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.domain.tasks import DEFAULT_TIMEZONE, TaskCard, TaskState
from tests.web_fixtures import (
    EVIDENCE_SECRET,
    PROJECT_ID,
    TASK_ID,
    build_web_api_fixture,
)

SECOND_TASK_ID = "task-002"


def _task(card_id: str, title: str, cutoff: datetime, *, due_in_days: int) -> TaskCard:
    return TaskCard(
        id=card_id,
        title=title,
        state=TaskState.pending,
        list_name="Fase",
        is_included=True,
        due_complete=False,
        due_date=cutoff + timedelta(days=due_in_days),
    )


class RiskReadModelTests(unittest.TestCase):
    """The projection of a risk must not change the risk itself."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = build_web_api_fixture(Path(self.temp.name))
        self.client = self.fixture.client

    def _summary(self, **params) -> dict:
        response = self.client.get(f"/api/v1/projects/{PROJECT_ID}/summary", params=params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_summary_risks_expose_readable_titles_and_reference_date(self):
        body = self._summary()
        self.assertTrue(body["risks"])
        for risk in body["risks"]:
            self.assertEqual(set(risk["task_titles"]), set(risk["task_ids"]))
            for task_id in risk["task_ids"]:
                self.assertTrue(risk["task_titles"][task_id])
                self.assertNotIn(EVIDENCE_SECRET, risk["task_titles"][task_id])
            self.assertEqual(risk["cutoff_datetime"], body["cutoff_datetime"])

    def test_risk_identity_and_traceability_are_unchanged(self):
        body = self._summary()
        for risk in body["risks"]:
            self.assertTrue(risk["risk_id"].startswith("risk-"))
            self.assertEqual(risk["project_id"], PROJECT_ID)
            self.assertEqual(risk["capture_id"], body["capture_id"])
            self.assertEqual(risk["source"], "deterministic")
            self.assertEqual(risk["task_count"], len(risk["task_ids"]))
            self.assertTrue(risk["summary"])
            self.assertTrue(risk["basis"])

    def test_task_detail_risks_expose_titles_and_reference_date(self):
        response = self.client.get(f"/api/v1/projects/{PROJECT_ID}/tasks/{TASK_ID}")
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertTrue(detail["risks"])
        for risk in detail["risks"]:
            self.assertIn(TASK_ID, risk["task_ids"])
            self.assertIn(TASK_ID, risk["task_titles"])
            self.assertTrue(risk["task_titles"][TASK_ID])
            self.assertNotIn(EVIDENCE_SECRET, risk["task_titles"][TASK_ID])
            self.assertEqual(risk["cutoff_datetime"], detail["risks"][0]["cutoff_datetime"])
            self.assertEqual(risk["capture_id"], detail["capture_id"])

    def test_risk_spanning_several_tasks_names_every_affected_task(self):
        cutoff = datetime(2026, 3, 2, 18, 0, tzinfo=DEFAULT_TIMEZONE)
        shared = WeeklySnapshot(
            id="snap-shared-risk",
            project_id=PROJECT_ID,
            cutoff_datetime=cutoff,
            captured_at=cutoff,
            tasks=[
                _task(TASK_ID, "Primera tarea compartida", cutoff, due_in_days=3),
                _task(SECOND_TASK_ID, "Segunda tarea compartida", cutoff, due_in_days=4),
            ],
        )
        capture = SQLiteStorageAdapter(
            self.fixture.database_path,
        ).save_snapshot_with_capture_receipt(shared)
        self.assertIsNotNone(capture)

        summary = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/summary",
            params={"capture_id": capture.capture_id},
        ).json()
        due_soon = next(risk for risk in summary["risks"] if risk["kind"] == "due_soon")
        self.assertEqual(due_soon["task_ids"], [TASK_ID, SECOND_TASK_ID])
        self.assertEqual(due_soon["task_titles"], {
            TASK_ID: "Primera tarea compartida",
            SECOND_TASK_ID: "Segunda tarea compartida",
        })

        detail = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/tasks/{SECOND_TASK_ID}",
            params={"capture_id": capture.capture_id},
        ).json()
        own = next(risk for risk in detail["risks"] if risk["kind"] == "due_soon")
        self.assertEqual(own["task_titles"], {
            TASK_ID: "Primera tarea compartida",
            SECOND_TASK_ID: "Segunda tarea compartida",
        })
        self.assertEqual(own["cutoff_datetime"], cutoff.isoformat())

    def test_task_detail_exposes_the_resolved_responsible_names(self):
        cutoff = datetime(2026, 3, 6, 18, 0, tzinfo=DEFAULT_TIMEZONE)
        named = WeeklySnapshot(
            id="snap-named-assignee",
            project_id=PROJECT_ID,
            cutoff_datetime=cutoff,
            captured_at=cutoff,
            tasks=[TaskCard(
                id=TASK_ID,
                title="Tarea con responsable",
                state=TaskState.pending,
                list_name="Fase",
                is_included=True,
                due_complete=False,
                due_date=cutoff + timedelta(days=10),
                assignee_ids=["mem_1", "mem_2"],
                assignee_names={"mem_1": "Ada Lovelace"},
            )],
        )
        capture = SQLiteStorageAdapter(
            self.fixture.database_path,
        ).save_snapshot_with_capture_receipt(named)
        detail = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/tasks/{TASK_ID}",
            params={"capture_id": capture.capture_id},
        ).json()
        # The identifier stays the identity of the responsible person; the name is a projection.
        self.assertEqual(detail["assignee_ids"], ["mem_1", "mem_2"])
        self.assertEqual(detail["assignee_names"], {"mem_1": "Ada Lovelace"})

    def test_a_capture_without_names_keeps_its_own_content(self):
        cutoff = datetime(2026, 3, 8, 18, 0, tzinfo=DEFAULT_TIMEZONE)
        unnamed = WeeklySnapshot(
            id="snap-unnamed-assignee",
            project_id=PROJECT_ID,
            cutoff_datetime=cutoff,
            captured_at=cutoff,
            tasks=[TaskCard(
                id=TASK_ID,
                title="Tarea sin nombre resuelto",
                state=TaskState.pending,
                list_name="Fase",
                is_included=True,
                due_complete=False,
                due_date=cutoff + timedelta(days=10),
                assignee_ids=["mem_1"],
            )],
        )
        capture = SQLiteStorageAdapter(
            self.fixture.database_path,
        ).save_snapshot_with_capture_receipt(unnamed)
        detail = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/tasks/{TASK_ID}",
            params={"capture_id": capture.capture_id},
        ).json()
        self.assertEqual(detail["assignee_ids"], ["mem_1"])
        self.assertEqual(detail["assignee_names"], {})
        self.assertNotIn("assignee_names", capture.snapshot.tasks[0].model_dump_json())

    def test_titles_of_tasks_outside_the_risk_are_not_leaked(self):
        cutoff = datetime(2026, 3, 4, 18, 0, tzinfo=DEFAULT_TIMEZONE)
        snapshot = WeeklySnapshot(
            id="snap-mixed-risk",
            project_id=PROJECT_ID,
            cutoff_datetime=cutoff,
            captured_at=cutoff,
            tasks=[
                _task(TASK_ID, "Tarea vencida", cutoff, due_in_days=-2),
                _task(SECOND_TASK_ID, "Tarea limpia", cutoff, due_in_days=90),
            ],
        )
        capture = SQLiteStorageAdapter(
            self.fixture.database_path,
        ).save_snapshot_with_capture_receipt(snapshot)
        summary = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/summary",
            params={"capture_id": capture.capture_id},
        ).json()
        overdue = next(risk for risk in summary["risks"] if risk["kind"] == "overdue")
        self.assertEqual(overdue["task_ids"], [TASK_ID])
        self.assertEqual(overdue["task_titles"], {TASK_ID: "Tarea vencida"})
        self.assertNotIn(SECOND_TASK_ID, overdue["task_titles"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
