"""Public HTTP contracts for MultiRepo configuration and history views."""

import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.api.app import ApiSettings, ProjectConfig, create_app
from agente_gestion.domain.code import (
    CIRunResult,
    CiMetricsArtifactMetadata,
    CiMetricsArtifactSuite,
    CiMetricsArtifactValues,
    CodeDiffSnippet,
)
from agente_gestion.domain.evidence import EvidenceItem, EvidenceProvenance, EvidenceSource
from agente_gestion.domain.findings import CandidateFinding, FindingVerification, VerificationVerdict
from agente_gestion.domain.policies import evaluate_policies
from agente_gestion.domain.report import (
    FinalReport,
    HumanDecisionState,
    RepositoryReportResult,
)
from agente_gestion.domain.repositories import (
    CommitResolutionStatus,
    RepositoryAccessStatus,
    RepositoryAnalysisState,
    RepositoryAvailabilityStatus,
    RepositoryConfiguration,
    RepositoryIdentity,
    RepositoryProvenance,
)
from agente_gestion.domain.run import ExecutionRun, RunStatus, compute_sha256
from agente_gestion.domain.snapshot import RepositorySnapshotResult, WeeklySnapshot
from agente_gestion.domain.tasks import DeterministicTaskMetrics


ORIGIN = "http://testserver"
PROJECT_ID = "MULTI-API"
PROJECT_TWO = "MULTI-OTHER"
OPERATOR_TOKEN = "synthetic-multirepo-operator-token-000000"
SHA = "a" * 40
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def _identity(repository_id: str, name: str) -> RepositoryIdentity:
    return RepositoryIdentity(id=repository_id, owner="example", repo=name)


def _state(identity: RepositoryIdentity, *, denied: bool = False) -> RepositoryAnalysisState:
    return RepositoryAnalysisState(
        identity=identity,
        branch="develop",
        target_commit_sha=None if denied else SHA,
        commit_resolution_status=(CommitResolutionStatus.failed if denied else CommitResolutionStatus.resolved),
        access_status=(RepositoryAccessStatus.denied if denied else RepositoryAccessStatus.allowed),
        availability_status=(RepositoryAvailabilityStatus.unavailable if denied else RepositoryAvailabilityStatus.available),
    )


def _ci(identity: RepositoryIdentity, run_id: str, *, frontend: bool) -> CIRunResult:
    attempt = 1 if frontend else 2
    artifact_name = f"ci-quality-{SHA}-{run_id}-{attempt}"
    values = CiMetricsArtifactValues(
        repository=f"{identity.owner}/{identity.repo}",
        commit_sha=SHA,
        workflow_run_id=run_id,
        workflow_run_attempt=None,
        status="success",
        total_tests=31 if frontend else 0,
        failed_tests=0,
        coverage_percentage=92.73 if frontend else None,
        coverage_metric="lines" if frontend else None,
        coverage_basis=(
            "TypeScript source lines; TSX/UI excluded" if frontend
            else "Backend executable source lines"
        ),
        test_suites={"unit": CiMetricsArtifactSuite(tests=31, failed=0, passed=31, success=True)}
        if frontend else None,
        e2e_status="not_implemented" if frontend else None,
    )
    metadata = CiMetricsArtifactMetadata(
        outcome="validated",
        certifies_metrics=True,
        schema_valid=True,
        semantics_valid=True,
        identity_bound=True,
        workflow_status_matches=True,
        artifact_name=artifact_name,
        metrics=values,
    )
    return CIRunResult(
        id=run_id,
        workflow_name="quality",
        workflow_path=(".github/workflows/ci-quality.yml" if frontend
                       else ".github/workflows/ci.yml"),
        commit_sha=SHA,
        status="success",
        total_tests=31 if frontend else 0,
        failed_tests=0,
        coverage_percentage=92.73 if frontend else None,
        repository_identity=identity,
        run_attempt=attempt,
        artifact_id="501" if frontend else "500",
        artifact_name=artifact_name,
        metrics_artifact=metadata,
        provenance=RepositoryProvenance(
            repository_identity=identity,
            commit_sha=SHA,
            source="github_actions",
            workflow_path=(".github/workflows/ci-quality.yml" if frontend
                           else ".github/workflows/ci.yml"),
            run_id=run_id,
            run_attempt=attempt,
        ),
    )


def _code(identity: RepositoryIdentity, content: str) -> CodeDiffSnippet:
    return CodeDiffSnippet(
        repository=f"{identity.owner}/{identity.repo}",
        commit_sha=SHA,
        file_path="src/shared.ts",
        lines=[1],
        content=content,
        evidence_scope="full_file",
        is_complete=True,
        total_lines=1,
        source_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        repository_identity=identity,
        provenance=RepositoryProvenance(
            repository_identity=identity, commit_sha=SHA, source="github",
        ),
    )


