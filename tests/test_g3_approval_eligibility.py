"""G3: authorization policy bound to the run, the source and the exact patch.

The approval chain is the one of the operation: a confirmed defect, an exact
patch, an independent favorable review of that patch, and the human approval.
Every link is bound to the same execution, repository, file and origin commit,
and a missing or changed link blocks the approval instead of weakening it.

The eligibility service is read-only: it reports the same fact that the
decision gate uses, so an interface can never interpret the rules on its own.
"""

import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.application.finding_decisions import (
    APPROVAL_REVIEW_REQUIRED_CODE,
    APPROVAL_REVIEW_REQUIRED_MESSAGE,
    FindingDecisionError,
    FindingDecisionService,
)
from agente_gestion.application.patch_review import (
    BLOCK_DEFECT_NOT_CONFIRMED,
    BLOCK_PATCH_NOT_IDENTIFIABLE,
    BLOCK_REVIEW_INSUFFICIENT,
    BLOCK_REVIEW_MISSING,
    BLOCK_REVIEW_NOT_APPLICABLE,
    BLOCK_REVIEW_UNFAVORABLE,
    BLOCK_SUGGESTED_CODE_REQUIRED,
    PatchReviewError,
    PatchReviewService,
    evaluate_approval_eligibility,
    favorable_patch_review,
    match_patch_review,
)
from agente_gestion.domain.code import CodeDiffSnippet
from agente_gestion.domain.code_action import (
    BranchExecutionResult,
    CodeSourceSnapshot,
    FindingActionOrigin,
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
    RepositoryIdentity,
    RepositoryProvenance,
)
from agente_gestion.domain.run import ExecutionRun, RunStatus
from agente_gestion.domain.snapshot import RepositorySnapshotResult, WeeklySnapshot
from agente_gestion.domain.tasks import DeterministicTaskMetrics


PROJECT_ID = "G3-POLICY"
RUN_ID = "run-g3-policy"
OTHER_RUN_ID = "run-g3-policy-other"
SNAPSHOT_ID = "snapshot-g3-policy"
REPORT_ID = "report-g3-policy"
FINDING_ID = "backend:f-g3-1"
REPOSITORY = "acme/backend"
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"
FILE_PATH = "src/shared.py"
BASE_BRANCH = "release/backend"
SOURCE = "def parse(value):\n    return value\n"
SOURCE_HASH = hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()
EXCERPT = "    return value"
SUGGESTED = "    return value.strip()"
PATCH_SHA256 = hashlib.sha256(
    SOURCE.replace(EXCERPT, SUGGESTED, 1).encode("utf-8"),
).hexdigest()


class _StubVerifier:
    """Returns a prepared verdict without calling any model."""

    def __init__(
        self,
        verdict: PatchReviewVerdict = PatchReviewVerdict.favorable,
        reason: str = "La correccion resuelve el defecto sin alterar el contexto.",
    ):
        self.verdict = verdict
        self.reason = reason
        self.calls: list[dict[str, Any]] = []

    def review_patch(self, *, project_id, finding, patch, patched_region, evidence_ids=None, **kwargs):
        self.calls.append({"patch": patch, "patched_region": patched_region})
        return PatchReviewOutcome(
            verdict=self.verdict, reason=self.reason, evidence_ids=list(evidence_ids or []),
        )


class _StubReview:
    """A minimal recorded review used by the pure policy tests."""

    def __init__(self, verdict: PatchReviewVerdict = PatchReviewVerdict.favorable):
        self.review_id = "patch-review-stub"
        self.verdict = verdict
        self.run_id = RUN_ID
        self.patch_sha256 = PATCH_SHA256


class _RecordingCodeActionPort:
    """Records every branch creation so a refusal can be proven inert."""

    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def create_verification_branch(self, **kwargs):
        self.calls.append(kwargs)
        return BranchExecutionResult(
            execution_mode="sandbox", branch_name=kwargs["target_branch"],
            branch_url=f"sandbox://branches/{kwargs['target_branch']}", commit_sha_applied="d" * 64,
        )


