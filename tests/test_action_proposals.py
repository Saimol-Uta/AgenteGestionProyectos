"""Offline contract tests for the consultative H2A domain boundary."""
import json
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from pydantic import ValidationError
from agente_gestion.domain.action_proposal import ActionProposal, validate_action_proposal
from agente_gestion.domain.evidence import EvidenceItem, EvidenceProvenance
from agente_gestion.domain.snapshot import SnapshotCapture, WeeklySnapshot
from agente_gestion.domain.tasks import TaskCard, TaskState


class ActionProposalTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 10, 12)
        self.card = TaskCard(id="task", external_id="external", title="Task",
                             observed_fields=["priority"], priority=None)
        self.capture = SnapshotCapture(capture_id="a" * 64, content_hash="a" * 64,
            snapshot=WeeklySnapshot(id="legacy-id", project_id="project",
                cutoff_datetime=self.now, captured_at=self.now, tasks=[self.card]))
        self.evidence = EvidenceItem(category="task", summary="Observed priority",
            provenance=EvidenceProvenance(id="e1", project_id="project", source="manual",
                timestamp=self.now, external_id="external", payload={"priority": None}))
        self.data = dict(proposal_id="p1", project_id="project", task_id="task",
            observed_capture_id="a" * 64, field="priority", current_value=None,
            proposed_value="high", evidence_ids=["e1"], rationale="Reduce risk",
            expected_impact="Earlier resolution")

    def errors(self, **changes):
        return validate_action_proposal(ActionProposal(**(self.data | changes)),
                                        self.capture, [self.evidence])

    def test_all_fields_and_round_trip(self):
        cases = [("priority", None, "high"), ("state", TaskState.unknown, TaskState.pending),
                 ("assignee_ids", None, []), ("assignee_ids", [], ["u"]),
                 ("start_date", None, self.now),
                 ("due_date", self.now, datetime(2026, 10, 11, 12, tzinfo=timezone.utc))]
        for field, current, proposed in cases:
            with self.subTest(field=field, current=current):
                card = self.card.model_copy(update={field: current, "observed_fields": [field]})
                capture = self.capture.model_copy(update={"snapshot":
                    self.capture.snapshot.model_copy(update={"tasks": [card]})})
                evidence = self.evidence.model_copy(deep=True)
                evidence.provenance.payload = {field: current}
                proposal = ActionProposal(**(self.data | dict(field=field,
                    current_value=current, proposed_value=proposed)))
                self.assertEqual(validate_action_proposal(proposal, capture, [evidence]), [])
                encoded = proposal.model_dump_json()
                self.assertEqual(ActionProposal.model_validate_json(encoded).model_dump_json(), encoded)
                self.assertEqual(json.loads(encoded)["authorization_status"], "not_authorized")
                self.assertEqual(json.loads(encoded)["execution_status"], "not_executed")
                self.assertEqual(json.loads(encoded)["review_status"], "pending")

    def test_schema_rejects_coercion_aliases_and_permission_claims(self):
        invalid = [{"field": "date"}, {"field": "assignee"}, {"field": "labels"},
            {"schema_version": "2"}, {"schema_version": 1}, {"actor": "admin"},
            {"permissions": ["execute"]}, {"authorized": True},
            {"authorization_status": "authorized"}, {"execution_status": "executed"},
            {"review_status": "approved"}, {"priority": "high"}, {"evidence_ids": [1]}]
        for field, values in [("priority", [True, 1, [], {}]),
                ("assignee_ids", ["u", [1], [True], {"u"}]),
                ("state", [None, "completed", "unknown", 1, True]),
                ("due_date", [1, True, "2026-10-10", date(2026, 10, 10)]),
                ("start_date", [1, False, "2026-10-10", date(2026, 10, 10)])]:
            invalid.extend({"field": field, key: value}
                           for key in ("current_value", "proposed_value") for value in values)
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                ActionProposal(**(self.data | change))
        for key in ("current_value", "proposed_value", "field"):
            data = dict(self.data)
            del data[key]
            with self.assertRaises(ValidationError):
                ActionProposal(**data)
        self.assertEqual(validate_action_proposal(self.data | {"actor": "admin"},
                         self.capture, [self.evidence]), ["invalid_proposal"])

    def test_datetime_and_assignment_representation(self):
        for value in (self.now, self.now.replace(tzinfo=timezone.utc)):
            proposal = ActionProposal(**(self.data | dict(field="start_date", proposed_value=value.isoformat())))
            self.assertEqual(proposal.proposed_value, value)
            self.assertEqual(proposal.proposed_value.tzinfo, value.tzinfo)
        proposal = ActionProposal(**(self.data | dict(field="assignee_ids", proposed_value=("b", "a"))))
        self.assertEqual(proposal.proposed_value, ["b", "a"])
        self.card.priority = "high"
        self.evidence.provenance.payload["priority"] = "high"
        self.assertEqual(self.errors(current_value="high"), ["no_change"])
        self.card.assignee_ids = ["a", "b"]
        self.card.observed_fields = ["assignee_ids"]
        self.evidence.provenance.payload = {"assignee_ids": ["b", "a"]}
        self.assertEqual(self.errors(field="assignee_ids", current_value=["b", "a"],
                                    proposed_value=["a", "b", "a"]), ["no_change"])

    def test_capture_project_target_and_observation_fail_closed(self):
        self.assertEqual(self.errors(observed_capture_id="legacy-id"), ["capture_mismatch"])
        self.capture.content_hash = "b" * 64
        self.assertIn("invalid_capture", self.errors())
        self.capture.content_hash = self.capture.capture_id = "not-a-hash"
        self.assertIn("invalid_capture", self.errors())
        self.capture.content_hash = self.capture.capture_id = "a" * 64
        self.assertEqual(self.errors(project_id="other"), ["project_mismatch"])
        self.assertEqual(self.errors(task_id="missing"), ["target_not_unique"])
        self.capture.snapshot.tasks.append(self.card)
        self.assertEqual(self.errors(), ["target_not_unique"])
        self.capture.snapshot.tasks.pop()
        for mask in (None, [], ["title"]):
            self.card.observed_fields = mask
            self.assertEqual(self.errors(), ["field_not_observed"])
        self.card.observed_fields = ["priority"]
        self.assertIn("current_value_mismatch", self.errors(current_value="low"))
        self.card.has_contradiction = True
        self.assertIn("conflicting_task", self.errors())

    def test_evidence_ids_and_claims_fail_closed(self):
        for ids in ([], ["e1", "e1"]):
            self.assertEqual(self.errors(evidence_ids=ids), ["invalid_evidence_ids"])
        self.assertEqual(self.errors(evidence_ids=["absent"]), ["evidence_not_unique"])
        proposal = ActionProposal(**self.data)
        self.assertEqual(validate_action_proposal(proposal, self.capture,
                         [self.evidence, self.evidence]), ["evidence_not_unique"])
        for change in ({"project_id": "other"}, {"external_id": "wrong"}):
            evidence = self.evidence.model_copy(deep=True)
            for key, value in change.items():
                setattr(evidence.provenance, key, value)
            self.assertEqual(validate_action_proposal(proposal, self.capture, [evidence]),
                             ["evidence_target_mismatch"])
        self.evidence.category = "diff"
        self.assertEqual(self.errors(), ["evidence_target_mismatch"])
        self.evidence.category = "task"
        for payload in ({}, {"priority": "low"}, {"priority": True},
                        {"priority": None, "observed_fields": []}):
            self.evidence.provenance.payload = payload
            self.assertIn("evidence_fact_mismatch", self.errors())
        for key in ("task_id", "card_id", "external_id"):
            self.evidence.provenance.payload = {"priority": None, key: "wrong"}
            self.assertEqual(self.errors(), ["evidence_target_mismatch"])
        self.evidence.provenance.external_id = None
        self.evidence.provenance.payload = {"priority": None}
        self.assertEqual(self.errors(), ["evidence_target_mismatch"])
        self.evidence.provenance.payload["task_id"] = "task"
        self.assertEqual(self.errors(), [])

    def test_pure_nonmutating_and_constructed_claims_rejected(self):
        proposal = ActionProposal(**self.data)
        before = [obj.model_dump_json() for obj in (proposal, self.capture, self.evidence)]
        with patch("socket.socket", side_effect=AssertionError("No network")):
            self.assertEqual(validate_action_proposal(proposal, self.capture, [self.evidence]), [])
        self.assertEqual(before, [obj.model_dump_json() for obj in (proposal, self.capture, self.evidence)])
        for claims in ({"authorization_status": "authorized"}, {"actor": "admin"},
                       {"permissions": ["execute"]}):
            forged = proposal.model_copy(update=claims)
            self.assertEqual(validate_action_proposal(forged, self.capture, [self.evidence]),
                             ["invalid_proposal"])
