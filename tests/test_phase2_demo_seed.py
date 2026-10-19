"""Public regressions for the fresh, source-complete unified demo seed."""

from pathlib import Path
import hashlib
import sqlite3
import tempfile
import unittest

from agente_gestion.api.app import ApiSettings, ProjectConfig, create_app
from fastapi.testclient import TestClient

from tests.web_fixtures import (
    OPERATOR_TOKEN,
    PROJECT_ID,
    REPORT_ID,
    SOURCE_CONTENT,
    SOURCE_REPOSITORY,
    SOURCE_SUGGESTION,
    TASK_ID,
    seed_unified_demo_data,
)


class Phase2DemoSeedTests(unittest.TestCase):
    def test_fresh_seed_is_repeatable_complete_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = seed_unified_demo_data(root / "first.sqlite", root / "first-runs")
            second = seed_unified_demo_data(root / "second.sqlite", root / "second-runs")

            self.assertEqual(first.project_id, PROJECT_ID)
            self.assertEqual(first.run_ids, second.run_ids)
            self.assertEqual(first.capture_ids, second.capture_ids)
            self.assertEqual(len(first.run_ids), 2)
            self.assertEqual(len(first.capture_ids), 2)

            database = root / "first.sqlite"
            with sqlite3.connect(database) as connection:
                counts = dict(connection.execute(
                    "SELECT project_id, count(*) FROM snapshot_captures GROUP BY project_id"
                ))
                run_count = connection.execute("SELECT count(*) FROM runs").fetchone()[0]
            self.assertEqual(counts[PROJECT_ID], 2)
            self.assertEqual(run_count, 2)

            from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter

            storage = SQLiteStorageAdapter(str(database), read_only=True)
            # Every regenerated history, including stored-only project fixtures, is source-complete.
            all_capture_ids = [
                capture.capture_id
                for project_id in counts
                for capture in storage.list_snapshot_captures(project_id)
            ]
            for capture_id in all_capture_ids:
                capture = storage.get_snapshot_capture(capture_id)
                self.assertIsNotNone(capture)
                snapshot = capture.snapshot
                self.assertTrue(snapshot.is_complete)
                self.assertTrue(snapshot.tasks_complete)
                self.assertTrue(snapshot.task_source_identity)
                self.assertEqual(len(snapshot.code_diffs), 1)
                source = snapshot.code_diffs[0]
                self.assertEqual(source.evidence_scope, "full_file")
                self.assertTrue(source.is_complete)
                self.assertFalse(source.truncated)
                self.assertEqual(source.lines, list(range(1, source.total_lines + 1)))
                self.assertEqual(source.content, SOURCE_CONTENT)
                self.assertEqual(
                    source.source_sha256,
                    hashlib.sha256(SOURCE_CONTENT.encode("utf-8")).hexdigest(),
                )

            self.assertEqual(first.runs_path, root / "first-runs")
            self.assertEqual(first.outbox_path, root / "first-outbox")
            self.assertTrue(any(path.is_file() for path in first.outbox_path.iterdir()))
            for run_id in first.run_ids:
                self.assertTrue((root / "first-runs" / run_id / "report.json").is_file())

            before = database.read_bytes()
            with self.assertRaises(FileExistsError):
                seed_unified_demo_data(database, root / "first-runs")
            self.assertEqual(database.read_bytes(), before)

    def test_public_history_technical_review_and_source_approval_are_real_and_local(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database_path = root / "web-fixture.sqlite"
            runs_path = root / "web-fixture-runs"
            outbox_dir = root / "web-fixture-outbox"
            seed = seed_unified_demo_data(database_path, runs_path, outbox_dir)
            branches_dir = root / "web-fixture-branches"
            settings = ApiSettings(
                db_path=str(database_path),
                runs_dir=str(runs_path),
                outbox_dir=str(outbox_dir),
                projects=(ProjectConfig(
                    id=PROJECT_ID, name="Proyecto de prueba", source_mode="fixtures",
                ),),
                operator_token=OPERATOR_TOKEN,
                operator_actor="operador-local-de-prueba",
                operator_project_ids=(PROJECT_ID,),
                allowed_origins=["http://testserver"],
                allowed_hosts=["testserver"],
                governance_enabled=True,
                report_decisions_enabled=False,
                weekly_cycle_enabled=False,
                branches_dir=str(branches_dir),
                github_repository=SOURCE_REPOSITORY,
                github_base_branch="main",
                external_writes_enabled=False,
            )
            client = TestClient(create_app(settings))
            self.addCleanup(client.close)

            history = client.get(f"/api/v1/projects/{PROJECT_ID}/history")
            self.assertEqual(history.status_code, 200, history.text)
            self.assertEqual(history.json()["total"], 2)
            before_capture, after_capture = (
                item["capture_id"] for item in history.json()["items"]
            )
            comparison = client.get(
                f"/api/v1/projects/{PROJECT_ID}/compare",
                params={"before": before_capture, "after": after_capture},
            )
            self.assertEqual(comparison.status_code, 200, comparison.text)
            self.assertTrue(any(
                change["task_id"] == TASK_ID
                for change in comparison.json()["changes"]
            ))

            runs = client.get("/api/v1/runs", params={"project_id": PROJECT_ID, "page_size": 10})
            self.assertEqual(runs.status_code, 200, runs.text)
            self.assertEqual(runs.json()["total"], 2)
            run_ids = {item["run_id"] for item in runs.json()["items"]}
            self.assertEqual(run_ids, set(seed.run_ids))
            current_run_id = seed.current_run_id
            current_run = client.get(f"/api/v1/runs/{current_run_id}")
            self.assertEqual(current_run.status_code, 200, current_run.text)
            self.assertEqual(current_run.json()["report_id"], REPORT_ID)
            self.assertEqual(seed.current_report_id, REPORT_ID)
            self.assertEqual(seed.report_id, REPORT_ID)
            report = client.get(f"/api/v1/projects/{PROJECT_ID}/reports/{REPORT_ID}")
            self.assertEqual(report.status_code, 200, report.text)
            self.assertEqual(report.json()["report_id"], REPORT_ID)

            findings = client.get(f"/api/v1/runs/{current_run_id}/findings")
            self.assertEqual(findings.status_code, 200, findings.text)
            self.assertGreaterEqual(findings.json()["total"], 1)
            finding_id = findings.json()["items"][0]["finding_id"]
            detail = client.get(f"/api/v1/runs/{current_run_id}/findings/{finding_id}")
            self.assertEqual(detail.status_code, 200, detail.text)
            finding_detail = detail.json()
            excerpt = finding_detail["code_excerpt"]
            self.assertEqual(excerpt["evidence_scope"], "full_file")
            self.assertFalse(excerpt["is_partial"])
            self.assertIn("return 1 / total", excerpt["content"])
            self.assertEqual(finding_detail["suggested_code"], SOURCE_SUGGESTION)
            self.assertEqual(finding_detail["decision_status"], "pending")
            self.assertIsNone(finding_detail["branch_name"])
            self.assertIsNone(finding_detail["branch_url"])

            proposals = client.get(f"/api/v1/projects/{PROJECT_ID}/proposals")
            self.assertEqual(proposals.status_code, 200, proposals.text)
            self.assertEqual(proposals.json()["total"], 1)
            review = proposals.json()["items"][0]["technical_review"]
            self.assertEqual(review["technical_status"], "ready")
            self.assertEqual(review["dry_run"]["status"], "SIMULATED")
            self.assertEqual(review["human_approval_status"], "required")
            self.assertFalse(review["execution_authorized"])

            login = client.post(
                "/api/v1/auth/login",
                headers={"Origin": "http://testserver"},
                json={"token": OPERATOR_TOKEN},
            )
            self.assertEqual(login.status_code, 200, login.text)
            csrf = login.json()["csrf_token"]
            capabilities = client.get("/api/v1/auth/session").json()["capabilities"]
            self.assertIn("quality:decide", capabilities)
            self.assertNotIn("governance:proposal_review", capabilities)
            branch = "quality/fixture-seed-approval"
            payload = {
                "decision": "approve",
                "target_branch": branch,
                "reason": "Confirmar el ejemplo completo aislado de la demo.",
            }
            headers = {
                "Origin": "http://testserver",
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "fixture-seed-approve-001",
            }
            approval_path = f"/api/v1/runs/{current_run_id}/findings/{finding_id}/decision"
            finding_path = f"/api/v1/runs/{current_run_id}/findings/{finding_id}"
            report_path = f"/api/v1/projects/{PROJECT_ID}/reports/{REPORT_ID}"
            audit_path = f"/api/v1/projects/{PROJECT_ID}/audit"
            finding_before = client.get(finding_path).json()
            report_before = client.get(report_path).json()
            audit_before_response = client.get(audit_path, params={"page_size": 100})
            self.assertEqual(audit_before_response.status_code, 200, audit_before_response.text)
            audit_before = audit_before_response.json()
            self.assertEqual(finding_before["decision_status"], "pending")

            class RecordingActionPort:
                def __init__(self):
                    self.calls = []

                def create_verification_branch(self, **kwargs):
                    self.calls.append(kwargs)
                    raise AssertionError("Blocked approval must not call the GitHub action port.")

            action_port = RecordingActionPort()
            client.app.state.code_action_port = action_port
            denied = client.post(approval_path, headers=headers, json=payload)
            self.assertEqual(denied.status_code, 409, denied.text)
            self.assertEqual(
                denied.json()["error"]["code"],
                "revision_independiente_requerida",
            )

            finding_after = client.get(finding_path).json()
            report_after = client.get(report_path).json()
            self.assertEqual(finding_after, finding_before)
            self.assertEqual(finding_after["decision_status"], "pending")
            self.assertIsNone(finding_after["branch_name"])
            self.assertIsNone(finding_after["branch_url"])
            audit_after_denial = client.get(audit_path, params={"page_size": 100})
            self.assertEqual(audit_after_denial.status_code, 200, audit_after_denial.text)
            self.assertEqual(audit_after_denial.json(), audit_before)
            self.assertEqual(report_after, report_before)
            self.assertEqual(action_port.calls, [])

            rejected = client.post(
                approval_path,
                headers=headers,
                json={"decision": "reject", "reason": "No aprobar sin revisión independiente."},
            )
            self.assertEqual(rejected.status_code, 200, rejected.text)
            result = rejected.json()
            self.assertEqual(result["decision_status"], "rejected")
            self.assertIsNone(result["branch_name"])
            self.assertIsNone(result["branch_url"])

            finding_after_rejection = client.get(finding_path).json()
            report_after_rejection = client.get(report_path).json()
            self.assertEqual(finding_after_rejection["decision_status"], "rejected")
            self.assertIsNone(finding_after_rejection["branch_name"])
            self.assertIsNone(finding_after_rejection["branch_url"])
            self.assertEqual(report_after_rejection, report_before)
            self.assertEqual(action_port.calls, [])

            artifact = (
                branches_dir / "quality" / "fixture-seed-approval" / "src" / "metrics.py"
            )
            self.assertFalse(branches_dir.exists())
            self.assertFalse(artifact.exists())
            audit_after_rejection = client.get(audit_path, params={"page_size": 100})
            self.assertEqual(audit_after_rejection.status_code, 200, audit_after_rejection.text)
            self.assertIn(
                result["audit_event_id"],
                {item["event_id"] for item in audit_after_rejection.json()["items"]},
            )


if __name__ == "__main__":
    unittest.main()
