"""G4: the quality API exposes review, eligibility, decision and publication.

The interface never interprets the authorization rules: it asks the shared
service and shows what the backend answered. The exact patch review, the
eligibility, the human decision and the publication are four separate facts,
and a simulated pull request can never be presented as a real one.
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

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from agente_gestion.adapters.github_code_action_adapter import CodeActionError
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.api.control_plane import ControlPlaneRepository
from agente_gestion.api.errors import ApiProblem
from agente_gestion.api.finding_actions import router as finding_actions_router
from agente_gestion.api.repository import SQLiteQueryRepository
from agente_gestion.api.routes import router as api_router
from agente_gestion.api.security import OperatorSessionStore
from agente_gestion.api.settings import ApiSettings, ProjectConfig
from agente_gestion.application.finding_publication import latest_finding_publication
from agente_gestion.application.patch_review import (
    BLOCK_DEFECT_NOT_CONFIRMED,
    BLOCK_REVIEW_MISSING,
    PatchReviewService,
)
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
from agente_gestion.domain.patch_review import PatchReviewOutcome, PatchReviewVerdict
from agente_gestion.domain.policies import PolicyEvaluationResult
from agente_gestion.domain.report import FinalReport, RepositoryReportResult
from agente_gestion.domain.repositories import (
    CommitResolutionStatus,
    RepositoryAnalysisState,
    RepositoryConfiguration,
    RepositoryIdentity,
    RepositoryProvenance,
)
from agente_gestion.domain.run import ExecutionRun, RunStatus
from agente_gestion.domain.snapshot import RepositorySnapshotResult, WeeklySnapshot
from agente_gestion.domain.tasks import DeterministicTaskMetrics


PROJECT_ID = "G4-QUALITY"
RUN_ID = "run-g4-quality"
OTHER_RUN_ID = "run-g4-quality-other"
SNAPSHOT_ID = "snapshot-g4-quality"
REPORT_ID = "report-g4-quality"
FINDING_ID = "backend:f-g4-1"
OPERATOR_TOKEN = "g4-isolated-operator-token-0123456789"
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"
REPOSITORY = "acme/backend"
FILE_PATH = "src/shared.py"
BASE_BRANCH = "develop"
SOURCE = "def parse(value):\n    return value\n"
SOURCE_HASH = hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()
EXCERPT = "    return value"
SUGGESTED = "    return value.strip()"
PATCH_SHA256 = hashlib.sha256(
    SOURCE.replace(EXCERPT, SUGGESTED, 1).encode("utf-8"),
).hexdigest()
OTHER_SUGGESTED = "    return str(value).strip()"
OTHER_PATCH_SHA256 = hashlib.sha256(
    SOURCE.replace(EXCERPT, OTHER_SUGGESTED, 1).encode("utf-8"),
).hexdigest()


class _StubVerifier:
    """Returns a prepared verdict without calling any model."""

    def __init__(self, verdict: PatchReviewVerdict = PatchReviewVerdict.favorable):
        self.verdict = verdict

    def review_patch(self, *, project_id, finding, patch, patched_region, evidence_ids=None, **kwargs):
        return PatchReviewOutcome(
            verdict=self.verdict,
            reason="La correccion resuelve el defecto sin alterar el contexto.",
            evidence_ids=list(evidence_ids or []),
        )


class _RecordingCodeActionPort:
    """Records every branch and pull request so a refusal can be proven inert.

    The port mirrors the sandbox behaviour of the real adapter: the branch is
    local and the pull request is explicitly simulated, with a deterministic
    number and a ``sandbox://`` address that no browser can open.
    """

    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self.pull_request_calls: list[dict[str, Any]] = []

    def create_verification_branch(self, **kwargs):
        self.calls.append(kwargs)
        return BranchExecutionResult(
            execution_mode="sandbox", branch_name=kwargs["target_branch"],
            branch_url=f"sandbox://branches/{kwargs['target_branch']}", commit_sha_applied="d" * 64,
        )

    def create_pull_request(
        self, *, repo, base_branch, branch_result, finding_id, actor, patch_sha256,
    ):
        self.pull_request_calls.append({
            "repo": repo, "base_branch": base_branch, "finding_id": finding_id,
            "actor": actor, "patch_sha256": patch_sha256,
            "head_branch": branch_result.branch_name,
            "head_commit_sha": branch_result.commit_sha_applied,
        })
        identity = json.dumps({
            "base_branch": base_branch, "finding_id": finding_id,
            "head_branch": branch_result.branch_name,
            "head_commit_sha": branch_result.commit_sha_applied,
            "patch_sha256": patch_sha256, "repository": repo,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        number = 1000 + int(hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8], 16) % 9000
        return PullRequestExecutionResult(
            execution_mode=branch_result.execution_mode,
            pull_request_number=number,
            pull_request_url=f"sandbox://pulls/{repo}/{number}",
            state="simulated",
            repository=repo,
            base_branch=base_branch,
            head_branch=branch_result.branch_name,
            head_commit_sha=branch_result.commit_sha_applied,
            recovered=False,
        )


class _FlakyPublicationPort(_RecordingCodeActionPort):
    """Refuses the first publication and succeeds on the recovery.

    It reproduces an interrupted publication: the branch and the commit are
    already created, so only the pull request is missing.
    """

    def __init__(self):
        super().__init__()
        self.refusals = 1

    def create_pull_request(self, **kwargs):
        if self.refusals > 0:
            self.refusals -= 1
            self.pull_request_calls.append({"refused": True, **kwargs})
            raise CodeActionError(
                "pull_request_no_confirmado",
                "No se pudo confirmar la creación del pull request.",
            )
        return super().create_pull_request(**kwargs)


class G4QualityApiFixture(unittest.TestCase):
    """One persisted run, finding, source, review and origin for the API tests."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.database_path = self.root / "g4-quality.sqlite"
        self.storage = SQLiteStorageAdapter(str(self.database_path))
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
        self._seed()
        self.client, self.port = self._build_client()
        self.addCleanup(self.client.close)
        self.addCleanup(self._tmp.cleanup)

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
            recommendation="evidencia_insuficiente",
            summary="Isolated G4 quality API fixture.",
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

    def _build_client(self):
        project = ProjectConfig(
            id=PROJECT_ID, name="G4 quality fixture", source_mode="fixtures",
            repositories=(RepositoryConfiguration(
                id="backend", owner="acme", repo="backend", branch=BASE_BRANCH,
            ),),
        )
        settings = ApiSettings(
            db_path=str(self.database_path),
            runs_dir=str(self.root / "runs"),
            outbox_dir=str(self.root / "outbox"),
            projects=(project,),
            governance_enabled=True,
            external_writes_enabled=False,
            github_repository=REPOSITORY,
            github_base_branch=BASE_BRANCH,
            branches_dir=str(self.root / "branches"),
            operator_token=OPERATOR_TOKEN,
            operator_project_ids=(PROJECT_ID,),
            allowed_origins=("http://testserver",),
            allowed_hosts=("testserver",),
        )
        app = FastAPI()
        app.state.settings = settings
        app.state.repository = SQLiteQueryRepository(settings)
        app.state.control_plane = ControlPlaneRepository(settings)
        app.state.sessions = OperatorSessionStore(settings)
        port = _RecordingCodeActionPort()
        app.state.code_action_port = port

        @app.exception_handler(ApiProblem)
        async def handle_api_problem(_request: Request, exc: ApiProblem):
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": {"code": exc.code, "message": exc.message}},
            )

        app.include_router(api_router)
        app.include_router(finding_actions_router)
        return TestClient(app), port

    # --- helpers ---------------------------------------------------------

    def _authenticate(self):
        login = self.client.post(
            "/api/v1/auth/login", json={"token": OPERATOR_TOKEN},
            headers={"Origin": "http://testserver"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        return {
            "Origin": "http://testserver",
            "X-CSRF-Token": login.json()["csrf_token"],
        }

    def _record_review(
        self, *, run_id: str = RUN_ID, verdict: PatchReviewVerdict = PatchReviewVerdict.favorable,
        suggested_code: str = SUGGESTED,
    ):
        service = PatchReviewService(self.storage, _StubVerifier(verdict))
        return service.review(
            project_id=PROJECT_ID, run_id=run_id, finding=self.finding,
            source=CodeSourceSnapshot(
                content=SOURCE, is_complete=True, repository=REPOSITORY, commit_sha=COMMIT_SHA,
                file_path=FILE_PATH, source_sha256=SOURCE_HASH, total_lines=2,
            ),
            original_excerpt=EXCERPT, suggested_code=suggested_code,
            repository=REPOSITORY, commit_sha=COMMIT_SHA, file_path=FILE_PATH,
            evidence_ids=[COMMIT_SHA],
        )

    def _confirm_defect(self, *, run_id: str = RUN_ID) -> None:
        self.storage.save_verifications(run_id, [FindingVerification(
            finding_id=FINDING_ID, verdict=VerificationVerdict.confirmed,
            reason="Defecto confirmado con evidencia.", verified_by="verifier_agent",
            evidence_ids=[COMMIT_SHA],
        )])

    def _detail(self):
        response = self.client.get(f"/api/v1/runs/{RUN_ID}/findings/{FINDING_ID}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _eligibility(self, body: dict | None = None):
        return self.client.post(
            f"/api/v1/runs/{RUN_ID}/findings/{FINDING_ID}/approval-eligibility",
            json=body or {}, headers=self._authenticate(),
        )


class FindingDetailAuthorizationTests(G4QualityApiFixture):
    """The detail separates review, eligibility, decision and publication."""

    def test_the_detail_exposes_the_exact_patch_review_and_the_eligibility(self):
        self._confirm_defect()
        review = self._record_review()
        detail = self._detail()
        self.assertIsNotNone(detail["patch_review"])
        self.assertEqual(detail["patch_review"]["review_id"], review.review_id)
        self.assertEqual(detail["patch_review"]["patch_sha256"], PATCH_SHA256)
        self.assertEqual(detail["patch_review"]["verdict"], "favorable")
        self.assertEqual(detail["patch_review"]["run_id"], RUN_ID)
        self.assertEqual(detail["patch_review"]["repository"], REPOSITORY)
        self.assertEqual(detail["patch_review"]["file_path"], FILE_PATH)
        self.assertTrue(detail["approval_eligibility"]["eligible"])
        self.assertTrue(detail["approval_eligibility"]["authorized"])
        self.assertTrue(detail["approval_eligibility"]["defect_confirmed"])
        self.assertIsNone(detail["approval_eligibility"]["blocked_reason"])
        self.assertEqual(detail["approval_eligibility"]["patch_sha256"], PATCH_SHA256)
        self.assertEqual(detail["approval_eligibility"]["review_id"], review.review_id)
        self.assertIsNone(detail["approval_eligibility"]["blocked_reason"])
        self.assertIsNone(detail["approval_eligibility"]["blocked_reason_message"])
        self.assertIn("publication", detail)
        self.assertIsNone(detail["publication"])

    def test_the_detail_reports_a_blocked_eligibility_without_a_review(self):
        self._confirm_defect()
        detail = self._detail()
        self.assertIsNone(detail["patch_review"])
        self.assertFalse(detail["approval_eligibility"]["eligible"])
        self.assertEqual(detail["approval_eligibility"]["blocked_reason"], BLOCK_REVIEW_MISSING)
        self.assertTrue(detail["approval_eligibility"]["blocked_reason_message"])

    def test_the_detail_reports_a_blocked_eligibility_without_a_confirmed_defect(self):
        self._record_review()
        detail = self._detail()
        self.assertFalse(detail["approval_eligibility"]["eligible"])
        self.assertEqual(
            detail["approval_eligibility"]["blocked_reason"], BLOCK_DEFECT_NOT_CONFIRMED,
        )

    def test_a_review_of_another_run_does_not_authorize_the_detail(self):
        self._confirm_defect()
        self._record_review(run_id=OTHER_RUN_ID)
        detail = self._detail()
        self.assertIsNone(detail["patch_review"])
        self.assertFalse(detail["approval_eligibility"]["eligible"])
        self.assertEqual(detail["approval_eligibility"]["blocked_reason"], BLOCK_REVIEW_MISSING)


class FindingEligibilityEndpointTests(G4QualityApiFixture):
    """The override evaluation is a backend fact, never an interface guess."""

    def test_the_endpoint_answers_eligible_for_the_reviewed_patch(self):
        self._confirm_defect()
        review = self._record_review()
        response = self._eligibility()
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["finding_id"], FINDING_ID)
        self.assertEqual(payload["run_id"], RUN_ID)
        self.assertTrue(payload["approval_eligibility"]["eligible"])
        self.assertEqual(payload["approval_eligibility"]["review_id"], review.review_id)
        self.assertEqual(payload["patch_review"]["patch_sha256"], PATCH_SHA256)

    def test_the_endpoint_refuses_to_extend_a_review_to_an_edited_patch(self):
        self._confirm_defect()
        self._record_review()
        response = self._eligibility({"suggested_code_override": OTHER_SUGGESTED})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertFalse(payload["approval_eligibility"]["eligible"])
        self.assertEqual(payload["approval_eligibility"]["patch_sha256"], OTHER_PATCH_SHA256)
        self.assertIsNone(payload["approval_eligibility"]["review_id"])
        self.assertIsNone(payload["patch_review"])
        self.assertTrue(payload["approval_eligibility"]["blocked_reason_message"])

    def test_the_endpoint_refuses_an_empty_override(self):
        response = self._eligibility({"suggested_code_override": "   "})
        self.assertEqual(response.status_code, 422, response.text)

    def test_the_endpoint_requires_a_manager_session(self):
        self._confirm_defect()
        self._record_review()
        response = self.client.post(
            f"/api/v1/runs/{RUN_ID}/findings/{FINDING_ID}/approval-eligibility", json={},
        )
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["error"]["code"], "sesion_requerida")

    def test_the_endpoint_writes_no_decision_and_no_audit(self):
        self._confirm_defect()
        self._record_review()
        before_findings = self.storage.get_findings(RUN_ID)
        before_audit = self.storage.list_security_audit_events(PROJECT_ID)
        response = self._eligibility()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.storage.get_findings(RUN_ID), before_findings)
        self.assertEqual(self.storage.list_security_audit_events(PROJECT_ID), before_audit)
        with closing(sqlite3.connect(self.database_path)) as connection:
            counts = tuple(connection.execute(
                f"SELECT count(*) FROM {table}",
            ).fetchone()[0] for table in (
                "finding_decisions", "finding_decision_requests",
            ))
        self.assertEqual(counts, (0, 0))
        self.assertEqual(self.port.calls, [])

    def test_the_endpoint_reports_an_unknown_finding(self):
        self._authenticate()
        response = self.client.post(
            f"/api/v1/runs/{RUN_ID}/findings/backend:f-missing/approval-eligibility",
            json={}, headers=self._authenticate(),
        )
        self.assertIn(response.status_code, {404, 409}, response.text)


