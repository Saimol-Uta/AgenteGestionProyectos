"""G3/G4: the closing integrated regression of the corrective circuit.

Three facts are proved here, end to end and without any model:

1. the normal MultiRepo pipeline reviews the exact corrective patch of every
   verified finding, so the approval chain of a repository-scoped finding is
   reachable at all;
2. an abandoned publication (the process died between the branch checkpoint and
   the pull request) is recovered with the same idempotency key, without
   duplicating the branch, the commit or the human decision;
3. the human session, the review and the source are revalidated before any
   recovery, and an attempt that still holds its lease is never stolen.
"""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.adapters.storage.artifacts import ArtifactStorageAdapter
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.application.finding_decisions import (
    APPROVAL_REVIEW_REQUIRED_CODE,
    FindingDecisionError,
    FindingDecisionService,
)
from agente_gestion.application.orchestrator import OrchestratorConfig, ProjectOrchestrator
from agente_gestion.domain.code import CodeDiffSnippet
from agente_gestion.domain.code_action import (
    BranchExecutionResult,
    CodeSourceSnapshot,
    FindingActionOrigin,
    PullRequestExecutionResult,
)
from agente_gestion.domain.findings import (
    CandidateFinding,
    FindingVerification,
    VerificationVerdict,
)
from agente_gestion.domain.llm import GenerationResult, GenerationStatus
from agente_gestion.domain.policies import PolicyEvaluationResult, QualityRule
from agente_gestion.domain.repositories import (
    CommitResolutionStatus,
    RepositoryAccessStatus,
    RepositoryAnalysisState,
    RepositoryAvailabilityStatus,
    RepositoryIdentity,
    RepositoryProvenance,
)
from agente_gestion.domain.report import FinalReport, RepositoryReportResult
from agente_gestion.domain.run import ExecutionRun, RunStatus
from agente_gestion.domain.snapshot import RepositorySnapshotResult, WeeklySnapshot
from agente_gestion.domain.tasks import DeterministicTaskMetrics, TaskCard, TaskState


PROJECT_ID = "G3G4-CIRCUIT"
BACKEND_ID = "backend"
FRONTEND_ID = "frontend"
BACKEND_REPO = "acme/backend"
FRONTEND_REPO = "acme/frontend"
BACKEND_SHA = "a" * 40
FRONTEND_SHA = "b" * 40
FILE_PATH = "src/shared.py"
SOURCE = "def calculate(total):\n    return 1 / total\n"
SOURCE_HASH = hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()
EXCERPT = "return 1 / total"
SUGGESTED = "return 0 if total == 0 else 1 / total"
BASE_BRANCH = "develop"
ORIGIN = "http://127.0.0.1:4173"
HOST = "127.0.0.1:4176"
OPERATOR_TOKEN = "g3g4-regression-operator-token-0123456789"

# The single-finding fixture of the publication seam.
RUN_ID = "run-g3g4-circuit"
SNAPSHOT_ID = "snapshot-g3g4-circuit"
REPORT_ID = "report-g3g4-circuit"
FINDING_ID = "backend:f-circuit-1"
REPOSITORY = "acme/backend"
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"
ACTOR = "gestor-local"


