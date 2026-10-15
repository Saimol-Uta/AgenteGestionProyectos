"""Public CLI checks for the isolated web-demonstration helper."""

from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMMAND = [sys.executable, str(ROOT / "scripts" / "serve_web_demo.py")]


class WebDemoCommandTests(unittest.TestCase):
    def test_help_documents_safe_datasets_without_starting_server(self):
        result = subprocess.run(COMMAND + ["--help"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("--dataset {fixture}", result.stdout)
        self.assertIn("--fixture-db", result.stdout)
        self.assertNotIn("historical", result.stdout)
        self.assertIn("127.0.0.1", result.stdout)
        self.assertIn("--port", result.stdout)

    def test_fixture_preparation_is_explicit_and_uses_only_fake_local_analysis(self):
        from fastapi.testclient import TestClient
        from agente_gestion.api.app import create_app
        from scripts.serve_web_demo import FIXTURE_OPERATOR_TOKEN, demo_settings

        with demo_settings("fixture") as settings:
            app = create_app(settings)
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
        from scripts.serve_web_demo import FIXTURE_CLOCK, FIXTURE_OPERATOR_TOKEN, demo_settings, fixture_executor

        with demo_settings("fixture") as settings:
            app = create_app(settings, analysis_executor=fixture_executor,
                             schedule_clock=lambda: FIXTURE_CLOCK)
            with TestClient(app, base_url="http://localhost:8000") as client:
                login = client.post("/api/v1/auth/login", headers={"Origin": "http://localhost:8000"},
                                    json={"token": FIXTURE_OPERATOR_TOKEN})
                self.assertEqual(login.status_code, 200, login.text)
                capabilities = client.get("/api/v1/auth/session").json()["capabilities"]
                self.assertTrue({"governance:proposal_execute", "quality:decide",
                                 "notifications:email_send", "reports:decide", "weekly_cycle:prepare",
                                 "weekly_cycle:confirm"}.issubset(capabilities), capabilities)
                self.assertFalse({"governance:proposal_review", "governance:dry_run_authorization",
                                  "governance:dry_run"} & set(capabilities))
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

    def test_confirmed_fixture_analysis_persists_a_new_run_and_is_idempotent(self):
        from fastapi.testclient import TestClient
        from agente_gestion.api.app import create_app
        from scripts.serve_web_demo import FIXTURE_CLOCK, FIXTURE_OPERATOR_TOKEN, demo_settings, fixture_executor

        with demo_settings("fixture") as settings:
            app = create_app(
                settings, analysis_executor=fixture_executor, schedule_clock=lambda: FIXTURE_CLOCK,
            )
            with TestClient(app, base_url="http://localhost:8000") as client:
                initial_runs = client.get("/api/v1/runs", params={
                    "project_id": "H5-TEST", "page_size": 10,
                })
                self.assertEqual(initial_runs.status_code, 200, initial_runs.text)
                initial_run_ids = {item["run_id"] for item in initial_runs.json()["items"]}
                initial_history = client.get("/api/v1/projects/H5-TEST/history")
                self.assertEqual(initial_history.json()["total"], 2)

                login = client.post(
                    "/api/v1/auth/login",
                    headers={"Origin": "http://localhost:8000"},
                    json={"token": FIXTURE_OPERATOR_TOKEN},
                )
                self.assertEqual(login.status_code, 200, login.text)
                headers = {
                    "Origin": "http://localhost:8000",
                    "X-CSRF-Token": login.json()["csrf_token"],
                    "Idempotency-Key": "fixture-analysis-confirm-001",
                }
                prepared = client.post(
                    "/api/v1/projects/H5-TEST/analysis/prepare",
                    headers=headers,
                    json={"request_analysis": True},
                )
                self.assertEqual(prepared.status_code, 200, prepared.text)
                preparation = prepared.json()
                self.assertTrue(preparation["eligible"])
                self.assertFalse(preparation["remote"]["required"])
                confirm_payload = {
                    "preparation_id": preparation["preparation_id"],
                    "configuration_fingerprint": preparation["configuration_fingerprint"],
                    "confirm_analysis": True,
                    "remote_consent": False,
                }
                accepted = client.post(
                    "/api/v1/projects/H5-TEST/analysis/confirm",
                    headers=headers,
                    json=confirm_payload,
                )
                self.assertEqual(accepted.status_code, 202, accepted.text)
                job_id = accepted.json()["job"]["job_id"]
                deadline = time.monotonic() + 5
                job = None
                while time.monotonic() < deadline:
                    response = client.get(f"/api/v1/analysis/jobs/{job_id}")
                    self.assertEqual(response.status_code, 200, response.text)
                    job = response.json()
                    if job["status"] in {"completed", "failed"}:
                        break
                    time.sleep(0.01)
                self.assertIsNotNone(job)
                self.assertEqual(job["status"], "completed")
                self.assertIsNotNone(job["run_id"])
                self.assertNotIn(job["run_id"], initial_run_ids)
                self.assertTrue(job["stage_progress_available"])

                created_run = client.get(f"/api/v1/runs/{job['run_id']}")
                self.assertEqual(created_run.status_code, 200, created_run.text)
                self.assertEqual(created_run.json()["run_id"], job["run_id"])
                history = client.get("/api/v1/projects/H5-TEST/history")
                self.assertEqual(history.status_code, 200, history.text)
                self.assertEqual(history.json()["total"], 3)
                latest = history.json()["items"][-1]
                self.assertNotIn(latest["capture_id"], {
                    item["capture_id"] for item in initial_history.json()["items"]
                })

                replay = client.post(
                    "/api/v1/projects/H5-TEST/analysis/confirm",
                    headers=headers,
                    json=confirm_payload,
                )
                self.assertEqual(replay.status_code, 202, replay.text)
                self.assertTrue(replay.json()["reused"])
                self.assertEqual(replay.json()["job"]["job_id"], job_id)
                self.assertEqual(client.get("/api/v1/projects/H5-TEST/history").json()["total"], 3)
                self.assertTrue(settings.db_path.endswith("fixture.sqlite"))
                self.assertNotEqual(Path(settings.job_ledger_path).resolve(), Path(settings.db_path).resolve())
                self.assertNotEqual(Path(settings.job_ledger_path).parent, Path(".runtime").resolve())
                self.assertFalse(settings.external_writes_enabled)

    def test_engine_check_help_does_not_start_a_job(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_web_engine.py"), "--help"],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        help_text = " ".join(result.stdout.split())
        self.assertIn("fuentes/LLM simulados", help_text)
        self.assertIn("base temporal", help_text)
        self.assertNotIn("OFFLINE_REAL_ENGINE_OK", result.stdout)

    def test_fixture_database_launcher_uses_matching_fresh_paths(self):
        from scripts.serve_web_demo import demo_settings
        from tests.web_fixtures import PROJECT_ID, seed_unified_demo_data

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "regenerated.sqlite"
            seed_runs = root / "regenerated-runs"
            seed_outbox = root / "regenerated-outbox"
            seed_unified_demo_data(database, seed_runs, seed_outbox)
            with demo_settings("fixture", fixture_db_path=database) as settings:
                self.assertEqual(Path(settings.db_path), database.resolve())
                self.assertEqual(settings.projects[0].id, PROJECT_ID)
                self.assertEqual(settings.projects[0].source_mode, "fixtures")
                self.assertEqual(Path(settings.runs_dir), seed_runs.resolve())
                self.assertEqual(Path(settings.outbox_dir), seed_outbox.resolve())
                self.assertNotEqual(Path(settings.job_ledger_path).resolve(), database.resolve())
                self.assertNotEqual(Path(settings.outbox_dir).resolve(), Path(".runtime/outbox").resolve())
                self.assertNotEqual(Path(settings.branches_dir).resolve(), Path(".runtime/branches").resolve())
                self.assertFalse(settings.external_writes_enabled)
                from agente_gestion.api.app import create_app
                from fastapi.testclient import TestClient

                with TestClient(create_app(settings)) as client:
                    project = client.get(f"/api/v1/projects/{PROJECT_ID}")
                    self.assertEqual(project.status_code, 200, project.text)
                    self.assertEqual(project.json()["source_mode"], "fixtures")
                    self.assertEqual(project.json()["summary"]["total_tasks"], 1)

    def test_legacy_historical_dataset_is_rejected_before_database_access(self):
        result = subprocess.run(COMMAND + ["--dataset", "historical"], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("invalid choice", result.stderr)
        self.assertIn("fixture", result.stderr)

    def test_invalid_port_is_rejected_before_server_or_database_access(self):
        result = subprocess.run(COMMAND + ["--port", "70000"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("el puerto debe estar entre 1024 y 65535", result.stderr)


if __name__ == "__main__":
    unittest.main()
