"""G3: durable publication of a reviewed branch as a pull request.

The human approval is the only authorization of a corrective change. The service
then creates the branch with the exact reviewed patch, persists the branch
checkpoint and the identity of the review that authorized it, and only after
that publishes one pull request against the protected ``develop`` base.

The properties under test are the ones that make a retry safe:

* the branch, the commit and the human decision are never duplicated;
* a publication is only reported as published when the typed result matches the
  authorized repository, head, base, commit and mode;
* an uncertain or refused publication is reported as partial, with the branch
  and the commit preserved, and the same idempotency key recovers it later
  without a second human decision;
* no remote call happens while the external writes are disabled.
"""

import hashlib
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agente_gestion.adapters.github_code_action_adapter import (
    CodeActionError,
    GitHubCodeActionAdapter,
)
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.application.finding_decisions import (
    APPROVAL_REVIEW_REQUIRED_CODE,
    FindingDecisionError,
    FindingDecisionService,
)
from agente_gestion.application.finding_publication import latest_finding_publication
from agente_gestion.application.patch_review import PatchReviewService
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
    RepositoryIdentity,
    RepositoryProvenance,
)
from agente_gestion.domain.run import ExecutionRun, RunStatus
from agente_gestion.domain.snapshot import RepositorySnapshotResult, WeeklySnapshot
from agente_gestion.domain.tasks import DeterministicTaskMetrics


PROJECT_ID = "G3-PUBLICATION"
RUN_ID = "run-g3-publication"
SNAPSHOT_ID = "snapshot-g3-publication"
REPORT_ID = "report-g3-publication"
FINDING_ID = "backend:f-g3-publication"
REPOSITORY = "acme/backend"
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"
FILE_PATH = "src/shared.py"
BASE_BRANCH = "release/backend"
PUBLICATION_BASE = "develop"
SOURCE = "def parse(value):\n    return value\n"
SOURCE_HASH = hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()
EXCERPT = "    return value"
SUGGESTED = "    return value.strip()"
PATCH_SHA256 = hashlib.sha256(
    SOURCE.replace(EXCERPT, SUGGESTED, 1).encode("utf-8"),
).hexdigest()
APPLIED_COMMIT = "d" * 40
PULL_REQUEST_NUMBER = 4210


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


class _StubCodeActionPort:
    """A code action port that records every branch and every publication."""

    def __init__(
        self,
        *,
        execution_mode: str = "sandbox",
        publication=None,
        publication_errors: list[Any] | None = None,
        before_publication=None,
    ):
        self.execution_mode = execution_mode
        self.branch_calls: list[dict[str, Any]] = []
        self.publication_calls: list[dict[str, Any]] = []
        self._publication = publication
        self._publication_errors = list(publication_errors or [])
        self._before_publication = before_publication

    def create_verification_branch(self, **kwargs):
        self.branch_calls.append(kwargs)
        branch = kwargs["target_branch"]
        url = (
            f"https://github.com/{kwargs['repo']}/tree/{branch}"
            if self.execution_mode == "live"
            else f"sandbox://branches/{branch}"
        )
        return BranchExecutionResult(
            execution_mode=self.execution_mode, branch_name=branch, branch_url=url,
            commit_sha_applied=APPLIED_COMMIT,
        )

    def create_pull_request(self, **kwargs):
        self.publication_calls.append(kwargs)
        if self._before_publication is not None:
            self._before_publication(kwargs)
        if self._publication_errors:
            error = self._publication_errors.pop(0)
            if error is not None:
                raise error
        if self._publication is not None:
            if callable(self._publication):
                return self._publication(kwargs)
            return self._publication
        branch = kwargs["branch_result"]
        repository = kwargs["repo"]
        if branch.execution_mode == "sandbox":
            url = f"sandbox://pulls/{repository}/{PULL_REQUEST_NUMBER}"
            state = "simulated"
        else:
            url = f"https://github.com/{repository}/pull/{PULL_REQUEST_NUMBER}"
            state = "open"
        return PullRequestExecutionResult(
            execution_mode=branch.execution_mode,
            pull_request_number=PULL_REQUEST_NUMBER,
            pull_request_url=url,
            state=state,
            repository=repository,
            base_branch=kwargs["base_branch"],
            head_branch=branch.branch_name,
            head_commit_sha=branch.commit_sha_applied,
        )


