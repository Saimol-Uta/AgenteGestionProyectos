"""Unit and integration tests for secure, verified action execution on Trello."""

from datetime import datetime, timezone
import hashlib
import json
import os
import tempfile
import unittest

from agente_gestion.domain.action_proposal import ActionProposal
from agente_gestion.domain.action_execution import (
    ExecutionActor,
    ExecutionAuthorization,
    ExecutionRequest,
    ExecutionResult,
    execute_proposal_action,
)
from agente_gestion.domain.evidence import EvidenceItem, EvidenceProvenance
from agente_gestion.domain.proposal_review import DecisionEvent, OfflineActorClaim
from agente_gestion.domain.snapshot import SnapshotCapture, WeeklySnapshot
from agente_gestion.domain.tasks import DEFAULT_TIMEZONE, TaskCard, TaskState
from agente_gestion.adapters.sources.simulated_trello_actions import SimulatedTrelloActionAdapter
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter


class ActionExecutionTests(unittest.TestCase):
    def setUp(self):
        self.cutoff = datetime(2026, 10, 12, 12, 0, tzinfo=DEFAULT_TIMEZONE)
        self.card_id = "card-123"
        self.old_due = datetime(2026, 10, 14, 17, 0, tzinfo=DEFAULT_TIMEZONE)
        self.new_due = datetime(2026, 10, 18, 17, 0, tzinfo=DEFAULT_TIMEZONE)

        self.card = TaskCard(
            id=self.card_id,
            external_id=self.card_id,
            title="Database index migration",
            state=TaskState.in_progress,
            list_name="Fase 3: Desarrollo",
            is_included=True,
            due_complete=False,
            due_date=self.old_due,
            assignee_ids=["dev-alpha"],
            observed_fields=["due_date", "start_date", "priority", "state", "assignee_ids", "title"],
        )

        self.snapshot = WeeklySnapshot(
            id="snap-1",
            project_id="PROJ-01",
            cutoff_datetime=self.cutoff,
            captured_at=self.cutoff,
            tasks=[self.card],
            task_source_identity="trello-test-board",
            tasks_complete=True,
        )

        raw_snap = self.snapshot.model_dump_json()
        snap_hash = hashlib.sha256(raw_snap.encode("utf-8")).hexdigest()
        self.capture = SnapshotCapture(
            capture_id=snap_hash,
            content_hash=snap_hash,
            snapshot=self.snapshot,
        )

        self.evidence = EvidenceItem(
            category="task",
            summary="Card task due date evidence",
            provenance=EvidenceProvenance(
                id="ev-1",
                source="trello",
                project_id="PROJ-01",
                external_id=self.card_id,
                timestamp=self.cutoff,
                payload={"title": self.card.title, "due_date": self.old_due.isoformat(), "card_id": self.card_id},
            ),
        )



        self.proposal = ActionProposal(
            proposal_id="prop-001",
            project_id="PROJ-01",
            task_id=self.card_id,
            observed_capture_id=self.capture.capture_id,
            field="due_date",
            current_value=self.old_due,
            proposed_value=self.new_due,
            evidence_ids=["ev-1"],
            rationale="Extend deadline to avoid incomplete database indexing",
            expected_impact="Mitigates risk without impacting downstream milestones",
        )

        # Exact digest of the proposal submission
        prop_raw = json.dumps(
            {"proposal": self.proposal.model_dump(mode="json"), "evidence": [self.evidence.model_dump(mode="json")]},
            sort_keys=True,
        )
        self.proposal_digest = hashlib.sha256(prop_raw.encode("utf-8")).hexdigest()

        # Decision events approving review and authorizing execution
        self.review_event = DecisionEvent(
            event_id="evt-1",
            project_id="PROJ-01",
            proposal_id="prop-001",
            capture_id=self.capture.capture_id,
            proposal_body_sha256=self.proposal_digest,
            expected_version=0,
            actor=OfflineActorClaim(actor_id="tech-lead"),
            decision="approve_offline",
        )

        # Authenticated manager actor
        self.manager_actor = ExecutionActor(
            actor_id="mgr-saimol",
            role="project_manager",
            auth_mechanism="hmac_token",
            auth_token="token-valid-hmac-9872",
        )

        self.authorization = ExecutionAuthorization(
            grant_id="grant-001",
            proposal_id="prop-001",
            proposal_body_sha256=self.proposal_digest,
            expected_version=1,
            actor=self.manager_actor,
            grant_token="grant-token-verified-signature-abc",
        )

        self.request = ExecutionRequest(
            request_id="req-exec-001",
            project_id="PROJ-01",
            proposal_id="prop-001",
            proposal_body_sha256=self.proposal_digest,
            capture_id=self.capture.capture_id,
            expected_version=1,
            authorization=self.authorization,
        )

        # Trello action double seeded with old due date
        self.adapter = SimulatedTrelloActionAdapter(
            initial_cards={
                self.card_id: {
                    "due_date": self.old_due,
                    "assignee_ids": ["dev-alpha"],
                }
            }
        )

    def test_successful_execution_and_post_verification(self):
        result = execute_proposal_action(
            request=self.request,
            proposal=self.proposal,
            capture=self.capture,
            evidence_items=[self.evidence],
            proposal_body_sha256=self.proposal_digest,
            events=[self.review_event],
            project_captures=[self.capture],
            action_port=self.adapter,
        )

        self.assertEqual(result.status, "EXECUTED")
        self.assertEqual(result.verification_status, "verified")
        self.assertEqual(result.field, "due_date")
        self.assertEqual(result.value_before, self.old_due)
        self.assertEqual(result.value_proposed, self.new_due)
        self.assertEqual(result.value_observed_after, self.new_due)
        self.assertEqual(len(self.adapter.mutation_log), 1)

    def test_already_applied_idempotence(self):
        # Set card in Trello to already have the proposed value
        self.adapter.set_card_field(self.card_id, "due_date", self.new_due)

        result = execute_proposal_action(
            request=self.request,
            proposal=self.proposal,
            capture=self.capture,
            evidence_items=[self.evidence],
            proposal_body_sha256=self.proposal_digest,
            events=[self.review_event],
            project_captures=[self.capture],
            action_port=self.adapter,
        )

        self.assertEqual(result.status, "ALREADY_APPLIED")
        self.assertIn("already_applied", result.reasons)
        self.assertEqual(result.verification_status, "verified")
        # No mutations applied
        self.assertEqual(len(self.adapter.mutation_log), 0)

    def test_concurrency_conflict_external_drift(self):
        # Card in Trello was modified externally to another date
        drift_date = datetime(2026, 10, 20, 10, 0, tzinfo=DEFAULT_TIMEZONE)
        self.adapter.set_card_field(self.card_id, "due_date", drift_date)

        result = execute_proposal_action(
            request=self.request,
            proposal=self.proposal,
            capture=self.capture,
            evidence_items=[self.evidence],
            proposal_body_sha256=self.proposal_digest,
            events=[self.review_event],
            project_captures=[self.capture],
            action_port=self.adapter,
        )

        self.assertEqual(result.status, "CONFLICT")
        self.assertIn("concurrency_conflict: external_drift", result.reasons)
        self.assertEqual(len(self.adapter.mutation_log), 0)

    def test_unauthenticated_offline_actor_rejected(self):
        with self.assertRaises(ValueError):
            ExecutionActor(
                actor_id="hacker",
                role="project_manager",
                auth_mechanism="test_authority",
                auth_token="asserted_offline",  # Forbidden
            )

    def test_unsupported_priority_field_rejected(self):
        # Trello does not support native priority
        priority_proposal = ActionProposal(
            proposal_id="prop-prio",
            project_id="PROJ-01",
            task_id=self.card_id,
            observed_capture_id=self.capture.capture_id,
            field="priority",
            current_value=None,
            proposed_value="high",
            evidence_ids=["ev-1"],
            rationale="Escalate priority",
            expected_impact="High focus",
        )
        prop_raw = json.dumps(
            {"proposal": priority_proposal.model_dump(mode="json"), "evidence": [self.evidence.model_dump(mode="json")]},
            sort_keys=True,
        )
        digest = hashlib.sha256(prop_raw.encode("utf-8")).hexdigest()

        review_evt = DecisionEvent(
            event_id="evt-prio",
            project_id="PROJ-01",
            proposal_id="prop-prio",
            capture_id=self.capture.capture_id,
            proposal_body_sha256=digest,
            expected_version=0,
            actor=OfflineActorClaim(actor_id="tech-lead"),
            decision="approve_offline",
        )
        auth = ExecutionAuthorization(
            grant_id="grant-prio",
            proposal_id="prop-prio",
            proposal_body_sha256=digest,
            expected_version=1,
            actor=self.manager_actor,
            grant_token="token-ok",
        )
        req = ExecutionRequest(
            request_id="req-prio",
            project_id="PROJ-01",
            proposal_id="prop-prio",
            proposal_body_sha256=digest,
            capture_id=self.capture.capture_id,
            expected_version=1,
            authorization=auth,
        )

        result = execute_proposal_action(
            request=req,
            proposal=priority_proposal,
            capture=self.capture,
            evidence_items=[self.evidence],
            proposal_body_sha256=digest,
            events=[review_evt],
            project_captures=[self.capture],
            action_port=self.adapter,
        )

        self.assertEqual(result.status, "DENIED")
        self.assertTrue(any("unsupported_trello_field: priority" in r for r in result.reasons))

    def test_unsupported_state_field_rejected(self):
        state_proposal = ActionProposal(
            proposal_id="prop-state",
            project_id="PROJ-01",
            task_id=self.card_id,
            observed_capture_id=self.capture.capture_id,
            field="state",
            current_value=TaskState.in_progress,
            proposed_value=TaskState.completed,
            evidence_ids=["ev-1"],
            rationale="Mark completed",
            expected_impact="Milestone done",
        )
        prop_raw = json.dumps(
            {"proposal": state_proposal.model_dump(mode="json"), "evidence": [self.evidence.model_dump(mode="json")]},
            sort_keys=True,
        )
        digest = hashlib.sha256(prop_raw.encode("utf-8")).hexdigest()
        review_evt = DecisionEvent(
            event_id="evt-state",
            project_id="PROJ-01",
            proposal_id="prop-state",
            capture_id=self.capture.capture_id,
            proposal_body_sha256=digest,
            expected_version=0,
            actor=OfflineActorClaim(actor_id="tech-lead"),
            decision="approve_offline",
        )
        auth = ExecutionAuthorization(
            grant_id="grant-state",
            proposal_id="prop-state",
            proposal_body_sha256=digest,
            expected_version=1,
            actor=self.manager_actor,
            grant_token="token-ok",
        )
        req = ExecutionRequest(
            request_id="req-state",
            project_id="PROJ-01",
            proposal_id="prop-state",
            proposal_body_sha256=digest,
            capture_id=self.capture.capture_id,
            expected_version=1,
            authorization=auth,
        )

        result = execute_proposal_action(
            request=req,
            proposal=state_proposal,
            capture=self.capture,
            evidence_items=[self.evidence],
            proposal_body_sha256=digest,
            events=[review_evt],
            project_captures=[self.capture],
            action_port=self.adapter,
        )

        self.assertEqual(result.status, "DENIED")
        self.assertTrue(any("unsupported_trello_field: state" in r for r in result.reasons))

    def test_stale_capture_rejected(self):
        # Create a newer capture in project captures
        newer_snap = WeeklySnapshot(
            id="snap-2",
            project_id="PROJ-01",
            cutoff_datetime=datetime(2026, 10, 19, 12, 0, tzinfo=DEFAULT_TIMEZONE),
            captured_at=datetime(2026, 10, 19, 12, 0, tzinfo=DEFAULT_TIMEZONE),
            tasks=[self.card],
            task_source_identity="trello-test-board",
            tasks_complete=True,
        )
        raw_newer = newer_snap.model_dump_json()
        newer_hash = hashlib.sha256(raw_newer.encode("utf-8")).hexdigest()
        newer_capture = SnapshotCapture(
            capture_id=newer_hash,
            content_hash=newer_hash,
            snapshot=newer_snap,
        )

        result = execute_proposal_action(
            request=self.request,
            proposal=self.proposal,
            capture=self.capture,
            evidence_items=[self.evidence],
            proposal_body_sha256=self.proposal_digest,
            events=[self.review_event],
            project_captures=[self.capture, newer_capture],
            action_port=self.adapter,
        )

        self.assertEqual(result.status, "DENIED")
        self.assertIn("stale_capture", result.reasons)

    def test_revoked_authorization_rejected(self):
        revoke_evt = DecisionEvent(
            event_id="evt-revoke",
            project_id="PROJ-01",
            proposal_id="prop-001",
            capture_id=self.capture.capture_id,
            proposal_body_sha256=self.proposal_digest,
            expected_version=1,
            actor=OfflineActorClaim(actor_id="admin"),
            decision="revoke",
        )

        result = execute_proposal_action(
            request=self.request,
            proposal=self.proposal,
            capture=self.capture,
            evidence_items=[self.evidence],
            proposal_body_sha256=self.proposal_digest,
            events=[self.review_event, revoke_evt],
            project_captures=[self.capture],
            action_port=self.adapter,
        )

        self.assertEqual(result.status, "DENIED")
        self.assertIn("authorization_revoked", result.reasons)

    def test_provider_write_failure_handled(self):
        self.adapter.simulate_write_failure = True

        result = execute_proposal_action(
            request=self.request,
            proposal=self.proposal,
            capture=self.capture,
            evidence_items=[self.evidence],
            proposal_body_sha256=self.proposal_digest,
            events=[self.review_event],
            project_captures=[self.capture],
            action_port=self.adapter,
        )

        self.assertEqual(result.status, "FAILED")
        self.assertIn("mutation_rejected_by_provider", result.reasons)


class SqliteExecutionLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_exec.db")
        self.storage = SQLiteStorageAdapter(db_path=self.db_path)

        self.actor = ExecutionActor(
            actor_id="mgr-1",
            role="project_manager",
            auth_mechanism="test_authority",
            auth_token="auth-token-123",
        )
        self.auth = ExecutionAuthorization(
            grant_id="grant-1",
            proposal_id="prop-1",
            proposal_body_sha256="a" * 64,
            expected_version=1,
            actor=self.actor,
            grant_token="grant-token-123",
        )
        self.request = ExecutionRequest(
            request_id="req-1",
            project_id="PROJ-1",
            proposal_id="prop-1",
            proposal_body_sha256="a" * 64,
            capture_id="b" * 64,
            expected_version=1,
            authorization=self.auth,
        )
        self.result = ExecutionResult(
            execution_id="exec-req-1",
            request_id="req-1",
            proposal_id="prop-1",
            status="EXECUTED",
            field="due_date",
            value_before="2026-10-10T12:00:00Z",
            value_proposed="2026-10-15T12:00:00Z",
            value_observed_after="2026-10-15T12:00:00Z",
            verification_status="verified",
            actor_id="mgr-1",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_append_and_retrieve_execution_audit(self):
        self.storage.append_proposal_execution(self.request, self.result)

        fetched = self.storage.get_proposal_execution("exec-req-1")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.execution_id, "exec-req-1")
        self.assertEqual(fetched.status, "EXECUTED")
        self.assertEqual(fetched.verification_status, "verified")
        self.assertEqual(fetched.actor_id, "mgr-1")

        listed = self.storage.list_proposal_executions("PROJ-1", "prop-1")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].execution_id, "exec-req-1")

    def test_execution_ledger_is_append_only(self):
        self.storage.append_proposal_execution(self.request, self.result)
        conn = self.storage._get_connection()
        try:
            with self.assertRaises(Exception):
                conn.execute("UPDATE proposal_executions SET status = 'FAILED'")
            with self.assertRaises(Exception):
                conn.execute("DELETE FROM proposal_executions WHERE execution_id = 'exec-req-1'")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
