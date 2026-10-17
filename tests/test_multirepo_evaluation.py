"""Deterministic repository-scoped quality evaluation contracts."""

import unittest

from agente_gestion.domain.code import CIRunResult
from agente_gestion.domain.policies import QualityRule
from agente_gestion.quality.evaluator import QualityEvaluator


class MultiRepoEvaluationTests(unittest.TestCase):
    def test_unavailable_repository_evidence_is_unknown_not_zero_or_approval(self):
        rules = [
            QualityRule(
                id="CRITICAL",
                description="No unverified critical findings",
                metric_key="open_critical_findings",
                operator="==",
                threshold=0,
            ),
            QualityRule(
                id="COVERAGE",
                description="Coverage minimum",
                metric_key="coverage_percentage",
                operator=">=",
                threshold=90,
            ),
        ]
        result = QualityEvaluator.evaluate(
            rules=rules,
            ci_run=None,
            findings=[],
            source_is_complete=False,
            incomplete_reason="Frontend access denied.",
            findings_complete=False,
            metric_evidence_complete=False,
            require_ci_status=True,
        )
        by_id = {item.rule_id: item for item in result.evaluations}

        self.assertIsNone(by_id["CRITICAL"].observed_value)
        self.assertEqual(by_id["CRITICAL"].status.value, "unknown")
        self.assertIsNone(by_id["COVERAGE"].observed_value)
        self.assertEqual(by_id["COVERAGE"].status.value, "unknown")
        self.assertIn("Q_REPOSITORY_FINDINGS_INCOMPLETE", result.mandatory_unknown_ids)
        self.assertIn("Q_REPOSITORY_CI_METRICS_UNAVAILABLE", result.mandatory_unknown_ids)
        self.assertIn("Q_REPOSITORY_CI_WORKFLOW", result.mandatory_unknown_ids)
        self.assertEqual(result.recommended_action, "evidencia_insuficiente")

    def test_known_zero_counts_remain_distinct_from_missing_metric_values(self):
        known_zero = CIRunResult(
            id="known-zero",
            workflow_name="quality",
            commit_sha="a" * 40,
            status="success",
            total_tests=0,
            failed_tests=0,
        )
        known = QualityEvaluator.extract_facts(ci_run=known_zero, findings=[])
        unknown = QualityEvaluator.extract_facts(ci_run=None, findings=[])

        self.assertEqual(known["total_tests"], 0)
        self.assertEqual(known["failed_tests"], 0)
        self.assertEqual(unknown["total_tests"], None)
        self.assertEqual(unknown["failed_tests"], None)


if __name__ == "__main__":
    unittest.main()
