"""Isolated HTTP walkthrough from deterministic Monitoring to Trello sandbox decisions."""
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from agente_gestion.api.app import create_app
from agente_gestion.api.settings import ApiSettings, ProjectConfig
from agente_gestion.application.orchestrator import OrchestratorConfig, ProjectOrchestrator
from agente_gestion.application.proposal_auto_review import ProposalAutoReviewService
from agente_gestion.domain.llm import GenerationResult, GenerationStatus
from agente_gestion.domain.report import DomainProposalRuleSet
from agente_gestion.domain.run import RunStatus
from tests import test_monitoring_proposals, test_orchestrator


class Req08DecisionWorkflowTests(unittest.TestCase):
    PROJECT = "P"
    ORIGIN = "http://127.0.0.1:5173"
    OPERATOR_TOKEN = "t2-fixture-operator-token-never-use-in-production"
    ACTOR = "gestora-e2e-fixture"

    def _harness(self, *, evidence=True, future_capture=False, authenticate=True):
        base = test_orchestrator.OrchestratorTestCase()
        base.setUp()
        self.addCleanup(base.tearDown)

        monitoring = test_monitoring_proposals.MonitoringProposalsTests()
        monitoring.setUp()
        monitoring.task.due_date = datetime(2026, 10, 14, 17)
        monitoring.evidence.provenance.payload.update(
            due_date=monitoring.task.due_date.isoformat(), observed_fields=["due_date"],
        )
        monitoring.rules["rules"][0].update(
            field="due_date", allowed_values=["2026-10-18T17:00:00"],
        )
        snapshot = base._sample_snapshot_no_code()
        snapshot.project_id = self.PROJECT
        snapshot.task_source_identity = "fixture:trello-board"
        snapshot.tasks_complete = True
        snapshot.tasks = [monitoring.task]

        if future_capture:
            later = snapshot.model_copy(update={
                "id": "later-capture",
                "cutoff_datetime": snapshot.cutoff_datetime + timedelta(days=10),
                "captured_at": snapshot.captured_at + timedelta(days=10),
            })
            base.sqlite.save_snapshot_with_capture_receipt(later)

        collection = SimpleNamespace(
            snapshot=snapshot,
            evidence=[monitoring.evidence] if evidence else [],
            target_commit_sha=None,
            records=[],
        )
        source = SimpleNamespace(collect_snapshot=Mock(return_value=collection))
        base.fake_provider.enqueue_responses([GenerationResult(
            status=GenerationStatus.complete,
            structured_data={
                "summary": "Weekly assessment.",
                "referenced_task_ids": ["T"],
                "evidence_ids": ["T"],
                "selected_choice_ids": ["choice-0"],
            },
            provider="fake_simulated", model="fake", duration_seconds=0.01,
        )])
        orchestrator = ProjectOrchestrator(
            base.sqlite, base.artifacts,
            base.fake_provider, base.fake_provider,
            aprobador_provider=base.fake_provider, source_adapter=source,
            config=OrchestratorConfig(project_id=self.PROJECT),
        )
        run = orchestrator.execute(
            proposal_rules=DomainProposalRuleSet.model_validate(monitoring.rules),
        )
        self.assertEqual(run.status, RunStatus.awaiting_human)
        self.assertIsNotNone(base.sqlite.get_report(run.report_id))

        root = Path(base.temp_dir)
        settings = ApiSettings(
            db_path=base.db_path,
            runs_dir=base.runs_dir,
            outbox_dir=str(root / "outbox"),
            job_ledger_path=str(root / "jobs.sqlite"),
            branches_dir=str(root / "branches"),
            projects=(ProjectConfig(id=self.PROJECT, name="Proyecto fixture", source_mode="fixtures"),),
            analysis_enabled=False,
            governance_enabled=True,
            operator_token=SecretStr(self.OPERATOR_TOKEN),
            operator_actor=self.ACTOR,
            operator_project_ids=(self.PROJECT,),
            external_writes_enabled=False,
        )
        client = TestClient(create_app(settings), base_url="http://127.0.0.1")
        self.addCleanup(client.close)
        csrf = None
        if authenticate:
            login = client.post(
                "/api/v1/auth/login", json={"token": self.OPERATOR_TOKEN},
                headers={"Origin": self.ORIGIN},
            )
            self.assertEqual(login.status_code, 200, login.text)
            csrf = login.json()["csrf_token"]
        return base, client, csrf, run

    def _proposal_list(self, client):
        response = client.get(f"/api/v1/projects/{self.PROJECT}/proposals?page_size=100")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _proposal_detail(self, client, proposal_id):
        response = client.get(
            f"/api/v1/projects/{self.PROJECT}/proposals/{proposal_id}"
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _decide(self, client, csrf, proposal_id, body, key):
        headers = {"Origin": self.ORIGIN, "Idempotency-Key": key}
        if csrf is not None:
            headers["X-CSRF-Token"] = csrf
        return client.post(
            f"/api/v1/projects/{self.PROJECT}/proposals/{proposal_id}/execute",
            json=body, headers=headers,
        )

    def _audit_event(self, client, *, event_type, proposal_id, outcome):
        response = client.get(
            f"/api/v1/projects/{self.PROJECT}/audit",
            params={"event_type": event_type, "page_size": 100},
        )
        self.assertEqual(response.status_code, 200, response.text)
        matches = [item for item in response.json()["items"]
                   if item["resource_id"] == proposal_id]
        self.assertEqual(len(matches), 1)
        event = matches[0]
        self.assertEqual(event["outcome"], outcome)
        self.assertIsNotNone(datetime.fromisoformat(event["occurred_at"]).tzinfo)
        detail = client.get(
            f"/api/v1/projects/{self.PROJECT}/audit/{event['event_id']}"
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        return event, detail.json()

    def _assert_public_receipt_audit(self, client, proposal_id, *, outcome, reason):
        self._audit_event(
            client, event_type="proposal_execution", proposal_id=proposal_id, outcome=outcome,
        )
        _event, security = self._audit_event(
            client, event_type="security", proposal_id=proposal_id,
            outcome="granted" if outcome == "EXECUTED" else "denied",
        )
        self.assertEqual(security["actor_id"], self.ACTOR)
        self.assertEqual(security["actor_verification"], "verified_local_session")
        self.assertEqual(security["details"]["human_reason"], reason)
        self.assertEqual(security["details"]["permission"], "web:proposal_execute")

    def test_monitoring_pipeline_publishes_proposal_and_missing_evidence_publishes_none(self):
        _base, client, _csrf, _run = self._harness()
        proposals = self._proposal_list(client)
        self.assertEqual(proposals["total"], 1)
        summary = proposals["items"][0]
        self.assertEqual(summary["technical_eligibility"], "eligible")
        self.assertEqual(summary["review_status"], "pending")
        self.assertEqual(summary["authorization_status"], "not_authorized")
        self.assertEqual(summary["execution_status"], "not_executed")
        detail = self._proposal_detail(client, summary["proposal_id"])
        self.assertEqual(detail["evidence"][0]["evidence_id"], "E")
        self.assertEqual(detail["technical_review"]["technical_status"], "ready")
        self.assertFalse(detail["technical_review"]["execution_authorized"])
        self.assertEqual(detail["relationship_availability"], "available")
        self.assertIsNone(detail["original_proposal_id"])
        self.assertEqual(detail["derived_proposal_ids"], [])
        self.assertEqual(detail["decision_history"], [])
        self.assertEqual(detail["decision_history_availability"], "available")
        self.assertIsNone(detail["latest_receipt"])

        _base, empty_client, empty_csrf, _empty_run = self._harness(evidence=False)
        empty = self._proposal_list(empty_client)
        self.assertEqual(empty["total"], 0)
        unavailable = self._decide(empty_client, empty_csrf, "proposal-without-evidence", {
            "decision": "approve_and_execute", "reason": "No hay evidencia suficiente.",
        }, "t2-no-evidence-key")
        self.assertEqual(unavailable.status_code, 404, unavailable.text)
        audit = empty_client.get(
            f"/api/v1/projects/{self.PROJECT}/audit",
            params={"event_type": "proposal_execution", "page_size": 100},
        )
        self.assertEqual(audit.status_code, 200, audit.text)
        self.assertEqual(audit.json()["total"], 0)

    def test_approval_is_sandboxed_replayable_and_auditable_through_public_http(self):
        _base, client, csrf, _run = self._harness()
        proposal_id = self._proposal_list(client)["items"][0]["proposal_id"]
        reason = "Aprobar en sandbox tras revisar la evidencia."
        body = {"decision": "approve_and_execute", "reason": reason}
        paths = client.app.openapi()["paths"]
        self.assertIn("/api/v1/projects/{project_id}/proposals/{proposal_id}/execute", paths)
        self.assertIn("/api/v1/runs/{run_id}/findings/{finding_id}/decision", paths)

        with patch("agente_gestion.application.proposal_execution.TrelloActionAdapter") as trello_port, \
                patch("requests.get") as get, patch("requests.put") as put, patch("requests.post") as post:
            response = self._decide(client, csrf, proposal_id, body, "t2-approve-key")
            replay = self._decide(client, csrf, proposal_id, body, "t2-approve-key")
        self.assertEqual(response.status_code, 200, response.text)
        receipt = response.json()
        self.assertEqual(receipt["execution_mode"], "sandbox")
        self.assertEqual(receipt["execution"]["status"], "SIMULATED")
        self.assertEqual(receipt["execution"]["result_status"], "EXECUTED")
        self.assertEqual(receipt["execution"]["verification_status"], "verified")
        self.assertFalse(receipt["execution"]["live_state_changed"])
        self.assertEqual(receipt["execution"]["actor_id"], self.ACTOR)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(replay.json()["execution"]["execution_id"], receipt["execution"]["execution_id"])
        trello_port.assert_not_called()
        get.assert_not_called()
        put.assert_not_called()
        post.assert_not_called()

        detail = self._proposal_detail(client, proposal_id)
        self.assertEqual(detail["review_status"], "approved_offline")
        self.assertEqual(detail["execution_status"], "EXECUTED")
        self.assertEqual(detail["decision_history_availability"], "available")
        self.assertEqual(len(detail["decision_history"]), 1)
        decision = detail["decision_history"][0]
        self.assertEqual(decision["decision"], "approve_and_execute")
        self.assertEqual(decision["actor_id"], self.ACTOR)
        self.assertEqual(decision["actor_verification"], "verified_local_session")
        self.assertEqual(decision["reason"], reason)
        self.assertIsNotNone(datetime.fromisoformat(decision["recorded_at"]).tzinfo)
        self.assertEqual(detail["latest_receipt"]["execution_mode"], "sandbox")
        self.assertEqual(detail["latest_receipt"]["status"], "EXECUTED")
        self.assertEqual(detail["latest_receipt"]["verification_status"], "verified")
        self.assertEqual(detail["latest_receipt"]["reasons"], [])
        self.assertIsNotNone(detail["latest_receipt"]["audit_event_id"])
        self._assert_public_receipt_audit(client, proposal_id, outcome="EXECUTED", reason=reason)
        execution_events = client.get(
            f"/api/v1/projects/{self.PROJECT}/audit",
            params={"event_type": "proposal_execution", "page_size": 100},
        ).json()
        self.assertEqual(execution_events["total"], 1)

    def test_incomplete_execution_outcomes_never_expose_unproven_receipt_fields(self):
        for outcome_state in ("missing", "tampered"):
            with self.subTest(outcome_state=outcome_state):
                base, client, csrf, _run = self._harness()
                proposal_id = self._proposal_list(client)["items"][0]["proposal_id"]
                response = self._decide(client, csrf, proposal_id, {
                    "decision": "approve_and_execute",
                    "reason": "Aprobación aislada para probar integridad del recibo.",
                }, f"t3-{outcome_state}-outcome-key")
                self.assertEqual(response.status_code, 200, response.text)
                execution_id = response.json()["execution"]["execution_id"]
                proven_detail = self._proposal_detail(client, proposal_id)
                self.assertEqual(proven_detail["receipt_availability"], "available")
                self.assertEqual(proven_detail["latest_receipt"]["execution_mode"], "sandbox")
                self.assertEqual(
                    proven_detail["latest_receipt"]["actor_verification"], "verified_local_session",
                )
                public_fields = set(self._proposal_list(client)["items"][0])
                proven_public_fields = {key: proven_detail[key] for key in public_fields}

                with sqlite3.connect(base.db_path) as connection:
                    row = connection.execute(
                        "SELECT request_id FROM proposal_executions WHERE execution_id=?",
                        (execution_id,),
                    ).fetchone()
                    self.assertIsNotNone(row)
                    request_id = row[0]
                    outcome = connection.execute(
                        "SELECT response_sha256 FROM proposal_execution_outcomes WHERE execution_id=?",
                        (request_id,),
                    ).fetchone()
                    self.assertIsNotNone(outcome)
                    if outcome_state == "missing":
                        connection.execute(
                            "DROP TRIGGER IF EXISTS proposal_execution_outcomes_no_DELETE",
                        )
                        cursor = connection.execute(
                            "DELETE FROM proposal_execution_outcomes WHERE execution_id=?",
                            (request_id,),
                        )
                        self.assertEqual(cursor.rowcount, 1)
                    else:
                        connection.execute(
                            "DROP TRIGGER IF EXISTS proposal_execution_outcomes_no_UPDATE",
                        )
                        cursor = connection.execute(
                            "UPDATE proposal_execution_outcomes SET response_sha256=? WHERE execution_id=?",
                            ("0" * 64, request_id),
                        )
                        self.assertEqual(cursor.rowcount, 1)
                    self.assertEqual(connection.execute(
                        "SELECT COUNT(*) FROM proposal_executions WHERE execution_id=?",
                        (execution_id,),
                    ).fetchone()[0], 1)
                    self.assertEqual(connection.execute(
                        "SELECT COUNT(*) FROM security_audit_log "
                        "WHERE project_id=? AND resource_id=? AND permission_checked='web:proposal_execute'",
                        (self.PROJECT, proposal_id),
                    ).fetchone()[0], 1)

                incomplete = self._proposal_detail(client, proposal_id)
                self.assertEqual(incomplete["receipt_availability"], "unknown")
                self.assertEqual(incomplete["relationship_availability"], "unknown")
                self.assertEqual(incomplete["decision_history_availability"], "unknown")
                self.assertIsNone(incomplete["latest_receipt"])
                self.assertIsNone(incomplete["original_proposal_id"])
                self.assertIsNone(incomplete["derived_proposal_ids"])
                self.assertEqual(
                    {key: incomplete[key] for key in public_fields}, proven_public_fields,
                )
                self.assertTrue(any(
                    item["decision"] == "approve_offline"
                    and item["actor_verification"] == "asserted_offline"
                    for item in incomplete["decision_history"]
                ))
                self.assertFalse(any(
                    item["actor_verification"] == "verified_local_session"
                    or item.get("execution_mode") is not None
                    for item in incomplete["decision_history"]
                ))

    def test_modified_proposal_is_revalidated_against_its_new_immutable_digest(self):
        _base, client, csrf, _run = self._harness()
        original_id = self._proposal_list(client)["items"][0]["proposal_id"]
        original_before = self._proposal_detail(client, original_id)
        reason = "Fecha corregida y revisada por la gestora."
        response = self._decide(client, csrf, original_id, {
            "decision": "modify_and_execute",
            "modified_due_date": "2026-10-20T17:00:00-05:00",
            "reason": reason,
        }, "t2-modify-key")
        self.assertEqual(response.status_code, 200, response.text)
        receipt = response.json()
        derived_id = receipt["proposal_id"]
        self.assertEqual(receipt["original_proposal_id"], original_id)
        self.assertNotEqual(derived_id, original_id)
        self.assertEqual(receipt["execution_mode"], "sandbox")
        self.assertEqual(receipt["execution"]["status"], "SIMULATED")
        self.assertEqual(receipt["execution"]["verification_status"], "verified")
        self.assertEqual(receipt["proposal"]["rationale"], reason)

        original_after = self._proposal_detail(client, original_id)
        derived = self._proposal_detail(client, derived_id)
        relationship_fields = {
            "relationship_availability", "original_proposal_id", "derived_proposal_ids",
            "decision_history_availability", "decision_history", "receipt_availability", "latest_receipt",
        }
        self.assertEqual(
            {key: value for key, value in original_after.items() if key not in relationship_fields},
            {key: value for key, value in original_before.items() if key not in relationship_fields},
        )
        self.assertEqual(original_after["proposal_body_sha256"], original_before["proposal_body_sha256"])
        self.assertEqual(original_after["version"], original_before["version"])
        self.assertEqual(original_after["review_status"], original_before["review_status"])
        self.assertEqual(original_after["authorization_status"], original_before["authorization_status"])
        self.assertEqual(original_after["execution_status"], original_before["execution_status"])
        self.assertEqual(original_after["original_proposal_id"], None)
        self.assertEqual(original_after["derived_proposal_ids"], [derived_id])
        self.assertEqual(derived["original_proposal_id"], original_id)
        self.assertEqual(derived["derived_proposal_ids"], [])
        self.assertNotEqual(derived["proposal_body_sha256"], original_before["proposal_body_sha256"])
        self.assertEqual(
            receipt["technical_review"]["proposal_body_sha256"], derived["proposal_body_sha256"],
        )
        self.assertEqual(
            receipt["technical_review"]["review_id"], derived["technical_review"]["review_id"],
        )
        self.assertEqual(derived["technical_review"]["technical_status"], "ready")
        self.assertEqual(derived["execution_status"], "EXECUTED")
        self.assertEqual(self._proposal_list(client)["pending_actionable_count"], 0)
        self.assertEqual(original_after["decision_history"][0]["decision"], "modify_and_execute")
        self.assertEqual(original_after["decision_history"][0]["related_proposal_id"], derived_id)
        self._assert_public_receipt_audit(client, derived_id, outcome="EXECUTED", reason=reason)

    def test_rejection_remains_available_for_technically_blocked_proposal(self):
        _base, client, csrf, _run = self._harness(future_capture=True)
        proposal_id = self._proposal_list(client)["items"][0]["proposal_id"]
        before = self._proposal_detail(client, proposal_id)
        self.assertEqual(before["freshness"], "stale")
        self.assertEqual(before["technical_review"]["technical_status"], "blocked")
        reason = "Rechazo la propuesta obsoleta; no corresponde ejecutar."
        response = self._decide(client, csrf, proposal_id, {
            "decision": "reject", "reason": reason,
        }, "t2-reject-blocked-key")
        self.assertEqual(response.status_code, 200, response.text)
        receipt = response.json()
        self.assertEqual(receipt["execution_mode"], "sandbox")
        self.assertEqual(receipt["execution"]["status"], "DENIED")
        self.assertIn("rejected_by_manager", receipt["execution"]["reasons"])
        after = self._proposal_detail(client, proposal_id)
        self.assertEqual(after["review_status"], "rejected")
        self.assertEqual(after["execution_status"], "DENIED")
        self.assertEqual(after["decision_history"][0]["decision"], "reject")
        self.assertEqual(after["decision_history"][0]["actor_id"], self.ACTOR)
        self.assertEqual(after["decision_history"][0]["reason"], reason)
        self.assertEqual(after["latest_receipt"]["status"], "DENIED")
        self.assertEqual(after["latest_receipt"]["execution_mode"], "sandbox")
        self._assert_public_receipt_audit(client, proposal_id, outcome="DENIED", reason=reason)

    def test_missing_operator_session_cannot_decide_or_read_sensitive_proposal_ledgers(self):
        _base, client, _csrf, _run = self._harness(authenticate=False)
        proposal_id = self._proposal_list(client)["items"][0]["proposal_id"]
        before = self._proposal_detail(client, proposal_id)
        self.assertTrue({"decision_history", "latest_receipt", "original_proposal_id", "derived_proposal_ids"}.isdisjoint(before))
        denied = self._decide(client, None, proposal_id, {
            "decision": "approve_and_execute", "reason": "Intento sin sesión.",
        }, "t2-no-session-key")
        self.assertEqual(denied.status_code, 401, denied.text)
        self.assertEqual(self._proposal_detail(client, proposal_id), before)
        audit = client.get(
            f"/api/v1/projects/{self.PROJECT}/proposals?page_size=100"
        )
        self.assertEqual(audit.status_code, 200, audit.text)
        self.assertEqual(audit.json()["items"][0]["execution_status"], "not_executed")

    def test_pending_actionable_count_is_project_wide_across_pages_and_excludes_decided(self):
        base, client, csrf, _run = self._harness()
        original_id = self._proposal_list(client)["items"][0]["proposal_id"]
        original, evidence = base.sqlite.get_action_proposal(original_id)
        capture = base.sqlite.get_snapshot_capture(original.observed_capture_id)
        self.assertIsNotNone(capture)
        for index in range(1, 4):
            proposal = original.model_copy(update={"proposal_id": f"proposal-count-{index:02d}"})
            base.sqlite.save_action_proposal(proposal, evidence)
            review = ProposalAutoReviewService(base.sqlite).review_and_record(
                proposal, capture, evidence, project_captures=[capture],
            )
            self.assertEqual(review.technical_status, "ready")

        first_page = client.get(
            f"/api/v1/projects/{self.PROJECT}/proposals?page=1&page_size=1"
        ).json()
        last_page = client.get(
            f"/api/v1/projects/{self.PROJECT}/proposals?page=4&page_size=1"
        ).json()
        self.assertEqual(first_page["total"], 4)
        self.assertEqual(len(first_page["items"]), 1)
        self.assertEqual(len(last_page["items"]), 1)
        self.assertEqual(first_page["pending_actionable_count"], 4)
        self.assertEqual(last_page["pending_actionable_count"], 4)

        rejected = self._decide(client, csrf, "proposal-count-01", {
            "decision": "reject", "reason": "Propuesta descartada con fundamento.",
        }, "t3-count-reject")
        self.assertEqual(rejected.status_code, 200, rejected.text)
        approved = self._decide(client, csrf, "proposal-count-02", {
            "decision": "approve_and_execute", "reason": "Propuesta ejecutada en sandbox.",
        }, "t3-count-approve")
        self.assertEqual(approved.status_code, 200, approved.text)

        page_after_decisions = client.get(
            f"/api/v1/projects/{self.PROJECT}/proposals?page=1&page_size=1"
        ).json()
        final_page = client.get(
            f"/api/v1/projects/{self.PROJECT}/proposals?page=4&page_size=1"
        ).json()
        self.assertEqual(page_after_decisions["total"], 4)
        self.assertEqual(page_after_decisions["pending_actionable_count"], 2)
        self.assertEqual(final_page["pending_actionable_count"], 2)

    def test_legacy_unavailable_ledgers_remain_unknown(self):
        _base, client, _csrf, _run = self._harness()
        proposal_id = self._proposal_list(client)["items"][0]["proposal_id"]
        control_plane = client.app.state.control_plane
        tables_method = control_plane._tables

        def legacy_tables(connection):
            return tables_method(connection) - {
                "proposal_decisions", "proposal_executions", "proposal_execution_outcomes",
            }

        with patch.object(control_plane, "_tables", side_effect=legacy_tables):
            collection = client.get(f"/api/v1/projects/{self.PROJECT}/proposals").json()
            detail = self._proposal_detail(client, proposal_id)
        self.assertIsNone(collection["pending_actionable_count"])
        self.assertEqual(detail["review_status"], "unknown")
        self.assertEqual(detail["execution_status"], "unknown")
        self.assertEqual(detail["decision_history_availability"], "unknown")
        self.assertEqual(detail["relationship_availability"], "unknown")
        self.assertEqual(detail["receipt_availability"], "unknown")
        self.assertIsNone(detail["latest_receipt"])


if __name__ == "__main__":
    unittest.main()