def _finding(identity: RepositoryIdentity, finding_id: str, evidence_id: str) -> CandidateFinding:
    return CandidateFinding(
        id=finding_id,
        claim=f"Finding for {identity.id}",
        category="otros",
        evidence_ids=[evidence_id],
        commit_sha=SHA,
        file_path="src/shared.ts",
        line=1,
        repository_identity=identity,
        provenance=RepositoryProvenance(
            repository_identity=identity, commit_sha=SHA, source="github",
        ),
    )


def _evidence(project_id: str, identity: RepositoryIdentity, evidence_id: str) -> EvidenceItem:
    return EvidenceItem(
        provenance=EvidenceProvenance(
            id=evidence_id,
            source=EvidenceSource.github,
            project_id=project_id,
            repository=f"{identity.owner}/{identity.repo}",
            commit_sha=SHA,
            file_path="src/shared.ts",
            lines=[1],
            timestamp=NOW,
            payload={"kind": "multirepo-api-fixture"},
        ),
        category="diff",
        summary=f"Evidence for {identity.id}.",
    )


def _report_ci(source: CIRunResult, *, certified: bool) -> CIRunResult:
    if certified:
        return source
    metadata = CiMetricsArtifactMetadata(
        outcome="workflow_status_mismatch",
        certifies_metrics=False,
        reason="Workflow status mismatch rejected report eligibility.",
        schema_valid=True,
        semantics_valid=True,
        identity_bound=True,
        workflow_status_matches=False,
        artifact_name=source.artifact_name,
    )
    return CIRunResult.model_validate(source.model_dump(mode="python") | {
        "total_tests": None,
        "failed_tests": None,
        "coverage_percentage": None,
        "metrics_artifact": metadata,
    })


def _repository_result(identity: RepositoryIdentity, ci: CIRunResult, code: CodeDiffSnippet):
    return RepositorySnapshotResult(
        repository_state=_state(identity),
        code_diffs=[code],
        ci_runs=[ci],
        is_complete=True,
    )


def _seed_report(
    storage: SQLiteStorageAdapter,
    project_id: str,
    run_id: str,
    report_id: str,
    results: list[RepositorySnapshotResult],
    report_results: list[RepositoryReportResult],
    findings: list[CandidateFinding],
    verifications: list[FindingVerification],
    evidence: list[EvidenceItem],
) -> None:
    snapshot = WeeklySnapshot(
        id=f"snapshot-{run_id}",
        project_id=project_id,
        cutoff_datetime=NOW,
        captured_at=NOW,
        is_complete=all(result.is_complete is True for result in results),
        incomplete_reason=None,
        repository_results=results,
    )
    run = ExecutionRun(
        run_id=run_id,
        project_id=project_id,
        status=RunStatus.awaiting_human,
        created_at=NOW,
        updated_at=NOW,
        snapshot_id=snapshot.id,
        config_hash=f"config-{run_id}",
        snapshot_hash=compute_sha256(snapshot),
        policy_version="multirepo-api-fixture-v1",
    )
    report = FinalReport(
        project_id=project_id,
        cutoff_datetime=NOW,
        metrics=DeterministicTaskMetrics(
            total_cards=0, included_cards=0, completed_cards=0, progress_percentage=None,
            overdue_card_ids=[], due_soon_card_ids=[], blocked_card_ids=[],
            unknown_card_ids=[], contradictory_card_ids=[],
        ),
        policy_evaluations=evaluate_policies([], {}),
        recommendation="evidencia_insuficiente",
        human_decision=HumanDecisionState.pending,
        summary="Synthetic repository-scoped API fixture.",
        report_id=report_id,
        run_id=run_id,
        repository_results=report_results,
    )
    storage.save_run(run)
    storage.save_snapshot(snapshot)
    storage.save_findings(run_id, findings)
    storage.save_verifications(run_id, verifications)
    storage.save_evidence(run_id, evidence)
    storage.save_report(report, report_id=report_id, run_id=run_id)


