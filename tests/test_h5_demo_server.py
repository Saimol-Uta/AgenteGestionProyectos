"""Public CLI checks for the isolated web-demonstration helper."""

from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMMAND = [sys.executable, str(ROOT / "scripts" / "serve_web_demo.py")]


class WebDemoCommandTests(unittest.TestCase):
    def test_help_documents_safe_datasets_without_starting_server(self):
        result = subprocess.run(COMMAND + ["--help"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("--dataset {historical,fixture}", result.stdout)
        self.assertIn("127.0.0.1", result.stdout)
        self.assertIn("--port", result.stdout)

    def test_fixture_preparation_is_explicit_and_uses_only_fake_local_analysis(self):
        from fastapi.testclient import TestClient
        from agente_gestion.api.app import create_app
        from scripts.serve_web_demo import FIXTURE_OPERATOR_TOKEN, demo_settings

        with demo_settings("fixture") as settings:
            # An injected test executor, never provider composition, owns any confirmed job.
            app = create_app(settings, analysis_executor=lambda *_args: "run-current")
            with TestClient(app, base_url="http://localhost:8000") as client:
                login = client.post("/api/v1/auth/login", headers={"Origin": "http://localhost:8000"},
                                    json={"token": FIXTURE_OPERATOR_TOKEN})
                self.assertEqual(login.status_code, 200, login.text)
                prepared = client.post("/api/v1/projects/H5-TEST/analysis/prepare",
                                       headers={"Origin": "http://localhost:8000",
                                                "X-CSRF-Token": login.json()["csrf_token"]},
                                       json={"request_analysis": True})
                self.assertEqual(prepared.status_code, 200, prepared.text)
                self.assertTrue(prepared.json()["eligible"])
                self.assertFalse(prepared.json()["remote"]["required"])
                self.assertTrue(all(p["provider"] == "fake_simulated"
                                    for p in prepared.json()["remote"]["providers"]))
                jobs = client.get("/api/v1/analysis/jobs?project_id=H5-TEST")
                self.assertEqual(jobs.status_code, 200, jobs.text)
                self.assertEqual(jobs.json()["total"], 0)

    def test_fixture_exposes_only_explicit_local_governance_and_weekly_preparation(self):
        from datetime import datetime, timezone
        from fastapi.testclient import TestClient
        from agente_gestion.api.app import create_app
        from scripts.serve_web_demo import FIXTURE_OPERATOR_TOKEN, demo_settings

        with demo_settings("fixture") as settings:
            app = create_app(settings, analysis_executor=lambda *_args: "run-current",
                             schedule_clock=lambda: datetime(2026, 1, 8, 18, 0, tzinfo=timezone.utc))
            with TestClient(app, base_url="http://localhost:8000") as client:
                login = client.post("/api/v1/auth/login", headers={"Origin": "http://localhost:8000"},
                                    json={"token": FIXTURE_OPERATOR_TOKEN})
                self.assertEqual(login.status_code, 200, login.text)
                capabilities = client.get("/api/v1/auth/session").json()["capabilities"]
                self.assertTrue({"governance:proposal_review", "governance:dry_run_authorization",
                                 "governance:dry_run", "reports:decide", "weekly_cycle:prepare",
                                 "weekly_cycle:confirm"}.issubset(capabilities), capabilities)
                schedule = client.get("/api/v1/projects/H5-TEST/schedule")
                self.assertEqual(schedule.status_code, 200, schedule.text)
                self.assertEqual(schedule.json()["availability"], "configured")
                self.assertTrue(schedule.json()["is_due"])
                self.assertEqual(schedule.json()["automatic_trigger_status"], "not_verified")
                prepared = client.post("/api/v1/projects/H5-TEST/schedule/manual-cycle/prepare",
                                       headers={"Origin": "http://localhost:8000",
                                                "X-CSRF-Token": login.json()["csrf_token"]},
                                       json={"request_cycle": True})
                self.assertEqual(prepared.status_code, 200, prepared.text)
                self.assertTrue(prepared.json()["eligible"])
                self.assertFalse(prepared.json()["remote"]["required"])
                self.assertEqual(prepared.json()["remote"]["providers"], [])
                self.assertEqual(client.get("/api/v1/analysis/jobs?project_id=H5-TEST").json()["total"], 0)
                self.assertEqual(client.get("/api/v1/projects/H5-TEST/schedule/manual-cycle/jobs").json()["total"], 0)
                self.assertFalse(client.get("/api/v1/system/health").json()["external_writes_enabled"])

    def test_engine_check_help_does_not_start_a_job(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_web_engine.py"), "--help"],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        help_text = " ".join(result.stdout.split())
        self.assertIn("fuentes/LLM simulados", help_text)
        self.assertIn("base temporal", help_text)
        self.assertNotIn("OFFLINE_REAL_ENGINE_OK", result.stdout)

    def test_invalid_port_is_rejected_before_server_or_database_access(self):
        result = subprocess.run(COMMAND + ["--port", "70000"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("el puerto debe estar entre 1024 y 65535", result.stderr)


if __name__ == "__main__":
    unittest.main()
