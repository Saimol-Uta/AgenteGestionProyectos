"""Public SQLite persistence contracts for repository-scoped analysis history."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.domain.code import (
    CIRunResult,
    CiMetricsArtifactMetadata,
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
from agente_gestion.domain.repository_history import RepositoryHistoryFilter
from agente_gestion.domain.repositories import (
    CommitResolutionStatus,
    RepositoryAccessStatus,
    RepositoryAnalysisState,
    RepositoryAvailabilityStatus,
    RepositoryIdentity,
    RepositoryProvenance,
)
from agente_gestion.domain.run import ExecutionRun, RunStatus, compute_sha256
from agente_gestion.domain.snapshot import RepositorySnapshotResult, WeeklySnapshot
from agente_gestion.domain.tasks import DeterministicTaskMetrics
from agente_gestion.ports.storage import StoragePort


SHA = "a" * 40
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def _identity(repository_id="backend", owner="example", repo="api"):
    return RepositoryIdentity(id=repository_id, owner=owner, repo=repo)


def _state(identity, sha=SHA, *, access="allowed", availability="available"):
    return RepositoryAnalysisState(
        identity=identity,
        branch="develop",
        target_commit_sha=sha,
        commit_resolution_status=CommitResolutionStatus.resolved,
        access_status=RepositoryAccessStatus(access),
        availability_status=RepositoryAvailabilityStatus(availability),
    )


def _ci(
    identity,
    *,
    sha=SHA,
    run_id="9100",
    attempt=2,
    artifact_id="501",
    payload_attempt=None,
    certified=True,
    artifact_name=None,
):
    name = artifact_name or f"ci-quality-{sha.lower()}-{run_id}-{attempt}"
    metrics = None
    if certified:
        metrics = CiMetricsArtifactValues(
            repository=f"{identity.owner}/{identity.repo}",
            commit_sha=sha,
            workflow_run_id=run_id,
            workflow_run_attempt=payload_attempt,
            status="success",
            total_tests=49,
            failed_tests=0,
            coverage_percentage=None,
            coverage_metric=None,
            coverage_basis="backend TypeScript source lines; producer-declared basis",
            test_suites=None,
            e2e_status="not_implemented",
        )
        metadata = CiMetricsArtifactMetadata(
            outcome="validated",
            certifies_metrics=True,
            schema_valid=True,
            semantics_valid=True,
            identity_bound=True,
            workflow_status_matches=True,
            artifact_name=name,
            metrics=metrics,
        )
    else:
        metadata = CiMetricsArtifactMetadata(
            outcome="workflow_status_mismatch",
            certifies_metrics=False,
            reason="Workflow outcome did not match the source document.",
            schema_valid=True,
            semantics_valid=True,
            identity_bound=True,
            workflow_status_matches=False,
            artifact_name=name,
        )
    return CIRunResult(
        id=run_id,
        workflow_name="quality",
        workflow_path=".github/workflows/ci-quality.yml",
        commit_sha=sha,
        status="success",
        total_tests=49 if certified else None,
        failed_tests=0 if certified else None,
        coverage_percentage=None,
        repository_identity=identity,
        run_attempt=attempt,
        artifact_id=artifact_id,
        artifact_name=name,
        metrics_artifact=metadata,
        provenance=RepositoryProvenance(
            repository_identity=identity,
            commit_sha=sha,
            source="github_actions",
            workflow_path=".github/workflows/ci-quality.yml",
            run_id=run_id,
            run_attempt=attempt,
        ),
    )


def _code(identity, sha=SHA):
    return CodeDiffSnippet(
        repository=f"{identity.owner}/{identity.repo}",
        commit_sha=sha,
        file_path="src/shared.py",
        lines=[1],
        content="same local path and commit in independent repositories",
        repository_identity=identity,
        provenance=RepositoryProvenance(
            repository_identity=identity,
            commit_sha=sha,
            source="github",
        ),
    )


def _repository_result(identity, ci_runs=(), *, code_diffs=(), complete=True, denied=False):
    return RepositorySnapshotResult(
        repository_state=_state(
            identity,
            access="denied" if denied else "allowed",
            availability="unavailable" if denied else "available",
        ),
        code_diffs=list(code_diffs),
        ci_runs=list(ci_runs),
        is_complete=complete,
        incomplete_reason="private repository permission denied" if denied else None,
    )


def _snapshot(
    project_id="multi-project",
    repository_results=(),
    *,
    snapshot_id="snapshot-1",
    captured_at=NOW,
    code_diffs=(),
    ci_runs=(),
):
    repositories = list(repository_results)
    complete = all(item.is_complete is True for item in repositories)
    return WeeklySnapshot(
        id=snapshot_id,
        project_id=project_id,
        cutoff_datetime=NOW,
        captured_at=captured_at,
        code_diffs=list(code_diffs),
        ci_runs=list(ci_runs),
        is_complete=complete,
        incomplete_reason=None if complete else "one or more repository results are partial",
        repository_results=repositories,
    )


def _run(snapshot, run_id):
    return ExecutionRun(
        run_id=run_id,
        project_id=snapshot.project_id,
        status=RunStatus.awaiting_human,
        created_at=NOW,
        updated_at=NOW,
        snapshot_id=snapshot.id,
        config_hash=f"config-{run_id}",
        snapshot_hash=compute_sha256(snapshot),
        policy_version="v1",
    )


def _report(snapshot, run_id, report_id, *, reject_metrics=True):
    scoped = next(result for result in snapshot.repository_results if result.repository_state.identity.id == "backend")
    source_ci = scoped.ci_runs[0]
    if reject_metrics:
        report_ci = source_ci.model_copy(update={
            "total_tests": None,
            "failed_tests": None,
            "coverage_percentage": None,
            "metrics_artifact": CiMetricsArtifactMetadata(
                outcome="workflow_status_mismatch",
                certifies_metrics=False,
                reason="Workflow status mismatch rejected report eligibility.",
                artifact_name=source_ci.artifact_name,
                schema_valid=True,
                semantics_valid=True,
                identity_bound=True,
                workflow_status_matches=False,
            ),
        })
    else:
        report_ci = source_ci

    finding = CandidateFinding(
        id=f"backend::{SHA}::finding-1",
        category="otros",
        claim="A scoped finding remains linked to its source evidence.",
        evidence_ids=["ev-backend"],
        commit_sha=SHA,
        file_path="src/app.py",
        line=4,
        repository_identity=scoped.repository_state.identity,
        provenance=RepositoryProvenance(
            repository_identity=scoped.repository_state.identity,
            commit_sha=SHA,
            source="github",
        ),
    )
    verification = FindingVerification(
        finding_id=finding.id,
        verdict=VerificationVerdict.confirmed,
        reason="The exact repository-scoped evidence supports this finding.",
        verified_by="test-verifier",
        evidence_ids=["ev-backend"],
    )
    repo_report = RepositoryReportResult(
        repository_state=scoped.repository_state,
        ci_result=report_ci,
        confirmed_findings=[finding],
        verifications=[verification],
        coverage_percentage=None,
        coverage_scope="backend TypeScript source lines; producer-declared basis",
        source_is_complete=scoped.is_complete,
    )
    return FinalReport(
        project_id=snapshot.project_id,
        cutoff_datetime=snapshot.cutoff_datetime,
        metrics=DeterministicTaskMetrics(
            total_cards=0,
            included_cards=0,
            completed_cards=0,
            progress_percentage=None,
            overdue_card_ids=[],
            due_soon_card_ids=[],
            blocked_card_ids=[],
            unknown_card_ids=[],
            contradictory_card_ids=[],
        ),
        policy_evaluations=evaluate_policies([], {}),
        recommendation="evidencia_insuficiente",
        summary="Repository-scoped synthetic report.",
        human_decision=HumanDecisionState.pending,
        report_id=report_id,
        run_id=run_id,
        repository_results=[repo_report],
    )


def _evidence(project_id, identity, *, evidence_id="ev-backend", sha=SHA):
    return EvidenceItem(
        provenance=EvidenceProvenance(
            id=evidence_id,
            source=EvidenceSource.github,
            project_id=project_id,
            repository=f"{identity.owner}/{identity.repo}",
            commit_sha=sha,
            file_path="src/app.py",
            timestamp=NOW,
            payload={"kind": "synthetic-test-fixture"},
        ),
        category="diff",
        summary="Repository- and commit-bound test evidence.",
    )


class MultiRepoStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "repository-history.sqlite"
        self.store = SQLiteStorageAdapter(str(self.path))

    def test_repository_history_query_is_an_optional_public_capability(self):
        self.assertNotIn("query_repository_history", StoragePort.__abstractmethods__)
        self.assertTrue(callable(getattr(SQLiteStorageAdapter, "query_repository_history", None)))
        legacy_type = type("LegacyStorage", (StoragePort,), {
            name: (lambda *args, **kwargs: None) for name in StoragePort.__abstractmethods__
        })
        legacy = legacy_type()
        with self.assertRaises(NotImplementedError):
            legacy.query_repository_history(RepositoryHistoryFilter(project_id="p"))

    def test_public_query_keeps_source_certificate_report_eligibility_and_relationships_distinct(self):
        backend = _identity()
        frontend = _identity("frontend", "example", "web")
        snapshot = _snapshot(repository_results=[
            _repository_result(backend, [_ci(backend, payload_attempt=None)]),
            _repository_result(frontend, complete=False, denied=True),
        ])
        run = _run(snapshot, "analysis-1")
        self.store.save_run(run)
        self.store.save_snapshot(snapshot)
        self.store.save_evidence(run.run_id, [_evidence(snapshot.project_id, backend)])

        source_finding = _report(snapshot, run.run_id, "report-1").repository_results[0].confirmed_findings[0]
        source_verification = _report(snapshot, run.run_id, "report-1").repository_results[0].verifications[0]
        self.store.save_findings(run.run_id, [source_finding])
        self.store.save_verifications(run.run_id, [source_verification])
        self.store.save_report(_report(snapshot, run.run_id, "report-1"), report_id="report-1", run_id=run.run_id)

        record, = self.store.query_repository_history(RepositoryHistoryFilter(
            project_id=snapshot.project_id,
            repository_id="backend",
            commit_sha=SHA,
            ci_run_id="9100",
            attempt=2,
        ))
        self.assertEqual(record.analysis_run_id, "analysis-1")
        self.assertEqual(record.report_id, "report-1")
        self.assertEqual(record.repository_result.repository_state.target_commit_sha, SHA)
        self.assertTrue(record.repository_result.ci_runs[0].metrics_artifact.certifies_metrics)
        self.assertEqual(record.repository_result.ci_runs[0].run_attempt, 2)
        self.assertIsNone(record.repository_result.ci_runs[0].metrics_artifact.metrics.workflow_run_attempt)
        self.assertEqual(record.repository_result.ci_runs[0].metrics_artifact.metrics.failed_tests, 0)
        self.assertIsNone(record.repository_result.ci_runs[0].metrics_artifact.metrics.coverage_percentage)
        self.assertEqual(
            record.repository_result.ci_runs[0].metrics_artifact.metrics.coverage_basis,
            "backend TypeScript source lines; producer-declared basis",
        )
        self.assertIsNone(record.repository_result.ci_runs[0].metrics_artifact.metrics.test_suites)
        self.assertEqual(record.repository_result.ci_runs[0].metrics_artifact.metrics.e2e_status, "not_implemented")

        source_observation, = record.artifact_observations
        self.assertTrue(source_observation.source_metrics_artifact.certifies_metrics)
        report_ci = record.report_result.ci_result
        self.assertFalse(report_ci.metrics_artifact.certifies_metrics)
        self.assertIsNone(report_ci.metrics_artifact.metrics)
        self.assertIsNone(report_ci.total_tests)
        self.assertIsNone(record.report_result.coverage_percentage)
        self.assertEqual(record.report_result.confirmed_findings[0].evidence_ids, ["ev-backend"])
        self.assertEqual(record.report_result.verifications[0].finding_id,
                         record.report_result.confirmed_findings[0].id)
        self.assertEqual(record.report_result.verifications[0].evidence_ids, ["ev-backend"])
        self.assertEqual([item.provenance.id for item in record.evidence], ["ev-backend"])
        self.assertEqual(self.store.get_findings(run.run_id)[0]["commit_sha"], SHA)
        self.assertEqual(self.store.get_verifications(run.run_id)[0]["evidence_ids"], ["ev-backend"])

        frontend_record, = self.store.query_repository_history(RepositoryHistoryFilter(
            project_id=snapshot.project_id, repository_id="frontend",
        ))
        self.assertEqual(frontend_record.repository_result.repository_state.access_status,
                         RepositoryAccessStatus.denied)
        self.assertFalse(frontend_record.repository_result.is_complete)
        self.assertEqual(frontend_record.repository_result.incomplete_reason,
                         "private repository permission denied")
        self.assertEqual(frontend_record.repository_result.ci_runs, [])
        self.assertEqual(frontend_record.artifact_observations, [])
        self.assertEqual(self.store.query_repository_history(RepositoryHistoryFilter(
            project_id=snapshot.project_id, repository_id="backend", attempt=1,
        )), [])
        self.assertEqual(len(self.store.query_repository_history(RepositoryHistoryFilter(
            ci_run_id="9100",
        ))), 1)

        self.store.register_human_decision(
            report_id="report-1",
            decision=HumanDecisionState.approved,
            reason="human decision retained",
            run_id=run.run_id,
        )
        self.store.save_snapshot(snapshot)
        self.assertEqual(len(self.store.query_repository_history(RepositoryHistoryFilter(
            project_id=snapshot.project_id, repository_id="backend", ci_run_id="9100", attempt=2,
        ))), 1)
        self.assertEqual(self.store.get_report("report-1").human_decision, HumanDecisionState.approved)

    def test_composite_identity_and_individual_filters_do_not_collide(self):
        backend = _identity()
        frontend = _identity("frontend", "example", "web")
        backend_ci_attempt_1 = _ci(backend, attempt=1, artifact_id="500", payload_attempt="1")
        backend_ci_attempt_2 = _ci(backend, attempt=2, artifact_id="501", payload_attempt=None)
        cases = [
            ("project-one", backend, [backend_ci_attempt_1, backend_ci_attempt_2]),
            ("project-one", frontend, [_ci(frontend, artifact_id="501")]),
            ("project-two", backend, [_ci(backend, artifact_id="501")]),
        ]
        for index, (project, identity, runs) in enumerate(cases):
            snapshot = _snapshot(
                project,
                [_repository_result(identity, runs, code_diffs=[_code(identity)])],
                snapshot_id=f"snapshot-{index}",
            )
            self.store.save_snapshot(snapshot)

        combined, = self.store.query_repository_history(RepositoryHistoryFilter(
            project_id="project-one",
            repository_id="backend",
            repository_owner="EXAMPLE",
            repository_name="API",
            commit_sha=SHA,
            ci_run_id="9100",
            attempt=2,
        ))
        self.assertEqual(combined.project_id, "project-one")
        self.assertEqual([run.run_attempt for run in combined.repository_result.ci_runs], [2])
        self.assertEqual(len(combined.artifact_observations), 1)
        self.assertEqual(len(self.store.query_repository_history(RepositoryHistoryFilter(
            project_id="project-one",
        ))), 2)
        self.assertEqual(len(self.store.query_repository_history(RepositoryHistoryFilter(
            repository_id="backend",
        ))), 2)
        self.assertEqual(len(self.store.query_repository_history(RepositoryHistoryFilter(
            repository_owner="example", repository_name="api",
        ))), 2)
        self.assertEqual(len(self.store.query_repository_history(RepositoryHistoryFilter(
            commit_sha=SHA,
        ))), 3)
        ci_matches = self.store.query_repository_history(RepositoryHistoryFilter(ci_run_id="9100"))
        self.assertEqual(len(ci_matches), 3)
        self.assertEqual({
            (row.project_id, row.repository_identity.id, row.repository_result.code_diffs[0].file_path)
            for row in ci_matches
        }, {
            ("project-one", "backend", "src/shared.py"),
            ("project-one", "frontend", "src/shared.py"),
            ("project-two", "backend", "src/shared.py"),
        })
        self.assertEqual(len(self.store.query_repository_history(RepositoryHistoryFilter(
            attempt=2,
        ))), 3)
        self.assertEqual(len(self.store.query_repository_history(RepositoryHistoryFilter(
            project_id="project-one", repository_id="backend", commit_sha=SHA,
            ci_run_id="9100", attempt=1,
        ))), 1)

        identity_keys = {
            (row.project_id, observation.identity.repository_identity.id,
             observation.identity.actual_attempt)
            for row in self.store.query_repository_history(RepositoryHistoryFilter(ci_run_id="9100"))
            for observation in row.artifact_observations
        }
        self.assertEqual(identity_keys, {
            ("project-one", "backend", 1),
            ("project-one", "backend", 2),
            ("project-one", "frontend", 2),
            ("project-two", "backend", 2),
        })

    def test_legacy_explicit_repository_identity_matches_names_case_insensitively(self):
        configured = _identity()
        source_identity = _identity(owner="EXAMPLE", repo="API")
        snapshot = _snapshot(
            project_id="legacy-case",
            snapshot_id="legacy-case-identity",
            code_diffs=[_code(configured)],
            ci_runs=[_ci(source_identity)],
        )
        self.store.save_snapshot(snapshot)

        record, = self.store.query_repository_history(RepositoryHistoryFilter(
            project_id="legacy-case", repository_owner="example", repository_name="api",
        ))
        self.assertEqual(record.source_scope, "legacy")
        self.assertEqual(record.repository_identity, configured)
        self.assertEqual(record.artifact_observations[0].identity.repository_identity, source_identity)
        self.assertEqual(record.legacy_ci_runs[0].repository_identity, source_identity)

    def test_same_artifact_recollection_keeps_distinct_captures_runs_and_decisions(self):
        identity = _identity()
        first = _snapshot(repository_results=[_repository_result(identity, [_ci(identity)])],
                          snapshot_id="capture-first")
        first_run = _run(first, "analysis-first")
        self.store.save_run(first_run)
        self.store.save_snapshot(first)
        self.store.save_report(_report(first, first_run.run_id, "report-first"),
                               report_id="report-first", run_id=first_run.run_id)
        self.store.register_human_decision(
            "report-first", HumanDecisionState.approved, "keep human state", first_run.run_id,
        )

        self.store.save_snapshot(first)
        second = _snapshot(
            repository_results=[_repository_result(identity, [_ci(identity)])],
            snapshot_id="capture-second",
            captured_at=NOW + timedelta(minutes=1),
        )
        second_run = _run(second, "analysis-second")
        self.store.save_run(second_run)
        self.store.save_snapshot(second)
        self.store.save_report(_report(second, second_run.run_id, "report-second"),
                               report_id="report-second", run_id=second_run.run_id)

        rows = self.store.query_repository_history(RepositoryHistoryFilter(
            project_id="multi-project", repository_id="backend", ci_run_id="9100", attempt=2,
        ))
        self.assertEqual(len(rows), 2)
        self.assertEqual({row.capture_id for row in rows}, {
            compute_sha256(first), compute_sha256(second),
        })
        self.assertEqual({row.analysis_run_id for row in rows}, {"analysis-first", "analysis-second"})
        self.assertEqual({row.report_id for row in rows}, {"report-first", "report-second"})
        artifact_keys = {
            (
                row.artifact_observations[0].identity.project_id,
                row.artifact_observations[0].identity.repository_identity.id,
                row.artifact_observations[0].identity.commit_sha,
                row.artifact_observations[0].identity.ci_run_id,
                row.artifact_observations[0].identity.actual_attempt,
                row.artifact_observations[0].identity.artifact_id,
                row.artifact_observations[0].identity.artifact_name,
            )
            for row in rows
        }
        self.assertEqual(len(artifact_keys), 1)
        self.assertEqual(self.store.get_report("report-first").human_decision,
                         HumanDecisionState.approved)

    def test_rejected_or_misbound_snapshot_input_has_no_partial_public_writes(self):
        identity = _identity()
        valid = _snapshot(repository_results=[_repository_result(identity, [_ci(identity)])])
        self.store.save_snapshot(valid)
        initial_captures = self.store.list_snapshot_captures(valid.project_id)
        initial_latest = self.store.get_snapshot(valid.id)
        initial_database = hashlib.sha256(self.path.read_bytes()).digest()

        invalid_name_ci = _ci(identity, certified=False, artifact_name="wrong-artifact")
        malformed = _snapshot(
            repository_results=[_repository_result(identity, [invalid_name_ci])],
            snapshot_id=valid.id,
            captured_at=NOW + timedelta(minutes=1),
        )
        with self.assertRaises(ValueError):
            self.store.save_snapshot(malformed)
        self.assertEqual(self.store.get_snapshot(valid.id), initial_latest)
        self.assertEqual(self.store.list_snapshot_captures(valid.project_id), initial_captures)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(), initial_database)
        self.assertEqual(len(self.store.query_repository_history(RepositoryHistoryFilter(
            project_id=valid.project_id, repository_id="backend",
        ))), 1)

        other = _identity("frontend", "example", "web")
        misbound = _snapshot(
            repository_results=[_repository_result(identity, [_ci(identity)])],
            snapshot_id=valid.id,
            captured_at=NOW + timedelta(minutes=2),
        )
        misbound.repository_results[0].ci_runs[0].repository_identity = other
        with self.assertRaises(ValueError):
            self.store.save_snapshot(misbound)
        self.assertEqual(self.store.get_snapshot(valid.id), initial_latest)
        self.assertEqual(self.store.list_snapshot_captures(valid.project_id), initial_captures)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(), initial_database)

        conflicting = _snapshot(
            repository_results=[_repository_result(identity, [
                _ci(identity), _ci(identity, certified=False),
            ])],
            snapshot_id=valid.id,
            captured_at=NOW + timedelta(minutes=3),
        )
        with self.assertRaises(ValueError):
            self.store.save_snapshot(conflicting)
        self.assertEqual(self.store.get_snapshot(valid.id), initial_latest)
        self.assertEqual(self.store.list_snapshot_captures(valid.project_id), initial_captures)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(), initial_database)

    def test_migration_indexes_only_validated_explicit_historical_artifact_provenance(self):
        path = Path(self.temporary.name) / "explicit-legacy.sqlite"
        identity = _identity()
        valid = _snapshot(
            project_id="legacy-multirepo",
            repository_results=[_repository_result(identity, [_ci(identity, payload_attempt=None)])],
            snapshot_id="legacy-explicit-valid",
        )
        malformed_ci = _ci(
            identity, run_id="9102", attempt=1, artifact_id="502", certified=False,
            artifact_name="not-the-actions-artifact-name",
        )
        malformed = _snapshot(
            project_id="legacy-multirepo",
            repository_results=[_repository_result(identity, [malformed_ci])],
            snapshot_id="legacy-explicit-malformed",
            captured_at=NOW + timedelta(minutes=1),
        )
        snapshots = [valid, malformed]
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("""CREATE TABLE snapshots (
                snapshot_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                cutoff_datetime TEXT NOT NULL, captured_at TEXT NOT NULL,
                is_complete INTEGER NOT NULL, incomplete_reason TEXT,
                data_json TEXT NOT NULL, content_hash TEXT
            )""")
            conn.execute("""CREATE TABLE runs (
                run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, snapshot_id TEXT NOT NULL,
                config_hash TEXT NOT NULL, snapshot_hash TEXT NOT NULL, policy_version TEXT NOT NULL,
                prompt_versions TEXT, schema_versions TEXT, duration_seconds REAL DEFAULT 0.0,
                error_message TEXT, report_id TEXT, is_simulated INTEGER DEFAULT 0
            )""")
            for snapshot in snapshots:
                conn.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
                    snapshot.id, snapshot.project_id, snapshot.cutoff_datetime.isoformat(),
                    snapshot.captured_at.isoformat(), int(snapshot.is_complete),
                    snapshot.incomplete_reason, snapshot.model_dump_json(), "legacy-caller-hash",
                ))

        migrated = SQLiteStorageAdapter(str(path))
        rows = migrated.query_repository_history(RepositoryHistoryFilter(
            project_id="legacy-multirepo", repository_id="backend",
        ))
        self.assertEqual(len(rows), 2)
        valid_row = next(row for row in rows if row.snapshot_id == valid.id)
        malformed_row = next(row for row in rows if row.snapshot_id == malformed.id)
        self.assertEqual(len(valid_row.artifact_observations), 1)
        self.assertTrue(valid_row.artifact_observations[0].source_metrics_artifact.certifies_metrics)
        self.assertIsNone(valid_row.repository_result.ci_runs[0].metrics_artifact.metrics.workflow_run_attempt)
        self.assertEqual(valid_row.repository_result.ci_runs[0].run_attempt, 2)
        self.assertEqual(malformed_row.artifact_observations, [])
        self.assertEqual(malformed_row.repository_result.ci_runs[0].artifact_name,
                         "not-the-actions-artifact-name")
        self.assertFalse(malformed_row.repository_result.ci_runs[0].metrics_artifact.certifies_metrics)

    def test_read_only_new_schema_queries_without_file_writes(self):
        identity = _identity()
        snapshot = _snapshot(repository_results=[_repository_result(identity, [_ci(identity)])])
        self.store.save_snapshot(snapshot)
        before = self.path.read_bytes()
        readonly = SQLiteStorageAdapter(str(self.path), read_only=True)
        result, = readonly.query_repository_history(RepositoryHistoryFilter(
            project_id=snapshot.project_id, repository_id="backend", attempt=2,
        ))
        self.assertTrue(result.artifact_observations[0].source_metrics_artifact.certifies_metrics)
        self.assertEqual(self.path.read_bytes(), before)

    def test_read_only_old_schema_is_explicit_and_historical_unknowns_stay_unknown(self):
        path = Path(self.temporary.name) / "legacy.sqlite"
        legacy_snapshot = WeeklySnapshot(
            id="legacy-snapshot",
            project_id="legacy-project",
            cutoff_datetime=NOW,
            captured_at=NOW,
            code_diffs=[CodeDiffSnippet(
                repository="legacy/repo",
                commit_sha=SHA,
                file_path="src/legacy.py",
                lines=[1],
                content="x = 1",
            )],
            ci_runs=[CIRunResult(
                id="9900",
                workflow_name="legacy-ci",
                commit_sha=SHA,
                status="success",
                run_attempt=2,
            )],
        )
        raw_snapshot = legacy_snapshot.model_dump_json()
        legacy_run = ExecutionRun(
            run_id="legacy-analysis",
            project_id="legacy-project",
            status=RunStatus.awaiting_human,
            created_at=NOW,
            updated_at=NOW,
            snapshot_id=legacy_snapshot.id,
            config_hash="legacy-config",
            snapshot_hash=hashlib.sha256(raw_snapshot.encode("utf-8")).hexdigest(),
            policy_version="v1",
        )
        report = FinalReport(
            project_id="legacy-project",
            cutoff_datetime=NOW,
            metrics=DeterministicTaskMetrics(
                total_cards=0, included_cards=0, completed_cards=0,
                progress_percentage=None, overdue_card_ids=[], due_soon_card_ids=[],
                blocked_card_ids=[], unknown_card_ids=[], contradictory_card_ids=[],
            ),
            policy_evaluations=evaluate_policies([], {}),
            recommendation="evidencia_insuficiente",
            summary="legacy report",
            human_decision=HumanDecisionState.approved,
            report_id="legacy-report",
            run_id=legacy_run.run_id,
            decision_reason="approved before additive migration",
        )
        evidence = _evidence("legacy-project", _identity(), evidence_id="legacy-evidence")
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("""CREATE TABLE snapshots (
                snapshot_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                cutoff_datetime TEXT NOT NULL, captured_at TEXT NOT NULL,
                is_complete INTEGER NOT NULL, incomplete_reason TEXT,
                data_json TEXT NOT NULL, content_hash TEXT
            )""")
            conn.execute("""CREATE TABLE runs (
                run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, snapshot_id TEXT NOT NULL,
                config_hash TEXT NOT NULL, snapshot_hash TEXT NOT NULL, policy_version TEXT NOT NULL,
                prompt_versions TEXT, schema_versions TEXT, duration_seconds REAL DEFAULT 0.0,
                error_message TEXT, report_id TEXT, is_simulated INTEGER DEFAULT 0
            )""")
            conn.execute("""CREATE TABLE findings (
                id TEXT NOT NULL, run_id TEXT NOT NULL, category TEXT, file_path TEXT,
                line INTEGER, commit_sha TEXT, evidence TEXT, suggested_fix TEXT,
                rule_id TEXT, confidence REAL, should_escalate INTEGER DEFAULT 0,
                escalation_reasons TEXT, PRIMARY KEY(id, run_id)
            )""")
            conn.execute("""CREATE TABLE evidence (
                id TEXT NOT NULL, run_id TEXT NOT NULL, source TEXT, title TEXT,
                content_hash TEXT, data_json TEXT, PRIMARY KEY(id, run_id)
            )""")
            conn.execute("""CREATE TABLE verifications (
                finding_id TEXT NOT NULL, run_id TEXT NOT NULL, verdict TEXT NOT NULL,
                reason TEXT, verified_by TEXT, evidence_ids TEXT,
                PRIMARY KEY(finding_id, run_id)
            )""")
            conn.execute("""CREATE TABLE reports (
                report_id TEXT PRIMARY KEY, run_id TEXT, project_id TEXT NOT NULL,
                version INTEGER DEFAULT 1, created_at TEXT NOT NULL, cutoff_datetime TEXT NOT NULL,
                recommendation TEXT NOT NULL, human_decision TEXT NOT NULL, decision_reason TEXT,
                decision_timestamp TEXT, data_json TEXT NOT NULL, markdown_text TEXT
            )""")
            conn.execute("""CREATE TABLE human_decisions (
                id TEXT PRIMARY KEY, report_id TEXT NOT NULL, run_id TEXT,
                decision TEXT NOT NULL, reason TEXT, recorded_at TEXT NOT NULL
            )""")
            conn.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
                legacy_snapshot.id, legacy_snapshot.project_id, NOW.isoformat(), NOW.isoformat(),
                1, None, raw_snapshot, "legacy-caller-hash",
            ))
            conn.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                legacy_run.run_id, legacy_run.project_id, legacy_run.status.value,
                NOW.isoformat(), NOW.isoformat(), legacy_snapshot.id, legacy_run.config_hash,
                legacy_run.snapshot_hash, legacy_run.policy_version, "{}", "{}", 0.0,
                None, "legacy-report", 0,
            ))
            conn.execute("INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                "legacy-finding", legacy_run.run_id, "otros", "src/legacy.py", 1, SHA,
                "x = 1", "review manually", "Q1", 0.5, 0, "[]",
            ))
            conn.execute("INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?)", (
                "legacy-evidence", legacy_run.run_id, EvidenceSource.github.value,
                evidence.summary, "legacy-content-hash", evidence.model_dump_json(),
            ))
            conn.execute("INSERT INTO verifications VALUES (?, ?, ?, ?, ?, ?)", (
                "legacy-finding", legacy_run.run_id, "confirmed", "legacy verification",
                "legacy-verifier", json.dumps(["legacy-evidence"]),
            ))
            conn.execute("INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                "legacy-report", legacy_run.run_id, report.project_id, report.report_version,
                NOW.isoformat(), NOW.isoformat(), report.recommendation,
                report.human_decision.value, report.decision_reason, None,
                report.model_dump_json(), "legacy markdown",
            ))
            conn.execute("INSERT INTO human_decisions VALUES (?, ?, ?, ?, ?, ?)", (
                "legacy-decision", "legacy-report", legacy_run.run_id, "approved",
                "approved before additive migration", NOW.isoformat(),
            ))

        before_read_only = path.read_bytes()
        readonly = SQLiteStorageAdapter(str(path), read_only=True)
        with self.assertRaisesRegex(NotImplementedError, "writable SQLite migration"):
            readonly.query_repository_history(RepositoryHistoryFilter(project_id="legacy-project"))
        self.assertEqual(readonly.get_snapshot(legacy_snapshot.id), legacy_snapshot)
        self.assertEqual(readonly.get_report("legacy-report").human_decision, HumanDecisionState.approved)
        self.assertEqual(path.read_bytes(), before_read_only)

        migrated = SQLiteStorageAdapter(str(path))
        self.assertEqual(migrated.get_snapshot(legacy_snapshot.id), legacy_snapshot)
        self.assertEqual(migrated.get_report("legacy-report").human_decision, HumanDecisionState.approved)
        self.assertEqual(migrated.get_findings(legacy_run.run_id)[0]["finding_id"], "legacy-finding")
        self.assertEqual(migrated.get_verifications(legacy_run.run_id)[0]["evidence_ids"], ["legacy-evidence"])
        self.assertEqual(migrated.get_evidence(legacy_run.run_id)[0], evidence)
        unknown = migrated.query_repository_history(RepositoryHistoryFilter(
            project_id="legacy-project",
        ))
        self.assertEqual(len(unknown), 2)
        self.assertTrue(all(record.repository_identity is None for record in unknown))
        self.assertTrue(all(record.analysis_run_id == "legacy-analysis" for record in unknown))
        self.assertTrue(all(record.report_id == "legacy-report" for record in unknown))
        self.assertEqual(migrated.query_repository_history(RepositoryHistoryFilter(
            project_id="legacy-project", repository_id="backend",
        )), [])


if __name__ == "__main__":
    unittest.main()
