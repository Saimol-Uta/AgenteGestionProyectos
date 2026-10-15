from datetime import datetime
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.application.proposal_auto_review import (
    ProposalAutoReviewService,
    review_proposal_technically,
)
from agente_gestion.domain.action_proposal import ActionProposal
from agente_gestion.domain.evidence import EvidenceItem, EvidenceProvenance
from agente_gestion.domain.snapshot import SnapshotCapture, WeeklySnapshot
from agente_gestion.domain.tasks import TaskCard, TaskState


class ProposalAutoReviewTests(unittest.TestCase):
    def setUp(self):
        self.cutoff = datetime(2026, 10, 12, 12)
        self.card = TaskCard(
            id="card-1", external_id="card-1", title="Actualizar base de datos",
            state=TaskState.in_progress, list_name="En curso", is_included=True,
            due_date=datetime(2026, 10, 14, 17), priority="media",
            assignee_ids=["dev-1"], observed_fields=["due_date", "priority", "assignee_ids"],
        )
        self.snapshot = WeeklySnapshot(
            id="snap-1", project_id="PROJ-1", cutoff_datetime=self.cutoff,
            captured_at=self.cutoff, tasks=[self.card], task_source_identity="fixture:board-1",
            tasks_complete=True,
        )
        digest = hashlib.sha256(self.snapshot.model_dump_json().encode()).hexdigest()
        self.capture = SnapshotCapture(capture_id=digest, content_hash=digest, snapshot=self.snapshot)
        self.evidence = EvidenceItem(
            category="task", summary="Hecho observado de Trello",
            provenance=EvidenceProvenance(
                id="ev-1", source="trello", project_id="PROJ-1", external_id="card-1",
                timestamp=self.cutoff,
                payload={"card_id": "card-1", "due_date": self.card.due_date.isoformat(),
                         "priority": "media", "assignee_ids": ["dev-1"]},
            ),
        )
        self.proposal = ActionProposal(
            proposal_id="proposal-1", project_id="PROJ-1", task_id="card-1",
            observed_capture_id=digest, field="due_date", current_value=self.card.due_date,
            proposed_value=datetime(2026, 10, 18, 17), evidence_ids=["ev-1"],
            rationale="Ajuste respaldado por evidencia", expected_impact="Evita un retraso",
        )

    def test_technical_readiness_and_dry_run_do_not_approve_or_execute(self):
        result = review_proposal_technically(
            self.proposal, self.capture, [self.evidence], project_captures=[self.capture],
        )

        self.assertEqual(result.technical_status, "ready")
        self.assertEqual(result.dry_run.status, "SIMULATED")
        self.assertEqual(result.dry_run.before, self.proposal.current_value)
        self.assertEqual(result.dry_run.after, self.proposal.proposed_value)
        self.assertFalse(result.live_state_changed)
        self.assertFalse(result.execution_authorized)
        self.assertEqual(result.human_approval_status, "required")

    def test_stale_capture_blocks_fresh_review_and_dry_run(self):
        newer_snapshot = self.snapshot.model_copy(update={
            "id": "snap-2", "captured_at": datetime(2026, 10, 13, 12),
        })
        newer_digest = hashlib.sha256(newer_snapshot.model_dump_json().encode()).hexdigest()
        newer_capture = SnapshotCapture(
            capture_id=newer_digest, content_hash=newer_digest, snapshot=newer_snapshot,
        )

        result = review_proposal_technically(
            self.proposal, self.capture, [self.evidence], project_captures=[self.capture, newer_capture],
        )

        self.assertEqual(result.technical_status, "blocked")
        self.assertEqual(result.dry_run.status, "denied")
        self.assertIn("stale_capture", result.technical_errors)
        self.assertFalse(result.execution_authorized)

    def test_persisted_technical_review_is_append_only_and_leaves_human_state_pending(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage = SQLiteStorageAdapter(str(Path(temporary) / "auto-review.sqlite"))
            capture = storage.save_snapshot_with_capture_receipt(self.snapshot)
            storage.save_action_proposal(self.proposal, [self.evidence])
            service = ProposalAutoReviewService(storage)

            first = service.review_and_record(
                self.proposal, capture, [self.evidence], project_captures=[capture],
            )
            second = service.review_and_record(
                self.proposal, capture, [self.evidence], project_captures=[capture],
            )
            state = storage.get_proposal_review_state("PROJ-1", self.proposal.proposal_id)
            connection = storage._get_connection()
            try:
                count = connection.execute("SELECT count(*) FROM proposal_technical_reviews").fetchone()[0]
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("UPDATE proposal_technical_reviews SET proposal_id='changed'")
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("DELETE FROM proposal_technical_reviews")
            finally:
                connection.close()

            self.assertEqual(first.review_id, second.review_id)
            self.assertEqual(first.proposal_body_sha256, state[0])
            self.assertEqual(count, 1)
            self.assertEqual(state[2].review_status, "pending")
            self.assertEqual(state[2].version, 0)
            self.assertFalse(first.execution_authorized)

    def test_orchestrator_records_technical_review_without_approval(self):
        from tests.test_orchestrator_proposals import OrchestratorProposalsTests

        fixture = OrchestratorProposalsTests()
        fixture.setUp()
        try:
            run = fixture.execute()
            summary = json.loads(run.stages["proposals"].output_summary)
            proposal, _evidence = fixture.store.list_action_proposals("P")[0]
            state = fixture.store.get_proposal_review_state("P", proposal.proposal_id)
            connection = fixture.store._get_connection()
            try:
                count = connection.execute("SELECT count(*) FROM proposal_technical_reviews").fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(summary["technical_ready"] + summary["technical_blocked"], 1)
            self.assertEqual(count, 1)
            self.assertEqual(state[2].review_status, "pending")
            self.assertEqual(state[2].version, 0)
        finally:
            fixture.doCleanups()

    def test_missing_evidence_fails_closed_without_human_approval(self):
        result = review_proposal_technically(
            self.proposal, self.capture, [], project_captures=[self.capture],
        )

        self.assertEqual(result.technical_status, "blocked")
        self.assertEqual(result.dry_run.status, "denied")
        self.assertFalse(result.execution_authorized)
        self.assertEqual(result.human_approval_status, "required")


if __name__ == "__main__":
    unittest.main()
