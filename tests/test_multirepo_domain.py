"""Public domain contracts for additive per-repository state and results."""

import json
import unittest
from datetime import datetime, timezone

from agente_gestion.domain.code import CIRunResult, CodeDiffSnippet
from agente_gestion.domain.findings import CandidateFinding
from agente_gestion.domain.policies import PolicyEvaluationResult
from agente_gestion.domain.report import FinalReport
from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.domain.tasks import DeterministicTaskMetrics
from agente_gestion.ports.source_collection import (
    SourceCollectionResult,
    SourceErrorInfo,
    SourceExecutionRecord,
    SourceExecutionStatus,
)
from agente_gestion.ports.source_errors import SourceErrorCategory, SourceProvider
from agente_gestion.domain.repositories import (
    CommitResolutionStatus,
    RepositoryAccessStatus,
    RepositoryAnalysisState,
    RepositoryAvailabilityStatus,
    RepositoryConfiguration,
    RepositoryIdentity,
    RepositoryProvenance,
)
from agente_gestion.domain.report import RepositoryReportResult
from agente_gestion.domain.snapshot import RepositorySnapshotResult
from agente_gestion.ports.source_collection import RepositorySourceCollectionResult


class MultiRepoDomainContractsTests(unittest.TestCase):
    def setUp(self):
        self.sha = "a" * 40
        self.identity = RepositoryIdentity(id="backend", owner="Josu-F1", repo="hormigestion-backend")
        self.state = RepositoryAnalysisState(
            identity=self.identity,
            branch="develop",
            commit_resolution_status=CommitResolutionStatus.pending,
            access_status=RepositoryAccessStatus.unknown,
            availability_status=RepositoryAvailabilityStatus.unknown,
        )
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def test_repository_configuration_and_analysis_states_preserve_unknowns(self):
        configured = RepositoryConfiguration(
            id="backend",
            owner="Josu-F1",
            repo="hormigestion-backend",
            branch="develop",
            quality_workflow_path=".github/workflows/ci.yml",
        )
        self.assertEqual(configured.id, self.identity.id)
        self.assertIsNone(configured.commit_sha)
        self.assertEqual(self.state.commit_resolution_status, CommitResolutionStatus.pending)
        self.assertIsNone(self.state.target_commit_sha)
        self.assertEqual(self.state.access_status, RepositoryAccessStatus.unknown)
        self.assertEqual(self.state.availability_status, RepositoryAvailabilityStatus.unknown)
        self.assertIsNone(self.state.model_dump()["target_commit_sha"])
        self.assertEqual(
            RepositoryAnalysisState.model_validate_json(self.state.model_dump_json()),
            self.state,
        )
        with self.assertRaises(ValueError):
            RepositoryAnalysisState(
                identity=self.identity,
                branch="develop",
                target_commit_sha=None,
                commit_resolution_status=CommitResolutionStatus.resolved,
            )

    def test_repository_configuration_rejects_invalid_identity_branch_sha_and_path(self):
        invalid = [
            {"id": "../backend", "owner": "o", "repo": "r", "branch": "develop"},
            {"id": "backend", "owner": "bad/owner", "repo": "r", "branch": "develop"},
            {"id": "backend", "owner": "o", "repo": "../r", "branch": "develop"},
            {"id": "backend", "owner": "o", "repo": "r", "branch": " "},
            {"id": "backend", "owner": "o", "repo": "r", "branch": "develop", "commit_sha": "short"},
            {
                "id": "backend", "owner": "o", "repo": "r", "branch": "develop",
                "quality_workflow_path": "../../outside.yml",
            },
        ]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                RepositoryConfiguration(**values)

    def test_code_and_ci_models_round_trip_identity_and_optional_provenance(self):
        provenance = RepositoryProvenance(
            repository_identity=self.identity,
            commit_sha=self.sha,
            source="github_actions",
            workflow_path=".github/workflows/ci.yml",
            run_id="run-123",
            run_attempt=2,
        )
        diff = CodeDiffSnippet(
            repository="Josu-F1/hormigestion-backend",
            commit_sha=self.sha,
            file_path="src/example.py",
            lines=[1],
            content="pass",
            repository_identity=self.identity,
            provenance=provenance,
        )
        ci = CIRunResult(
            id="run-123",
            workflow_name="Quality",
            commit_sha=self.sha,
            status="success",
            repository_identity=self.identity,
            workflow_path=".github/workflows/ci.yml",
            run_attempt=2,
            artifact_id="artifact-123",
            artifact_name=f"ci-quality-{self.sha}-run-123-2",
            provenance=provenance,
        )
        finding = CandidateFinding(
            id="finding-1", claim="Repository-bound finding", commit_sha=self.sha,
            file_path="src/example.py", line=1,
            repository_identity=self.identity, provenance=provenance,
        )

        loaded_diff = CodeDiffSnippet.model_validate_json(diff.model_dump_json())
        loaded_ci = CIRunResult.model_validate_json(ci.model_dump_json())
        loaded_finding = CandidateFinding.model_validate_json(finding.model_dump_json())
        self.assertEqual(loaded_diff.repository_identity, self.identity)
        self.assertEqual(loaded_diff.provenance, provenance)
        self.assertEqual(loaded_ci.run_attempt, 2)
        self.assertEqual(loaded_ci.artifact_id, "artifact-123")
        self.assertEqual(loaded_ci.provenance.repository_identity, self.identity)
        self.assertEqual(loaded_finding.repository_identity, self.identity)
        self.assertEqual(loaded_finding.provenance.commit_sha, self.sha)
        other_identity = RepositoryIdentity(id="frontend", owner="Josu-F1", repo="hormigestion-frontend")
        with self.assertRaises(ValueError):
            CIRunResult(
                id="run-123", workflow_name="Quality", commit_sha=self.sha, status="success",
                repository_identity=self.identity,
                provenance=RepositoryProvenance(repository_identity=other_identity, commit_sha=self.sha),
            )

    def test_legacy_code_ci_finding_and_snapshot_loading_keeps_provenance_unknown(self):
        legacy_diff = CodeDiffSnippet.model_validate({
            "repository": "old/repository",
            "commit_sha": "legacy-short-sha",
            "file_path": "src/old.py",
            "lines": [1],
            "content": "old",
        })
        legacy_ci = CIRunResult.model_validate({
            "id": "old-run", "workflow_name": "CI", "commit_sha": "legacy-short-sha", "status": "success",
        })
        finding = CandidateFinding(
            id="finding-1", claim="Legacy finding", commit_sha="legacy-short-sha",
            file_path="src/old.py", line=1,
        )
        snapshot = WeeklySnapshot(
            id="legacy-snapshot", project_id="legacy-project", cutoff_datetime=self.now,
            captured_at=self.now, code_diffs=[legacy_diff], ci_runs=[legacy_ci],
        )

        self.assertIsNone(legacy_diff.repository_identity)
        self.assertIsNone(legacy_diff.provenance)
        self.assertNotIn("repository_identity", legacy_diff.model_dump())
        self.assertNotIn("provenance", legacy_diff.model_dump())
        self.assertIsNone(legacy_ci.repository_identity)
        self.assertIsNone(legacy_ci.provenance)
        self.assertNotIn("repository_identity", legacy_ci.model_dump())
        self.assertNotIn("provenance", legacy_ci.model_dump())
        self.assertIsNone(finding.repository_identity)
        self.assertIsNone(finding.provenance)
        self.assertEqual(snapshot.repository_results, [])
        self.assertNotIn("repository_results", snapshot.model_dump())
        self.assertEqual(WeeklySnapshot.model_validate_json(snapshot.model_dump_json()), snapshot)

    def test_repository_results_allow_partial_failure_without_losing_valid_records(self):
        ci = CIRunResult(
            id="run-123", workflow_name="Quality", commit_sha=self.sha, status="success",
            repository_identity=self.identity,
        )
        failed_record = SourceExecutionRecord(
            provider=SourceProvider.github_actions,
            status=SourceExecutionStatus.failed,
            duration_seconds=0.2,
            error=SourceErrorInfo(
                provider=SourceProvider.github_actions,
                category=SourceErrorCategory.permission_denied,
            ),
            repository_identity=self.identity,
            target_commit_sha=self.sha,
        )
        repository_snapshot = RepositorySnapshotResult(
            repository_state=self.state,
            ci_runs=[ci],
            is_complete=False,
            incomplete_reason="CI permission state is unresolved",
        )
        repository_collection = RepositorySourceCollectionResult(
            repository_state=self.state,
            evidence=[],
            records=[failed_record],
            is_complete=False,
            incomplete_reason="GitHub Actions permission denied",
        )
        snapshot = WeeklySnapshot(
            id="partial-snapshot", project_id="project", cutoff_datetime=self.now,
            captured_at=self.now, ci_runs=[ci], is_complete=False,
            repository_results=[repository_snapshot],
        )
        collected = SourceCollectionResult(
            snapshot=snapshot,
            records=[failed_record],
            repository_results=[repository_collection],
        )

        self.assertFalse(collected.snapshot.repository_results[0].is_complete)
        self.assertEqual(collected.snapshot.repository_results[0].ci_runs[0].id, "run-123")
        self.assertEqual(collected.repository_results[0].records[0].status, SourceExecutionStatus.failed)
        self.assertEqual(collected.repository_results[0].repository_state.identity, self.identity)
        self.assertEqual(SourceCollectionResult.model_validate_json(collected.model_dump_json()), collected)

        resolved_state = RepositoryAnalysisState(
            identity=self.identity,
            branch="develop",
            target_commit_sha=self.sha,
            commit_resolution_status=CommitResolutionStatus.resolved,
        )
        wrong_commit_ci = CIRunResult(
            id="wrong-run", workflow_name="Quality", commit_sha="b" * 40, status="success",
            repository_identity=self.identity,
        )
        with self.assertRaises(ValueError):
            RepositorySnapshotResult(repository_state=resolved_state, ci_runs=[wrong_commit_ci])

    def test_final_report_holds_independent_repository_findings_without_global_primary(self):
        repository_result = RepositoryReportResult(
            repository_state=self.state,
            ci_result=None,
            coverage_percentage=None,
            coverage_scope=None,
        )
        report = FinalReport(
            project_id="project",
            cutoff_datetime=self.now,
            metrics=DeterministicTaskMetrics(total_cards=0, included_cards=0, completed_cards=0),
            policy_evaluations=PolicyEvaluationResult(
                evaluations=[], passed_rule_ids=[], failed_rule_ids=[], unknown_rule_ids=[],
                mandatory_failed_ids=[], mandatory_unknown_ids=[],
                recommended_action="evidencia_insuficiente",
            ),
            recommendation="evidencia_insuficiente",
            summary="Repository-specific results are not yet available.",
            repository_results=[repository_result],
        )
        loaded = FinalReport.model_validate_json(report.model_dump_json())
        self.assertEqual(len(loaded.repository_results), 1)
        self.assertEqual(loaded.repository_results[0].repository_state.identity, self.identity)
        self.assertIsNone(loaded.repository_results[0].coverage_percentage)
        self.assertIsNone(loaded.repository_results[0].coverage_scope)
        self.assertNotIn("repository", loaded.model_dump())

    def test_unknown_provenance_input_remains_null_and_unasserted(self):
        raw = {
            "id": "run-unknown", "workflow_name": "Quality", "commit_sha": self.sha,
            "status": "success", "repository_identity": None, "provenance": None,
            "workflow_path": None, "run_attempt": None, "artifact_id": None,
        }
        loaded = CIRunResult.model_validate(raw)
        self.assertIsNone(loaded.repository_identity)
        self.assertIsNone(loaded.provenance)
        self.assertIsNone(loaded.workflow_path)
        self.assertIsNone(loaded.run_attempt)
        serialized = json.loads(loaded.model_dump_json())
        self.assertNotIn("provenance", serialized)
        self.assertNotIn("repository_identity", serialized)
        round_tripped = CIRunResult.model_validate_json(loaded.model_dump_json())
        self.assertIsNone(round_tripped.provenance)
        self.assertIsNone(round_tripped.repository_identity)


if __name__ == "__main__":
    unittest.main()
