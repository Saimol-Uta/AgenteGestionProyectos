"""Reporting contracts for repository-scoped evidence and uncertainty."""

import unittest
from datetime import datetime, timezone

from agente_gestion.application.reporting import generate_report_markdown
from agente_gestion.domain.code import CIRunResult
from agente_gestion.domain.policies import PolicyEvaluationResult, QualityRule, evaluate_policies
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
    RepositoryIdentity,
)
from agente_gestion.domain.tasks import DeterministicTaskMetrics


class MultiRepoReportingTests(unittest.TestCase):
    def _report(self):
        identity = RepositoryIdentity(id="frontend", owner="example", repo="web")
        state = RepositoryAnalysisState(
            identity=identity,
            branch="develop",
            target_commit_sha="a" * 40,
            commit_resolution_status=CommitResolutionStatus.resolved,
            access_status=RepositoryAccessStatus.allowed,
            availability_status=RepositoryAvailabilityStatus.available,
        )
        ci = CIRunResult(
            id="run-frontend-42",
            workflow_name="quality",
            workflow_path=".github/workflows/quality.yml",
            commit_sha="a" * 40,
            status="failure",
            total_tests=31,
            failed_tests=2,
            coverage_percentage=92.73,
            run_attempt=2,
            artifact_name=f"ci-quality-{'a' * 40}-run-frontend-42-2",
        )
        repo = RepositoryReportResult(
            repository_state=state,
            ci_result=ci,
            coverage_percentage=92.73,
            coverage_scope="loaded TypeScript lines excluding TSX and UI",
        )
        evaluation = evaluate_policies([
            QualityRule(id="COVERAGE", description="Coverage", metric_key="coverage_percentage", operator=">=", threshold=90),
        ], {"coverage_percentage": None})
        return FinalReport(
            project_id="sample",
            cutoff_datetime=datetime(2026, 10, 20, tzinfo=timezone.utc),
            metrics=DeterministicTaskMetrics(
                total_cards=0, included_cards=0, completed_cards=0,
                progress_percentage=None, overdue_card_ids=[], due_soon_card_ids=[],
                blocked_card_ids=[], unknown_card_ids=[], contradictory_card_ids=[],
            ),
            policy_evaluations=evaluation,
            recommendation="evidencia_insuficiente",
            summary="Resumen del proyecto.",
            human_decision=HumanDecisionState.pending,
            repository_results=[repo],
        )

    def test_markdown_keeps_per_repository_sha_workflow_attempt_metrics_and_basis(self):
        markdown = generate_report_markdown(self._report())
        for value in (
            "example/web", "a" * 40, "run-frontend-42", "intento 2",
            "31", "2", "92.7", "loaded TypeScript lines excluding TSX and UI",
            "workflow `failure`", "No se calcula cobertura global",
        ):
            self.assertIn(value, markdown)
        self.assertNotIn("Cobertura global: 92.7%", markdown)


if __name__ == "__main__":
    unittest.main()
