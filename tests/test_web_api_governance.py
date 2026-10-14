"""HTTP contract coverage for proposal governance, reports, scheduling and audit."""

from datetime import datetime, timezone
import time
import os
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.api.settings import load_api_settings
from agente_gestion.domain.notification_outbox import OutboxMessage, OutboxReceipt
from agente_gestion.domain.weekly_schedule import WeeklySchedule
from tests.web_fixtures import (
    EVIDENCE_SECRET,
    OPERATOR_TOKEN,
    PROJECT_ID,
    REPORT_ID,
    build_web_api_fixture,
)


ORIGIN = "http://testserver"
PROPOSAL_ID = "proposal-date-001"


class WebApiGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = build_web_api_fixture(
            Path(self.temp.name), governance_enabled=True, report_decisions_enabled=True,
        )
        self.client = self.fixture.client
        self.addCleanup(self.client.close)

    def _login(self) -> str:
        response = self.client.post(
            "/api/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"token": OPERATOR_TOKEN},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["csrf_token"]

    def _mutation_headers(self, csrf: str, key: str = "governance-key-0001"):
        return {
            "Origin": ORIGIN,
            "X-CSRF-Token": csrf,
            "Idempotency-Key": key,
            "Content-Type": "application/json",
        }

    def test_read_contracts_project_persisted_proposals_reports_schedule_outbox_and_audit(self):
        proposals = self.client.get(f"/api/v1/projects/{PROJECT_ID}/proposals")
        self.assertEqual(proposals.status_code, 200, proposals.text)
        proposal_page = proposals.json()
        self.assertEqual(proposal_page["availability"], "available")
        self.assertEqual(proposal_page["total"], 1)
        proposal = proposal_page["items"][0]
        self.assertEqual(proposal["proposal_id"], PROPOSAL_ID)
        self.assertEqual(proposal["review_status"], "pending")
        self.assertEqual(proposal["authorization_status"], "not_authorized")
        self.assertEqual(proposal["execution_status"], "not_executed")
        self.assertEqual(proposal["freshness"], "current")
        self.assertNotIn(EVIDENCE_SECRET, proposals.text)
        self.assertNotIn("payload", proposals.text)

        proposal_detail = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/proposals/{PROPOSAL_ID}"
        )
        self.assertEqual(proposal_detail.status_code, 200, proposal_detail.text)
        self.assertEqual(proposal_detail.json()["decision_history"], [])
        self.assertEqual(proposal_detail.json()["dry_runs"], [])

        reports = self.client.get(f"/api/v1/projects/{PROJECT_ID}/reports")
        self.assertEqual(reports.status_code, 200, reports.text)
        self.assertEqual(reports.json()["total"], 1)
        report = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/reports/{REPORT_ID}"
        )
        self.assertEqual(report.status_code, 200, report.text)
        self.assertEqual(report.json()["run_id"], "run-current")
        self.assertEqual(report.json()["human_decision"], "pending")
        self.assertIn("Tarea", report.json()["markdown_text"])
        self.assertNotIn("<script", report.text.lower())
        self.assertNotIn("javascript:", report.text.lower())
        self.assertNotIn("attacker.invalid", report.text)
        self.assertNotIn(EVIDENCE_SECRET, report.text)

        download = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/reports/{REPORT_ID}/download",
            params={"format": "markdown"},
        )
        self.assertEqual(download.status_code, 200, download.text)
        self.assertIn("text/markdown", download.headers["content-type"])
        self.assertIn("attachment", download.headers["content-disposition"])
        self.assertNotIn("<script", download.text.lower())
        self.assertNotIn("javascript:", download.text.lower())
        self.assertNotIn("attacker.invalid", download.text)

        configuration = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/configuration"
        )
        self.assertEqual(configuration.status_code, 200, configuration.text)
        self.assertEqual(configuration.json()["editable_fields"], [])
        self.assertNotIn(EVIDENCE_SECRET, configuration.text)
        self.assertNotIn("api_key", configuration.text.lower())

        schedule = self.client.get(f"/api/v1/projects/{PROJECT_ID}/schedule")
        self.assertEqual(schedule.status_code, 200, schedule.text)
        self.assertEqual(schedule.json()["availability"], "unknown")
        self.assertEqual(schedule.json()["automatic_trigger_status"], "not_verified")

        outbox = self.client.get(f"/api/v1/projects/{PROJECT_ID}/schedule/outbox")
        self.assertEqual(outbox.status_code, 200, outbox.text)
        self.assertEqual(outbox.json()["total"], 1)
        receipt = outbox.json()["items"][0]
        self.assertTrue(receipt["artifact_available"])
        self.assertNotIn("file_path", receipt)
        delivery_id = receipt["delivery_id"]
        outbox_detail = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/schedule/outbox/{delivery_id}"
        )
        self.assertEqual(outbox_detail.status_code, 200, outbox_detail.text)
        self.assertNotIn("<script", outbox_detail.text.lower())
        outbox_download = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/schedule/outbox/{delivery_id}/download",
            params={"format": "markdown"},
        )
        self.assertEqual(outbox_download.status_code, 200, outbox_download.text)
        self.assertNotIn("<script", outbox_download.text.lower())
        self.assertNotIn("attacker.invalid", outbox_download.text)

        self.assertEqual(self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/audit"
        ).status_code, 401)
        csrf = self._login()
        audit = self.client.get(f"/api/v1/projects/{PROJECT_ID}/audit")
        self.assertEqual(audit.status_code, 200, audit.text)
        types = {item["event_type"] for item in audit.json()["items"]}
        self.assertTrue({"analysis_run", "outbox_delivery"}.issubset(types))
        self.assertTrue(all(
            item["actor_id"] is None or item["actor_verification"] == "verified_local_session"
            for item in audit.json()["items"]
        ))

    def test_proposal_review_and_verified_audit_commit_atomically(self):
        csrf = self._login()
        path = f"/api/v1/projects/{PROJECT_ID}/proposals/{PROPOSAL_ID}"
        proposal = self.client.get(path).json()
        body = {
            "decision": "approve",
            "expected_version": proposal["version"],
            "proposal_body_sha256": proposal["proposal_body_sha256"],
            "capture_id": proposal["capture_id"],
        }
        headers = self._mutation_headers(csrf, "atomic-proposal-review-01")
        with sqlite3.connect(self.fixture.database_path) as connection:
            connection.execute(
                "CREATE TRIGGER fail_proposal_audit BEFORE INSERT ON security_audit_log "
                "WHEN NEW.permission_checked='web:proposal_review' "
                "BEGIN SELECT RAISE(ABORT, 'test audit insert fault'); END"
            )
        failed = self.client.post(path + "/review", headers=headers, json=body)
        self.assertEqual(failed.status_code, 503, failed.text)
        after_failure = self.client.get(path)
        self.assertEqual(after_failure.status_code, 200, after_failure.text)
        self.assertEqual(after_failure.json()["review_status"], "pending")
        self.assertEqual(after_failure.json()["version"], 0)
        self.assertEqual(after_failure.json()["decision_history"], [])
        with sqlite3.connect(self.fixture.database_path) as connection:
            audit_count = connection.execute(
                "SELECT count(*) FROM security_audit_log "
                "WHERE project_id=? AND permission_checked='web:proposal_review' AND resource_id=?",
                (PROJECT_ID, PROPOSAL_ID),
            ).fetchone()[0]
        self.assertEqual(audit_count, 0)
        with sqlite3.connect(self.fixture.database_path) as connection:
            connection.execute("DROP TRIGGER fail_proposal_audit")
        retried = self.client.post(path + "/review", headers=headers, json=body)
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertFalse(retried.json()["reused"])
        self.assertEqual(retried.json()["proposal"]["review_status"], "approved_offline")
        replay = self.client.post(path + "/review", headers=headers, json=body)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["reused"])
        with sqlite3.connect(self.fixture.database_path) as connection:
            audit_count = connection.execute(
                "SELECT count(*) FROM security_audit_log "
                "WHERE project_id=? AND permission_checked='web:proposal_review' AND resource_id=?",
                (PROJECT_ID, PROPOSAL_ID),
            ).fetchone()[0]
        self.assertEqual(audit_count, 1)

    def test_review_authorization_and_dry_run_are_distinct_bound_idempotent_operations(self):
        csrf = self._login()
        path = f"/api/v1/projects/{PROJECT_ID}/proposals/{PROPOSAL_ID}"
        proposal = self.client.get(path).json()
        body = {
            "decision": "approve",
            "expected_version": proposal["version"],
            "proposal_body_sha256": proposal["proposal_body_sha256"],
            "capture_id": proposal["capture_id"],
        }
        headers = self._mutation_headers(csrf, "proposal-review-0001")
        missing_csrf = self.client.post(path + "/review", headers={
            "Origin": ORIGIN, "Idempotency-Key": "proposal-review-0001",
        }, json=body)
        self.assertEqual(missing_csrf.status_code, 403)

        reviewed = self.client.post(path + "/review", headers=headers, json=body)
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        result = reviewed.json()
        self.assertFalse(result["reused"])
        self.assertEqual(result["proposal"]["review_status"], "approved_offline")
        self.assertEqual(result["proposal"]["authorization_status"], "not_authorized")
        self.assertEqual(result["proposal"]["execution_status"], "not_executed")
        self.assertIsNone(result["proposal"]["decision_history"][0]["actor_id"])
        self.assertEqual(result["proposal"]["decision_history"][0]["actor_provenance"], "asserted_offline")
        self.assertFalse(result["proposal"].get("real_execution_allowed", False))

        replay = self.client.post(path + "/review", headers=headers, json=body)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["reused"])
        conflict = self.client.post(path + "/review", headers=headers, json={**body, "decision": "reject"})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "idempotencia_conflictiva")

        authorization_body = {
            "authorize": True,
            "expected_version": result["proposal"]["version"],
            "proposal_body_sha256": proposal["proposal_body_sha256"],
            "capture_id": proposal["capture_id"],
        }
        authorized = self.client.post(path + "/dry-run-authorization", headers=self._mutation_headers(
            csrf, "proposal-auth-0001",
        ), json=authorization_body)
        self.assertEqual(authorized.status_code, 200, authorized.text)
        self.assertEqual(authorized.json()["proposal"]["authorization_status"], "authorized_dry_run")
        self.assertEqual(authorized.json()["proposal"]["version"], 2)
        permission_event = next(
            event["event_id"] for event in authorized.json()["proposal"]["decision_history"]
            if event["decision"] == "authorize_dry_run"
        )
        dry_run_body = {
            "expected_version": authorized.json()["proposal"]["version"],
            "proposal_body_sha256": proposal["proposal_body_sha256"],
            "capture_id": proposal["capture_id"],
            "permission_event_id": permission_event,
        }
        dry_headers = self._mutation_headers(csrf, "proposal-dry-run-0001")
        with sqlite3.connect(self.fixture.database_path) as connection:
            connection.execute(
                "CREATE TRIGGER fail_dry_run_audit BEFORE INSERT ON security_audit_log "
                "WHEN NEW.permission_checked='web:dry_run' "
                "BEGIN SELECT RAISE(ABORT, 'test audit insert fault'); END"
            )
        failed_simulation = self.client.post(path + "/dry-runs", headers=dry_headers, json=dry_run_body)
        self.assertEqual(failed_simulation.status_code, 503, failed_simulation.text)
        self.assertEqual(self.client.get(path).json()["dry_runs"], [])
        with sqlite3.connect(self.fixture.database_path) as connection:
            audit_count = connection.execute(
                "SELECT count(*) FROM security_audit_log "
                "WHERE project_id=? AND permission_checked='web:dry_run' AND resource_id=?",
                (PROJECT_ID, PROPOSAL_ID),
            ).fetchone()[0]
        self.assertEqual(audit_count, 0)
        with sqlite3.connect(self.fixture.database_path) as connection:
            connection.execute("DROP TRIGGER fail_dry_run_audit")
        simulated = self.client.post(path + "/dry-runs", headers=dry_headers, json=dry_run_body)
        self.assertEqual(simulated.status_code, 200, simulated.text)
        self.assertEqual(simulated.json()["dry_run"]["status"], "SIMULATED")
        self.assertEqual(simulated.json()["dry_run"]["execution_status"], "not_executed")
        self.assertFalse(simulated.json()["dry_run"]["live_state_changed"])
        self.assertFalse(simulated.json()["dry_run"]["real_execution_allowed"])
        replayed_simulation = self.client.post(path + "/dry-runs", headers=dry_headers, json=dry_run_body)
        self.assertEqual(replayed_simulation.status_code, 200, replayed_simulation.text)
        self.assertTrue(replayed_simulation.json()["reused"])
        self.assertFalse(self.client.get("/api/v1/system/health").json()["external_writes_enabled"])

        audit = self.client.get(f"/api/v1/projects/{PROJECT_ID}/audit").json()
        web_actor_events = [event for event in audit["items"] if event["event_id"] == result["audit_event_id"]]
        self.assertEqual(len(web_actor_events), 1)
        self.assertEqual(web_actor_events[0]["actor_verification"], "verified_local_session")
        self.assertEqual(web_actor_events[0]["actor_id"], "operador-local-de-prueba")

    def test_report_decision_and_verified_audit_commit_atomically(self):
        csrf = self._login()
        path = f"/api/v1/projects/{PROJECT_ID}/reports/{REPORT_ID}/decision"
        body = {
            "expected_report_version": 1,
            "expected_human_decision": "pending",
            "decision": "approved",
            "reason": None,
        }
        headers = self._mutation_headers(csrf, "atomic-report-decision-01")
        with sqlite3.connect(self.fixture.database_path) as connection:
            connection.execute(
                "CREATE TRIGGER fail_report_audit BEFORE INSERT ON security_audit_log "
                "WHEN NEW.permission_checked='web:report_decision' "
                "BEGIN SELECT RAISE(ABORT, 'test audit insert fault'); END"
            )

        failed = self.client.post(path, headers=headers, json=body)
        self.assertEqual(failed.status_code, 503, failed.text)
        self.assertEqual(failed.json()["error"]["code"], "base_no_disponible")
        after_failure = self.client.get(f"/api/v1/projects/{PROJECT_ID}/reports/{REPORT_ID}")
        self.assertEqual(after_failure.status_code, 200, after_failure.text)
        self.assertEqual(after_failure.json()["human_decision"], "pending")
        self.assertEqual(after_failure.json()["report_version"], 1)
        self.assertEqual(after_failure.json()["decision_history"], [])
        with sqlite3.connect(self.fixture.database_path) as connection:
            audit_count = connection.execute(
                "SELECT count(*) FROM security_audit_log "
                "WHERE project_id=? AND permission_checked='web:report_decision' AND resource_id=?",
                (PROJECT_ID, REPORT_ID),
            ).fetchone()[0]
            decision_count = connection.execute(
                "SELECT count(*) FROM human_decisions WHERE report_id=?", (REPORT_ID,),
            ).fetchone()[0]
        self.assertEqual(audit_count, 0)
        self.assertEqual(decision_count, 0)

        with sqlite3.connect(self.fixture.database_path) as connection:
            connection.execute("DROP TRIGGER fail_report_audit")
        succeeded = self.client.post(path, headers=headers, json=body)
        self.assertEqual(succeeded.status_code, 200, succeeded.text)
        self.assertFalse(succeeded.json()["reused"])
        audit_event_id = succeeded.json()["audit_event_id"]
        self.assertEqual(succeeded.json()["report"]["human_decision"], "approved")
        self.assertEqual(len(succeeded.json()["report"]["decision_history"]), 1)
        self.assertEqual(self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/audit/{audit_event_id}"
        ).json()["actor_verification"], "verified_local_session")

        replay = self.client.post(path, headers=headers, json=body)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["reused"])
        self.assertEqual(len(replay.json()["report"]["decision_history"]), 1)
        conflicting = self.client.post(path, headers=headers, json={**body, "reason": "solicitud distinta"})
        self.assertEqual(conflicting.status_code, 409)
        self.assertEqual(conflicting.json()["error"]["code"], "idempotencia_conflictiva")
        stale = self.client.post(path, headers=self._mutation_headers(csrf, "atomic-report-stale-01"), json=body)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["code"], "version_de_informe_obsoleta")
        with sqlite3.connect(self.fixture.database_path) as connection:
            audit_count = connection.execute(
                "SELECT count(*) FROM security_audit_log "
                "WHERE project_id=? AND permission_checked='web:report_decision' AND resource_id=?",
                (PROJECT_ID, REPORT_ID),
            ).fetchone()[0]
            decision_count = connection.execute(
                "SELECT count(*) FROM human_decisions WHERE report_id=?", (REPORT_ID,),
            ).fetchone()[0]
        self.assertEqual(audit_count, 1)
        self.assertEqual(decision_count, 1)

    def test_protected_human_report_decision_is_version_bound_and_idempotent(self):
        csrf = self._login()
        path = f"/api/v1/projects/{PROJECT_ID}/reports/{REPORT_ID}/decision"
        body = {
            "expected_report_version": 1,
            "expected_human_decision": "pending",
            "decision": "approved",
            "reason": None,
        }
        headers = self._mutation_headers(csrf, "report-decision-0001")
        missing_csrf = self.client.post(path, headers={
            "Origin": ORIGIN, "Idempotency-Key": "report-decision-0001",
        }, json=body)
        self.assertEqual(missing_csrf.status_code, 403)

        decided = self.client.post(path, headers=headers, json=body)
        self.assertEqual(decided.status_code, 200, decided.text)
        response = decided.json()
        self.assertFalse(response["reused"])
        self.assertEqual(response["report"]["human_decision"], "approved")
        self.assertEqual(response["decision_event"]["decision"], "approved")
        self.assertIsNone(response["decision_event"]["actor_id"])
        self.assertEqual(response["decision_event"]["actor_verification"], "unknown")

        replay = self.client.post(path, headers=headers, json=body)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["reused"])
        other_key = self.client.post(path, headers=self._mutation_headers(
            csrf, "report-decision-0002",
        ), json=body)
        self.assertEqual(other_key.status_code, 409)
        self.assertEqual(other_key.json()["error"]["code"], "version_de_informe_obsoleta")
        self.assertEqual(self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/reports/{REPORT_ID}"
        ).json()["human_decision"], "approved")

    def test_outbox_symlink_and_report_binding_are_rejected_for_downloads(self):
        outside = Path(self.temp.name) / "outside.json"
        outside.write_text("secret-artifact-marker", encoding="utf-8")
        delivery_id = "dlv-symlink-0001"
        window_id = "b" * 64
        expected = self.fixture.outbox_dir / f"{PROJECT_ID}_{window_id[:16]}_{delivery_id}.json"
        os.symlink(outside, expected)
        storage = SQLiteStorageAdapter(str(self.fixture.database_path))
        message = OutboxMessage(
            message_id="message-symlink-0001", project_id=PROJECT_ID,
            window_id=window_id, report_id=REPORT_ID, channel="file",
            subject="Recibo con ruta simbólica", summary="No se debe leer el destino.",
            body_markdown="Contenido generado y ligado al informe.", metrics={}, risks=[],
        )
        storage.append_outbox_receipt(
            OutboxReceipt(
                delivery_id=delivery_id, message_id=message.message_id, project_id=PROJECT_ID,
                window_id=window_id, channel="file", status="DELIVERED", file_path=str(expected),
            ),
            message,
        )
        receipt = self.client.get(f"/api/v1/projects/{PROJECT_ID}/schedule/outbox").json()["items"][0]
        self.assertEqual(receipt["delivery_id"], delivery_id)
        self.assertFalse(receipt["artifact_available"])
        self.assertNotIn(str(outside), str(receipt))
        download = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/schedule/outbox/{delivery_id}/download",
            params={"format": "json"},
        )
        self.assertEqual(download.status_code, 404)
        self.assertNotIn("secret-artifact-marker", download.text)

    def test_weekly_schedule_settings_require_complete_explicit_server_configuration(self):
        config = Path(self.temp.name) / "project-config.yaml"
        config.write_text(
            "project:\n  id: H5-TEST\n  name: Proyecto de prueba\n"
            "sources:\n  mode: fixtures\n",
            encoding="utf-8",
        )
        environment = {
            "AGENTE_CONFIG_PATH": str(config),
            "AGENTE_DB_PATH": str(Path(self.temp.name) / "settings.sqlite"),
            "H5_WEEKLY_CYCLE_ENABLED": "true",
            "H5_WEEKLY_SCHEDULE_PROJECT_ID": PROJECT_ID,
            "H5_WEEKLY_SCHEDULE_WEEKDAY": "3",
            "H5_WEEKLY_SCHEDULE_LOCAL_TIME": "13:00",
            "H5_WEEKLY_SCHEDULE_TIMEZONE": "America/Guayaquil",
            "H5_ALLOWED_ORIGINS": ORIGIN,
            "H5_ALLOWED_HOSTS": "testserver",
        }
        with patch.dict(os.environ, environment, clear=True):
            incomplete = load_api_settings()
        self.assertTrue(incomplete.weekly_cycle_enabled)
        self.assertIsNone(incomplete.weekly_schedule)
        self.assertEqual(incomplete.weekly_schedule_status, "incomplete")

        with patch.dict(os.environ, {
            **environment,
            "H5_WEEKLY_SCHEDULE_WINDOW_RULE": "previous_scheduled_week",
        }, clear=True):
            configured = load_api_settings()
        self.assertEqual(configured.weekly_schedule_status, "configured")
        self.assertEqual(configured.weekly_schedule.project_id, PROJECT_ID)
        self.assertEqual(configured.weekly_schedule.weekday, 3)

        with patch.dict(os.environ, {
            **environment,
            "H5_WEEKLY_SCHEDULE_WEEKDAY": "7",
            "H5_WEEKLY_SCHEDULE_WINDOW_RULE": "previous_scheduled_week",
        }, clear=True):
            invalid = load_api_settings()
        self.assertIsNone(invalid.weekly_schedule)
        self.assertEqual(invalid.weekly_schedule_status, "invalid")

    def test_configuration_projection_reports_presence_without_secret_values(self):
        self.client.close()
        config = Path(self.temp.name) / "safe-analysis-config.yaml"
        config.write_text(
            "sources:\n  mode: real\n"
            "  trello:\n    board_id: BOARD-H5\n    api_key: fixture-private-key-value\n"
            "    token: fixture-private-token-value\n"
            "  github:\n    owner: acme\n    repo: h5-test\n    branch: main\n"
            f"    commit_sha: {('0123456789abcdef' * 2) + '01234567'}\n"
            "providers:\n  quality:\n    provider: groq\n    model: llama-safe\n"
            "    api_key: fixture-provider-key-value\n",
            encoding="utf-8",
        )
        root = Path(self.temp.name) / "configuration"
        self.fixture = build_web_api_fixture(
            root, database_name="configuration.sqlite", analysis_config_path=config,
        )
        self.client = self.fixture.client
        self.addCleanup(self.client.close)
        response = self.client.get(f"/api/v1/projects/{PROJECT_ID}/configuration")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["repository"]["configured"])
        self.assertEqual(body["repository"]["owner"], "acme")
        self.assertTrue(body["trello"]["configured"])
        self.assertEqual(body["trello"]["credential_status"], "configured")
        self.assertNotIn("fixture-private-key-value", response.text)
        self.assertNotIn("fixture-private-token-value", response.text)
        self.assertNotIn("fixture-provider-key-value", response.text)
        self.assertNotIn("api_key", response.text.lower())
        self.assertEqual(body["editable_fields"], [])

    def test_weekly_cycle_confirmation_rejects_report_state_changed_after_preparation(self):
        self.client.close()
        root = Path(self.temp.name) / "weekly-report-binding"
        schedule = WeeklySchedule(
            project_id=PROJECT_ID, weekday=3, local_time="13:00",
            timezone="America/Guayaquil", window_rule="previous_scheduled_week",
        )
        fixed_time = datetime(2026, 1, 8, 18, 0, tzinfo=timezone.utc)
        self.fixture = build_web_api_fixture(
            root, database_name="weekly-report-binding.sqlite", weekly_cycle_enabled=True,
            report_decisions_enabled=True, weekly_schedule=schedule,
            schedule_clock=lambda: fixed_time,
        )
        self.client = self.fixture.client
        self.addCleanup(self.client.close)
        csrf = self._login()
        prepared = self.client.post(
            f"/api/v1/projects/{PROJECT_ID}/schedule/manual-cycle/prepare",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
            json={"request_cycle": True},
        )
        self.assertEqual(prepared.status_code, 200, prepared.text)
        self.assertTrue(prepared.json()["eligible"])
        decision = self.client.post(
            f"/api/v1/projects/{PROJECT_ID}/reports/{REPORT_ID}/decision",
            headers=self._mutation_headers(csrf, "report-binding-change-01"),
            json={
                "expected_report_version": 1, "expected_human_decision": "pending",
                "decision": "approved", "reason": None,
            },
        )
        self.assertEqual(decision.status_code, 200, decision.text)
        before_confirm = self.fixture.database_path.read_bytes()
        confirm = self.client.post(
            f"/api/v1/projects/{PROJECT_ID}/schedule/manual-cycle/confirm",
            headers=self._mutation_headers(csrf, "weekly-cycle-stale-0001"),
            json={
                "preparation_id": prepared.json()["preparation_id"],
                "configuration_fingerprint": prepared.json()["configuration_fingerprint"],
                "confirm_cycle": True, "remote_consent": False,
            },
        )
        self.assertEqual(confirm.status_code, 409)
        self.assertEqual(confirm.json()["error"]["code"], "configuracion_cambiada")
        self.assertEqual(self.fixture.database_path.read_bytes(), before_confirm)
        self.assertFalse(self.fixture.job_ledger_path.exists())

    def test_weekly_cycle_stale_jobs_are_only_persisted_interrupted_on_protected_prepare(self):
        self.client.close()
        root = Path(self.temp.name) / "weekly-recovery"
        schedule = WeeklySchedule(
            project_id=PROJECT_ID, weekday=3, local_time="13:00",
            timezone="America/Guayaquil", window_rule="previous_scheduled_week",
        )
        self.fixture = build_web_api_fixture(
            root, database_name="weekly-recovery.sqlite", weekly_cycle_enabled=True,
            weekly_schedule=schedule,
        )
        self.client = self.fixture.client
        self.addCleanup(self.client.close)
        csrf = self._login()
        from agente_gestion.api.weekly_cycles import WeeklyCycleLedger
        ledger = WeeklyCycleLedger(self.client.app.state.settings)
        row, reused = ledger.create_job(
            project_id=PROJECT_ID, idempotency_hash="a" * 64,
            request_fingerprint="b" * 64, preparation_id="preparation-fixture-001",
            configuration_fingerprint="c" * 64, window_id="d" * 64, queue_capacity=3,
        )
        self.assertFalse(reused)
        before = self.fixture.job_ledger_path.read_bytes()
        jobs = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/schedule/manual-cycle/jobs"
        )
        self.assertEqual(jobs.status_code, 200, jobs.text)
        self.assertEqual(jobs.json()["availability"], "available")
        self.assertEqual(jobs.json()["items"][0]["job_id"], row["job_id"])
        self.assertEqual(jobs.json()["items"][0]["status"], "interrupted")
        self.assertEqual(self.fixture.job_ledger_path.read_bytes(), before)
        prepared = self.client.post(
            f"/api/v1/projects/{PROJECT_ID}/schedule/manual-cycle/prepare",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
            json={"request_cycle": True},
        )
        self.assertEqual(prepared.status_code, 200, prepared.text)
        self.assertEqual(ledger.get(row["job_id"])["status"], "interrupted")

    def test_manual_weekly_cycle_is_explicit_local_bounded_and_idempotent(self):
        self.client.close()
        root = Path(self.temp.name) / "weekly"
        schedule = WeeklySchedule(
            project_id=PROJECT_ID, weekday=3, local_time="13:00",
            timezone="America/Guayaquil", window_rule="previous_scheduled_week",
        )
        fixed_time = datetime(2026, 1, 8, 18, 0, tzinfo=timezone.utc)
        self.fixture = build_web_api_fixture(
            root, database_name="weekly.sqlite", weekly_cycle_enabled=True,
            weekly_schedule=schedule, schedule_clock=lambda: fixed_time,
        )
        self.client = self.fixture.client
        self.addCleanup(self.client.close)
        csrf = self._login()
        before = self.fixture.database_path.read_bytes()
        schedule_view = self.client.get(f"/api/v1/projects/{PROJECT_ID}/schedule")
        self.assertEqual(schedule_view.status_code, 200, schedule_view.text)
        self.assertEqual(schedule_view.json()["availability"], "configured")
        self.assertTrue(schedule_view.json()["is_due"])
        self.assertTrue(schedule_view.json()["can_run_manually"])
        self.assertFalse(schedule_view.json()["remote"]["required"])
        self.assertEqual(self.fixture.database_path.read_bytes(), before)
        self.assertFalse(self.fixture.job_ledger_path.exists())

        prepared = self.client.post(
            f"/api/v1/projects/{PROJECT_ID}/schedule/manual-cycle/prepare",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
            json={"request_cycle": True},
        )
        self.assertEqual(prepared.status_code, 200, prepared.text)
        self.assertTrue(prepared.json()["eligible"])
        self.assertEqual(prepared.json()["remote"]["required"], False)
        body = {
            "preparation_id": prepared.json()["preparation_id"],
            "configuration_fingerprint": prepared.json()["configuration_fingerprint"],
            "confirm_cycle": True,
            "remote_consent": False,
        }
        accepted = self.client.post(
            f"/api/v1/projects/{PROJECT_ID}/schedule/manual-cycle/confirm",
            headers=self._mutation_headers(csrf, "weekly-cycle-0001"),
            json=body,
        )
        self.assertEqual(accepted.status_code, 202, accepted.text)
        job_id = accepted.json()["job"]["job_id"]
        deadline = time.monotonic() + 3
        polled = None
        while time.monotonic() < deadline:
            polled = self.client.get(
                f"/api/v1/projects/{PROJECT_ID}/schedule/manual-cycle/jobs/{job_id}"
            )
            self.assertEqual(polled.status_code, 200, polled.text)
            if polled.json()["status"] in {"completed", "failed", "interrupted"}:
                break
            time.sleep(0.01)
        self.assertIsNotNone(polled)
        self.assertEqual(polled.json()["status"], "completed", polled.text)
        self.assertEqual(polled.json()["outcome"], "EXECUTED")
        self.assertFalse(polled.json()["remote_consent"])
        self.assertIsNotNone(polled.json()["delivery_id"])

        replay = self.client.post(
            f"/api/v1/projects/{PROJECT_ID}/schedule/manual-cycle/confirm",
            headers=self._mutation_headers(csrf, "weekly-cycle-0001"),
            json=body,
        )
        self.assertEqual(replay.status_code, 202, replay.text)
        self.assertTrue(replay.json()["reused"])
        self.assertEqual(replay.json()["job"]["job_id"], job_id)
        outbox = self.client.get(f"/api/v1/projects/{PROJECT_ID}/schedule/outbox").json()
        self.assertEqual(outbox["total"], 2)
        self.assertTrue(any(item["delivery_id"] == polled.json()["delivery_id"] for item in outbox["items"]))
        after_schedule = self.client.get(f"/api/v1/projects/{PROJECT_ID}/schedule").json()
        self.assertTrue(after_schedule["is_dispatched"])
        self.assertFalse(after_schedule["can_run_manually"])

    def test_server_disabled_proposal_and_report_mutations_do_not_change_engine_database(self):
        self.client.close()
        self.fixture = build_web_api_fixture(
            Path(self.temp.name) / "disabled", database_name="disabled.sqlite",
        )
        self.client = self.fixture.client
        self.addCleanup(self.client.close)
        csrf = self._login()
        before = self.fixture.database_path.read_bytes()
        headers = self._mutation_headers(csrf)
        proposal_response = self.client.post(
            f"/api/v1/projects/{PROJECT_ID}/proposals/{PROPOSAL_ID}/review",
            headers=headers,
            json={
                "decision": "approve",
                "expected_version": 0,
                "proposal_body_sha256": "0" * 64,
                "capture_id": "0" * 64,
            },
        )
        self.assertEqual(proposal_response.status_code, 503)
        self.assertEqual(proposal_response.json()["error"]["code"], "gobernanza_no_habilitada")

        report_response = self.client.post(
            f"/api/v1/projects/{PROJECT_ID}/reports/{REPORT_ID}/decision",
            headers={**headers, "Idempotency-Key": "report-decision-0001"},
            json={
                "expected_report_version": 1,
                "expected_human_decision": "pending",
                "decision": "approved",
                "reason": None,
            },
        )
        self.assertEqual(report_response.status_code, 503)
        self.assertEqual(report_response.json()["error"]["code"], "decision_informe_no_habilitada")
        self.assertEqual(self.fixture.database_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
