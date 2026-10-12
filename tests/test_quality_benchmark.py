"""Unit and regression tests for quality benchmark and false positive/negative evaluation (REQ-07)."""

import unittest

from agente_gestion.quality.benchmark import (
    get_standard_ground_truth_dataset,
    run_quality_benchmark,
)


class QualityBenchmarkTests(unittest.TestCase):
    def test_standard_dataset_composition(self):
        dataset = get_standard_ground_truth_dataset()
        self.assertGreaterEqual(len(dataset), 6)
        categories = {s.category for s in dataset}
        self.assertIn("visible_vulnerability", categories)
        self.assertIn("partial_absence_trap", categories)
        self.assertIn("ci_coverage_trap", categories)
        self.assertIn("line_out_of_bounds_trap", categories)
        self.assertIn("clean_code", categories)

    def test_run_quality_benchmark_metrics_and_gate(self):
        report = run_quality_benchmark()

        # Gate must pass
        self.assertTrue(report.passed_quality_gate)

        m = report.metrics
        # Exactly 0 False Positives on adversarial traps
        self.assertEqual(m.false_positives, 0)
        self.assertEqual(m.false_positive_rate, 0.0)

        # 0 False Negatives on visible defects
        self.assertEqual(m.false_negatives, 0)
        self.assertEqual(m.false_negative_rate, 0.0)

        # 100% Precision and Recall
        self.assertEqual(m.precision, 1.0)
        self.assertEqual(m.recall, 1.0)
        self.assertEqual(m.specificity, 1.0)

        # Verify True Positives and True Negatives counts
        self.assertEqual(m.true_positives, 2)
        self.assertEqual(m.true_negatives, 4)

        # Verify CI rules checked
        self.assertGreaterEqual(len(report.ci_rules_evaluated), 3)
        rule_ids = {r["rule_id"] for r in report.ci_rules_evaluated}
        self.assertIn("Q1", rule_ids)
        self.assertIn("Q2", rule_ids)
        self.assertIn("Q3", rule_ids)


if __name__ == "__main__":
    unittest.main()

