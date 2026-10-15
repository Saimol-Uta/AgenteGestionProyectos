"""Public application seams for Phase 2, using disposable state only."""
import unittest
from unittest.mock import patch

from agente_gestion.api.app import create_app
from agente_gestion.application.proposal_auto_review import ProposalAutoReviewService
from agente_gestion.adapters.sources.sandbox_task_actions import SandboxTaskActions
from tests import test_phase2_trello as trello_fixture


class Phase2ApplicationIntegrationTests(unittest.TestCase):
    def test_factory_registers_both_governed_action_endpoints(self):
        paths = set(create_app().openapi()["paths"])
        self.assertIn("/api/v1/runs/{run_id}/findings/{finding_id}/decision", paths)
        self.assertIn("/api/v1/projects/{project_id}/proposals/{proposal_id}/execute", paths)

    def _fixture(self, **kwargs):
        helper = trello_fixture.ProposalExecutionApiTests()
        temp, client, csrf, storage = helper._client(**kwargs)
        self.addCleanup(temp.cleanup)
        return helper, client, csrf, storage

    def test_technical_review_is_projected_without_claiming_human_approval(self):
        helper, client, _csrf, storage = self._fixture()
        proposal, evidence = storage.get_action_proposal("proposal-1")
        capture = storage.get_snapshot_capture(proposal.observed_capture_id)
        expected = ProposalAutoReviewService(storage).review_and_record(
            proposal, capture, evidence, project_captures=storage.list_snapshot_captures(helper.PROJECT),
        )
        response = client.get(f"/api/v1/projects/{helper.PROJECT}/proposals/proposal-1")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["review_status"], "pending")
        self.assertEqual(body["technical_review"]["review_id"], expected.review_id)
        self.assertEqual(body["technical_review"]["technical_status"], "ready")
        self.assertEqual(body["technical_review"]["dry_run"]["status"], "SIMULATED")
        self.assertFalse(body["technical_review"]["execution_authorized"])

    def test_live_execution_requires_explicit_board_binding_before_adapter_calls(self):
        helper, client, csrf, _storage = self._fixture(source_mode="real", external_writes_enabled=True)
        client.app.state.settings = client.app.state.settings.model_copy(update={"trello_board_ids": {}})
        with patch("agente_gestion.application.proposal_execution.TrelloActionAdapter") as adapter:
            response = helper._execute(client, csrf)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["error"]["code"], "tablero_trello_requerido")
        adapter.assert_not_called()

    def test_live_execution_uses_configured_board_not_logical_project_id(self):
        helper, client, csrf, storage = self._fixture(source_mode="real", external_writes_enabled=True)
        client.app.state.settings = client.app.state.settings.model_copy(update={
            "trello_board_ids": {helper.PROJECT: "actual-board-id"},
        })
        proposal, _evidence = storage.get_action_proposal("proposal-1")
        port = SandboxTaskActions({"card-1": {"due_date": proposal.current_value}})
        with patch("agente_gestion.application.proposal_execution.TrelloActionAdapter", return_value=port) as adapter:
            response = helper._execute(client, csrf)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["execution"]["status"], "EXECUTED")
        self.assertEqual(adapter.call_args.kwargs["board_id"], "actual-board-id")
