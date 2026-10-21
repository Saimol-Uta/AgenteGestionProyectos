"""Append-only comment acceptance, evidence and existing service regression."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.adapters.sources.trello_action_adapter import TrelloActionAdapter
from agente_gestion.adapters.sources.sandbox_task_actions import SandboxTaskActions
from agente_gestion.domain.action_proposal import ActionProposal, validate_action_proposal
from agente_gestion.domain.evidence import EvidenceItem, EvidenceProvenance
from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.domain.tasks import TaskCard, TaskState

CARD = "6a9582c16a2c16f6106609e2"
LABEL = "6a957e9173d4d13d0c5db616"
TEXT = "Seguimiento de validación: la implementación del módulo Dashboard y reportería se encuentra desarrollada. Permanecen pendientes las pruebas funcionales y la verificación de los criterios de aceptación. Se solicita registrar los resultados y sus evidencias antes de marcar la actividad como completada. Se conserva la fecha y el responsable oficiales."
NOW = datetime(2026, 10, 20, 23, tzinfo=timezone.utc)


def setup_comment(storage):
    card = TaskCard(id=CARD, external_id=CARD, title="Dashboard", state=TaskState.in_progress,
        priority="alta", due_date=datetime(2026, 10, 19, 22, tzinfo=timezone.utc),
        due_complete=False, is_archived=False, assignee_ids=["owner"], list_id="phase3",
        observed_fields=["priority", "due_date", "due_complete", "is_archived"])
    capture = storage.save_snapshot_with_capture_receipt(WeeklySnapshot(id="comment-snapshot", project_id="HORMIGESTION",
        captured_at=NOW, cutoff_datetime=NOW, tasks=[card], tasks_complete=True, task_source_identity="trello:official"))
    evidence = [EvidenceItem(category="task", summary="Observed official task", provenance=EvidenceProvenance(
        id="task-evidence", source="trello", project_id="HORMIGESTION", external_id=CARD, timestamp=NOW,
        payload={**card.model_dump(mode="json"), "priority_label_ids": [LABEL]})),
        EvidenceItem(category="human_confirmation", summary="Owner confirms implementation developed, tests pending",
        provenance=EvidenceProvenance(id="human-evidence", source="manual", project_id="HORMIGESTION",
        external_id=CARD, timestamp=NOW, payload={"implementation_developed": True, "validation_pending": True,
                                               "confirmed_by": "owner-confirmation"}))]
    proposal = ActionProposal(proposal_id="dashboard-followup", project_id="HORMIGESTION", task_id=CARD,
        observed_capture_id=capture.capture_id, field="comment", operation="append_comment", current_value=None,
        proposed_value=TEXT, evidence_ids=[e.provenance.id for e in evidence],
        comment_context={"priority_label_id": LABEL, "expected_due_date": "2026-10-19", "human_evidence_id": "human-evidence"},
        rationale="Owner requests functional validation evidence", expected_impact="Verify acceptance before closing")
    return card, capture, evidence, proposal


class CommentContractTests(unittest.TestCase):
    def test_deterministic_choices_require_separate_human_and_task_evidence(self):
        from agente_gestion.agents.context import monitoring_choices
        from agente_gestion.domain.report import DomainProposalRuleSet
        from agente_gestion.domain.risks import build_task_risks
        with tempfile.TemporaryDirectory() as root:
            storage = SQLiteStorageAdapter(str(Path(root) / "db.sqlite"))
            card, capture, evidence, proposal = setup_comment(storage)
            rules = DomainProposalRuleSet(project_id="HORMIGESTION", rules=[dict(rule_id="dashboard-validation",
                task_ids=[CARD], field="comment", operation="append_comment", comment_context=proposal.comment_context,
                allowed_values=[TEXT], rationale=proposal.rationale, expected_impact=proposal.expected_impact,
                risk_kind="overdue")])
            risks = build_task_risks(capture.snapshot, capture.capture_id, timezone_name="America/Guayaquil")
            choices, errors = monitoring_choices("HORMIGESTION", [card], [card.model_dump(mode="json")], rules, evidence, risks)
            self.assertEqual(errors, [])
            self.assertEqual(len(choices), 1)
            self.assertEqual(choices[0].proposed_value, TEXT)
            self.assertEqual(choices[0].operation, "append_comment")
            self.assertEqual(monitoring_choices("HORMIGESTION", [card], [], rules, evidence[:1], risks)[0], [])

    def test_comment_is_evidence_bound_not_a_mutable_field(self):
        with tempfile.TemporaryDirectory() as root:
            storage = SQLiteStorageAdapter(str(Path(root) / "db.sqlite"))
            card, capture, evidence, proposal = setup_comment(storage)
            self.assertEqual(validate_action_proposal(proposal, capture, evidence), [])
            storage.save_action_proposal(proposal, evidence)
            self.assertEqual(storage.get_action_proposal(proposal.proposal_id)[0], proposal)
            self.assertTrue(validate_action_proposal(proposal, capture, evidence[:1]))
            evidence[1].provenance.payload["validation_pending"] = False
            self.assertTrue(validate_action_proposal(proposal, capture, evidence))

    def test_sandbox_appends_without_replacing_card_fields(self):
        port = SandboxTaskActions({CARD: {"priority": "alta", "due_complete": False}})
        original = dict(port._cards[CARD])
        native_id = port.append_card_comment(CARD, TEXT)
        self.assertTrue(native_id.startswith("sandbox-comment-"))
        self.assertEqual(port.read_card_comment(CARD, native_id), TEXT)
        self.assertEqual(port._cards[CARD], original)
        with self.assertRaises(ValueError):
            port.apply_card_field_update(CARD, "comment", TEXT)

    def test_live_append_guard_and_exact_native_action_readback(self):
        with patch("requests.post") as post:
            with self.assertRaises(ValueError):
                TrelloActionAdapter("board", "key", "token").append_card_comment(CARD, TEXT)
            post.assert_not_called()
        port = TrelloActionAdapter("board", "key", "token", allow_comment_writes=True)
        action = {"id": "action1", "type": "commentCard", "data": {"card": {"id": CARD},
                  "board": {"id": "board"}, "text": TEXT}}
        response = Mock(status_code=200); response.json.return_value = action
        with patch.object(port, "_request_card_data", return_value={"idBoard": "board"}), \
             patch("requests.post", return_value=response) as post, patch("requests.get", return_value=response):
            self.assertEqual(port.append_card_comment(CARD, TEXT), "action1")
            self.assertEqual(port.read_card_comment(CARD, "action1"), TEXT)
            self.assertEqual(post.call_args.kwargs["data"], {"text": TEXT})
            self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