class _CircuitProvider(FakeProvider):
    """Fake provider that also answers the independent patch review."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[Any] = []

    def generate(self, request):
        self.requests.append(request)
        if request.schema_name == "patch_review_output_v1":
            finding = (request.evidences.get("finding") or {})
            return GenerationResult(
                status=GenerationStatus.complete,
                structured_data={"reviews": [{
                    "finding_id": finding.get("id", "f-fake-1"),
                    "verdict": "favorable",
                    "reason": "La correccion resuelve el defecto sin alterar el contexto.",
                    "evidence_ids": list(finding.get("evidence_ids") or [])[:1],
                }]},
                provider=self.provider_name, model=request.model or self.model,
                duration_seconds=0.01, prompt_tokens=100, completion_tokens=50,
            )
        return super().generate(request)


def _repository_result(repo_id: str, repository: str, sha: str) -> RepositorySnapshotResult:
    """One pinned repository with a complete, immutable source file."""
    owner, name = repository.split("/", 1)
    identity = RepositoryIdentity(id=repo_id, owner=owner, repo=name)
    state = RepositoryAnalysisState(
        identity=identity, branch=BASE_BRANCH, target_commit_sha=sha,
        commit_resolution_status=CommitResolutionStatus.resolved,
        access_status=RepositoryAccessStatus.allowed,
        availability_status=RepositoryAvailabilityStatus.available,
    )
    code = CodeDiffSnippet(
        repository=repository, commit_sha=sha, file_path=FILE_PATH, lines=[1, 2],
        content=SOURCE, evidence_scope="full_file", is_complete=True, total_lines=2,
        source_sha256=SOURCE_HASH, repository_identity=identity,
        provenance=RepositoryProvenance(
            repository_identity=identity, commit_sha=sha, source="github",
        ),
    )
    return RepositorySnapshotResult(
        repository_state=state, code_diffs=[code], ci_runs=[], is_complete=True,
        incomplete_reason=None,
    )


class _CrashPort:
    """A code action port whose pull request dies with the process."""

    def __init__(self, *, crash: bool = True):
        self.crash = crash
        self.branch_calls: list[dict[str, Any]] = []
        self.publication_calls: list[dict[str, Any]] = []

    def create_verification_branch(self, **kwargs):
        self.branch_calls.append(kwargs)
        branch = kwargs["target_branch"]
        return BranchExecutionResult(
            execution_mode="sandbox", branch_name=branch,
            branch_url=f"sandbox://branches/{branch}", commit_sha_applied="d" * 64,
        )

    def create_pull_request(self, **kwargs):
        self.publication_calls.append(kwargs)
        if self.crash:
            raise KeyboardInterrupt("simulated process kill")
        branch = kwargs["branch_result"]
        number = 4321
        return PullRequestExecutionResult(
            execution_mode="sandbox", pull_request_number=number,
            pull_request_url=f"sandbox://pulls/{kwargs['repo']}/{number}", state="simulated",
            repository=kwargs["repo"], base_branch=kwargs["base_branch"],
            head_branch=branch.branch_name, head_commit_sha=branch.commit_sha_applied,
        )


class MultiRepoChainTests(unittest.TestCase):
    """The normal MultiRepo pipeline reaches the approval chain."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.store = SQLiteStorageAdapter(str(self.root / "analysis.sqlite"))
        self.artifacts = ArtifactStorageAdapter(str(self.root / "runs"))
        self.provider = _CircuitProvider()
        self.now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

    def _snapshot(self) -> WeeklySnapshot:
        return WeeklySnapshot(
            id="snapshot-circuit", project_id=PROJECT_ID,
            cutoff_datetime=self.now, captured_at=self.now,
            tasks=[TaskCard(id="T1", title="Shared task", state=TaskState.completed)],
            code_diffs=[], ci_runs=[],
            policies=[QualityRule(
                id="TESTS", description="Tests are known", metric_key="total_tests",
                operator=">=", threshold=1,
            )],
            is_complete=True,
            repository_results=[
                _repository_result(BACKEND_ID, BACKEND_REPO, BACKEND_SHA),
                _repository_result(FRONTEND_ID, FRONTEND_REPO, FRONTEND_SHA),
            ],
        )

    def _orchestrator(self) -> ProjectOrchestrator:
        return ProjectOrchestrator(
            sqlite_storage=self.store, artifact_storage=self.artifacts,
            elaborador_provider=self.provider, revisor_provider=self.provider,
            verificador_provider=self.provider, aprobador_provider=self.provider,
            config=OrchestratorConfig(
                project_id=PROJECT_ID, allow_remote_ai=True, max_llm_calls_per_run=12,
                runs_dir=str(self.root / "runs"), db_path=str(self.root / "analysis.sqlite"),
                is_simulated=True,
            ),
        )

    def test_the_pipeline_reviews_every_verified_correction_with_its_exact_identity(self):
        run = self._orchestrator().execute(snapshot=self._snapshot())

        with closing(sqlite3.connect(self.root / "analysis.sqlite")) as connection:
            connection.row_factory = sqlite3.Row
            rows = [dict(row) for row in connection.execute(
                "SELECT review_id, project_id, run_id, finding_id, repository, commit_sha,"
                " file_path, patch_sha256, original_sha256, review_json FROM patch_reviews"
                " ORDER BY repository",
            ).fetchall()]
        self.assertEqual(len(rows), 2)
        by_repository = {row["repository"]: row for row in rows}
        self.assertEqual(set(by_repository), {BACKEND_REPO, FRONTEND_REPO})
        for repository, sha in ((BACKEND_REPO, BACKEND_SHA), (FRONTEND_REPO, FRONTEND_SHA)):
            row = by_repository[repository]
            self.assertEqual(row["project_id"], PROJECT_ID)
            self.assertEqual(row["run_id"], run.run_id)
            self.assertEqual(row["commit_sha"], sha)
            self.assertEqual(row["file_path"], FILE_PATH)
            self.assertEqual(row["original_sha256"], SOURCE_HASH)
            self.assertEqual(row["patch_sha256"], hashlib.sha256(
                SOURCE.replace(EXCERPT, SUGGESTED, 1).encode("utf-8"),
            ).hexdigest())
            self.assertIn(f"::{sha}::", row["finding_id"])
            review = json.loads(row["review_json"])
            self.assertEqual(review["verdict"], "favorable")
            self.assertEqual(review["project_id"], PROJECT_ID)
            self.assertEqual(review["run_id"], run.run_id)
            self.assertEqual(review["repository"], repository)
            self.assertEqual(review["commit_sha"], sha)
            self.assertEqual(review["original_sha256"], SOURCE_HASH)

    def test_the_review_of_one_repository_never_covers_the_other(self):
        self._orchestrator().execute(snapshot=self._snapshot())

        with closing(sqlite3.connect(self.root / "analysis.sqlite")) as connection:
            connection.row_factory = sqlite3.Row
            rows = [dict(row) for row in connection.execute(
                "SELECT repository, commit_sha, file_path FROM patch_reviews",
            ).fetchall()]
        pairs = {(row["repository"], row["commit_sha"], row["file_path"]) for row in rows}
        self.assertEqual(len(pairs), len(rows))
        self.assertIn((BACKEND_REPO, BACKEND_SHA, FILE_PATH), pairs)
        self.assertIn((FRONTEND_REPO, FRONTEND_SHA, FILE_PATH), pairs)

    def test_the_multirepo_review_unlocks_the_eligibility_and_the_sandbox_approval(self):
        run = self._orchestrator().execute(snapshot=self._snapshot())
        finding_id = f"{BACKEND_ID}::{BACKEND_SHA}::f-fake-1"

        settings, client = self._api_client()
        csrf = self._login(client)

        eligibility = client.post(
            f"/api/v1/runs/{run.run_id}/findings/{finding_id}/approval-eligibility",
            json={}, headers={"origin": ORIGIN, "x-csrf-token": csrf},
        )
        self.assertEqual(eligibility.status_code, 200, eligibility.text)
        body = eligibility.json()["approval_eligibility"]
        self.assertTrue(body["eligible"], body)
        self.assertTrue(body["defect_confirmed"], body)
        self.assertIsNone(body["blocked_reason"])

        decision = client.post(
            f"/api/v1/runs/{run.run_id}/findings/{finding_id}/decision",
            json={"decision": "approve"},
            headers={"origin": ORIGIN, "x-csrf-token": csrf, "Idempotency-Key": "g3g4-multirepo-0001"},
        )
        self.assertEqual(decision.status_code, 200, decision.text)
        receipt = decision.json()
        self.assertEqual(receipt["decision_status"], "approved")
        self.assertEqual(receipt["publication_status"], "published")
        self.assertEqual(receipt["pull_request_state"], "simulated")
        self.assertIs(receipt["pull_request_simulated"], True)
        self.assertEqual(receipt["repository_name"], "backend")
        self.assertIsNotNone(receipt["branch_name"])
        self.assertIsNotNone(receipt["commit_sha_applied"])
        client.close()

    def _api_client(self):
        from fastapi.testclient import TestClient
        from pydantic import SecretStr

        from agente_gestion.api.app import create_app
        from agente_gestion.api.settings import ApiSettings, ProjectConfig
        from agente_gestion.domain.repositories import RepositoryConfiguration

        settings = ApiSettings(
            db_path=str(self.root / "analysis.sqlite"),
            runs_dir=str(self.root / "api-runs"), outbox_dir=str(self.root / "outbox"),
            job_ledger_path=str(self.root / "jobs.sqlite"),
            branches_dir=str(self.root / "branches"),
            projects=(ProjectConfig(
                id=PROJECT_ID, name="Circuito G3/G4", source_mode="fixtures",
                repositories=(
                    RepositoryConfiguration(
                        id=BACKEND_ID, owner="acme", repo="backend", branch=BASE_BRANCH),
                    RepositoryConfiguration(
                        id=FRONTEND_ID, owner="acme", repo="frontend", branch=BASE_BRANCH),
                ),
            ),),
            analysis_enabled=False, governance_enabled=True, report_decisions_enabled=False,
            weekly_cycle_enabled=False, external_writes_enabled=False,
            github_repository=BACKEND_REPO, github_base_branch=BASE_BRANCH,
            operator_token=SecretStr(OPERATOR_TOKEN), operator_actor="gestor",
            operator_project_ids=(PROJECT_ID,), allowed_origins=(ORIGIN,),
            allowed_hosts=("127.0.0.1",),
        )
        return settings, TestClient(create_app(settings), base_url=f"http://{HOST}")

    def _login(self, client) -> str:
        response = client.post(
            "/api/v1/auth/login", json={"token": OPERATOR_TOKEN}, headers={"origin": ORIGIN},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["csrf_token"]


class _SingleFindingFixture(unittest.TestCase):
    """One persisted run, finding, source and review shared by the crash tests."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.storage = SQLiteStorageAdapter(db_path=str(self.root / "agente.db"))
        self.identity = RepositoryIdentity(id="backend", owner="acme", repo="backend")
        self.provenance = RepositoryProvenance(
            repository_identity=self.identity, commit_sha=COMMIT_SHA, source="multirepo",
        )
        self.finding = CandidateFinding(
            id=FINDING_ID, commit_sha=COMMIT_SHA, file_path=FILE_PATH, line=2,
            evidence=EXCERPT, evidence_ids=[COMMIT_SHA], suggested_code=SUGGESTED,
            suggested_fix="Normalizar el valor de entrada.",
            improvement_summary="Normaliza el valor de entrada.",
            repository_identity=self.identity, provenance=self.provenance,
        )
        self.source = CodeSourceSnapshot(
            content=SOURCE, is_complete=True, repository=REPOSITORY, commit_sha=COMMIT_SHA,
            file_path=FILE_PATH, source_sha256=SOURCE_HASH, total_lines=2,
        )
        self.origin = FindingActionOrigin(
            project_id=PROJECT_ID, run_id=RUN_ID, finding_id=FINDING_ID,
            repository_identity=self.identity, repository=REPOSITORY, commit_sha=COMMIT_SHA,
            file_path=FILE_PATH, source_sha256=SOURCE_HASH,
            excerpt_sha256=hashlib.sha256(EXCERPT.encode("utf-8")).hexdigest(),
            base_branch=BASE_BRANCH, configured_for_code_action=True, multi_repository=False,
        )
        self._seed()

    def _seed(self) -> None:
        snippet = CodeDiffSnippet(
            repository=REPOSITORY, repository_identity=self.identity, provenance=self.provenance,
            commit_sha=COMMIT_SHA, file_path=FILE_PATH, lines=[1, 2], content=SOURCE,
            evidence_scope="full_file", is_complete=True, total_lines=2, source_sha256=SOURCE_HASH,
        )
        state = RepositoryAnalysisState(
            identity=self.identity, branch=BASE_BRANCH, target_commit_sha=COMMIT_SHA,
            commit_resolution_status=CommitResolutionStatus.resolved,
        )
        now = datetime(2026, 1, 2, tzinfo=timezone.utc)
        self.storage.save_snapshot(WeeklySnapshot(
            id=SNAPSHOT_ID, project_id=PROJECT_ID,
            cutoff_datetime=datetime(2026, 1, 1, tzinfo=timezone.utc), captured_at=now,
            code_diffs=[snippet],
            repository_results=[RepositorySnapshotResult(
                repository_state=state, code_diffs=[snippet], is_complete=True,
            )],
        ))
        self.storage.save_findings(RUN_ID, [self.finding])
        self.storage.save_report(FinalReport(
            project_id=PROJECT_ID,
            cutoff_datetime=datetime(2026, 1, 1, tzinfo=timezone.utc),
            metrics=DeterministicTaskMetrics(total_cards=0, included_cards=0, completed_cards=0),
            pending_findings=[self.finding], candidate_findings=[self.finding],
            policy_evaluations=PolicyEvaluationResult(
                evaluations=[], passed_rule_ids=[], failed_rule_ids=[], unknown_rule_ids=[],
                mandatory_failed_ids=[], mandatory_unknown_ids=[],
                recommended_action="evidencia_insuficiente",
            ),
            recommendation="evidencia_insuficiente", summary="G3G4 crash fixture.",
            report_id=REPORT_ID, run_id=RUN_ID,
            repository_results=[RepositoryReportResult(
                repository_state=state, pending_findings=[self.finding],
            )],
        ), report_id=REPORT_ID, run_id=RUN_ID)
        self.storage.save_run(ExecutionRun(
            run_id=RUN_ID, project_id=PROJECT_ID, status=RunStatus.awaiting_human,
            created_at=now, updated_at=now, snapshot_id=SNAPSHOT_ID,
            config_hash="a" * 64, snapshot_hash="b" * 64, report_id=REPORT_ID,
        ))
        self.storage.save_verifications(RUN_ID, [FindingVerification(
            finding_id=FINDING_ID, verdict=VerificationVerdict.confirmed,
            reason="Defecto confirmado con evidencia.", verified_by="verifier_agent",
            evidence_ids=[COMMIT_SHA],
        )])

    def _record_review(self, *, verdict: str = "favorable") -> None:
        from agente_gestion.application.patch_review import PatchReviewService
        from agente_gestion.domain.patch_review import PatchReviewOutcome, PatchReviewVerdict

        class _Verifier:
            def review_patch(self, *, project_id, finding, patch, patched_region,
                              evidence_ids=None, **kwargs):
                return PatchReviewOutcome(
                    verdict=PatchReviewVerdict(verdict),
                    reason="La correccion resuelve el defecto sin alterar el contexto.",
                    evidence_ids=list(evidence_ids or []),
                )

        PatchReviewService(self.storage, _Verifier()).review(
            project_id=PROJECT_ID, run_id=RUN_ID, finding=self.finding, source=self.source,
            original_excerpt=EXCERPT, suggested_code=SUGGESTED, repository=REPOSITORY,
            commit_sha=COMMIT_SHA, file_path=FILE_PATH, evidence_ids=[COMMIT_SHA],
        )

    def _decide(self, service: FindingDecisionService, **overrides):
        arguments = {
            "project_id": PROJECT_ID, "run_id": RUN_ID, "finding_id": FINDING_ID,
            "candidate": self.finding, "decision": "approve",
            "idempotency_key": "g3g4-crash-0001", "actor_id": ACTOR,
            "session_cookie": "synthetic-cookie", "original_excerpt": EXCERPT,
            "source": self.source, "repository": REPOSITORY, "base_branch": BASE_BRANCH,
            "origin": self.origin,
        }
        return service.decide(**{**arguments, **overrides})

    def _expire_the_lease(self) -> None:
        """Simulate the passage of time without sleeping."""
        with closing(sqlite3.connect(self.root / "agente.db")) as connection:
            connection.execute(
                "UPDATE finding_decision_requests SET lease_expires_at=? WHERE state='processing'",
                ("2000-01-01T00:00:00+00:00",),
            )
            connection.commit()

    def _request_row(self) -> dict[str, Any]:
        with closing(sqlite3.connect(self.root / "agente.db")) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM finding_decision_requests WHERE idempotency_key=?",
                ("g3g4-crash-0001",),
            ).fetchone()
        return dict(row) if row is not None else {}

    def _ledger_row(self) -> dict[str, Any]:
        with closing(sqlite3.connect(self.root / "agente.db")) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM finding_publications WHERE finding_id=?", (FINDING_ID,),
            ).fetchone()
        return dict(row) if row is not None else {}


class AbandonedPublicationTests(_SingleFindingFixture):
    """The crash between the checkpoint and the pull request is recoverable."""

    def _crash(self, port: _CrashPort) -> None:
        self._record_review()
        service = FindingDecisionService(self.storage, port)
        with self.assertRaises(KeyboardInterrupt):
            self._decide(service)

    def test_the_crash_leaves_the_checkpoint_and_the_request_pending(self):
        port = _CrashPort()
        self._crash(port)

        request = self._request_row()
        self.assertEqual(request["state"], "processing")
        ledger = self._ledger_row()
        self.assertEqual(ledger["stage"], "branch_created")
        self.assertEqual(ledger["head_branch"], port.branch_calls[0]["target_branch"])
        self.assertEqual(ledger["head_commit_sha"], "d" * 64)

    def test_an_attempt_that_still_holds_its_lease_is_never_stolen(self):
        port = _CrashPort()
        self._crash(port)
        service = FindingDecisionService(self.storage, port)

        with self.assertRaises(FindingDecisionError) as caught:
            self._decide(service)

        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.code, "decision_en_curso")
        self.assertEqual(len(port.publication_calls), 1)
        self.assertEqual(self._request_row()["state"], "processing")

    def test_the_abandoned_publication_is_recovered_with_the_same_key(self):
        port = _CrashPort()
        self._crash(port)
        self._expire_the_lease()
        port.crash = False
        service = FindingDecisionService(self.storage, port)

        result = self._decide(service)

        self.assertEqual(result["decision_status"], "approved")
        self.assertEqual(result["publication_status"], "published")
        self.assertEqual(result["pull_request_number"], 4321)
        self.assertEqual(result["pull_request_state"], "simulated")
        # The branch and the commit are the ones that were already created.
        self.assertEqual(len(port.branch_calls), 1)
        self.assertEqual(len(port.publication_calls), 2)
        self.assertEqual(result["branch_name"], port.branch_calls[0]["target_branch"])
        self.assertEqual(result["commit_sha_applied"], "d" * 64)
        # One human decision, one durable publication.
        with closing(sqlite3.connect(self.root / "agente.db")) as connection:
            decisions = connection.execute("SELECT count(*) FROM finding_decisions").fetchone()[0]
            requests = connection.execute("SELECT count(*) FROM finding_decision_requests").fetchone()[0]
        self.assertEqual((decisions, requests), (1, 1))
        self.assertEqual(self._request_row()["state"], "completed")
        self.assertEqual(self._ledger_row()["stage"], "published")

    def test_the_recovery_is_audited(self):
        port = _CrashPort()
        self._crash(port)
        self._expire_the_lease()
        port.crash = False
        service = FindingDecisionService(self.storage, port)
        self._decide(service)

        with closing(sqlite3.connect(self.root / "agente.db")) as connection:
            connection.row_factory = sqlite3.Row
            audits = [dict(row) for row in connection.execute(
                "SELECT event_id, permission_checked, decision, reason FROM security_audit_log"
                " ORDER BY rowid",
            ).fetchall()]
        publications = [row for row in audits if row["permission_checked"] == "web:finding_publication"]
        decisions = [row for row in audits if row["permission_checked"] == "web:finding_decision"]
        self.assertEqual(len(publications), 1)
        self.assertEqual(publications[0]["decision"], "granted")
        self.assertEqual(len(decisions), 1)

    def test_another_key_cannot_take_over_an_abandoned_approval(self):
        port = _CrashPort()
        self._crash(port)
        self._expire_the_lease()
        port.crash = False
        service = FindingDecisionService(self.storage, port)

        with self.assertRaises(FindingDecisionError) as caught:
            self._decide(service, idempotency_key="g3g4-crash-0002")

        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.code, "decision_abandonada")
        self.assertEqual(len(port.branch_calls), 1)
        self.assertEqual(len(port.publication_calls), 1)

    def test_the_recovery_revalidates_the_review(self):
        port = _CrashPort()
        self._crash(port)
        self._expire_the_lease()
        port.crash = False
        self._record_review(verdict="unfavorable")
        service = FindingDecisionService(self.storage, port)

        with self.assertRaises(FindingDecisionError) as caught:
            self._decide(service)

        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.code, APPROVAL_REVIEW_REQUIRED_CODE)
        self.assertEqual(len(port.publication_calls), 1)
        self.assertEqual(self._ledger_row()["stage"], "branch_created")

    def test_the_recovery_revalidates_the_actor(self):
        port = _CrashPort()
        self._crash(port)
        self._expire_the_lease()
        port.crash = False
        service = FindingDecisionService(self.storage, port)

        with self.assertRaises(FindingDecisionError) as caught:
            self._decide(service, actor_id="otro-gestor")

        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.code, "idempotencia_conflictiva")
        self.assertEqual(len(port.publication_calls), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