class _NoNetworkHttp:
    """Any remote call is a failure: the sandbox never reaches the network."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def request(self, method, url, headers, body=None):
        self.calls.append((method, url))
        raise AssertionError(f"Unexpected remote call: {method} {url}")


class _PublicationFixture(unittest.TestCase):
    """One persisted run, finding, source and origin shared by the workflow tests."""

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
            summary="Isolated G3 publication fixture.",
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
        suggested_code: str = SUGGESTED,
    ):
        service = PatchReviewService(self.storage, _StubVerifier(verdict, reason))
        return service.review(
            project_id=PROJECT_ID, run_id=run_id, finding=self.finding,
            source=self.source, original_excerpt=EXCERPT, suggested_code=suggested_code,
            repository=REPOSITORY, commit_sha=COMMIT_SHA, file_path=FILE_PATH,
            evidence_ids=[COMMIT_SHA],
        )

    def _confirm_defect(self) -> None:
        self.storage.save_verifications(RUN_ID, [FindingVerification(
            finding_id=FINDING_ID, verdict=VerificationVerdict.confirmed,
            reason="Defecto confirmado con evidencia.", verified_by="verifier_agent",
            evidence_ids=[COMMIT_SHA],
        )])

    def _decide_arguments(self, **overrides):
        arguments = {
            "project_id": PROJECT_ID, "run_id": RUN_ID, "finding_id": FINDING_ID,
            "candidate": self.finding, "decision": "approve",
            "idempotency_key": "g3-publication-0001", "actor_id": "gestor-local",
            "session_cookie": "synthetic-cookie", "original_excerpt": EXCERPT,
            "source": self.source, "repository": REPOSITORY, "base_branch": BASE_BRANCH,
            "origin": self.origin,
        }
        return {**arguments, **overrides}

    def _decide(self, service: FindingDecisionService, **overrides):
        return service.decide(**self._decide_arguments(**overrides))

    def _audit_rows(self) -> list[dict[str, Any]]:
        with closing(sqlite3.connect(self.root / "agente.db")) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT event_id, permission_checked, decision, reason FROM security_audit_log"
                " ORDER BY recorded_at, event_id",
            ).fetchall()
        return [dict(row) for row in rows]

    def _decision_rows(self) -> int:
        with closing(sqlite3.connect(self.root / "agente.db")) as connection:
            return connection.execute("SELECT count(*) FROM finding_decisions").fetchone()[0]


class PublicationWorkflowTests(_PublicationFixture):
    """The complete approve, branch, checkpoint and publication workflow."""

    def test_the_approval_publishes_one_pull_request_against_develop(self):
        self._confirm_defect()
        review = self._record_review()
        port = _StubCodeActionPort()
        service = FindingDecisionService(self.storage, port)

        result = self._decide(service)

        self.assertEqual(result["decision_status"], "approved")
        self.assertEqual(result["publication_status"], "published")
        self.assertEqual(result["pull_request_number"], PULL_REQUEST_NUMBER)
        self.assertEqual(result["pull_request_url"], f"sandbox://pulls/{REPOSITORY}/{PULL_REQUEST_NUMBER}")
        self.assertEqual(result["pull_request_state"], "simulated")
        self.assertIs(result["pull_request_simulated"], True)
        self.assertIsNone(result["publication_error_code"])
        self.assertIsNone(result["publication_error_message"])
        self.assertEqual(result["patch_review_id"], review.review_id)
        self.assertEqual(result["patch_sha256"], PATCH_SHA256)
        self.assertEqual(result["branch_name"], port.branch_calls[0]["target_branch"])
        self.assertEqual(result["commit_sha_applied"], APPLIED_COMMIT)

        self.assertEqual(len(port.branch_calls), 1)
        self.assertEqual(len(port.publication_calls), 1)
        publication = port.publication_calls[0]
        self.assertEqual(publication["repo"], REPOSITORY)
        self.assertEqual(publication["base_branch"], PUBLICATION_BASE)
        self.assertEqual(publication["patch_sha256"], PATCH_SHA256)
        self.assertEqual(publication["finding_id"], FINDING_ID)
        self.assertEqual(publication["actor"], "gestor-local")
        self.assertEqual(publication["branch_result"].branch_name, result["branch_name"])
        self.assertEqual(publication["branch_result"].commit_sha_applied, APPLIED_COMMIT)

    def test_the_existing_decision_keys_are_preserved(self):
        self._confirm_defect()
        self._record_review()
        service = FindingDecisionService(self.storage, _StubCodeActionPort())
        result = self._decide(service)
        for key in (
            "reused", "audit_event_id", "decision_status", "branch_name", "branch_url",
            "commit_sha_applied", "suggested_code", "improvement_summary", "execution_mode",
        ):
            self.assertIn(key, result)
        self.assertIs(result["reused"], False)
        self.assertEqual(result["execution_mode"], "sandbox")
        self.assertEqual(result["suggested_code"], SUGGESTED)

    def test_the_branch_checkpoint_is_durable_before_the_publication(self):
        self._confirm_defect()
        review = self._record_review()
        observed: dict[str, Any] = {}

        def before_publication(_kwargs):
            receipt = latest_finding_publication(
                self.storage, project_id=PROJECT_ID, run_id=RUN_ID, finding_id=FINDING_ID,
            )
            observed["receipt"] = receipt

        port = _StubCodeActionPort(before_publication=before_publication)
        service = FindingDecisionService(self.storage, port)
        result = self._decide(service)

        receipt = observed["receipt"]
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["publication_status"], "partial")
        self.assertEqual(receipt["branch_name"], result["branch_name"])
        self.assertEqual(receipt["commit_sha_applied"], APPLIED_COMMIT)
        self.assertEqual(receipt["patch_sha256"], PATCH_SHA256)
        self.assertEqual(receipt["patch_review_id"], review.review_id)
        self.assertEqual(receipt["repository"], REPOSITORY)
        self.assertEqual(receipt["base_branch"], PUBLICATION_BASE)
        self.assertEqual(result["publication_status"], "published")

    def test_a_rejection_never_publishes(self):
        service = FindingDecisionService(self.storage, _StubCodeActionPort())
        result = self._decide(service, decision="reject", idempotency_key="g3-publication-reject")
        self.assertEqual(result["decision_status"], "rejected")
        self.assertEqual(result["publication_status"], "not_requested")
        self.assertIsNone(result["pull_request_number"])
        self.assertIsNone(result["pull_request_url"])
        self.assertIsNone(result["pull_request_state"])
        self.assertIsNone(result["pull_request_simulated"])
        self.assertIsNone(result["publication_error_code"])
        self.assertIsNone(result["publication_error_message"])

    def test_the_publication_receipt_is_readable_for_the_interface(self):
        self._confirm_defect()
        review = self._record_review()
        service = FindingDecisionService(self.storage, _StubCodeActionPort())
        result = self._decide(service)

        receipt = latest_finding_publication(
            self.storage, project_id=PROJECT_ID, run_id=RUN_ID, finding_id=FINDING_ID,
        )
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["publication_status"], "published")
        self.assertEqual(receipt["pull_request_number"], PULL_REQUEST_NUMBER)
        self.assertEqual(receipt["pull_request_url"], result["pull_request_url"])
        self.assertEqual(receipt["pull_request_state"], "simulated")
        self.assertIs(receipt["pull_request_simulated"], True)
        self.assertEqual(receipt["patch_review_id"], review.review_id)
        self.assertEqual(receipt["patch_sha256"], PATCH_SHA256)
        self.assertEqual(receipt["branch_name"], result["branch_name"])
        self.assertEqual(receipt["commit_sha_applied"], result["commit_sha_applied"])
        self.assertEqual(receipt["repository"], REPOSITORY)
        self.assertEqual(receipt["base_branch"], PUBLICATION_BASE)
        self.assertIsNone(latest_finding_publication(
            self.storage, project_id=PROJECT_ID, run_id=RUN_ID, finding_id="backend:unknown",
        ))

    def test_a_historical_database_is_never_migrated_by_a_read(self):
        with closing(sqlite3.connect(self.root / "agente.db")) as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
        self.assertNotIn("finding_publications", tables)

        self.assertIsNone(latest_finding_publication(
            self.storage, project_id=PROJECT_ID, run_id=RUN_ID, finding_id=FINDING_ID,
        ))

        with closing(sqlite3.connect(self.root / "agente.db")) as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
        self.assertNotIn("finding_publications", tables)


class PublicationRefusalTests(_PublicationFixture):
    """Nothing is published without the whole authorization chain."""

    def _assert_refused(self, port, service, **overrides):
        with self.assertRaises(FindingDecisionError) as caught:
            self._decide(service, **overrides)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.code, APPROVAL_REVIEW_REQUIRED_CODE)
        self.assertEqual(port.branch_calls, [])
        self.assertEqual(port.publication_calls, [])
        self.assertIsNone(latest_finding_publication(
            self.storage, project_id=PROJECT_ID, run_id=RUN_ID, finding_id=FINDING_ID,
        ))
        self.assertEqual(self._decision_rows(), 0)

    def test_a_missing_review_blocks_the_approval(self):
        self._confirm_defect()
        port = _StubCodeActionPort()
        service = FindingDecisionService(self.storage, port)
        self._assert_refused(port, service)

    def test_a_missing_defect_confirmation_blocks_the_approval(self):
        self._record_review()
        port = _StubCodeActionPort()
        service = FindingDecisionService(self.storage, port)
        self._assert_refused(port, service)

    def test_an_unfavorable_review_blocks_the_approval(self):
        self._confirm_defect()
        self._record_review(
            verdict=PatchReviewVerdict.unfavorable, reason="La correccion no resuelve el defecto.",
        )
        port = _StubCodeActionPort()
        service = FindingDecisionService(self.storage, port)
        self._assert_refused(port, service)

    def test_a_changed_patch_blocks_the_approval(self):
        self._confirm_defect()
        self._record_review()
        port = _StubCodeActionPort()
        service = FindingDecisionService(self.storage, port)
        self._assert_refused(
            port, service, suggested_code_override="    return value.upper()",
        )

    def test_a_stale_source_commit_blocks_the_approval(self):
        self._confirm_defect()
        self._record_review()
        port = _StubCodeActionPort()
        service = FindingDecisionService(self.storage, port)
        stale = self.source.model_copy(update={"commit_sha": "e" * 40})
        self._assert_refused(port, service, source=stale)

    def test_a_protected_head_branch_is_never_published(self):
        self._confirm_defect()
        self._record_review()
        port = _StubCodeActionPort()
        service = FindingDecisionService(self.storage, port)
        result = self._decide(service, target_branch="main")

        self.assertEqual(result["decision_status"], "approved")
        self.assertEqual(result["publication_status"], "partial")
        self.assertEqual(result["publication_error_code"], "rama_cabecera_invalida")
        self.assertEqual(port.publication_calls, [])
        self.assertEqual(result["branch_name"], "main")


class PublicationPartialFailureTests(_PublicationFixture):
    """An uncertain publication keeps the branch and is recovered by a replay."""

    def _uncertain_error(self) -> CodeActionError:
        return CodeActionError(
            "pull_request_no_confirmado",
            "No se pudo confirmar si GitHub creó el pull request; no se repetirá la publicación a ciegas.",
            503,
        )

    def test_a_partial_publication_keeps_the_branch_and_reports_the_error(self):
        self._confirm_defect()
        self._record_review()
        port = _StubCodeActionPort(publication_errors=[self._uncertain_error()])
        service = FindingDecisionService(self.storage, port)

        result = self._decide(service)

        self.assertEqual(result["decision_status"], "approved")
        self.assertEqual(result["publication_status"], "partial")
        self.assertEqual(result["publication_error_code"], "pull_request_no_confirmado")
        self.assertIn("no se repetirá la publicación a ciegas", result["publication_error_message"])
        self.assertIsNone(result["pull_request_number"])
        self.assertIsNone(result["pull_request_url"])
        self.assertIsNone(result["pull_request_state"])
        self.assertIsNone(result["pull_request_simulated"])
        self.assertIsNotNone(result["branch_name"])
        self.assertEqual(result["commit_sha_applied"], APPLIED_COMMIT)
        self.assertEqual(self.storage.get_findings(RUN_ID)[0]["decision_status"], "approved")
        self.assertEqual(self.storage.get_findings(RUN_ID)[0]["branch_name"], result["branch_name"])

        receipt = latest_finding_publication(
            self.storage, project_id=PROJECT_ID, run_id=RUN_ID, finding_id=FINDING_ID,
        )
        self.assertEqual(receipt["publication_status"], "partial")
        self.assertEqual(receipt["publication_error_code"], "pull_request_no_confirmado")
        self.assertEqual(receipt["commit_sha_applied"], APPLIED_COMMIT)

    def test_the_publication_attempt_is_audited(self):
        self._confirm_defect()
        self._record_review()
        port = _StubCodeActionPort(publication_errors=[self._uncertain_error()])
        service = FindingDecisionService(self.storage, port)
        self._decide(service)

        audits = self._audit_rows()
        publications = [row for row in audits if row["permission_checked"] == "web:finding_publication"]
        self.assertEqual(len(publications), 1)
        self.assertEqual(publications[0]["decision"], "denied")
        self.assertIn("pull_request_no_confirmado", publications[0]["reason"])

    def test_the_same_idempotency_key_recovers_the_publication_without_a_second_decision(self):
        self._confirm_defect()
        self._record_review()
        port = _StubCodeActionPort(publication_errors=[self._uncertain_error()])
        service = FindingDecisionService(self.storage, port)

        first = self._decide(service)
        self.assertEqual(first["publication_status"], "partial")

        second = self._decide(service)

        self.assertIs(second["reused"], True)
        self.assertEqual(second["decision_status"], "approved")
        self.assertEqual(second["publication_status"], "published")
        self.assertEqual(second["pull_request_number"], PULL_REQUEST_NUMBER)
        self.assertEqual(second["pull_request_state"], "simulated")
        self.assertIs(second["pull_request_simulated"], True)
        self.assertIsNone(second["publication_error_code"])
        self.assertEqual(second["branch_name"], first["branch_name"])
        self.assertEqual(second["commit_sha_applied"], first["commit_sha_applied"])
        self.assertEqual(len(port.branch_calls), 1)
        self.assertEqual(len(port.publication_calls), 2)
        self.assertEqual(self._decision_rows(), 1)

        audits = self._audit_rows()
        publications = [row for row in audits if row["permission_checked"] == "web:finding_publication"]
        self.assertEqual(len(publications), 2)
        self.assertEqual(publications[-1]["decision"], "granted")
        decisions = [row for row in audits if row["permission_checked"] == "web:finding_decision"]
        self.assertEqual(len(decisions), 1)

        receipt = latest_finding_publication(
            self.storage, project_id=PROJECT_ID, run_id=RUN_ID, finding_id=FINDING_ID,
        )
        self.assertEqual(receipt["publication_status"], "published")
        self.assertEqual(receipt["pull_request_number"], PULL_REQUEST_NUMBER)

    def test_the_replay_of_a_published_publication_does_not_publish_again(self):
        self._confirm_defect()
        self._record_review()
        port = _StubCodeActionPort()
        service = FindingDecisionService(self.storage, port)

        first = self._decide(service)
        second = self._decide(service)

        self.assertIs(second["reused"], True)
        self.assertEqual(second["publication_status"], "published")
        self.assertEqual(second["pull_request_number"], first["pull_request_number"])
        self.assertEqual(len(port.branch_calls), 1)
        self.assertEqual(len(port.publication_calls), 1)
        self.assertEqual(self._decision_rows(), 1)

    def test_a_stale_review_blocks_the_recovery(self):
        self._confirm_defect()
        self._record_review()
        port = _StubCodeActionPort(publication_errors=[self._uncertain_error()])
        service = FindingDecisionService(self.storage, port)
        self._decide(service)

        self._record_review(
            verdict=PatchReviewVerdict.unfavorable, reason="Una revision posterior lo descarta.",
        )

        with self.assertRaises(FindingDecisionError) as caught:
            self._decide(service)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.code, APPROVAL_REVIEW_REQUIRED_CODE)
        self.assertEqual(len(port.publication_calls), 1)
        receipt = latest_finding_publication(
            self.storage, project_id=PROJECT_ID, run_id=RUN_ID, finding_id=FINDING_ID,
        )
        self.assertEqual(receipt["publication_status"], "partial")

    def test_a_changed_source_blocks_the_recovery(self):
        self._confirm_defect()
        self._record_review()
        port = _StubCodeActionPort(publication_errors=[self._uncertain_error()])
        service = FindingDecisionService(self.storage, port)
        self._decide(service)

        changed = CodeSourceSnapshot(
            content=SOURCE.replace("def parse", "def parse_value"), is_complete=True,
            repository=REPOSITORY, commit_sha=COMMIT_SHA, file_path=FILE_PATH,
            source_sha256=hashlib.sha256(
                SOURCE.replace("def parse", "def parse_value").encode("utf-8"),
            ).hexdigest(),
            total_lines=2,
        )
        with self.assertRaises(FindingDecisionError) as caught:
            self._decide(service, source=changed)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(len(port.publication_calls), 1)

    def test_a_recovery_that_fails_again_stays_partial_without_duplicating(self):
        self._confirm_defect()
        self._record_review()
        port = _StubCodeActionPort(publication_errors=[self._uncertain_error(), self._uncertain_error()])
        service = FindingDecisionService(self.storage, port)

        first = self._decide(service)
        second = self._decide(service)

        self.assertEqual(first["publication_status"], "partial")
        self.assertIs(second["reused"], True)
        self.assertEqual(second["publication_status"], "partial")
        self.assertEqual(second["publication_error_code"], "pull_request_no_confirmado")
        self.assertEqual(second["branch_name"], first["branch_name"])
        self.assertEqual(second["commit_sha_applied"], first["commit_sha_applied"])
        self.assertEqual(len(port.branch_calls), 1)
        self.assertEqual(len(port.publication_calls), 2)
        self.assertEqual(self._decision_rows(), 1)
        receipt = latest_finding_publication(
            self.storage, project_id=PROJECT_ID, run_id=RUN_ID, finding_id=FINDING_ID,
        )
        self.assertEqual(receipt["publication_status"], "partial")
        self.assertEqual(receipt["attempts"], 2)

    def test_the_concurrent_retry_does_not_duplicate_the_branch_or_the_decision(self):
        self._confirm_defect()
        self._record_review()
        started = threading.Event()
        release = threading.Event()
        outcomes: dict[str, Any] = {}

        class _BlockingPort(_StubCodeActionPort):
            def create_verification_branch(self, **kwargs):
                started.set()
                release.wait(10)
                return super().create_verification_branch(**kwargs)

        port = _BlockingPort()
        service = FindingDecisionService(self.storage, port)

        def first() -> None:
            try:
                outcomes["first"] = self._decide(service)
            except Exception as exc:  # pragma: no cover - the failure is asserted below
                outcomes["first"] = exc

        def second() -> None:
            started.wait(10)
            try:
                outcomes["second"] = self._decide(service)
            except Exception as exc:
                outcomes["second"] = exc
            finally:
                release.set()

        threads = [threading.Thread(target=first), threading.Thread(target=second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)

        self.assertIsInstance(outcomes["first"], dict)
        self.assertEqual(outcomes["first"]["publication_status"], "published")
        self.assertIsInstance(outcomes["second"], FindingDecisionError)
        self.assertEqual(outcomes["second"].status_code, 409)
        self.assertEqual(outcomes["second"].code, "decision_en_curso")
        self.assertEqual(len(port.branch_calls), 1)
        self.assertEqual(len(port.publication_calls), 1)
        self.assertEqual(self._decision_rows(), 1)


class PublicationTrustTests(_PublicationFixture):
    """A publication is only claimed when the typed result matches the authorization."""

    def _refused_publication(self, publication, *, execution_mode: str = "sandbox"):
        self._confirm_defect()
        self._record_review()
        port = _StubCodeActionPort(publication=publication, execution_mode=execution_mode)
        service = FindingDecisionService(self.storage, port)
        result = self._decide(service)
        self.assertEqual(result["decision_status"], "approved")
        self.assertEqual(result["publication_status"], "partial")
        self.assertIsNone(result["pull_request_number"])
        self.assertIsNone(result["pull_request_url"])
        return result, port

    def test_a_result_of_another_repository_is_refused(self):
        def publication(kwargs):
            branch = kwargs["branch_result"]
            return PullRequestExecutionResult(
                execution_mode=branch.execution_mode,
                pull_request_number=PULL_REQUEST_NUMBER,
                pull_request_url=f"sandbox://pulls/acme/other/{PULL_REQUEST_NUMBER}",
                state="simulated", repository="acme/other", base_branch=kwargs["base_branch"],
                head_branch=branch.branch_name, head_commit_sha=branch.commit_sha_applied,
            )

        result, _port = self._refused_publication(publication)
        self.assertEqual(result["publication_error_code"], "publicacion_no_coincidente")

    def test_a_result_of_another_head_branch_is_refused(self):
        def publication(kwargs):
            return PullRequestExecutionResult(
                execution_mode="sandbox", pull_request_number=PULL_REQUEST_NUMBER,
                pull_request_url=f"sandbox://pulls/{kwargs['repo']}/{PULL_REQUEST_NUMBER}",
                state="simulated", repository=kwargs["repo"], base_branch=kwargs["base_branch"],
                head_branch="quality/otra-rama", head_commit_sha=APPLIED_COMMIT,
            )

        result, _port = self._refused_publication(publication)
        self.assertEqual(result["publication_error_code"], "rama_cabecera_inconsistente")

    def test_a_result_of_another_commit_is_refused(self):
        def publication(kwargs):
            branch = kwargs["branch_result"]
            return PullRequestExecutionResult(
                execution_mode=branch.execution_mode,
                pull_request_number=PULL_REQUEST_NUMBER,
                pull_request_url=f"sandbox://pulls/{kwargs['repo']}/{PULL_REQUEST_NUMBER}",
                state="simulated", repository=kwargs["repo"], base_branch=kwargs["base_branch"],
                head_branch=branch.branch_name, head_commit_sha="e" * 40,
            )

        result, _port = self._refused_publication(publication)
        self.assertEqual(result["publication_error_code"], "commit_de_cabecera_inconsistente")

    def test_a_result_of_another_base_is_refused(self):
        def publication(kwargs):
            branch = kwargs["branch_result"]
            return PullRequestExecutionResult(
                execution_mode=branch.execution_mode,
                pull_request_number=PULL_REQUEST_NUMBER,
                pull_request_url=f"sandbox://pulls/{kwargs['repo']}/{PULL_REQUEST_NUMBER}",
                state="simulated", repository=kwargs["repo"], base_branch="main",
                head_branch=branch.branch_name, head_commit_sha=branch.commit_sha_applied,
            )

        result, _port = self._refused_publication(publication)
        self.assertEqual(result["publication_error_code"], "rama_base_no_protegida")

    def test_a_live_result_for_a_sandbox_branch_is_refused(self):
        def publication(kwargs):
            branch = kwargs["branch_result"]
            return PullRequestExecutionResult(
                execution_mode="live", pull_request_number=PULL_REQUEST_NUMBER,
                pull_request_url=f"https://github.com/{kwargs['repo']}/pull/{PULL_REQUEST_NUMBER}",
                state="open", repository=kwargs["repo"], base_branch=kwargs["base_branch"],
                head_branch=branch.branch_name, head_commit_sha=branch.commit_sha_applied,
            )

        result, _port = self._refused_publication(publication)
        self.assertEqual(result["publication_error_code"], "modo_de_publicacion_inconsistente")

    def test_an_untyped_result_is_refused(self):
        result, _port = self._refused_publication({"pull_request_number": 1})
        self.assertEqual(result["publication_error_code"], "resultado_de_pull_request_invalido")

    def test_an_untrusted_url_is_refused(self):
        def publication(kwargs):
            branch = kwargs["branch_result"]
            return PullRequestExecutionResult(
                execution_mode="live", pull_request_number=PULL_REQUEST_NUMBER,
                pull_request_url=f"https://example.invalid/pull/{PULL_REQUEST_NUMBER}",
                state="open", repository=kwargs["repo"], base_branch=kwargs["base_branch"],
                head_branch=branch.branch_name, head_commit_sha=branch.commit_sha_applied,
            )

        result, _port = self._refused_publication(publication, execution_mode="live")
        self.assertEqual(result["publication_error_code"], "url_de_pull_request_invalida")

    def test_a_port_without_the_publication_operation_never_claims_a_publication(self):
        class _LegacyPort:
            def __init__(self):
                self.calls: list[dict[str, Any]] = []

            def create_verification_branch(self, **kwargs):
                self.calls.append(kwargs)
                return BranchExecutionResult(
                    execution_mode="sandbox", branch_name=kwargs["target_branch"],
                    branch_url=f"sandbox://branches/{kwargs['target_branch']}",
                    commit_sha_applied=APPLIED_COMMIT,
                )

        self._confirm_defect()
        self._record_review()
        port = _LegacyPort()
        service = FindingDecisionService(self.storage, port)
        result = self._decide(service)
        self.assertEqual(result["decision_status"], "approved")
        self.assertEqual(result["publication_status"], "partial")
        self.assertEqual(result["publication_error_code"], "publicacion_no_disponible")
        self.assertIsNone(result["pull_request_number"])


class SandboxPublicationTests(_PublicationFixture):
    """The real adapter never reaches the network while external writes are off."""

    def test_no_remote_call_happens_when_external_writes_are_disabled(self):
        self._confirm_defect()
        self._record_review()
        http = _NoNetworkHttp()
        adapter = GitHubCodeActionAdapter(
            branches_dir=str(self.root / "branches"), repository=REPOSITORY,
            base_branch=BASE_BRANCH, external_writes_enabled=False, project_mode="fixtures",
            http_client=http, source_lookup=lambda repo, path: self.source, origin=self.origin,
        )
        service = FindingDecisionService(self.storage, adapter)

        result = self._decide(service)

        self.assertEqual(result["decision_status"], "approved")
        self.assertEqual(result["publication_status"], "published")
        self.assertEqual(result["pull_request_state"], "simulated")
        self.assertIs(result["pull_request_simulated"], True)
        self.assertIsInstance(result["pull_request_number"], int)
        self.assertEqual(http.calls, [])
        self.assertTrue(result["pull_request_url"].startswith("sandbox://pulls/"))
        self.assertEqual(result["execution_mode"], "sandbox")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