def _seed_multi_project(database_path: Path) -> None:
    storage = SQLiteStorageAdapter(str(database_path))
    backend = _identity("backend", "backend")
    frontend = _identity("frontend", "frontend")
    backend_ci = _ci(backend, "9100", frontend=False)
    frontend_ci = _ci(frontend, "9200", frontend=True)
    backend_code = _code(backend, "const backendSource = true;\n")
    frontend_code = _code(frontend, "const frontendSource = true;\n")
    backend_finding = _finding(backend, "finding-backend", "evidence-backend")
    frontend_finding = _finding(frontend, "finding-frontend", "evidence-frontend")
    backend_verification = FindingVerification(
        finding_id=backend_finding.id, verdict=VerificationVerdict.confirmed,
        reason="Backend evidence verified.", verified_by="fixture-verifier",
        evidence_ids=["evidence-backend"],
    )
    frontend_verification = FindingVerification(
        finding_id=frontend_finding.id, verdict=VerificationVerdict.confirmed,
        reason="Frontend evidence verified.", verified_by="fixture-verifier",
        evidence_ids=["evidence-frontend"],
    )
    source_results = [
        _repository_result(backend, backend_ci, backend_code),
        _repository_result(frontend, frontend_ci, frontend_code),
    ]
    report_results = [
        RepositoryReportResult(
            repository_state=_state(backend),
            ci_result=_report_ci(backend_ci, certified=False),
            confirmed_findings=[backend_finding],
            verifications=[backend_verification],
            coverage_percentage=None,
            coverage_scope="Backend executable source lines",
            source_is_complete=True,
        ),
        RepositoryReportResult(
            repository_state=_state(frontend),
            ci_result=_report_ci(frontend_ci, certified=True),
            confirmed_findings=[frontend_finding],
            verifications=[frontend_verification],
            coverage_percentage=92.73,
            coverage_scope="TypeScript source lines; TSX/UI excluded",
            source_is_complete=True,
        ),
    ]
    _seed_report(
        storage, PROJECT_ID, "run-multi-api", "report-multi-api", source_results,
        report_results, [backend_finding, frontend_finding],
        [backend_verification, frontend_verification],
        [_evidence(PROJECT_ID, backend, "evidence-backend"),
         _evidence(PROJECT_ID, frontend, "evidence-frontend")],
    )

    # A separate capture records a denied sibling without erasing the successful result above.
    denied = RepositorySnapshotResult(
        repository_state=_state(frontend, denied=True),
        is_complete=False,
        incomplete_reason="private repository permission denied",
    )
    partial_backend = _repository_result(backend, backend_ci, backend_code)
    _seed_report(
        storage, PROJECT_ID, "run-multi-partial", "report-multi-partial",
        [partial_backend, denied], [], [], [], [],
    )

    # Project two deliberately reuses the local repository ID, SHA, CI run ID and path.
    other_backend = _identity("backend", "backend")
    other_ci = _ci(other_backend, "9100", frontend=False)
    other_code = _code(other_backend, "const otherProjectSource = true;\n")
    other_finding = _finding(other_backend, "finding-other-project", "evidence-other-project")
    other_verification = FindingVerification(
        finding_id=other_finding.id, verdict=VerificationVerdict.confirmed,
        reason="Other-project evidence verified.", verified_by="fixture-verifier",
        evidence_ids=["evidence-other-project"],
    )
    _seed_report(
        storage, PROJECT_TWO, "run-multi-other", "report-multi-other",
        [_repository_result(other_backend, other_ci, other_code)],
        [RepositoryReportResult(
            repository_state=_state(other_backend), ci_result=other_ci,
            confirmed_findings=[other_finding], verifications=[other_verification],
            coverage_percentage=None, coverage_scope="Backend executable source lines",
            source_is_complete=True,
        )],
        [other_finding], [other_verification],
        [_evidence(PROJECT_TWO, other_backend, "evidence-other-project")],
    )


