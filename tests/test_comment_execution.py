"""Existing execution service persists append receipts, decisions and replay barriers."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.adapters.sources.sandbox_task_actions import SandboxTaskActions
from agente_gestion.application.proposal_execution import ProposalExecutionService, ProposalExecutionInput, ProposalExecutionError
from tests.test_corrective_comments import setup_comment, TEXT


class CommentServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = SQLiteStorageAdapter(str(Path(self.temp.name) / "test.sqlite"))
        self.card, self.capture, self.evidence, self.proposal = setup_comment(self.storage)
        self.storage.save_action_proposal(self.proposal, self.evidence)
        self.service = ProposalExecutionService(self.storage)

    def execute(self, body=None, key="one", live=False):
        return self.service.execute(project_id="HORMIGESTION", proposal_id=self.proposal.proposal_id,
            body=ProposalExecutionInput.model_validate(body or {"decision": "approve_and_execute", "reason": "Reviewed exact text"}),
            actor_id="simulated-manager", session_cookie="isolated-test-session", idempotency_key=key,
            source_mode="real" if live else "fixtures", external_writes_enabled=live,
            trello_api_key="fake-key" if live else None, trello_token="fake-token" if live else None,
            trello_board_id="board", trello_priority_labels={})

    def test_approval_replay_and_different_key_do_not_append_twice(self):
        with patch.object(SandboxTaskActions, "append_card_comment", autospec=True,
                          side_effect=SandboxTaskActions.append_card_comment) as append:
            receipt = self.execute()
            self.assertEqual(receipt.execution["status"], "SIMULATED")
            self.assertEqual(receipt.execution["verification_status"], "verified")
            self.assertTrue(receipt.execution["native_action_id"].startswith("sandbox-comment-"))
            self.assertTrue(self.execute().replayed)
            with self.assertRaises(ProposalExecutionError):
                self.execute(key="different")
            self.assertEqual(append.call_count, 1)

    def test_modification_gets_new_proposal_review_and_exact_text(self):
        modified = TEXT + "\nAttach evidence of the existing criteria."
        receipt = self.execute({"decision": "modify_and_execute", "modified_comment": modified, "reason": "Reviewed revision"})
        self.assertNotEqual(receipt.proposal_id, self.proposal.proposal_id)
        self.assertEqual(receipt.technical_review["technical_status"], "ready")
        self.assertEqual(receipt.execution["value_proposed"], modified)
        with self.assertRaises(ProposalExecutionError):
            self.execute(key="original-after-modification")

    def test_reject_has_no_append(self):
        with patch.object(SandboxTaskActions, "append_card_comment") as append:
            receipt = self.execute({"decision": "reject", "reason": "Do not publish"})
            self.assertEqual(receipt.execution["result_status"], "DENIED")
            append.assert_not_called()

    def test_changed_text_invalidates_the_original_authorization(self):
        from agente_gestion.domain.action_execution import execute_proposal_action, ExecutionRequest
        self.execute()
        digest, events, projection = self.storage.get_proposal_review_state("HORMIGESTION", self.proposal.proposal_id)
        with self.storage._get_connection() as db:
            raw = db.execute("SELECT request_json FROM proposal_executions").fetchone()[0]
        request = ExecutionRequest.model_validate_json(raw)
        port = SandboxTaskActions({self.card.id: self.card.model_dump(mode="json")})
        result = execute_proposal_action(request=request, proposal=self.proposal.model_copy(update={"proposed_value": TEXT + " altered"}),
            capture=self.capture, evidence_items=self.evidence, proposal_body_sha256=digest,
            events=events, project_captures=[self.capture], action_port=port)
        self.assertEqual(result.status, "DENIED")
        self.assertIn("comment_content_binding_mismatch", result.reasons)
        self.assertEqual(port.mutation_log, [])

    def test_external_switch_off_and_reject_cannot_smuggle_an_edited_comment(self):
        for decision in ("approve_and_execute", "reject"):
            with self.assertRaises(ValueError):
                ProposalExecutionInput(decision=decision, modified_comment=TEXT, reason="Invalid payload")
        with patch("requests.sessions.Session.request", side_effect=AssertionError("No network permitted")) as network:
            self.execute()
            network.assert_not_called()

    def test_uncertain_live_append_replays_failure_without_a_second_call(self):
        from agente_gestion.adapters.sources.trello_action_adapter import TrelloActionAdapter
        with patch.object(TrelloActionAdapter, "has_write_permission", return_value=True), \
             patch.object(TrelloActionAdapter, "read_comment_target", return_value={
                 **self.card.model_dump(mode="json"), "priority_label_ids": [self.proposal.comment_context.priority_label_id]}), \
             patch.object(TrelloActionAdapter, "append_card_comment", side_effect=TimeoutError("uncertain")) as append:
            receipt = self.execute(live=True)
            self.assertEqual(receipt.execution["result_status"], "FAILED")
            self.assertTrue(self.execute(live=True).replayed)
            with self.assertRaises(ProposalExecutionError):
                self.execute({"decision": "modify_and_execute", "modified_comment": TEXT + " revised", "reason": "Retry"}, key="other", live=True)
            self.assertEqual(append.call_count, 1)


if __name__ == "__main__":
    unittest.main()