class FindingDecisionPublicationContractTests(G4QualityApiFixture):
    """The decision response carries the publication contract of the parent task."""

    def test_the_decision_response_exposes_the_frozen_publication_fields(self):
        self._confirm_defect()
        self._record_review()
        response = self.client.post(
            f"/api/v1/runs/{RUN_ID}/findings/{FINDING_ID}/decision",
            json={"decision": "reject", "reason": "El defecto no es accionable."},
            headers={**self._authenticate(), "Idempotency-Key": "g4-decision-contract-0001"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        for field in (
            "publication_status", "publication_error_code", "publication_error_message",
            "pull_request_number", "pull_request_url", "pull_request_state",
            "pull_request_simulated", "patch_review_id", "patch_sha256",
        ):
            self.assertIn(field, payload)
        # A decision that publishes nothing reports exactly that, and never a
        # pull request that does not exist.
        self.assertEqual(payload["publication_status"], "not_requested")
        self.assertIsNone(payload["publication_error_code"])
        self.assertIsNone(payload["publication_error_message"])
        self.assertIsNone(payload["pull_request_number"])
        self.assertIsNone(payload["pull_request_url"])
        self.assertIsNone(payload["pull_request_state"])
        self.assertIsNone(payload["pull_request_simulated"])
        self.assertEqual(payload["decision_status"], "rejected")
        self.assertEqual(self.port.calls, [])
        self.assertEqual(self.port.pull_request_calls, [])

    def test_the_decision_is_refused_without_the_reviewed_chain(self):
        response = self.client.post(
            f"/api/v1/runs/{RUN_ID}/findings/{FINDING_ID}/decision",
            json={"decision": "approve"},
            headers={**self._authenticate(), "Idempotency-Key": "g4-approve-blocked-0001"},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["error"]["code"], "revision_independiente_requerida",
        )
        self.assertEqual(self.port.calls, [])


class FindingPublicationIntegrationTests(G4QualityApiFixture):
    """The integrated contract: the service publishes the reviewed branch.

    The publication service is integrated, so this proof is never skipped: the
    decision response carries the reviewed identity and the positive receipt
    of one simulated pull request, the durable ledger agrees with it and the
    audit records the attempt.
    """

    def test_the_decision_response_carries_the_reviewed_identity_and_the_publication(self):
        self._confirm_defect()
        review = self._record_review()
        response = self.client.post(
            f"/api/v1/runs/{RUN_ID}/findings/{FINDING_ID}/decision",
            json={"decision": "approve", "reason": "Correccion revisada y aprobada."},
            headers={**self._authenticate(), "Idempotency-Key": "g4-approve-published-0001"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["patch_sha256"], PATCH_SHA256)
        self.assertEqual(payload["patch_review_id"], review.review_id)
        self.assertEqual(payload["publication_status"], "published")
        self.assertIsNone(payload["publication_error_code"])
        self.assertIsNone(payload["publication_error_message"])
        self.assertEqual(payload["pull_request_state"], "simulated")
        self.assertTrue(payload["pull_request_simulated"])
        self.assertTrue(payload["pull_request_number"])
        self.assertTrue(payload["pull_request_url"])
        self.assertEqual(payload["decision_status"], "approved")

        # The branch and the commit of the receipt are the authorized ones.
        self.assertTrue(payload["branch_name"])
        self.assertTrue(payload["commit_sha_applied"])
        self.assertTrue(payload["branch_name"].startswith("quality/"))
        self.assertNotEqual(payload["branch_name"], "develop")
        self.assertEqual(len(payload["commit_sha_applied"]), 64)

        # The adapter was asked for exactly one pull request of the reviewed patch.
        self.assertEqual(len(self.port.pull_request_calls), 1)
        publication_call = self.port.pull_request_calls[0]
        self.assertEqual(publication_call["repo"], REPOSITORY)
        self.assertEqual(publication_call["base_branch"], "develop")
        self.assertEqual(publication_call["patch_sha256"], PATCH_SHA256)
        self.assertEqual(publication_call["head_branch"], payload["branch_name"])
        self.assertEqual(publication_call["head_commit_sha"], payload["commit_sha_applied"])

        # The simulated pull request is addressed as a simulation, never as a
        # real pull request of GitHub.
        self.assertTrue(payload["pull_request_url"].startswith("sandbox://pulls/"))
        self.assertTrue(
            payload["pull_request_url"].endswith(f"/{payload['pull_request_number']}"),
        )

        # The durable receipt agrees with the response.
        receipt = latest_finding_publication(
            self.storage, project_id=PROJECT_ID, run_id=RUN_ID, finding_id=FINDING_ID,
        )
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["publication_status"], "published")
        self.assertEqual(receipt["stage"], "published")
        self.assertEqual(receipt["pull_request_number"], payload["pull_request_number"])
        self.assertEqual(receipt["pull_request_url"], payload["pull_request_url"])
        self.assertEqual(receipt["pull_request_state"], "simulated")
        self.assertTrue(receipt["pull_request_simulated"])
        self.assertEqual(receipt["patch_review_id"], review.review_id)
        self.assertEqual(receipt["patch_sha256"], PATCH_SHA256)
        self.assertEqual(receipt["branch_name"], payload["branch_name"])
        self.assertEqual(receipt["commit_sha_applied"], payload["commit_sha_applied"])
        self.assertEqual(receipt["base_branch"], "develop")

        # The audit records the granted publication.
        audits = [
            entry for entry in self.storage.list_security_audit_events(PROJECT_ID)
            if entry.permission_checked == "web:finding_publication"
        ]
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].decision, "granted")
        self.assertIn(
            f"Pull request {payload['pull_request_number']} published against develop",
            audits[0].reason,
        )
        self.assertIn("sandbox mode", audits[0].reason)

    def test_a_partial_publication_is_recovered_with_the_same_idempotency_key(self):
        self._confirm_defect()
        self._record_review()
        flaky = _FlakyPublicationPort()
        self.client.app.state.code_action_port = flaky
        headers = {**self._authenticate(), "Idempotency-Key": "g4-approve-partial-0001"}
        path = f"/api/v1/runs/{RUN_ID}/findings/{FINDING_ID}/decision"
        body = {"decision": "approve", "reason": "Correccion revisada y aprobada."}

        first = self.client.post(path, json=body, headers=headers)
        self.assertEqual(first.status_code, 200, first.text)
        partial = first.json()
        # The branch and the commit survive the failure of the pull request.
        self.assertEqual(partial["decision_status"], "approved")
        self.assertEqual(partial["publication_status"], "partial")
        self.assertEqual(partial["publication_error_code"], "pull_request_no_confirmado")
        self.assertTrue(partial["publication_error_message"])
        self.assertIsNone(partial["pull_request_number"])
        self.assertIsNone(partial["pull_request_url"])
        self.assertTrue(partial["branch_name"])
        self.assertTrue(partial["commit_sha_applied"])

        # The same idempotency key recovers the publication: no second human
        # decision and no second branch.
        second = self.client.post(path, json=body, headers=headers)
        self.assertEqual(second.status_code, 200, second.text)
        recovered = second.json()
        self.assertTrue(recovered["reused"])
        self.assertEqual(recovered["publication_status"], "published")
        self.assertIsNone(recovered["publication_error_code"])
        self.assertEqual(recovered["pull_request_state"], "simulated")
        self.assertTrue(recovered["pull_request_simulated"])
        self.assertTrue(recovered["pull_request_number"])
        self.assertTrue(recovered["pull_request_url"])
        self.assertEqual(recovered["branch_name"], partial["branch_name"])
        self.assertEqual(recovered["commit_sha_applied"], partial["commit_sha_applied"])
        self.assertEqual(len(flaky.calls), 1)
        self.assertEqual(len(flaky.pull_request_calls), 2)

        # Both attempts are audited: the refusal and the granted recovery.
        audits = [
            entry for entry in self.storage.list_security_audit_events(PROJECT_ID)
            if entry.permission_checked == "web:finding_publication"
        ]
        self.assertEqual(len(audits), 2)
        self.assertEqual(audits[0].decision, "denied")
        self.assertIn("pull_request_no_confirmado", audits[0].reason)
        self.assertEqual(audits[1].decision, "granted")
        # The recovery is audited as a recovery, not as a new publication.
        self.assertIn(
            f"Pull request {recovered['pull_request_number']} recovered against develop",
            audits[1].reason,
        )

    def test_the_replayed_decision_reuses_the_published_pull_request(self):
        self._confirm_defect()
        self._record_review()
        headers = {**self._authenticate(), "Idempotency-Key": "g4-approve-replay-0001"}
        first = self.client.post(
            f"/api/v1/runs/{RUN_ID}/findings/{FINDING_ID}/decision",
            json={"decision": "approve", "reason": "Correccion revisada y aprobada."},
            headers=headers,
        )
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.post(
            f"/api/v1/runs/{RUN_ID}/findings/{FINDING_ID}/decision",
            json={"decision": "approve", "reason": "Correccion revisada y aprobada."},
            headers=headers,
        )
        self.assertEqual(second.status_code, 200, second.text)
        replayed = second.json()
        self.assertTrue(replayed["reused"])
        self.assertEqual(
            replayed["pull_request_number"], first.json()["pull_request_number"],
        )
        self.assertEqual(replayed["pull_request_url"], first.json()["pull_request_url"])
        # The publication is never duplicated by a replay.
        self.assertEqual(len(self.port.pull_request_calls), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