class MultiRepoApiTests(unittest.TestCase):
    def _settings(self, root: Path, *, seed: bool = False) -> ApiSettings:
        if seed:
            _seed_multi_project(root / "analysis.sqlite")
        return ApiSettings(
            db_path=str(root / "analysis.sqlite"),
            job_ledger_path=str(root / "jobs.sqlite"),
            projects=(
                ProjectConfig(
                    id=PROJECT_ID, name="MultiRepo fixture", source_mode="real",
                    repositories=(
                        RepositoryConfiguration(
                            id="backend", owner="example", repo="backend", branch="develop",
                            commit_sha="b" * 40, quality_workflow_path=".github/workflows/ci.yml",
                        ),
                        RepositoryConfiguration(
                            id="frontend", owner="example", repo="frontend", branch="develop",
                            quality_workflow_path=".github/workflows/ci-quality.yml",
                        ),
                    ),
                ),
                ProjectConfig(
                    id=PROJECT_TWO, name="Other project", source_mode="real",
                    repositories=(RepositoryConfiguration(
                        id="backend", owner="example", repo="backend", branch="develop",
                    ),),
                ),
            ),
            operator_token=SecretStr(OPERATOR_TOKEN),
            operator_project_ids=(PROJECT_ID,),
            allowed_origins=(ORIGIN,),
            allowed_hosts=("testserver",),
            external_writes_enabled=False,
        )

    def _login(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/login", json={"token": OPERATOR_TOKEN},
            headers={"Origin": ORIGIN},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_repository_results_are_project_scoped_and_expose_static_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self._settings(root)
            with TestClient(create_app(settings)) as client:
                path = f"/api/v1/projects/{PROJECT_ID}/repository-results"
                unauthenticated = client.get(path)
                self.assertEqual(unauthenticated.status_code, 401)
                self.assertEqual(unauthenticated.json()["error"]["code"], "sesion_requerida")

                self._login(client)
                response = client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual(body["project_id"], PROJECT_ID)
                self.assertEqual(body["availability"], "unknown")
                self.assertEqual(
                    [item["repository_id"] for item in body["repositories"]],
                    ["backend", "frontend"],
                )
                self.assertEqual(body["items"], [])
                self.assertFalse((root / "analysis.sqlite").exists())
                self.assertFalse((root / "jobs.sqlite").exists())

    def test_repository_results_are_project_and_repository_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _seed_multi_project(root / "analysis.sqlite")
            settings = self._settings(root).model_copy(update={
                "operator_project_ids": (PROJECT_ID, PROJECT_TWO),
            })
            with TestClient(create_app(settings)) as client:
                self._login(client)
                path = f"/api/v1/projects/{PROJECT_ID}/repository-results"
                response = client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual(body["availability"], "available")
                self.assertEqual(body["total"], 4)
                self.assertTrue(all(item["repository"] for item in body["items"]))

                backend = next(
                    item for item in body["items"]
                    if item["report_id"] == "report-multi-api"
                    and item["repository"]["repository_id"] == "backend"
                )
                self.assertEqual([item["finding_id"] for item in backend["findings"]], ["finding-backend"])
                backend_ci = backend["ci_runs"][0]
                self.assertTrue(backend_ci["source_artifact_validation"]["source_metrics_certified"])
                self.assertFalse(backend_ci["report_metrics_eligible"])
                self.assertIsNone(backend_ci["report_metrics"])

                filtered = client.get(path, params={"repository_id": "frontend", "ci_run_id": "9200"})
                self.assertEqual(filtered.status_code, 200, filtered.text)
                self.assertEqual(filtered.json()["total"], 1)
                filtered_item = filtered.json()["items"][0]
                self.assertEqual(filtered_item["repository"]["repository_id"], "frontend")
                self.assertEqual(filtered_item["coverage_percentage"], 92.73)
                self.assertEqual(filtered_item["ci_runs"][0]["actual_attempt"], 1)
                self.assertEqual(filtered_item["findings"][0]["finding_id"], "finding-frontend")

                other = client.get(
                    f"/api/v1/projects/{PROJECT_TWO}/repository-results",
                    params={"repository_id": "backend"},
                )
                self.assertEqual(other.status_code, 200, other.text)
                self.assertEqual(other.json()["project_id"], PROJECT_TWO)
                self.assertEqual(other.json()["total"], 1)
                self.assertEqual(
                    other.json()["items"][0]["findings"][0]["finding_id"],
                    "finding-other-project",
                )

                invalid = client.get(path, params={"repository_owner": "example"})
                self.assertEqual(invalid.status_code, 422)

    def test_findings_filters_and_code_excerpts_remain_bound_to_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _seed_multi_project(root / "analysis.sqlite")
            settings = self._settings(root)
            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/runs/run-multi-api/findings",
                    params={
                        "repository_id": "frontend", "commit_sha": SHA,
                        "ci_run_id": "9200", "attempt": 1,
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                page = response.json()
                self.assertEqual(page["total"], 1)
                self.assertEqual(page["items"][0]["finding_id"], "finding-frontend")
                self.assertEqual(page["items"][0]["repository_id"], "frontend")

                conflicting = client.get(
                    "/api/v1/runs/run-multi-api/findings",
                    params={"repository_id": "backend", "ci_run_id": "9200"},
                )
                self.assertEqual(conflicting.status_code, 200, conflicting.text)
                self.assertEqual(conflicting.json()["total"], 0)

                detail = client.get(
                    "/api/v1/runs/run-multi-api/findings/finding-frontend",
                )
                self.assertEqual(detail.status_code, 200, detail.text)
                self.assertEqual(
                    detail.json()["code_excerpt"]["content"], "const frontendSource = true;",
                )
                self.assertNotIn("backendSource", detail.text)

if __name__ == "__main__":
    unittest.main()
