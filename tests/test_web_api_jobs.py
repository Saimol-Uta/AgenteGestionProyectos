"""Public HTTP contracts for opt-in, bounded analysis jobs."""

from dataclasses import replace
from pathlib import Path
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agente_gestion.api.analysis_jobs import load_analysis_plan
from tests.web_fixtures import OPERATOR_TOKEN, PROJECT_ID, build_web_api_fixture


ORIGIN = "http://testserver"
RUN_ID = "run-current"


class WebApiAnalysisJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls: list[tuple[str, bool]] = []
        self.finished = threading.Event()
        self.executor_error: str | None = None

        def executor(project_id, _plan, remote_consent):
            self.calls.append((project_id, remote_consent))
            if self.executor_error is not None:
                raise RuntimeError(self.executor_error)
            self.finished.set()
            return RUN_ID

        config_path = self.root / "analysis.yaml"
        config_path.write_text(
            "allow_remote_ai: true\n"
            "quality_provider: groq\n"
            "groq_api_key: test-groq-credential-not-for-logs\n",
            encoding="utf-8",
        )
        self.config_path = config_path
        self.fixture = build_web_api_fixture(
            self.root,
            analysis_enabled=True,
            analysis_executor=executor,
            analysis_config_path=config_path,
        )
        self.client = self.fixture.client
        self.addCleanup(self.client.close)
        self.csrf = self._login()

    def _login(self) -> str:
        response = self.client.post(
            "/api/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"token": OPERATOR_TOKEN},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("analysis:prepare", response.json()["capabilities"])
        return response.json()["csrf_token"]

    def _mutation_headers(self, csrf: str | None = None) -> dict[str, str]:
        return {
            "Origin": ORIGIN,
            "X-CSRF-Token": csrf or self.csrf,
            "Content-Type": "application/json",
        }

    def _prepare(self, project_id: str = PROJECT_ID):
        return self.client.post(
            f"/api/v1/projects/{project_id}/analysis/prepare",
            headers=self._mutation_headers(),
            json={"request_analysis": True},
        )

    def _confirm(
        self, preparation: dict, *, remote_consent: bool, key: str = "confirm-0001",
        project_id: str = PROJECT_ID,
    ):
        return self.client.post(
            f"/api/v1/projects/{project_id}/analysis/confirm",
            headers={**self._mutation_headers(), "Idempotency-Key": key},
            json={
                "preparation_id": preparation["preparation_id"],
                "configuration_fingerprint": preparation["configuration_fingerprint"],
                "confirm_analysis": True,
                "remote_consent": remote_consent,
            },
        )

    def test_preflight_is_protected_and_does_not_create_database_or_job_ledger(self) -> None:
        before = self.fixture.database_path.read_bytes()
        self.client.cookies.clear()
        unauthenticated = self._prepare()
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(self.client.get(f"/api/v1/analysis/jobs?project_id={PROJECT_ID}").status_code, 401)
        self.assertFalse(self.fixture.job_ledger_path.exists())

        self.csrf = self._login()
        missing_csrf = self.client.post(
            f"/api/v1/projects/{PROJECT_ID}/analysis/prepare",
            headers={"Origin": ORIGIN},
            json={"request_analysis": True},
        )
        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(self._prepare("STORED-ONLY").status_code, 404)

        response = self._prepare()
        self.assertEqual(response.status_code, 200, response.text)
        prepared = response.json()
        self.assertTrue(prepared["eligible"])
        self.assertEqual(prepared["preflight"]["configuration_status"], "ready")
        self.assertTrue(prepared["remote"]["required"])
        self.assertTrue(prepared["remote"]["server_allowed"])
        self.assertEqual(prepared["remote"]["availability"], "not_checked")
        self.assertNotIn("test-groq-credential-not-for-logs", response.text)
        self.assertEqual(self.fixture.database_path.read_bytes(), before)
        self.assertFalse(self.fixture.job_ledger_path.exists())
        self.assertEqual(self.calls, [])

    def test_preparation_is_bound_to_the_operator_session(self) -> None:
        prepared_response = self._prepare()
        self.assertEqual(prepared_response.status_code, 200, prepared_response.text)
        prepared = prepared_response.json()
        self.csrf = self._login()

        rejected = self._confirm(prepared, remote_consent=True)
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(rejected.json()["error"]["code"], "preparacion_sesion_distinta")
        self.assertFalse(self.fixture.job_ledger_path.exists())
        self.assertEqual(self.calls, [])

    def test_server_remote_permission_cannot_be_overridden_by_client_consent(self) -> None:
        self.config_path.write_text(
            "allow_remote_ai: false\n"
            "quality_provider: groq\n"
            "groq_api_key: test-groq-credential-not-for-logs\n",
            encoding="utf-8",
        )
        response = self._prepare()
        self.assertEqual(response.status_code, 200, response.text)
        prepared = response.json()
        self.assertFalse(prepared["eligible"])
        self.assertTrue(prepared["remote"]["required"])
        self.assertFalse(prepared["remote"]["server_allowed"])
        self.assertNotIn("test-groq-credential-not-for-logs", response.text)

        rejected = self._confirm(prepared, remote_consent=True)
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(rejected.json()["error"]["code"], "remoto_no_autorizado")
        self.assertFalse(self.fixture.job_ledger_path.exists())
        self.assertEqual(self.calls, [])

    def test_remote_consent_idempotency_and_polling(self) -> None:
        prepared_response = self._prepare()
        self.assertEqual(prepared_response.status_code, 200, prepared_response.text)
        prepared = prepared_response.json()

        no_consent = self._confirm(prepared, remote_consent=False)
        self.assertEqual(no_consent.status_code, 409)
        self.assertEqual(no_consent.json()["error"]["code"], "consentimiento_remoto_requerido")
        self.assertFalse(self.fixture.job_ledger_path.exists())
        self.assertEqual(self.calls, [])

        accepted = self._confirm(prepared, remote_consent=True)
        self.assertEqual(accepted.status_code, 202, accepted.text)
        body = accepted.json()
        self.assertFalse(body["reused"])
        self.assertEqual(body["job"]["remote_consent"], True)
        self.assertEqual(body["poll_url"], f"/api/v1/analysis/jobs/{body['job']['job_id']}")
        self.assertTrue(self.finished.wait(timeout=2))

        replay = self._confirm(prepared, remote_consent=True)
        self.assertEqual(replay.status_code, 202, replay.text)
        self.assertTrue(replay.json()["reused"])
        self.assertEqual(replay.json()["job"]["job_id"], body["job"]["job_id"])
        self.assertEqual(self.calls, [(PROJECT_ID, True)])
        conflict = self._confirm(prepared, remote_consent=False)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "idempotencia_conflictiva")

        # A replay of an accepted idempotent request remains safe after its in-memory preflight expires.
        self.fixture.client.app.state.analysis_jobs._prepared.pop(prepared["preparation_id"], None)
        replay_after_expiry = self._confirm(prepared, remote_consent=True)
        self.assertEqual(replay_after_expiry.status_code, 202, replay_after_expiry.text)
        self.assertTrue(replay_after_expiry.json()["reused"])
        self.assertEqual(self.calls, [(PROJECT_ID, True)])

        job_id = body["job"]["job_id"]
        deadline = time.monotonic() + 2
        polled = None
        while time.monotonic() < deadline:
            polled = self.client.get(f"/api/v1/analysis/jobs/{job_id}")
            self.assertEqual(polled.status_code, 200, polled.text)
            if polled.json()["status"] == "completed":
                break
            time.sleep(0.01)
        self.assertIsNotNone(polled)
        job = polled.json()
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["run_id"], RUN_ID)
        self.assertTrue(job["stage_progress_available"])

        page = self.client.get(f"/api/v1/analysis/jobs?project_id={PROJECT_ID}")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual(page.json()["total"], 1)
        self.assertEqual(page.json()["items"][0]["job_id"], job_id)
        self.assertNotEqual(self.fixture.job_ledger_path.resolve(), self.fixture.database_path.resolve())

    def test_executor_failure_is_terminal_and_does_not_expose_exception_text(self) -> None:
        self.executor_error = "api_token=private-executor-failure-marker"
        prepared = self._prepare().json()
        accepted = self._confirm(prepared, remote_consent=True)
        self.assertEqual(accepted.status_code, 202, accepted.text)
        job_id = accepted.json()["job"]["job_id"]

        deadline = time.monotonic() + 2
        response = None
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/v1/analysis/jobs/{job_id}")
            if response.json()["status"] == "failed":
                break
            time.sleep(0.01)
        self.assertIsNotNone(response)
        self.assertEqual(response.json()["status"], "failed")
        self.assertEqual(response.json()["error_code"], "analysis_failed")
        self.assertNotIn("private-executor-failure-marker", response.text)
        self.assertNotIn(b"private-executor-failure-marker", self.fixture.job_ledger_path.read_bytes())

    def test_restart_exposes_interrupted_without_get_writes_then_persists_on_prepare(self) -> None:
        from agente_gestion.api.analysis_jobs import AnalysisJobLedger

        ledger = AnalysisJobLedger(str(self.fixture.job_ledger_path), str(self.fixture.database_path))
        row, reused = ledger.create_job(
            project_id=PROJECT_ID,
            idempotency_hash="a" * 64,
            request_fingerprint="b" * 64,
            preparation_id="preparation-fixture-001",
            configuration_fingerprint="c" * 64,
            remote_consent=False,
            queue_capacity=3,
        )
        self.assertFalse(reused)
        ledger.update(row["job_id"], status="running")
        before = self.fixture.job_ledger_path.read_bytes()

        polled = self.client.get(f"/api/v1/analysis/jobs/{row['job_id']}")
        self.assertEqual(polled.status_code, 200, polled.text)
        self.assertEqual(polled.json()["status"], "interrupted")
        self.assertEqual(self.fixture.job_ledger_path.read_bytes(), before)
        self.assertEqual(ledger.get(row["job_id"])["status"], "running")

        prepared = self._prepare()
        self.assertEqual(prepared.status_code, 200, prepared.text)
        self.assertEqual(ledger.get(row["job_id"])["status"], "interrupted")

    def test_queue_capacity_rejects_a_second_project_while_worker_is_active(self) -> None:
        from agente_gestion.api.app import create_app
        from agente_gestion.api.settings import ApiSettings, ProjectConfig

        self.fixture.client.close()
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        self.addCleanup(release.set)

        def blocking_executor(_project_id, _plan, _remote_consent):
            started.set()
            release.wait(timeout=3)
            finished.set()
            return RUN_ID

        settings = ApiSettings(
            db_path=str(self.fixture.database_path),
            projects=[
                ProjectConfig(id=PROJECT_ID, name="Proyecto de prueba", source_mode="fixtures"),
                ProjectConfig(id="SECOND-PROJECT", name="Segundo proyecto", source_mode="fixtures"),
            ],
            operator_token=OPERATOR_TOKEN,
            operator_project_ids=[PROJECT_ID, "SECOND-PROJECT"],
            allowed_origins=[ORIGIN],
            allowed_hosts=["testserver"],
            analysis_enabled=True,
            analysis_config_path=str(self.config_path),
            job_ledger_path=str(self.fixture.job_ledger_path),
            analysis_queue_capacity=1,
        )
        from fastapi.testclient import TestClient
        self.client = TestClient(create_app(settings, analysis_executor=blocking_executor))
        self.addCleanup(self.client.close)
        self.csrf = self._login()
        first_prepared = self._prepare(PROJECT_ID).json()
        first = self._confirm(first_prepared, remote_consent=True, key="first-job-0001")
        self.assertEqual(first.status_code, 202, first.text)
        self.assertTrue(started.wait(timeout=2))

        duplicate_prepared = self._prepare(PROJECT_ID).json()
        duplicate = self._confirm(duplicate_prepared, remote_consent=True, key="duplicate-job-0001")
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["error"]["code"], "analisis_en_curso")

        second_prepared = self._prepare("SECOND-PROJECT").json()
        second = self._confirm(
            second_prepared, remote_consent=True, key="second-job-0001", project_id="SECOND-PROJECT",
        )
        self.assertEqual(second.status_code, 429, second.text)
        self.assertEqual(second.json()["error"]["code"], "cola_de_analisis_llena")
        release.set()
        self.assertTrue(finished.wait(timeout=2))
        job_id = first.json()["job"]["job_id"]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            terminal = self.client.get(f"/api/v1/analysis/jobs/{job_id}")
            self.assertEqual(terminal.status_code, 200, terminal.text)
            if terminal.json()["status"] == "completed":
                break
            time.sleep(0.01)
        self.assertEqual(terminal.json()["status"], "completed", terminal.text)

    def test_configuration_change_invalidates_preparation(self) -> None:
        state = {"changed": False}

        def plan_loader(settings, project_id):
            plan = load_analysis_plan(settings, project_id)
            return replace(plan, fingerprint=("f" * 64 if state["changed"] else plan.fingerprint))

        # Rebuild only the app wiring around the existing temporary fixture and database.
        self.fixture.client.close()
        from agente_gestion.api.app import create_app
        from agente_gestion.api.settings import ApiSettings, ProjectConfig

        settings = ApiSettings(
            db_path=str(self.fixture.database_path),
            projects=[ProjectConfig(id=PROJECT_ID, name="Proyecto de prueba", source_mode="fixtures")],
            operator_token=OPERATOR_TOKEN,
            operator_project_ids=[PROJECT_ID],
            allowed_origins=[ORIGIN],
            allowed_hosts=["testserver"],
            analysis_enabled=True,
            analysis_config_path=str(self.config_path),
            job_ledger_path=str(self.fixture.job_ledger_path),
        )
        from fastapi.testclient import TestClient
        self.client = TestClient(create_app(settings, analysis_plan_loader=plan_loader))
        self.addCleanup(self.client.close)
        self.csrf = self._login()
        prepared = self._prepare().json()
        state["changed"] = True
        rejected = self._confirm(prepared, remote_consent=True)
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(rejected.json()["error"]["code"], "configuracion_cambiada")
        self.assertFalse(self.fixture.job_ledger_path.exists())
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
