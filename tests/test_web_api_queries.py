"""Deterministic HTTP coverage for public read-only H5 tracking routes."""

import hashlib
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

from agente_gestion.api.app import ApiSettings, ProjectConfig, create_app
from agente_gestion.api.settings import load_api_settings
from tests.web_fixtures import (
    EVIDENCE_SECRET,
    OPERATOR_TOKEN,
    PROJECT_ID,
    TASK_ID,
    build_web_api_fixture,
)
from fastapi.testclient import TestClient


@contextmanager
def working_directory(path: Path):
    original = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(original)


class WebApiQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = build_web_api_fixture(Path(self.temp.name))
        self.client = self.fixture.client

    def test_projects_summary_tasks_history_compare_and_detail_use_real_captures(self):
        before = self.fixture.database_path.read_bytes()
        projects = self.client.get("/api/v1/projects").json()
        self.assertEqual(projects["total"], 2)
        by_id = {item["id"]: item for item in projects["items"]}
        self.assertEqual(by_id[PROJECT_ID]["name"], "Proyecto de prueba")
        self.assertEqual(by_id[PROJECT_ID]["summary"]["total_tasks"], 1)
        self.assertEqual(by_id["STORED-ONLY"]["source_mode"], "stored")

        summary = self.client.get(f"/api/v1/projects/{PROJECT_ID}/summary").json()
        self.assertEqual(summary["state_counts"]["en_curso"], 1)
        self.assertTrue(summary["captured_at"].endswith("-05:00"))
        tasks = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/tasks",
            params={"state": "en_curso", "q": "crítica", "page": 1, "page_size": 1,
                    "sort": "title", "direction": "desc"},
        ).json()
        self.assertEqual((tasks["total"], tasks["items"][0]["id"]), (1, TASK_ID))
        self.assertEqual(tasks["items"][0]["state"], "en_curso")
        self.assertNotIn("evidence", tasks["items"][0])
        self.assertEqual(self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/tasks",
            params={"assignee_id": "operator-1", "list_name": "en curso", "critical_only": "true"},
        ).json()["total"], 1)
        self.assertEqual(self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/tasks", params={"overdue_only": "true"}
        ).json()["total"], 0)
        self.assertEqual(self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/tasks", params={"due_soon_only": "true"}
        ).json()["total"], 1)
        incompatible = self.client.get(f"/api/v1/projects/{PROJECT_ID}/tasks", params={
            "overdue_only": "true", "due_soon_only": "true",
        })
        self.assertEqual(incompatible.status_code, 422)
        self.assertEqual(incompatible.json()["error"]["code"], "filtros_incompatibles")

        history = self.client.get(f"/api/v1/projects/{PROJECT_ID}/history").json()
        self.assertEqual(history["total"], 2)
        self.assertEqual(history["items"][-1]["capture_id"], self.fixture.current_capture_id)
        comparison = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/compare",
            params={"before": self.fixture.older_capture_id,
                    "after": self.fixture.current_capture_id},
        ).json()
        self.assertTrue(any(change["task_id"] == TASK_ID for change in comparison["changes"]))
        self.assertNotIn(EVIDENCE_SECRET, self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/compare",
            params={"before": self.fixture.older_capture_id,
                    "after": self.fixture.current_capture_id},
        ).text)

        detail_response = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/tasks/{TASK_ID}",
            params={"capture_id": self.fixture.current_capture_id},
        )
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.json()
        self.assertEqual(detail["title"], f"Tarea crítica api_token=[REDACTADO]")
        self.assertTrue(detail["due_date"].endswith("-05:00"))
        self.assertTrue(detail["evidence"][0]["provenance"]["timestamp"].endswith("-05:00"))
        self.assertIn("Descripción de fixture", detail["detail"])
        self.assertNotIn(EVIDENCE_SECRET, detail_response.text)
        self.assertEqual(len(detail["evidence"]), 1)
        self.assertNotIn("payload", detail["evidence"][0]["provenance"])
        self.assertEqual(len(detail["history"]), 1)
        self.assertIsNone(detail["history"][0]["actor"])
        self.assertEqual(detail["history"][0]["capture_id"], self.fixture.current_capture_id)
        self.assertEqual(detail["history"][0]["previous_capture_id"], self.fixture.older_capture_id)
        older_detail = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/tasks/{TASK_ID}",
            params={"capture_id": self.fixture.older_capture_id},
        ).json()
        self.assertEqual(older_detail["evidence"], [])
        self.assertEqual(older_detail["history"], [])

        self.assertEqual(hashlib.sha256(self.fixture.database_path.read_bytes()).digest(),
                         hashlib.sha256(before).digest())
        with closing(sqlite3.connect(self.fixture.database_path)) as connection:
            counts_after = tuple(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                                 for table in ("runs", "snapshots", "snapshot_captures", "evidence"))
        self.assertEqual(counts_after, (2, 3, 3, 2))

    def test_persisted_runs_stages_findings_quality_policies_and_executive_summary(self):
        before = self.fixture.database_path.read_bytes()
        first_page = self.client.get("/api/v1/runs", params={
            "project_id": PROJECT_ID, "page": 1, "page_size": 1,
        })
        self.assertEqual(first_page.status_code, 200)
        runs = first_page.json()
        self.assertEqual(runs["total"], 2)
        self.assertEqual(runs["items"][0]["run_id"], "run-current")
        self.assertEqual(self.client.get("/api/v1/runs", params={
            "project_id": PROJECT_ID, "page": 2, "page_size": 1,
        }).json()["items"][0]["run_id"], "run-older")
        self.assertEqual(self.client.get("/api/v1/runs/run-current").json()["status"], "completed")

        stages = self.client.get("/api/v1/runs/run-current/stages").json()["items"]
        stage_by_name = {stage["stage_name"]: stage for stage in stages}
        self.assertTrue(stage_by_name["quality"]["recorded"])
        self.assertEqual(stage_by_name["quality"]["status"], "completed")
        self.assertEqual(stage_by_name["verifier"]["status"], "skipped")
        self.assertFalse(stage_by_name["verifier"]["providers"])

        findings = self.client.get("/api/v1/runs/run-current/findings").json()
        self.assertEqual(findings["total"], 1)
        finding = findings["items"][0]
        self.assertEqual(finding["finding_id"], "finding-candidate-001")
        self.assertTrue(finding["candidate"])
        self.assertEqual(finding["verification_status"], "insufficient")
        detail_response = self.client.get(
            "/api/v1/runs/run-current/findings/finding-candidate-001",
        )
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.json()
        self.assertEqual(detail["claim"], "Revisar validación de entrada")
        self.assertEqual(detail["verification"]["verdict"], "insufficient")
        self.assertEqual(detail["code_excerpt"]["evidence_id"], "evidence-code-001")
        self.assertTrue(detail["code_excerpt"]["is_partial"])
        self.assertTrue(detail["code_excerpt"]["redacted"])
        self.assertNotIn(EVIDENCE_SECRET, detail_response.text)

        evidence = self.client.get("/api/v1/runs/run-current/evidence").json()
        self.assertEqual(evidence["total"], 2)
        self.assertNotIn("payload", str(evidence))
        quality = self.client.get(f"/api/v1/projects/{PROJECT_ID}/quality").json()
        self.assertEqual(quality["finding_counts"]["candidate"], 1)
        self.assertIsNone(quality["ci"]["coverage_percentage"])
        policies = self.client.get(f"/api/v1/projects/{PROJECT_ID}/policies").json()
        self.assertEqual([item["rule_id"] for item in policies["items"]], ["Q1", "Q2", "Q3"])
        self.assertEqual({item["status"] for item in policies["items"]}, {"pass", "fail", "unknown"})

        executive = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/executive-summary",
        ).json()
        self.assertEqual(executive["task_summary"]["total_tasks"], 1)
        self.assertEqual(len(executive["capture_series"]), 2)
        self.assertEqual(executive["quality"]["run_id"], "run-current")
        self.assertEqual(executive["latest_report"]["run_id"], "run-current")
        self.assertEqual(executive["runs"]["total"], 2)
        stored_quality = self.client.get("/api/v1/projects/STORED-ONLY/quality").json()
        self.assertEqual(stored_quality["availability"], "unknown")
        self.assertIsNone(stored_quality["finding_counts"])
        self.assertIsNone(stored_quality["ci"])
        self.assertEqual(self.client.get("/api/v1/projects/STORED-ONLY/policies").json()["availability"], "unknown")
        self.assertEqual(self.fixture.database_path.read_bytes(), before)

    def test_invalid_filters_and_identifiers_are_standardized_and_safe(self):
        for params in (
            {"state": "not-a-domain-state"},
            {"sort": "created_at"},
            {"page_size": 101},
            {"page": 0},
        ):
            with self.subTest(params=params):
                response = self.client.get(f"/api/v1/projects/{PROJECT_ID}/tasks", params=params)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(set(response.json()), {"error"})
                self.assertIn("message", response.json()["error"])
        self.assertIn(self.client.get("/api/v1/projects/..").status_code, (404, 422))
        self.assertEqual(self.client.get("/api/v1/projects/UNKNOWN").status_code, 404)
        openapi = self.client.get("/openapi.json").json()
        self.assertIn("/api/v1/projects/{project_id}/tasks/{task_id}", openapi["paths"])
        detail_schema = openapi["components"]["schemas"]["TaskDetailResponse"]["properties"]
        self.assertIn("evidence", detail_schema)
        self.assertIn("history", detail_schema)
        self.assertEqual(
            openapi["components"]["schemas"]["TaskState"]["enum"],
            ["pending", "en_curso", "completada", "bloqueada", "desconocido"],
        )

    def test_missing_database_is_empty_and_get_does_not_create_it(self):
        missing = Path(self.temp.name) / "not-created" / "absent.sqlite"
        app = create_app(ApiSettings(
            db_path=str(missing),
            projects=[ProjectConfig(id=PROJECT_ID, name="Proyecto sin capturas")],
            allowed_origins=["http://testserver"],
            allowed_hosts=["testserver"],
        ))
        client = TestClient(app)
        self.assertEqual(client.get("/api/v1/projects").json()["items"][0]["summary"], None)
        self.assertEqual(client.get(f"/api/v1/projects/{PROJECT_ID}/tasks").json()["items"], [])
        self.assertEqual(client.get(f"/api/v1/projects/{PROJECT_ID}/history").json()["items"], [])
        summary = client.get(f"/api/v1/projects/{PROJECT_ID}/summary")
        self.assertEqual(summary.status_code, 404)
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())

    def test_missing_config_does_not_invent_public_projects(self):
        missing_db = Path(self.temp.name) / "missing-config.sqlite"
        missing_db_path = str(missing_db)
        with patch.dict(os.environ, {
            "AGENTE_DB_PATH": missing_db_path,
            "H5_ALLOWED_ORIGINS": "http://testserver",
            "H5_ALLOWED_HOSTS": "testserver",
        }, clear=True):
            self.assertNotIn("AGENTE_CONFIG_PATH", os.environ)
            with working_directory(Path(self.temp.name)):
                settings = load_api_settings()
            with TestClient(create_app(settings)) as client:
                response = client.get("/api/v1/projects")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])
        self.assertEqual(response.json()["total"], 0)
        self.assertFalse(missing_db.exists())

    def test_malformed_config_does_not_invent_public_projects(self):
        malformed_config = Path(self.temp.name) / "malformed.yaml"
        malformed_config.write_text("project: [unterminated\n", encoding="utf-8")
        malformed_db = Path(self.temp.name) / "malformed-config.sqlite"
        with patch.dict(os.environ, {
            "AGENTE_CONFIG_PATH": str(malformed_config),
            "AGENTE_DB_PATH": str(malformed_db),
            "H5_ALLOWED_ORIGINS": "http://testserver",
            "H5_ALLOWED_HOSTS": "testserver",
        }, clear=True):
            settings = load_api_settings()
        with TestClient(create_app(settings)) as client:
            response = client.get("/api/v1/projects")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])
        self.assertEqual(response.json()["total"], 0)
        self.assertFalse(malformed_db.exists())

    def test_missing_config_keeps_projects_discovered_from_existing_storage(self):
        with patch.dict(os.environ, {
            "AGENTE_DB_PATH": str(self.fixture.database_path),
            "H5_ALLOWED_ORIGINS": "http://testserver",
            "H5_ALLOWED_HOSTS": "testserver",
        }, clear=True):
            self.assertNotIn("AGENTE_CONFIG_PATH", os.environ)
            with working_directory(Path(self.temp.name)):
                settings = load_api_settings()
        with TestClient(create_app(settings)) as client:
            response = client.get("/api/v1/projects")
        self.assertEqual(response.status_code, 200)
        projects = response.json()["items"]
        self.assertEqual({project["id"] for project in projects}, {PROJECT_ID, "STORED-ONLY"})
        self.assertNotIn("proyecto-demo", {project["id"] for project in projects})
        self.assertTrue(all(project["source_mode"] == "stored" for project in projects))

    def test_configured_project_and_paths_load_from_allowlisted_yaml_without_dotenv(self):
        config = Path(self.temp.name) / "web-config.yaml"
        database_path = Path(self.temp.name) / "configured.sqlite"
        config.write_text(
            "project:\n  id: HORMIGESTION\n  name: Hormigestion\n"
            "  timezone: America/Guayaquil\nsources:\n  mode: real\n"
            "execution:\n  due_soon_days: 9\n"
            f"db_path: {database_path}\nruns_dir: {self.temp.name}/runs\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {
            "AGENTE_CONFIG_PATH": str(config),
            "AGENTE_DB_PATH": "",
            "H5_OPERATOR_TOKEN": "",
            "H5_ALLOWED_ORIGINS": "http://testserver",
            "H5_ALLOWED_HOSTS": "testserver",
        }, clear=True):
            settings = load_api_settings()
        self.assertEqual(settings.projects[0].id, "HORMIGESTION")
        self.assertEqual(settings.projects[0].source_mode, "real")
        self.assertEqual(settings.db_path, str(database_path))
        self.assertEqual(settings.runs_dir, f"{self.temp.name}/runs")
        self.assertEqual(settings.due_soon_days, 9)


if __name__ == "__main__":
    unittest.main()