class _PolicyFixture(unittest.TestCase):
    """One persisted run, finding, source and origin shared by the policy tests."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
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

    def tearDown(self) -> None:
        self._tmp.cleanup()

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
            summary="Isolated G3 authorization policy fixture.",
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

    def _record_review(
        self,
        *,
        run_id: str = RUN_ID,
        verdict: PatchReviewVerdict = PatchReviewVerdict.favorable,
        reason: str = "La correccion resuelve el defecto sin alterar el contexto.",
        source: CodeSourceSnapshot | None = None,
        repository: str = REPOSITORY,
        commit_sha: str = COMMIT_SHA,
        file_path: str = FILE_PATH,
        suggested_code: str = SUGGESTED,
        original_excerpt: str = EXCERPT,
    ):
        service = PatchReviewService(self.storage, _StubVerifier(verdict, reason))
        return service.review(
            project_id=PROJECT_ID, run_id=run_id, finding=self.finding,
            source=source or self.source, original_excerpt=original_excerpt,
            suggested_code=suggested_code, repository=repository, commit_sha=commit_sha,
            file_path=file_path, evidence_ids=[COMMIT_SHA],
        )

    def _confirm_defect(
        self,
        *,
        run_id: str = RUN_ID,
        finding_id: str = FINDING_ID,
        verdict: VerificationVerdict = VerificationVerdict.confirmed,
        verified_by: str = "verifier_agent",
    ) -> None:
        self.storage.save_verifications(run_id, [FindingVerification(
            finding_id=finding_id, verdict=verdict, reason="Defecto confirmado con evidencia.",
            verified_by=verified_by, evidence_ids=[COMMIT_SHA],
        )])

    def _match(self, **overrides):
        identity = {
            "project_id": PROJECT_ID, "run_id": RUN_ID, "finding_id": FINDING_ID,
            "patch_sha256": PATCH_SHA256, "original_sha256": SOURCE_HASH,
            "repository": REPOSITORY, "commit_sha": COMMIT_SHA, "file_path": FILE_PATH,
        }
        return match_patch_review(self.storage, **{**identity, **overrides})

    def _eligibility(self, service: FindingDecisionService, **overrides):
        arguments = {
            "project_id": PROJECT_ID, "run_id": RUN_ID, "finding_id": FINDING_ID,
            "candidate": self.finding, "original_excerpt": EXCERPT, "source": self.source,
            "repository": REPOSITORY, "base_branch": BASE_BRANCH, "origin": self.origin,
        }
        return service.approval_eligibility(**{**arguments, **overrides})

    def _decide(self, service: FindingDecisionService, **overrides):
        arguments = {
            "project_id": PROJECT_ID, "run_id": RUN_ID, "finding_id": FINDING_ID,
            "candidate": self.finding, "decision": "approve", "idempotency_key": "g3-approve-0001",
            "actor_id": "gestor-local", "session_cookie": "synthetic-cookie",
            "original_excerpt": EXCERPT, "source": self.source, "repository": REPOSITORY,
            "base_branch": BASE_BRANCH, "origin": self.origin,
        }
        return service.decide(**{**arguments, **overrides})


class ApprovalEligibilityPolicyTests(unittest.TestCase):
    """The shared policy is pure: no storage, no clock, no side effect."""

    def test_the_policy_requires_a_confirmed_defect_and_a_favorable_review(self):
        review = _StubReview()
        eligible = evaluate_approval_eligibility(
            context_valid=True, defect_confirmed=True, patch_sha256=PATCH_SHA256,
            matched_review=review,
        )
        self.assertTrue(eligible.eligible)
        self.assertTrue(eligible.authorized)
        self.assertIsNone(eligible.blocked_reason)
        self.assertEqual(eligible.review_id, review.review_id)
        self.assertEqual(eligible.patch_sha256, PATCH_SHA256)

        without_defect = evaluate_approval_eligibility(
            context_valid=True, defect_confirmed=False, patch_sha256=PATCH_SHA256,
            matched_review=review,
        )
        self.assertFalse(without_defect.eligible)
        self.assertFalse(without_defect.authorized)
        self.assertEqual(without_defect.blocked_reason, BLOCK_DEFECT_NOT_CONFIRMED)

        without_review = evaluate_approval_eligibility(
            context_valid=True, defect_confirmed=True, patch_sha256=PATCH_SHA256,
            matched_review=None, review_blocked_reason=BLOCK_REVIEW_MISSING,
        )
        self.assertFalse(without_review.eligible)
        self.assertEqual(without_review.blocked_reason, BLOCK_REVIEW_MISSING)

    def test_the_policy_blocks_an_unfavorable_or_insufficient_review(self):
        for verdict, expected in (
            (PatchReviewVerdict.unfavorable, BLOCK_REVIEW_UNFAVORABLE),
            (PatchReviewVerdict.insufficient, BLOCK_REVIEW_INSUFFICIENT),
        ):
            with self.subTest(verdict=verdict):
                eligibility = evaluate_approval_eligibility(
                    context_valid=True, defect_confirmed=True, patch_sha256=PATCH_SHA256,
                    matched_review=_StubReview(verdict=verdict),
                )
                self.assertFalse(eligibility.eligible)
                self.assertEqual(eligibility.blocked_reason, expected)

    def test_the_policy_blocks_a_patch_that_cannot_be_identified(self):
        eligibility = evaluate_approval_eligibility(
            context_valid=True, defect_confirmed=True, patch_sha256=None,
        )
        self.assertFalse(eligibility.eligible)
        self.assertEqual(eligibility.blocked_reason, BLOCK_PATCH_NOT_IDENTIFIABLE)

    def test_the_policy_does_not_authorize_an_unverifiable_context(self):
        eligibility = evaluate_approval_eligibility(
            context_valid=False, defect_confirmed=True, patch_sha256=PATCH_SHA256,
            matched_review=_StubReview(),
        )
        self.assertFalse(eligibility.eligible)
        self.assertEqual(eligibility.blocked_reason, "fuente_no_verificable")
        self.assertIsNone(eligibility.authorization_reason)


class ReviewIdentityTests(_PolicyFixture):
    """A review only covers the exact identity it judged."""

    def test_a_review_of_another_run_does_not_authorize(self):
        self._record_review(run_id=OTHER_RUN_ID)
        match = self._match()
        self.assertIsNone(match.review)
        self.assertEqual(match.blocked_reason, BLOCK_REVIEW_MISSING)

    def test_a_review_of_another_original_source_does_not_authorize(self):
        self._record_review()
        match = self._match(original_sha256="f" * 64)
        self.assertIsNone(match.review)
        self.assertEqual(match.blocked_reason, BLOCK_REVIEW_NOT_APPLICABLE)

    def test_a_review_of_another_repository_commit_or_file_does_not_authorize(self):
        self._record_review()
        for changed in (
            {"repository": "acme/frontend"},
            {"commit_sha": "b" * 40},
            {"file_path": "src/otro.py"},
        ):
            with self.subTest(changed=changed):
                match = self._match(**changed)
                self.assertIsNone(match.review)
                self.assertEqual(match.blocked_reason, BLOCK_REVIEW_NOT_APPLICABLE)

    def test_the_repository_is_matched_without_case_sensitivity(self):
        self._record_review(repository="Acme/Backend")
        match = self._match()
        self.assertIsNotNone(match.review)
        self.assertEqual(match.review.verdict, PatchReviewVerdict.favorable)

    def test_a_changed_patch_does_not_authorize(self):
        self._record_review()
        match = self._match(patch_sha256="c" * 64)
        self.assertIsNone(match.review)
        self.assertEqual(match.blocked_reason, BLOCK_REVIEW_NOT_APPLICABLE)

    def test_the_favorable_review_is_bound_to_the_run_and_the_original_hash(self):
        self._record_review()
        self.assertIsNotNone(favorable_patch_review(
            self.storage, project_id=PROJECT_ID, run_id=RUN_ID, finding_id=FINDING_ID,
            patch_sha256=PATCH_SHA256, original_sha256=SOURCE_HASH,
            repository=REPOSITORY, commit_sha=COMMIT_SHA, file_path=FILE_PATH,
        ))
        self.assertIsNone(favorable_patch_review(
            self.storage, project_id=PROJECT_ID, run_id=OTHER_RUN_ID, finding_id=FINDING_ID,
            patch_sha256=PATCH_SHA256, original_sha256=SOURCE_HASH,
            repository=REPOSITORY, commit_sha=COMMIT_SHA, file_path=FILE_PATH,
        ))
        self.assertIsNone(favorable_patch_review(
            self.storage, project_id=PROJECT_ID, run_id=RUN_ID, finding_id=FINDING_ID,
            patch_sha256=PATCH_SHA256, original_sha256="f" * 64,
            repository=REPOSITORY, commit_sha=COMMIT_SHA, file_path=FILE_PATH,
        ))


class ReviewPersistenceTests(_PolicyFixture):
    """The recorded review is immutable and its replay is the persisted row."""

    def test_the_same_review_is_recorded_once_and_the_persisted_row_is_returned(self):
        first = self._record_review()
        second = self._record_review()
        self.assertEqual(first.review_id, second.review_id)
        self.assertEqual(second.reviewed_at, first.reviewed_at)
        with closing(sqlite3.connect(self.root / "agente.db")) as connection:
            rows = connection.execute("SELECT count(*) FROM patch_reviews").fetchone()[0]
        self.assertEqual(rows, 1)

    def test_a_conflicting_row_with_the_same_identifier_is_refused(self):
        import json

        from agente_gestion.domain.patch_review import PatchReviewOutcome

        outcome = PatchReviewOutcome(
            verdict=PatchReviewVerdict.favorable,
            reason="La correccion resuelve el defecto sin alterar el contexto.",
        )
        review_id = PatchReviewService._review_id(
            PROJECT_ID, RUN_ID, FINDING_ID, PATCH_SHA256, outcome,
        )
        conflicting = {
            "review_id": review_id, "project_id": PROJECT_ID, "run_id": RUN_ID,
            "finding_id": FINDING_ID, "repository": "acme/frontend", "commit_sha": COMMIT_SHA,
            "file_path": FILE_PATH, "patch_sha256": PATCH_SHA256,
            "original_sha256": SOURCE_HASH, "verdict": "favorable",
            "reason": outcome.reason, "reviewed_by": "verifier_agent",
            "evidence_ids": [COMMIT_SHA], "reviewed_at": "2026-01-02T00:00:00+00:00",
        }
        payload = json.dumps(conflicting, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        # The table is created by the first recorded review.
        self._record_review(reason="Una revision anterior de otro contenido.")
        with closing(sqlite3.connect(self.root / "agente.db")) as connection:
            connection.execute(
                "INSERT INTO patch_reviews (review_id, project_id, run_id, finding_id, repository,"
                " commit_sha, file_path, patch_sha256, original_sha256, review_json, review_sha256,"
                " recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    review_id, PROJECT_ID, RUN_ID, FINDING_ID, "acme/frontend",
                    COMMIT_SHA, FILE_PATH, PATCH_SHA256, SOURCE_HASH, payload,
                    hashlib.sha256(payload.encode("utf-8")).hexdigest(), "2026-01-02T00:00:00+00:00",
                ),
            )
            connection.commit()
        with self.assertRaises(PatchReviewError) as caught:
            self._record_review()
        self.assertEqual(caught.exception.code, "revision_en_conflicto")
        with closing(sqlite3.connect(self.root / "agente.db")) as connection:
            stored = connection.execute(
                "SELECT repository FROM patch_reviews WHERE review_id=?", (review_id,),
            ).fetchone()
            rows = connection.execute("SELECT count(*) FROM patch_reviews").fetchone()[0]
        self.assertEqual(stored[0], "acme/frontend")
        self.assertEqual(rows, 2)


class ApprovalEligibilityServiceTests(_PolicyFixture):
    """The eligibility service is read-only and shares the gate policy."""

    def test_the_eligibility_reports_the_matched_review_without_writing(self):
        self._confirm_defect()
        review = self._record_review()
        port = _RecordingCodeActionPort()
        service = FindingDecisionService(self.storage, port)
        before_findings = self.storage.get_findings(RUN_ID)
        eligibility = self._eligibility(service)
        self.assertTrue(eligibility["eligible"])
        self.assertTrue(eligibility["authorized"])
        self.assertTrue(eligibility["defect_confirmed"])
        self.assertIsNone(eligibility["blocked_reason"])
        self.assertEqual(eligibility["patch_sha256"], PATCH_SHA256)
        self.assertEqual(eligibility["review_id"], review.review_id)
        self.assertEqual(eligibility["review"]["run_id"], RUN_ID)
        self.assertEqual(eligibility["review"]["patch_sha256"], PATCH_SHA256)
        self.assertEqual(port.calls, [])
        self.assertEqual(self.storage.get_findings(RUN_ID), before_findings)
        with closing(sqlite3.connect(self.root / "agente.db")) as connection:
            counts = tuple(connection.execute(
                f"SELECT count(*) FROM {table}",
            ).fetchone()[0] for table in (
                "finding_decisions", "finding_decision_requests", "security_audit_log",
            ))
        self.assertEqual(counts, (0, 0, 0))

    def test_the_eligibility_blocks_without_a_confirmed_defect(self):
        self._record_review()
        port = _RecordingCodeActionPort()
        service = FindingDecisionService(self.storage, port)
        eligibility = self._eligibility(service)
        self.assertFalse(eligibility["eligible"])
        self.assertFalse(eligibility["authorized"])
        self.assertFalse(eligibility["defect_confirmed"])
        self.assertEqual(eligibility["blocked_reason"], BLOCK_DEFECT_NOT_CONFIRMED)
        self.assertEqual(port.calls, [])

    def test_the_eligibility_blocks_without_a_favorable_review(self):
        self._confirm_defect()
        port = _RecordingCodeActionPort()
        service = FindingDecisionService(self.storage, port)
        eligibility = self._eligibility(service)
        self.assertFalse(eligibility["eligible"])
        self.assertEqual(eligibility["blocked_reason"], BLOCK_REVIEW_MISSING)
        self.assertIsNone(eligibility["review_id"])
        self.assertEqual(port.calls, [])

    def test_the_eligibility_blocks_an_unfavorable_review(self):
        self._confirm_defect()
        self._record_review(
            verdict=PatchReviewVerdict.unfavorable, reason="La correccion no resuelve el defecto.",
        )
        service = FindingDecisionService(self.storage, _RecordingCodeActionPort())
        eligibility = self._eligibility(service)
        self.assertFalse(eligibility["eligible"])
        self.assertEqual(eligibility["blocked_reason"], BLOCK_REVIEW_UNFAVORABLE)

    def test_the_eligibility_blocks_a_patch_that_cannot_be_identified(self):
        self._confirm_defect()
        self._record_review()
        service = FindingDecisionService(self.storage, _RecordingCodeActionPort())
        eligibility = self._eligibility(service, suggested_code_override="")
        self.assertFalse(eligibility["eligible"])
        self.assertEqual(eligibility["blocked_reason"], BLOCK_SUGGESTED_CODE_REQUIRED)
        self.assertIsNone(eligibility["patch_sha256"])

    def test_the_eligibility_blocks_an_excerpt_that_does_not_match_the_source(self):
        self._confirm_defect()
        self._record_review()
        service = FindingDecisionService(self.storage, _RecordingCodeActionPort())
        eligibility = self._eligibility(service, original_excerpt="    return attacker")
        self.assertFalse(eligibility["eligible"])
        self.assertEqual(eligibility["blocked_reason"], "fuente_no_verificable")
        self.assertIsNone(eligibility["patch_sha256"])

    def test_the_decision_gate_uses_the_same_policy(self):
        self._record_review()
        port = _RecordingCodeActionPort()
        service = FindingDecisionService(self.storage, port)
        with self.assertRaises(FindingDecisionError) as caught:
            self._decide(service)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.code, APPROVAL_REVIEW_REQUIRED_CODE)
        self.assertEqual(caught.exception.message, APPROVAL_REVIEW_REQUIRED_MESSAGE)
        self.assertEqual(port.calls, [])
        with closing(sqlite3.connect(self.root / "agente.db")) as connection:
            counts = tuple(connection.execute(
                f"SELECT count(*) FROM {table}",
            ).fetchone()[0] for table in (
                "finding_decisions", "finding_decision_requests", "security_audit_log",
            ))
        self.assertEqual(counts, (0, 0, 0))

    def test_the_approval_proceeds_when_the_whole_chain_is_present(self):
        self._confirm_defect()
        review = self._record_review()
        port = _RecordingCodeActionPort()
        service = FindingDecisionService(self.storage, port)
        result = self._decide(service, idempotency_key="g3-approve-chain-0001")
        self.assertEqual(result["decision_status"], "approved")
        self.assertEqual(len(port.calls), 1)
        self.assertEqual(self.storage.get_findings(RUN_ID)[0]["decision_status"], "approved")
        with closing(sqlite3.connect(self.root / "agente.db")) as connection:
            reason = connection.execute(
                "SELECT reason FROM security_audit_log WHERE permission_checked='web:finding_decision'",
            ).fetchone()[0]
        self.assertIn(review.review_id, reason)
        self.assertIn(PATCH_SHA256, reason)

    def test_a_rejection_does_not_require_a_review(self):
        port = _RecordingCodeActionPort()
        service = FindingDecisionService(self.storage, port)
        result = self._decide(
            service, decision="reject", idempotency_key="g3-reject-0001",
            reason="El defecto no es accionable.",
        )
        self.assertEqual(result["decision_status"], "rejected")
        self.assertEqual(port.calls, [])
        self.assertEqual(self.storage.get_findings(RUN_ID)[0]["decision_status"], "rejected")

    def test_requested_changes_do_not_require_a_review(self):
        port = _RecordingCodeActionPort()
        service = FindingDecisionService(self.storage, port)
        result = self._decide(
            service, decision="request_changes", idempotency_key="g3-changes-0001",
            reason="Ajustar la correccion antes de revisar.",
        )
        self.assertEqual(result["decision_status"], "changes_requested")
        self.assertEqual(port.calls, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
