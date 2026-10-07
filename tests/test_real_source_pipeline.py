"""Integration tests for bounded T5c: audited real-source pipeline integration.

Tests full pipeline execution with real composite and provider adapters,
injectable fake HTTP transport, fake LLM providers, and SQLite/artifact persistence.
Verifies target SHA pinning, empty commit handling, incomplete source availability gates,
truthful audit persistence, and CLI integration.
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from pydantic import SecretStr

from agente_gestion.application.config import Settings
from agente_gestion.adapters.sources.factory import create_composite_source
from agente_gestion.adapters.sources.http_transport import (
    HttpGetTransportPort,
    HttpResponse,
)
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.adapters.storage.artifacts import ArtifactStorageAdapter
from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.application.orchestrator import ProjectOrchestrator, OrchestratorConfig
from agente_gestion.quality.evaluator import QualityEvaluator
from agente_gestion.domain.run import RunStatus, StageStatus
from agente_gestion.domain.report import HumanDecisionState
from agente_gestion.domain.llm import GenerationResult, GenerationStatus
from agente_gestion.domain.evidence import EvidenceItem, EvidenceProvenance, EvidenceSource
from agente_gestion.ports.source_errors import SourceError, SourceErrorCategory, SourceProvider
from agente_gestion.cli import main, run_command


TARGET_SHA = "1" * 40
MISMATCH_SHA = "2" * 40
DEFAULT_TZ = ZoneInfo("America/Guayaquil")


class MockPipelineTransport(HttpGetTransportPort):
    """Deterministic fake HTTP transport for real adapter pipeline tests."""

    def __init__(self, responses: dict[str, HttpResponse | Exception] | None = None):
        self.responses = responses or {}
        self.calls: list[dict] = []

    def get(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        timeout_seconds: float | None = None,
    ) -> HttpResponse:
        self.calls.append({"url": url, "params": params, "headers": headers})
        if url in self.responses:
            resp = self.responses[url]
            if isinstance(resp, Exception):
                raise resp
            return resp

        # Default mock HTTP responses
        if "api.trello.com/1/boards/" in url and "/lists/all" in url:
            return HttpResponse(
                status_code=200,
                body=[{"id": "list-done", "idBoard": "board-1", "name": "Done", "closed": False}],
            )
        if "api.trello.com/1/boards/" in url and "/cards/all" in url:
            return HttpResponse(
                status_code=200,
                body=[{
                    "id": "card-1",
                    "idBoard": "board-1",
                    "idList": "list-done",
                    "name": "Implement feature X",
                    "desc": "Detail of task",
                    "closed": False,
                    "due": "2026-10-06T12:00:00Z",
                    "idMembers": [],
                    "labels": [],
                }],
            )
        if "api.github.com/repos/" in url and f"/commits/{TARGET_SHA}" in url:
            return HttpResponse(
                status_code=200,
                body={
                    "sha": TARGET_SHA,
                    "files": [{
                        "filename": "src/module.py",
                        "status": "modified",
                        "patch": "@@ -1,2 +1,3 @@\n def func():\n+    return 42\n     pass\n",
                    }],
                },
            )
        if "api.github.com/repos/" in url and "/actions/runs" in url:
            return HttpResponse(
                status_code=200,
                body={
                    "total_count": 1,
                    "workflow_runs": [{
                        "id": 1001,
                        "name": "CI",
                        "head_sha": TARGET_SHA,
                        "status": "completed",
                        "conclusion": "success",
                        "head_branch": "main",
                        "created_at": "2026-10-06T10:00:00Z",
                        "updated_at": "2026-10-06T10:05:00Z",
                    }],
                },
            )
        if "api.github.com/repos/test-owner/test-repo" in url and not url.endswith("/runs") and not url.endswith("/all"):
            return HttpResponse(status_code=200, body={"default_branch": "main"})

        return HttpResponse(status_code=200, body={})


class RealSourcePipelineTestCase(unittest.TestCase):
    """Integration test suite for bounded T5c real-source pipeline execution."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_pipeline.db")
        self.runs_dir = os.path.join(self.temp_dir, "runs")
        self.policies_path = os.path.join(self.temp_dir, "policies.yaml")
        with open(self.policies_path, "w", encoding="utf-8") as f:
            f.write(
                "policies:\n"
                "  - id: Q1\n"
                "    description: Tests must not fail\n"
                "    metric_key: failed_tests\n"
                "    operator: '=='\n"
                "    threshold: 0\n"
                "    source: ci\n"
                "    mandatory: false\n"
            )

        self.sqlite = SQLiteStorageAdapter(db_path=self.db_path)
        self.artifacts = ArtifactStorageAdapter(base_dir=self.runs_dir)
        self.fake_provider = FakeProvider()

        self.settings = Settings(
            _env_file=None,
            source_mode="real",
            project_id="test-proj",
            trello_board_id="board-1",
            trello_api_key="key-123",
            trello_token="tok-123",
            trello_completed_list_ids=["list-done"],
            github_owner="test-owner",
            github_repo="test-repo",
            github_commit_sha=TARGET_SHA,
            policies_path=self.policies_path,
            db_path=self.db_path,
            runs_dir=self.runs_dir,
            is_fake=True,
            timezone="America/Guayaquil",
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_full_pipeline_happy_path_with_audited_records_and_evidence(self):
        """Full pipeline with real Composite sources, Fake transport, and Fake LLM completes successfully."""
        transport = MockPipelineTransport()
        composite = create_composite_source(self.settings, commit_sha=TARGET_SHA, transport=transport)

        config = OrchestratorConfig(
            project_id="test-proj",
            commit_sha=TARGET_SHA,
            target_commit_sha=TARGET_SHA,
            db_path=self.db_path,
            runs_dir=self.runs_dir,
            is_simulated=True,
            timezone="America/Guayaquil",
        )
        orchestrator = ProjectOrchestrator(
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=self.fake_provider,
            revisor_provider=self.fake_provider,
            verificador_provider=self.fake_provider,
            aprobador_provider=self.fake_provider,
            source_adapter=composite,
            config=config,
        )

        run = orchestrator.execute(project_id="test-proj")

        self.assertEqual(run.status, RunStatus.awaiting_human)
        self.assertIn("source_collection", run.stages)
        source_stage = run.stages["source_collection"]
        self.assertEqual(source_stage.status, StageStatus.completed)
        self.assertIsNotNone(source_stage.output_summary)
        summary_data = json.loads(source_stage.output_summary)
        self.assertEqual(summary_data["target_commit_sha"], TARGET_SHA)
        self.assertTrue(summary_data["is_complete"])
        self.assertEqual(len(summary_data["records"]), 4)

        # Verify evidence persisted in SQLite and retrievable
        stored_ev = self.sqlite.get_evidence(run.run_id)
        self.assertGreater(len(stored_ev), 0)
        trello_ev = next((e for e in stored_ev if hasattr(e, "provenance") and e.provenance.source == EvidenceSource.trello), None)
        self.assertIsNotNone(trello_ev)

        # Verify execution_metrics.json artifact has source_collection data
        metrics_file = os.path.join(self.runs_dir, run.run_id, "execution_metrics.json")
        self.assertTrue(os.path.exists(metrics_file))
        with open(metrics_file, "r", encoding="utf-8") as f:
            exec_metrics = json.load(f)
        self.assertIn("source_collection", exec_metrics)
        self.assertEqual(exec_metrics["source_collection"]["target_commit_sha"], TARGET_SHA)

        # Verify report in SQLite
        report = self.sqlite.get_latest_report("test-proj")
        self.assertIsNotNone(report)
        self.assertEqual(report.human_decision, HumanDecisionState.pending)

    def test_pipeline_empty_commit_still_carries_target_sha_to_ci(self):
        """When code commit has 0 diff files, target commit SHA is still pinned and passed to CI matching."""
        empty_commit_resp = {"sha": TARGET_SHA, "files": []}
        transport = MockPipelineTransport({
            f"https://api.github.com/repos/test-owner/test-repo/commits/{TARGET_SHA}": HttpResponse(
                status_code=200, body=empty_commit_resp
            ),
        })
        composite = create_composite_source(self.settings, commit_sha=TARGET_SHA, transport=transport)

        config = OrchestratorConfig(
            project_id="test-proj",
            target_commit_sha=TARGET_SHA,
            db_path=self.db_path,
            runs_dir=self.runs_dir,
            is_simulated=True,
        )
        orchestrator = ProjectOrchestrator(
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=self.fake_provider,
            revisor_provider=self.fake_provider,
            verificador_provider=self.fake_provider,
            aprobador_provider=self.fake_provider,
            source_adapter=composite,
            config=config,
        )

        run = orchestrator.execute(project_id="test-proj")

        self.assertEqual(run.status, RunStatus.awaiting_human)
        # Quality stage skipped because code_diffs is empty
        self.assertEqual(run.stages["quality"].status, StageStatus.skipped)
        # Matching CI evaluated against pinned target SHA
        report = self.sqlite.get_latest_report("test-proj")
        self.assertIsNotNone(report)
        self.assertIsNotNone(report.ci_result)
        self.assertEqual(report.ci_result.commit_sha, TARGET_SHA)

    def test_pipeline_ci_mismatched_sha_evaluates_to_insufficient(self):
        """When CI run has mismatched commit SHA, matching CI is None and mandatory checks become unknown/insufficient."""
        # Add mandatory policy for CI
        with open(self.policies_path, "w", encoding="utf-8") as f:
            f.write(
                "policies:\n"
                "  - id: Q1\n"
                "    description: Tests pass\n"
                "    metric_key: failed_tests\n"
                "    operator: '=='\n"
                "    threshold: 0\n"
                "    source: ci\n"
                "    mandatory: true\n"
            )

        # CI run returns mismatching SHA
        mismatched_ci_resp = {
            "total_count": 1,
            "workflow_runs": [{
                "id": 2002,
                "name": "CI",
                "head_sha": MISMATCH_SHA,
                "status": "completed",
                "conclusion": "success",
                "head_branch": "main",
                "created_at": "2026-10-06T10:00:00Z",
                "updated_at": "2026-10-06T10:05:00Z",
            }],
        }
        transport = MockPipelineTransport({
            "https://api.github.com/repos/test-owner/test-repo/actions/runs": HttpResponse(
                status_code=200, body=mismatched_ci_resp
            ),
        })
        composite = create_composite_source(self.settings, commit_sha=TARGET_SHA, transport=transport)

        config = OrchestratorConfig(
            project_id="test-proj",
            target_commit_sha=TARGET_SHA,
            db_path=self.db_path,
            runs_dir=self.runs_dir,
            is_simulated=True,
        )
        orchestrator = ProjectOrchestrator(
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=self.fake_provider,
            revisor_provider=self.fake_provider,
            verificador_provider=self.fake_provider,
            aprobador_provider=self.fake_provider,
            source_adapter=composite,
            config=config,
        )

        run = orchestrator.execute(project_id="test-proj")

        self.assertEqual(run.status, RunStatus.awaiting_human)
        report = self.sqlite.get_latest_report("test-proj")
        self.assertIsNotNone(report)
        self.assertIsNone(report.ci_result)
        self.assertEqual(report.policy_evaluations.recommended_action, "evidencia_insuficiente")
        self.assertIn("Q1", report.policy_evaluations.mandatory_unknown_ids)
        self.assertEqual(report.recommendation, "evidencia_insuficiente")

    def test_pipeline_trello_failure_records_audit_and_enforces_availability_gate(self):
        """When Trello fails, snapshot is incomplete, no fixture fallback occurs, and availability gate prevents approval."""
        failing_transport = MockPipelineTransport({
            "https://api.trello.com/1/boards/board-1/lists/all": HttpResponse(
                status_code=401, body={"message": "invalid key"}
            ),
        })
        composite = create_composite_source(self.settings, commit_sha=TARGET_SHA, transport=failing_transport)

        # Enqueue monitoring response compatible with 0 tasks
        monitoring_result = GenerationResult(
            status=GenerationStatus.complete,
            structured_data={
                "summary": "Resumen semanal: no hay tareas disponibles.",
                "risks": ["Origen de tareas inaccesible."],
                "suggested_actions": ["Verificar credenciales Trello."],
                "referenced_task_ids": [],
                "evidence_ids": [],
            },
            provider="fake_simulated",
            model="fake-model-v1",
            duration_seconds=0.01,
        )
        self.fake_provider.enqueue_responses([monitoring_result])

        config = OrchestratorConfig(
            project_id="test-proj",
            target_commit_sha=TARGET_SHA,
            db_path=self.db_path,
            runs_dir=self.runs_dir,
            is_simulated=True,
        )
        orchestrator = ProjectOrchestrator(
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=self.fake_provider,
            revisor_provider=self.fake_provider,
            verificador_provider=self.fake_provider,
            aprobador_provider=self.fake_provider,
            source_adapter=composite,
            config=config,
        )

        run = orchestrator.execute(project_id="test-proj")

        self.assertEqual(run.status, RunStatus.awaiting_human)
        snapshot = self.sqlite.get_snapshot(run.snapshot_id)
        self.assertIsNotNone(snapshot)
        self.assertFalse(snapshot.is_complete)
        self.assertEqual(len(snapshot.tasks), 0)

        report = self.sqlite.get_latest_report("test-proj")
        self.assertIsNotNone(report)
        self.assertEqual(report.recommendation, "evidencia_insuficiente")
        self.assertIn("Q_SOURCE_AVAILABILITY_INCOMPLETE", report.policy_evaluations.mandatory_unknown_ids)

    def test_pipeline_github_postresolution_failure_omits_target_sha_and_sanitizes_audit(self):
        """When GitHub diff retrieval encounters a known secret post-resolution, target commit SHA is omitted and audit is sanitized."""
        secret = "key-123"  # Configured in self.settings.trello_api_key
        echo_commit_resp = {
            "sha": TARGET_SHA,
            "files": [{
                "filename": "src/module.py",
                "status": "modified",
                "patch": f"@@ -1,2 +1,3 @@\n def func():\n+    api_key = '{secret}'\n     pass\n",
            }],
        }
        transport = MockPipelineTransport({
            f"https://api.github.com/repos/test-owner/test-repo/commits/{TARGET_SHA}": HttpResponse(
                status_code=200, body=echo_commit_resp
            ),
        })
        composite = create_composite_source(self.settings, commit_sha=TARGET_SHA, transport=transport)

        config = OrchestratorConfig(
            project_id="test-proj",
            commit_sha=TARGET_SHA,
            target_commit_sha=TARGET_SHA,
            db_path=self.db_path,
            runs_dir=self.runs_dir,
            is_simulated=True,
            timezone="America/Guayaquil",
        )
        orchestrator = ProjectOrchestrator(
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=self.fake_provider,
            revisor_provider=self.fake_provider,
            verificador_provider=self.fake_provider,
            aprobador_provider=self.fake_provider,
            source_adapter=composite,
            config=config,
        )

        run = orchestrator.execute(project_id="test-proj")

        self.assertEqual(run.status, RunStatus.awaiting_human)
        self.assertIn("source_collection", run.stages)
        source_stage = run.stages["source_collection"]
        self.assertEqual(source_stage.status, StageStatus.completed)

        # Stage summary has target_commit_sha null and no secret
        summary_data = json.loads(source_stage.output_summary)
        self.assertIsNone(summary_data["target_commit_sha"])
        self.assertFalse(summary_data["is_complete"])
        self.assertNotIn(secret, source_stage.output_summary)

        # SQLite persisted stage execution record has target_commit_sha null
        persisted_stages = self.sqlite.get_stage_results(run.run_id)
        persisted_stage = persisted_stages["source_collection"]
        persisted_summary = json.loads(persisted_stage.output_summary)
        self.assertIsNone(persisted_summary["target_commit_sha"])
        self.assertNotIn(secret, persisted_stage.output_summary)

        # execution_metrics.json artifact has source_collection.target_commit_sha null and no secret
        metrics_file = os.path.join(self.runs_dir, run.run_id, "execution_metrics.json")
        self.assertTrue(os.path.exists(metrics_file))
        with open(metrics_file, "r", encoding="utf-8") as f:
            metrics_raw = f.read()
            exec_metrics = json.loads(metrics_raw)
        self.assertIn("source_collection", exec_metrics)
        self.assertIsNone(exec_metrics["source_collection"]["target_commit_sha"])
        self.assertNotIn(secret, metrics_raw)

        # Snapshot in SQLite has code_diffs empty, is incomplete, and contains no secret
        snapshot = self.sqlite.get_snapshot(run.snapshot_id)
        self.assertIsNotNone(snapshot)
        self.assertFalse(snapshot.is_complete)
        self.assertEqual(len(snapshot.code_diffs), 0)
        self.assertNotIn(secret, snapshot.model_dump_json())

        # Persisted evidence in SQLite contains no secret
        stored_ev = self.sqlite.get_evidence(run.run_id)
        for ev in stored_ev:
            self.assertNotIn(secret, ev.model_dump_json())

        # Report in SQLite has human_decision pending and recommendation evidencia_insuficiente
        report = self.sqlite.get_latest_report("test-proj")
        self.assertIsNotNone(report)
        self.assertEqual(report.human_decision, HumanDecisionState.pending)
        self.assertEqual(report.recommendation, "evidencia_insuficiente")
        self.assertNotIn(secret, report.model_dump_json())

    def test_quality_evaluator_mandatory_fail_plus_incomplete_retains_solicitar_cambios(self):
        """Mandatory policy failure takes precedence over incomplete source availability (never hides failure)."""
        from agente_gestion.domain.policies import QualityRule
        from agente_gestion.domain.code import CIRunResult

        rules = [
            QualityRule(
                id="Q_TESTS",
                description="Tests pass",
                metric_key="failed_tests",
                operator="==",
                threshold=0,
                mandatory=True,
            )
        ]
        ci_run = CIRunResult(
            id="ci-failed",
            workflow_name="tests",
            commit_sha=TARGET_SHA,
            status="failure",
            total_tests=10,
            failed_tests=2,
        )

        res = QualityEvaluator.evaluate(
            rules=rules,
            ci_run=ci_run,
            source_is_complete=False,
            incomplete_reason="Trello source failed",
        )

        self.assertIn("Q_TESTS", res.mandatory_failed_ids)
        self.assertIn("Q_SOURCE_AVAILABILITY_INCOMPLETE", res.mandatory_unknown_ids)
        # Solicitar cambios MUST be retained despite incomplete source
        self.assertEqual(res.recommended_action, "solicitar_cambios")

    def test_cli_run_real_sources_fake_llm_succeeds(self):
        """CLI run command with --sources real --fake executes successfully with injected transport."""
        fake_tr = MockPipelineTransport()
        config_path = os.path.join(self.temp_dir, "cli_real_cfg.yaml")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(
                f"sources:\n"
                f"  mode: real\n"
                f"  trello:\n"
                f"    board_id: board-1\n"
                f"    api_key: key-123\n"
                f"    token: tok-123\n"
                f"    mapping:\n"
                f"      completed_list_ids: ['list-done']\n"
                f"  github:\n"
                f"    owner: test-owner\n"
                f"    repo: test-repo\n"
                f"    commit_sha: {TARGET_SHA}\n"
                f"  policies_path: {self.policies_path}\n"
            )

        cmd = [
            "run",
            "test-proj",
            "--sources", "real",
            "--fake",
            "--commit", TARGET_SHA,
            "--config", config_path,
            "--db-path", self.db_path,
            "--output-dir", self.runs_dir,
        ]
        stdout_capture = io.StringIO()
        with patch("agente_gestion.adapters.sources.factory.BoundedGetTransport", return_value=fake_tr):
            with patch.object(sys, "argv", ["agente_gestion"] + cmd), patch("sys.stdout", stdout_capture):
                exit_code = main()

        self.assertEqual(exit_code, 0)
        out = stdout_capture.getvalue()
        self.assertIn("RESUMEN DE EJECUCIÓN", out)
        self.assertIn("awaiting_human", out)


if __name__ == "__main__":
    unittest.main()
