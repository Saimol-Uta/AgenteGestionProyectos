"""Public orchestration contracts for repository-isolated quality analysis."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.adapters.sources.http_transport import HttpResponse
from agente_gestion.adapters.sources.multirepo import MultiRepoSourcesAdapter
from agente_gestion.adapters.storage.artifacts import ArtifactStorageAdapter
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.application.config import Settings
from agente_gestion.application.orchestrator import OrchestratorConfig, ProjectOrchestrator
from agente_gestion.application.reporting import generate_report_markdown
from agente_gestion.domain.ci_metrics import CiArtifactOutcome, CiWorkflowStatus
from agente_gestion.domain.code import (
    CIRunResult,
    CiMetricsArtifactMetadata,
    CiMetricsArtifactValues,
    CodeDiffSnippet,
)
from agente_gestion.domain.policies import QualityRule
from agente_gestion.domain.repositories import (
    CommitResolutionStatus,
    RepositoryAccessStatus,
    RepositoryAnalysisState,
    RepositoryAvailabilityStatus,
    RepositoryConfiguration,
    RepositoryIdentity,
    RepositoryProvenance,
)
from agente_gestion.domain.snapshot import RepositorySnapshotResult, WeeklySnapshot
from agente_gestion.domain.tasks import TaskCard, TaskState
from agente_gestion.domain.run import RunStatus, StageStatus, compute_sha256
from agente_gestion.domain.llm import GenerationResult, GenerationStatus


class CapturingFakeProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return super().generate(request)


class ForeignFindingProvider(CapturingFakeProvider):
    def generate(self, request):
        if request.role == "revisor":
            self.requests.append(request)
            scope = request.evidences["repository_scope"]
            snippet = request.evidences["code_snippets"][0]
            foreign_identity = {"id": "other", "owner": "foreign", "repo": "repository"}
            return GenerationResult(
                status=GenerationStatus.complete,
                structured_data={"findings": [{
                    "id": "local-finding",
                    "rule_id": None,
                    "claim": "A candidate finding with a foreign declared origin.",
                    "severity": "high",
                    "evidence_ids": [snippet["commit_sha"]],
                    "commit_sha": scope["target_commit_sha"],
                    "file_path": snippet["file_path"],
                    "line": snippet["lines"][0],
                    "confidence": 0.9,
                    "requires_verification": True,
                    "category": "division_cero",
                    "evidence": "return 1 / total",
                    "suggested_code": "return 0",
                    "improvement_summary": "Preserva el origen declarado.",
                    "repository_identity": foreign_identity,
                    "provenance": {
                        "repository_identity": foreign_identity,
                        "commit_sha": scope["target_commit_sha"],
                        "source": "github",
                    },
                }]},
                provider="fake_simulated",
                model=self.model,
                duration_seconds=0.01,
                prompt_tokens=100,
                completion_tokens=50,
            )
        return super().generate(request)


class CrossReferenceVerifierProvider(CapturingFakeProvider):
    def generate(self, request):
        if request.role == "verificador":
            self.requests.append(request)
            finding = request.evidences["findings"][0]
            return GenerationResult(
                status=GenerationStatus.complete,
                structured_data={"verifications": [{
                    "finding_id": "other-repository::" + finding["commit_sha"] + "::f-fake-1",
                    "verdict": "confirmed",
                    "reason": "This foreign reference must be rejected.",
                    "evidence_ids": finding["evidence_ids"][:1],
                }]},
                provider="fake_simulated",
                model=self.model,
                duration_seconds=0.01,
                prompt_tokens=100,
                completion_tokens=50,
            )
        return super().generate(request)


def _repository_result(
    repo_id: str,
    repo_name: str,
    sha: str,
    run_id: str,
    *,
    total_tests: int | None,
    failed_tests: int | None,
    coverage: float | None,
    coverage_basis: str,
    workflow_status: CiWorkflowStatus = CiWorkflowStatus.success,
    certified: bool = True,
    is_complete: bool = True,
) -> RepositorySnapshotResult:
    owner, repo = repo_name.split("/", 1)
    identity = RepositoryIdentity(id=repo_id, owner=owner, repo=repo)
    state = RepositoryAnalysisState(
        identity=identity,
        branch="develop",
        target_commit_sha=sha,
        commit_resolution_status=CommitResolutionStatus.resolved,
        access_status=RepositoryAccessStatus.allowed,
        availability_status=RepositoryAvailabilityStatus.available,
    )
    source = "def calculate(total):\n    return 1 / total\n"
    code = CodeDiffSnippet(
        repository=repo_name,
        commit_sha=sha,
        file_path="src/shared.py",
        lines=[1, 2],
        content=source,
        evidence_scope="full_file",
        is_complete=True,
        total_lines=2,
        source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        repository_identity=identity,
        provenance=RepositoryProvenance(
            repository_identity=identity,
            commit_sha=sha,
            source="github",
        ),
    )
    artifact_metrics = CiMetricsArtifactValues(
        repository=repo_name,
        commit_sha=sha,
        workflow_run_id=run_id,
        workflow_run_attempt="1",
        status=workflow_status,
        total_tests=total_tests,
        failed_tests=failed_tests,
        coverage_percentage=coverage,
        coverage_metric="lines",
        coverage_basis=coverage_basis,
        test_suites=None,
        e2e_status="not_implemented" if "frontend" in repo_id else None,
    ) if certified else None
    ci = CIRunResult(
        id=run_id,
        workflow_name="quality",
        workflow_path=".github/workflows/quality.yml",
        commit_sha=sha,
        status=workflow_status.value,
        total_tests=total_tests,
        failed_tests=failed_tests,
        coverage_percentage=coverage,
        repository_identity=identity,
        run_attempt=1,
        artifact_name=f"ci-quality-{sha}-{run_id}-1",
        metrics_artifact=CiMetricsArtifactMetadata(
            outcome=(CiArtifactOutcome.validated if certified else CiArtifactOutcome.invalid_schema),
            certifies_metrics=certified,
            schema_valid=certified,
            semantics_valid=certified,
            identity_bound=certified,
            workflow_status_matches=certified,
            artifact_name=f"ci-quality-{sha}-{run_id}-1",
            metrics=artifact_metrics,
        ),
        provenance=RepositoryProvenance(
            repository_identity=identity,
            commit_sha=sha,
            source="github_actions",
            workflow_path=".github/workflows/quality.yml",
            run_id=run_id,
            run_attempt=1,
        ),
    )
    return RepositorySnapshotResult(
        repository_state=state,
        code_diffs=[code],
        ci_runs=[ci],
        is_complete=is_complete,
        incomplete_reason=None if is_complete else "Repository source is incomplete.",
    )


class MultiRepoOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        root = Path(self._temp.name)
        self.store = SQLiteStorageAdapter(str(root / "analysis.sqlite"))
        self.artifacts = ArtifactStorageAdapter(str(root / "runs"))
        self.provider = CapturingFakeProvider()
        self.now = datetime(2026, 10, 20, 12, 0, tzinfo=ZoneInfo("UTC"))

    def _snapshot(self, repositories, *, complete=True):
        return WeeklySnapshot(
            id="multi-snapshot",
            project_id="Hormigestion",
            cutoff_datetime=self.now,
            captured_at=self.now,
            tasks=[TaskCard(id="T1", title="Shared task", state=TaskState.completed)],
            # MultiRepo source contracts intentionally leave legacy lists empty.
            code_diffs=[],
            ci_runs=[],
            policies=[
                QualityRule(id="TESTS", description="Tests are known", metric_key="total_tests", operator=">=", threshold=1),
                QualityRule(id="COVERAGE", description="Coverage threshold", metric_key="coverage_percentage", operator=">=", threshold=90),
                QualityRule(id="FAILURES", description="No failed tests", metric_key="failed_tests", operator="==", threshold=0),
            ],
            is_complete=complete,
            repository_results=repositories,
        )

    def _orchestrator(self, *, max_calls=8):
        return ProjectOrchestrator(
            sqlite_storage=self.store,
            artifact_storage=self.artifacts,
            elaborador_provider=self.provider,
            revisor_provider=self.provider,
            verificador_provider=self.provider,
            aprobador_provider=self.provider,
            config=OrchestratorConfig(
                project_id="Hormigestion",
                allow_remote_ai=True,
                max_llm_calls_per_run=max_calls,
                runs_dir=str(Path(self._temp.name) / "runs"),
                db_path=str(Path(self._temp.name) / "analysis.sqlite"),
            ),
        )

    def _partial_source_adapter(self):
        sha = "d" * 40
        marker = "safe-partial-source-marker"

        class Transport:
            def __init__(inner_self):
                inner_self.calls = []

            def get(inner_self, url, params=None, headers=None, timeout_seconds=None):
                path = urlsplit(url).path
                inner_self.calls.append({"path": path, "params": params})
                if f"/commits/{sha}" in path:
                    return HttpResponse(status_code=200, body={
                        "sha": sha,
                        "files": [
                            {"filename": "styles/site.css", "status": "modified", "patch": None,
                             "sha": "e" * 40},
                            {"filename": "src/backend.py", "status": "modified",
                             "patch": "@@ -0,0 +1,1 @@\n+" + marker + "\n", "sha": "f" * 40},
                        ],
                    }, bytes_received=128)
                if "/contents/" in path:
                    return HttpResponse(status_code=404, body={}, bytes_received=8)
                if path.endswith("/actions/runs"):
                    return HttpResponse(status_code=200, body={"total_count": 0, "workflow_runs": []},
                                         bytes_received=32)
                raise AssertionError(f"Unexpected fake transport request: {path}")

        class EmptyTasks:
            def get_tasks(self, project_id):
                return []

            def get_evidence(self, project_id):
                return []

        class EmptyPolicies:
            def get_policies(self, project_id):
                return []

        repository = RepositoryConfiguration(
            id="backend", owner="example-org", repo="backend", branch="develop", commit_sha=sha,
            quality_workflow_path=".github/workflows/quality.yml",
        )
        settings = Settings(
            project_id="Hormigestion", source_mode="real", github_repositories=(repository,),
            http_max_response_bytes=4096,
        )
        transport = Transport()
        source = MultiRepoSourcesAdapter(
            settings,
            transport=transport,
            task_source_factory=lambda _transport: EmptyTasks(),
            policy_source_factory=EmptyPolicies,
        )
        return source, transport, marker, sha

    def _collection_orchestrator(self, source_adapter, provider=None):
        selected = provider or self.provider
        return ProjectOrchestrator(
            sqlite_storage=self.store,
            artifact_storage=self.artifacts,
            elaborador_provider=selected,
            revisor_provider=selected,
            verificador_provider=selected,
            aprobador_provider=selected,
            source_adapter=source_adapter,
            config=OrchestratorConfig(
                project_id="Hormigestion",
                allow_remote_ai=True,
                max_llm_calls_per_run=8,
                runs_dir=str(Path(self._temp.name) / "runs"),
                db_path=str(Path(self._temp.name) / "analysis.sqlite"),
            ),
        )

    def test_orchestrator_persists_partial_source_receipts_records_and_provenance(self):
        source, transport, marker, sha = self._partial_source_adapter()
        run = self._collection_orchestrator(source).execute()
        source_stage = run.stages["source_collection"]
        persisted_stage = self.store.get_stage_results(run.run_id)["source_collection"]
        source_summary = json.loads(source_stage.output_summary)
        persisted_summary = json.loads(persisted_stage.output_summary)
        metrics_path = Path(self._temp.name) / "runs" / run.run_id / "execution_metrics.json"
        execution_metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        metrics_summary = execution_metrics["source_collection"]

        self.assertEqual(persisted_summary, source_summary)
        self.assertEqual(metrics_summary, source_summary)
        self.assertIsNone(source_summary["target_commit_sha"])
        self.assertFalse(source_summary["is_complete"])
        self.assertGreater(source_summary["budget_receipt"]["http_calls"], 0)
        self.assertIn("files", source_summary["budget_receipt"])
        self.assertEqual(len(source_summary["repository_results"]), 1)
        repository = source_summary["repository_results"][0]
        self.assertEqual(repository["repository_identity"], {
            "id": "backend", "owner": "example-org", "repo": "backend",
        })
        self.assertEqual(repository["target_commit_sha"], sha)
        self.assertFalse(repository["is_complete"])
        self.assertIn("styles/site.css", repository["incomplete_reason"])
        self.assertEqual(repository["budget_receipt"]["files"], 2)
        self.assertTrue(any(record["provider"] == "github" for record in repository["records"]))
        self.assertTrue(any(record["provider"] == "github_actions" for record in repository["records"]))
        self.assertNotIn(marker, source_stage.output_summary)
        self.assertNotIn("code_diffs", repository)
        self.assertNotIn("evidence", repository)
        record_duration = sum(record["duration_seconds"] for record in source_summary["records"])
        record_duration += sum(record["duration_seconds"] for record in repository["records"])
        self.assertAlmostEqual(source_stage.duration_seconds, record_duration)
        self.assertEqual(source_stage.data_hash, compute_sha256(source_stage.output_summary))
        self.assertTrue(any(call["path"].endswith("/actions/runs") for call in transport.calls))

    def test_failure_artifacts_keep_repository_source_diagnostics(self):
        class FailingProvider(CapturingFakeProvider):
            def generate(self, request):
                raise RuntimeError("controlled synthetic provider failure")

        source, _transport, marker, _sha = self._partial_source_adapter()
        run = self._collection_orchestrator(source, FailingProvider()).execute()
        source_stage = self.store.get_stage_results(run.run_id)["source_collection"]
        source_summary = json.loads(source_stage.output_summary)
        metrics_path = Path(self._temp.name) / "runs" / run.run_id / "execution_metrics.json"
        execution_metrics = json.loads(metrics_path.read_text(encoding="utf-8"))

        self.assertEqual(run.status, RunStatus.failed)
        self.assertEqual(execution_metrics["source_collection"], source_summary)
        self.assertFalse(source_summary["is_complete"])
        self.assertEqual(source_summary["repository_results"][0]["budget_receipt"]["files"], 2)
        self.assertNotIn(marker, source_stage.output_summary)
        self.assertFalse((metrics_path.parent / "report.json").exists())

    def test_empty_repository_results_keep_legacy_report_shape_and_human_decision(self):
        run = self._orchestrator().execute(snapshot=self._snapshot([]))
        report = self.store.get_report(run.report_id)

        self.assertEqual(report.repository_results, [])
        self.assertNotIn("repository_results", report.model_dump(mode="json"))
        self.assertEqual(run.stages["quality"].status, StageStatus.skipped)
        self.assertNotIn("quality:backend", run.stages)
        self.assertEqual(report.human_decision.value, "pending")

    def test_two_repositories_keep_independent_identity_ci_metrics_findings_and_report(self):
        backend_sha = "a" * 40
        frontend_sha = "b" * 40
        backend = _repository_result(
            "backend", "Josu-F1/hormigestion-backend", backend_sha, "run-backend",
            total_tests=49, failed_tests=0, coverage=97.9,
            coverage_basis="loaded TypeScript unit-test lines",
        )
        frontend = _repository_result(
            "frontend", "Josu-F1/hormigestion-frontend", frontend_sha, "run-frontend",
            total_tests=31, failed_tests=0, coverage=92.73,
            coverage_basis="loaded TypeScript lines excluding TSX and UI",
        )

        run = self._orchestrator().execute(snapshot=self._snapshot([backend, frontend]))
        report = self.store.get_report(run.report_id)

        self.assertIn("quality:backend", run.stages)
        self.assertIn("quality:frontend", run.stages)
        self.assertEqual(run.stages["quality:backend"].status, StageStatus.completed)
        self.assertEqual(run.stages["quality:frontend"].status, StageStatus.completed)
        self.assertEqual(run.stages["verifier:backend"].status, StageStatus.completed)
        self.assertEqual(run.stages["verifier:frontend"].status, StageStatus.completed)
        self.assertEqual(len(report.repository_results), 2)
        self.assertTrue(all(
            item.observed_value is None for item in report.policy_evaluations.evaluations
        ))
        by_id = {item.repository_state.identity.id: item for item in report.repository_results}
        self.assertEqual(set(by_id), {"backend", "frontend"})
        for repo_id, sha, run_id, tests, coverage, basis in (
            ("backend", backend_sha, "run-backend", 49, 97.9, "loaded TypeScript unit-test lines"),
            ("frontend", frontend_sha, "run-frontend", 31, 92.73, "loaded TypeScript lines excluding TSX and UI"),
        ):
            result = by_id[repo_id]
            self.assertEqual(result.repository_state.target_commit_sha, sha)
            self.assertEqual(result.ci_result.id, run_id)
            self.assertEqual(result.ci_result.total_tests, tests)
            self.assertEqual(result.coverage_percentage, coverage)
            self.assertEqual(result.coverage_scope, basis)
            self.assertEqual(len(result.confirmed_findings), 1)
            self.assertEqual(result.confirmed_findings[0].repository_identity.id, repo_id)
            self.assertIn(f"{repo_id}::{sha}::", result.confirmed_findings[0].id)
        self.assertIsNone(report.ci_result)
        quality_requests = [request for request in self.provider.requests if request.role == "revisor"]
        verifier_requests = [request for request in self.provider.requests if request.role == "verificador"]
        # The independent verifier judges the defect and, separately, the
        # correction that answers it: one call of each kind per repository.
        verification_requests = [
            request for request in verifier_requests
            if request.schema_name != "patch_review_output_v1"
        ]
        review_requests = [
            request for request in verifier_requests
            if request.schema_name == "patch_review_output_v1"
        ]
        self.assertEqual(len(quality_requests), 2)
        self.assertEqual(len(verification_requests), 2)
        self.assertEqual(len(review_requests), 2)
        for request in quality_requests:
            self.assertEqual(request.max_generation_tokens, 768)
        self.assertEqual(sum(request.max_generation_tokens for request in quality_requests), 1536)
        self.assertEqual(sum(request.max_generation_tokens for request in verification_requests), 1024)
        report_request = next(request for request in self.provider.requests if request.role == "aprobador")
        self.assertNotIn("repository_results", report_request.evidences)
        self.assertIsNone(report_request.evidences.get("ci_result"))
        expected = {"backend": backend_sha, "frontend": frontend_sha}
        for request in quality_requests:
            scope = request.evidences["repository_scope"]
            repo_id = scope["identity"]["id"]
            self.assertEqual(scope["target_commit_sha"], expected[repo_id])
            self.assertEqual(len(request.evidences["code_snippets"]), 1)
            self.assertEqual(request.evidences["code_snippets"][0]["commit_sha"], expected[repo_id])
            self.assertEqual(scope["ci_result"]["commit_sha"], expected[repo_id])
            self.assertEqual(
                scope["ci_result"]["repository_identity"]["id"], repo_id,
            )
        for request in verification_requests:
            scope = request.evidences["repository_scope"]
            repo_id = scope["identity"]["id"]
            finding = request.evidences["findings"][0]
            self.assertTrue(finding["id"].startswith(f"{repo_id}::{expected[repo_id]}::"))
            self.assertEqual(finding["repository_identity"]["id"], repo_id)
            self.assertEqual(request.evidences["code_snippets"][0]["commit_sha"], expected[repo_id])
        for request in review_requests:
            # The correction is judged with its own repository, commit and file.
            patch = request.evidences["patch"]
            repo_id = "backend" if patch["repository"] == "Josu-F1/hormigestion-backend" else "frontend"
            self.assertEqual(patch["commit_sha"], expected[repo_id])
            self.assertEqual(patch["file_path"], "src/shared.py")
            self.assertEqual(
                request.evidences["finding"]["commit_sha"], expected[repo_id],
            )
        self.assertIsNone(by_id["frontend"].ci_result.metrics_artifact.metrics.test_suites)
        self.assertEqual(by_id["frontend"].ci_result.metrics_artifact.metrics.e2e_status, "not_implemented")

        markdown = generate_report_markdown(report)
        for marker in (backend_sha, frontend_sha, "run-backend", "run-frontend", "49", "31", "97.9", "92.7", "loaded TypeScript unit-test lines", "excluding TSX and UI"):
            self.assertIn(marker, markdown)
        self.assertNotIn("Cobertura global: 95.3%", markdown)
        self.assertEqual(report.human_decision.value, "pending")

    def test_denied_frontend_keeps_backend_result_and_project_policy_unknown(self):
        backend = _repository_result(
            "backend", "example/backend", "a" * 40, "backend-run",
            total_tests=10, failed_tests=0, coverage=95.0,
            coverage_basis="backend line coverage",
        )
        frontend_identity = RepositoryIdentity(id="frontend", owner="example", repo="private-web")
        denied_frontend = RepositorySnapshotResult(
            repository_state=RepositoryAnalysisState(
                identity=frontend_identity,
                branch="develop",
                commit_resolution_status=CommitResolutionStatus.failed,
                access_status=RepositoryAccessStatus.denied,
                availability_status=RepositoryAvailabilityStatus.unknown,
            ),
            is_complete=False,
            incomplete_reason="Repository source collection denied access.",
        )

        run = self._orchestrator().execute(
            snapshot=self._snapshot([backend, denied_frontend], complete=False),
        )
        report = self.store.get_report(run.report_id)
        by_id = {item.repository_state.identity.id: item for item in report.repository_results}

        self.assertEqual(run.stages["quality:backend"].status, StageStatus.completed)
        self.assertEqual(run.stages["quality:frontend"].status, StageStatus.needs_attention)
        self.assertEqual(by_id["backend"].ci_result.total_tests, 10)
        self.assertEqual(len(by_id["backend"].confirmed_findings), 1)
        self.assertIsNone(by_id["frontend"].ci_result)
        self.assertIsNone(by_id["frontend"].coverage_percentage)
        self.assertTrue(any("denied" in issue for issue in by_id["frontend"].source_problems))
        self.assertEqual(report.policy_evaluations.recommended_action, "evidencia_insuficiente")
        self.assertIn("1 de 2 repositorios", report.summary)
        self.assertNotIn("metricas verificadas", report.summary)
        self.assertNotEqual(report.recommendation, "recomendar_aprobacion")
        self.assertEqual(report.human_decision.value, "pending")
        quality_requests = [request for request in self.provider.requests if request.role == "revisor"]
        self.assertEqual(len(quality_requests), 1)
        self.assertEqual(quality_requests[0].evidences["repository_scope"]["identity"]["id"], "backend")

    def test_invalid_ci_artifact_does_not_turn_raw_zeroes_into_metrics(self):
        known_zero = _repository_result(
            "backend", "example/backend", "a" * 40, "zero-run",
            total_tests=0, failed_tests=0, coverage=None,
            coverage_basis="backend unit-test lines",
        )
        raw_zero_only = _repository_result(
            "frontend", "example/frontend", "b" * 40, "unknown-run",
            total_tests=0, failed_tests=0, coverage=88.0,
            coverage_basis="untrusted producer value",
            certified=False,
        )

        run = self._orchestrator().execute(snapshot=self._snapshot([known_zero, raw_zero_only]))
        report = self.store.get_report(run.report_id)
        by_id = {item.repository_state.identity.id: item for item in report.repository_results}

        self.assertEqual(by_id["backend"].ci_result.total_tests, 0)
        self.assertEqual(by_id["backend"].ci_result.failed_tests, 0)
        self.assertIsNone(by_id["backend"].coverage_percentage)
        self.assertEqual(by_id["backend"].coverage_scope, "backend unit-test lines")
        self.assertIsNone(by_id["frontend"].ci_result.total_tests)
        self.assertIsNone(by_id["frontend"].ci_result.failed_tests)
        self.assertIsNone(by_id["frontend"].ci_result.coverage_percentage)
        self.assertIsNone(by_id["frontend"].coverage_percentage)
        self.assertIsNone(by_id["frontend"].coverage_scope)
        self.assertIn("Q_REPOSITORY_CI_METRICS_UNAVAILABLE", by_id["frontend"].policy_evaluations.mandatory_unknown_ids)
        self.assertEqual(report.policy_evaluations.recommended_action, "solicitar_cambios")

    def test_failed_workflow_keeps_valid_metrics_but_cannot_evaluate_green(self):
        failed = _repository_result(
            "backend", "example/backend", "a" * 40, "failed-run",
            total_tests=10, failed_tests=2, coverage=93.5,
            coverage_basis="backend source lines",
            workflow_status=CiWorkflowStatus.failure,
        )

        run = self._orchestrator().execute(snapshot=self._snapshot([failed]))
        report = self.store.get_report(run.report_id)
        result = report.repository_results[0]
        workflow_eval = next(
            item for item in result.policy_evaluations.evaluations
            if item.rule_id == "Q_REPOSITORY_CI_WORKFLOW"
        )

        self.assertEqual(result.ci_result.status, "failure")
        self.assertEqual(result.ci_result.total_tests, 10)
        self.assertEqual(result.ci_result.failed_tests, 2)
        self.assertEqual(workflow_eval.status.value, "fail")
        self.assertEqual(report.policy_evaluations.recommended_action, "solicitar_cambios")

    def test_workflow_status_mismatch_removes_certificate_from_public_report(self):
        backend = _repository_result(
            "backend", "example/backend", "a" * 40, "status-mismatch-run",
            total_tests=10, failed_tests=0, coverage=95.0,
            coverage_basis="backend unit-test lines",
        )
        frontend = _repository_result(
            "frontend", "example/frontend", "b" * 40, "valid-sibling-run",
            total_tests=12, failed_tests=0, coverage=92.0,
            coverage_basis="frontend lines excluding TSX and UI",
        )
        source_run = backend.ci_runs[0]
        source_metadata = source_run.metrics_artifact
        mismatched_metrics = source_metadata.metrics.model_copy(
            update={"status": CiWorkflowStatus.failure},
        )
        mismatched_metadata = source_metadata.model_copy(
            update={"metrics": mismatched_metrics},
        )
        mismatched_run = source_run.model_copy(
            update={"metrics_artifact": mismatched_metadata},
        )
        mismatched_backend = backend.model_copy(update={"ci_runs": [mismatched_run]})
        snapshot = self._snapshot([backend, frontend])
        snapshot.repository_results = [mismatched_backend, frontend]
        original_snapshot_json = snapshot.model_dump_json()

        class CountingNotifications:
            def __init__(self):
                self.calls = 0

            def send_project_email(self, *_args, **_kwargs):
                self.calls += 1

        notifications = CountingNotifications()
        orchestrator = self._orchestrator()
        orchestrator.manager_notifications = notifications
        orchestrator.manager_email = "test@example.invalid"

        run = orchestrator.execute(snapshot=snapshot)
        report = self.store.get_report(run.report_id)
        by_id = {item.repository_state.identity.id: item for item in report.repository_results}
        rejected = by_id["backend"]
        sibling = by_id["frontend"]

        self.assertFalse(rejected.ci_result.metrics_artifact.certifies_metrics)
        self.assertFalse(rejected.ci_result.metrics_artifact.workflow_status_matches)
        self.assertEqual(rejected.ci_result.metrics_artifact.outcome, CiArtifactOutcome.validated)
        self.assertTrue(rejected.ci_result.metrics_artifact.schema_valid)
        self.assertTrue(rejected.ci_result.metrics_artifact.semantics_valid)
        self.assertTrue(rejected.ci_result.metrics_artifact.identity_bound)
        self.assertIn("workflow status", rejected.ci_result.metrics_artifact.reason.lower())
        self.assertIsNone(rejected.ci_result.metrics_artifact.metrics)
        self.assertIsNone(rejected.ci_result.total_tests)
        self.assertIsNone(rejected.ci_result.failed_tests)
        self.assertIsNone(rejected.ci_result.coverage_percentage)
        self.assertIsNone(rejected.coverage_percentage)
        self.assertIn(
            "Q_REPOSITORY_CI_METRICS_UNAVAILABLE",
            rejected.policy_evaluations.mandatory_unknown_ids,
        )
        self.assertEqual(report.policy_evaluations.recommended_action, "evidencia_insuficiente")
        self.assertNotEqual(report.recommendation, "recomendar_aprobacion")
        self.assertEqual(report.human_decision.value, "pending")

        serialized_report = report.model_dump_json()
        restored_report = type(report).model_validate_json(serialized_report)
        restored_backend = next(
            item for item in restored_report.repository_results
            if item.repository_state.identity.id == "backend"
        )
        self.assertFalse(restored_backend.ci_result.metrics_artifact.certifies_metrics)
        self.assertFalse(restored_backend.ci_result.metrics_artifact.workflow_status_matches)
        self.assertIsNone(restored_backend.ci_result.metrics_artifact.metrics)
        markdown = generate_report_markdown(report)
        self.assertIn("métricas certificadas: `false`", markdown.lower())
        self.assertIn("pruebas totales / fallidas:** desconocido / desconocido", markdown.lower())
        self.assertIn("**cobertura:** desconocida", markdown.lower())

        self.assertTrue(sibling.ci_result.metrics_artifact.certifies_metrics)
        self.assertEqual(sibling.ci_result.total_tests, 12)
        self.assertEqual(sibling.coverage_percentage, 92.0)
        self.assertEqual(sibling.coverage_scope, "frontend lines excluding TSX and UI")
        self.assertEqual(snapshot.model_dump_json(), original_snapshot_json)
        self.assertEqual(notifications.calls, 0)
        self.assertIsNone(orchestrator.source_adapter)

    def test_misbound_code_sha_is_rejected_without_relabeling(self):
        original = _repository_result(
            "backend", "example/backend", "a" * 40, "bound-run",
            total_tests=5, failed_tests=0, coverage=91.0,
            coverage_basis="backend lines",
        )
        foreign_code = original.code_diffs[0].model_copy(update={"commit_sha": "c" * 40})
        misbound = original.model_copy(update={"code_diffs": [foreign_code]})
        snapshot = self._snapshot([original])
        snapshot.repository_results = [misbound]

        run = self._orchestrator().execute(snapshot=snapshot)
        result = self.store.get_report(run.report_id).repository_results[0]

        self.assertEqual(result.repository_state.target_commit_sha, "a" * 40)
        self.assertEqual(result.quality_status, StageStatus.needs_attention)
        self.assertEqual(result.confirmed_findings, [])
        self.assertEqual(result.pending_findings, [])
        self.assertTrue(any("fixed repository scope" in issue for issue in result.source_problems))
        self.assertEqual(result.ci_result.total_tests, 5)
        self.assertEqual([request for request in self.provider.requests if request.role == "revisor"], [])

    def test_ci_metrics_with_a_misbound_run_sha_are_not_used(self):
        original = _repository_result(
            "backend", "example/backend", "a" * 40, "bound-run",
            total_tests=10, failed_tests=0, coverage=95.0,
            coverage_basis="backend lines",
        )
        foreign_ci = original.ci_runs[0].model_copy(update={"commit_sha": "c" * 40})
        misbound = original.model_copy(update={"ci_runs": [foreign_ci]})
        snapshot = self._snapshot([original])
        snapshot.repository_results = [misbound]

        run = self._orchestrator().execute(snapshot=snapshot)
        result = self.store.get_report(run.report_id).repository_results[0]

        self.assertIsNone(result.ci_result)
        self.assertIsNone(result.coverage_percentage)
        self.assertIn("Q_REPOSITORY_CI_METRICS_UNAVAILABLE", result.policy_evaluations.mandatory_unknown_ids)
        self.assertTrue(any("Malformed CI evidence" in issue for issue in result.source_problems))
        self.assertNotEqual(self.store.get_report(run.report_id).recommendation, "recomendar_aprobacion")

    def test_global_call_budget_preserves_partial_repository_results(self):
        backend = _repository_result(
            "backend", "example/backend", "a" * 40, "backend-run",
            total_tests=10, failed_tests=0, coverage=95.0,
            coverage_basis="backend lines",
        )
        frontend = _repository_result(
            "frontend", "example/frontend", "b" * 40, "frontend-run",
            total_tests=12, failed_tests=0, coverage=92.0,
            coverage_basis="frontend lines excluding UI",
        )
        run = self._orchestrator(max_calls=2).execute(snapshot=self._snapshot([backend, frontend]))
        report = self.store.get_report(run.report_id)
        by_id = {item.repository_state.identity.id: item for item in report.repository_results}

        self.assertEqual(run.status.value, "needs_attention")
        self.assertEqual(run.stages["quality:backend"].status, StageStatus.completed)
        self.assertEqual(run.stages["quality:frontend"].status, StageStatus.needs_attention)
        self.assertEqual(by_id["backend"].ci_result.total_tests, 10)
        self.assertEqual(by_id["frontend"].ci_result.total_tests, 12)
        self.assertGreaterEqual(len(by_id["backend"].pending_findings), 1)
        self.assertEqual(report.human_decision.value, "pending")
        self.assertEqual(run.stages["report"].status, StageStatus.needs_attention)
        self.assertEqual(len([request for request in self.provider.requests if request.role == "revisor"]), 1)
        self.assertNotEqual(report.recommendation, "recomendar_aprobacion")

    def test_foreign_model_provenance_is_rejected_without_relabeling(self):
        repository = _repository_result(
            "backend", "example/backend", "a" * 40, "backend-run",
            total_tests=10, failed_tests=0, coverage=95.0,
            coverage_basis="backend lines",
        )
        foreign_provider = ForeignFindingProvider()
        orchestrator = self._orchestrator()
        orchestrator.revisor_provider = foreign_provider

        run = orchestrator.execute(snapshot=self._snapshot([repository]))
        report = self.store.get_report(run.report_id)
        result = report.repository_results[0]

        self.assertEqual(result.quality_status, StageStatus.needs_attention)
        self.assertEqual(report.candidate_findings, [])
        self.assertEqual(result.confirmed_findings, [])
        self.assertTrue(any("foreign repository identity" in issue for issue in result.source_problems))
        self.assertNotEqual(report.recommendation, "recomendar_aprobacion")

    def test_verifier_cannot_confirm_a_finding_from_another_repository(self):
        repository = _repository_result(
            "backend", "example/backend", "a" * 40, "backend-run",
            total_tests=10, failed_tests=0, coverage=95.0,
            coverage_basis="backend lines",
        )
        cross_reference_provider = CrossReferenceVerifierProvider()
        orchestrator = self._orchestrator()
        orchestrator.verificador_provider = cross_reference_provider

        run = orchestrator.execute(snapshot=self._snapshot([repository]))
        report = self.store.get_report(run.report_id)
        result = report.repository_results[0]

        self.assertEqual(result.verification_status, StageStatus.needs_attention)
        self.assertEqual(len(result.pending_findings), 1)
        self.assertEqual(result.confirmed_findings, [])
        self.assertEqual(result.verifications, [])
        self.assertGreaterEqual(len([r for r in cross_reference_provider.requests if r.role == "verificador"]), 2)
        self.assertEqual(result.pending_findings[0].repository_identity.id, "backend")
        self.assertNotEqual(report.recommendation, "recomendar_aprobacion")


if __name__ == "__main__":
    unittest.main()
