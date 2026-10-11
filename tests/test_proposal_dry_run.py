"""Deterministic stored-input simulation, without a storage or execution driver."""
import json
import unittest
from datetime import datetime, timezone, timedelta

from pydantic import ValidationError
from tests import test_proposal_review as fixtures
from agente_gestion.domain.action_proposal import ActionProposal
from agente_gestion.domain.tasks import TaskState
from agente_gestion.domain.proposal_dry_run import DryRunRequest, DryRunResult, simulate_proposal


class DryRunTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ProposalReviewTests()
        self.fixture.setUp()
        self.proposal, self.capture = self.fixture.proposal, self.fixture.capture
        self.evidence, self.digest = self.fixture.evidence, self.fixture.digest
        self.capture.snapshot.tasks[0].state = TaskState.pending
        self.capture.snapshot.tasks_complete = True
        self.capture.snapshot.task_source_identity = "manual:mapper-v1"
        self.events = [self.fixture.event(), self.fixture.event("authorize_dry_run", 1)]
        self.captures = [self.capture]
        self.request = DryRunRequest(request_id="r1", project_id="project", proposal_id="p1",
            proposal_body_sha256=self.digest, capture_id=self.capture.capture_id,
            permission_event_id="event-1", expected_version=2)

    def run_guard(self, **changes):
        return simulate_proposal(**(dict(request=self.request, proposal=self.proposal,
            capture=self.capture, evidence_items=self.evidence, proposal_body_sha256=self.digest,
            events=self.events, project_captures=self.captures) | changes))

    def deny(self, reason, **changes):
        result = self.run_guard(**changes)
        self.assertEqual(result.status, "denied")
        self.assertIn(reason, result.reasons)
        self.assertIsNone(result.field)
        self.assertFalse(result.live_state_changed)
        self.assertFalse(result.real_execution_allowed)
        self.assertEqual(result.execution_status, "not_executed")

    def test_all_fields_json_and_no_mutation(self):
        for field, before, after in [("priority", None, "high"),
                ("assignee_ids", ["existing-member"], []), ("assignee_ids", None, []),
                ("start_date", None, datetime(2026, 10, 11, tzinfo=timezone.utc)),
                ("due_date", datetime(2026, 10, 10), None),
                ("state", TaskState.pending, TaskState.in_progress)]:
            with self.subTest(field=field):
                self.setUp()
                card = self.capture.snapshot.tasks[0]
                setattr(card, field, before)
                card.observed_fields = [field]
                self.evidence[0].provenance.payload = {field: before}
                self.proposal = ActionProposal(**(self.proposal.model_dump() | dict(
                    field=field, current_value=before, proposed_value=after)))
                original = [x.model_dump_json() for x in [self.proposal, self.capture, *self.evidence, *self.events]]
                result = self.run_guard()
                self.assertEqual(result.status, "SIMULATED")
                self.assertEqual(result.field, field)
                data = json.loads(result.model_dump_json())
                expected = json.loads(self.proposal.model_dump_json())
                self.assertEqual(data["before"], expected["current_value"])
                self.assertEqual(data["after"], expected["proposed_value"])
                self.assertEqual(data["execution_status"], "not_executed")
                self.assertFalse(data["live_state_changed"])
                self.assertFalse(data["real_execution_allowed"])
                self.assertEqual(original, [x.model_dump_json() for x in [self.proposal, self.capture, *self.evidence, *self.events]])
                self.assertEqual(result, self.run_guard())

    def test_bindings_strict_request_and_unsafe_result(self):
        for key, value in [("request_id", " "), ("expected_version", True),
                ("proposal_body_sha256", "bad"), ("authorized", False),
                ("proposed_value", "low"), ("role", "admin")]:
            with self.subTest(key=key), self.assertRaises(ValidationError):
                DryRunRequest(**(self.request.model_dump() | {key: value}))
        for key in ("project_id", "proposal_id", "capture_id", "proposal_body_sha256"):
            self.deny("binding_mismatch", request=self.request.model_copy(update={key: "c" * 64}))
        self.deny("invalid_inputs", request=self.request.model_copy(update={"authorized": False}))
        self.deny("invalid_inputs", proposal_body_sha256=123)
        forged = DryRunResult.model_construct(status="SIMULATED", execution_status="executed",
            real_execution_allowed=True, live_state_changed=True)
        self.assertEqual(forged.execution_status, "not_executed")
        self.assertFalse(forged.real_execution_allowed)
        self.assertFalse(forged.live_state_changed)

    def test_permissions_versions_and_reused_request(self):
        self.deny("permission_not_effective", events=self.events[:1])
        self.deny("version_mismatch", request=self.request.model_copy(update={"expected_version": 1}))
        self.deny("permission_event_mismatch", request=self.request.model_copy(update={"permission_event_id": "event-0"}))
        for decision in ("deny", "revoke", "reject"):
            self.deny("permission_not_effective", events=self.events + [self.fixture.event(decision, 2)])
        self.events.append(self.fixture.event("authorize_dry_run", 2))
        self.deny("version_mismatch")
        self.request = self.request.model_copy(update={"expected_version": 3})
        self.deny("permission_event_mismatch")
        self.request = self.request.model_copy(update={"permission_event_id": "event-2"})
        self.assertEqual(self.run_guard().status, "SIMULATED")
        self.events[0].actor.__dict__["authenticated"] = True
        self.deny("invalid_inputs")

    def test_latest_unique_utc_and_duplicate_integrity(self):
        self.deny("missing_captures", project_captures=[])
        newer = self.capture.model_copy(deep=True)
        newer.capture_id = newer.content_hash = "c" * 64
        newer.snapshot.captured_at += timedelta(seconds=1)
        self.deny("stale_capture", project_captures=[self.capture, newer])
        newer.snapshot.captured_at = self.capture.snapshot.captured_at.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=2)))
        self.deny("ambiguous_latest_capture", project_captures=[self.capture, newer])
        self.assertEqual(self.run_guard(project_captures=[self.capture, self.capture.model_copy(deep=True)]).status, "SIMULATED")
        newer.capture_id = newer.content_hash = self.capture.capture_id
        self.deny("capture_id_conflict", project_captures=[self.capture, newer])
        newer.capture_id = newer.content_hash = "c" * 64
        self.deny("unknown_capture", project_captures=[newer])
        newer.snapshot.project_id = "foreign"
        self.deny("project_mismatch", project_captures=[newer])

    def test_source_target_observations_and_forged_models(self):
        for attr, value, code in [("tasks_complete", None, "incomplete_task_source"),
                ("tasks_complete", False, "incomplete_task_source"),
                ("task_source_identity", None, "unknown_task_source")]:
            self.setUp()
            setattr(self.capture.snapshot, attr, value)
            self.deny(code)
        for attr, value, code in [("is_included", False, "target_ineligible"),
                ("is_archived", True, "target_ineligible"),
                ("state", TaskState.unknown, "target_ineligible"),
                ("has_contradiction", True, "conflicting_task"),
                ("observed_fields", None, "field_not_observed"),
                ("priority", "low", "current_value_mismatch"),
                ("due_date", True, "invalid_inputs")]:
            self.setUp()
            setattr(self.capture.snapshot.tasks[0], attr, value)
            self.deny(code)
        self.setUp()
        self.deny("evidence_not_unique", evidence_items=[])
        self.evidence[0].provenance.payload["priority"] = "low"
        self.deny("evidence_fact_mismatch")
        self.setUp()
        self.capture.snapshot.tasks = []
        self.deny("target_not_unique")
        self.setUp()
        self.capture.snapshot.tasks *= 2
        self.deny("target_not_unique")
        self.setUp()
        self.capture.snapshot.__dict__["trusted"] = False
        self.deny("invalid_inputs")

    def test_missing_metadata_and_invalid_field_values(self):
        for field in ("priority", "assignee_ids"):
            self.setUp()
            self.capture.snapshot.tasks[0].observed_fields = []
            self.proposal = self.proposal.model_copy(update={"field": field, "proposed_value": [] if field == "assignee_ids" else "high"})
            self.deny("field_not_observed")
        for value in (True, 123, "2026-10-10"):
            self.setUp()
            self.proposal = self.proposal.model_copy(update={"field": "due_date", "proposed_value": value})
            self.deny("invalid_inputs")
        self.setUp()
        self.capture.content_hash = "invalid"
        self.deny("invalid_capture")
        self.setUp()
        self.events[1] = self.events[1].model_copy(update={"proposal_body_sha256": "c" * 64})
        self.deny("invalid_decisions")
