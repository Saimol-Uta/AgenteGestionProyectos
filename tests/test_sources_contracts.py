"""Focused contract tests for T1: Source errors, policy port, collection boundary, and local YAML policies."""

import math
import os
import tempfile
import traceback
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agente_gestion.domain.code import CIRunResult, CodeDiffSnippet
from agente_gestion.domain.evidence import EvidenceItem, EvidenceProvenance, EvidenceSource
from agente_gestion.domain.policies import (
    QualityRule,
    RuleEvaluationStatus,
    evaluate_policies,
)
from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.domain.tasks import TaskCard, TaskState
from agente_gestion.ports.source_errors import (
    SourceError,
    SourceErrorCategory,
    SourceProvider,
)
from agente_gestion.ports.sources import (
    CISourcePort,
    CodeSourcePort,
    PolicySourcePort,
    TaskSourcePort,
)
from agente_gestion.ports.source_collection import (
    SourceCollectionPort,
    SourceCollectionResult,
    SourceErrorInfo,
    SourceExecutionRecord,
    SourceExecutionStatus,
)
from agente_gestion.adapters.sources.fixtures import FixtureSourcesAdapter
from agente_gestion.adapters.sources.local_policy import LocalYamlPolicyAdapter


class TestSourceErrorsContract(unittest.TestCase):
    """Tests for SourceError, safe messages, and non-leakage."""

    def test_enum_providers_and_categories(self):
        self.assertEqual(SourceProvider.trello.value, "trello")
        self.assertEqual(SourceProvider.github.value, "github")
        self.assertEqual(SourceProvider.github_actions.value, "github_actions")
        self.assertEqual(SourceProvider.local_policy.value, "local_policy")

        expected_cats = {
            "authentication",
            "permission_denied",
            "rate_limit",
            "timeout",
            "network",
            "malformed_payload",
            "not_found",
            "configuration",
            "limit_exceeded",
        }
        for cat_name in expected_cats:
            self.assertIn(cat_name, SourceErrorCategory.__members__)

    def test_safe_str_and_repr_messages(self):
        err = SourceError(SourceProvider.local_policy, SourceErrorCategory.not_found)
        self.assertIsInstance(err, Exception)
        self.assertEqual(err.provider, SourceProvider.local_policy)
        self.assertEqual(err.category, SourceErrorCategory.not_found)

        # Message is fixed and predictable, no arbitrary parameters
        self.assertEqual(str(err), "Source 'local_policy' failed with error category 'not_found'.")
        self.assertIn("local_policy", repr(err))
        self.assertIn("not_found", repr(err))

    def test_no_secret_leak_in_str_repr_or_rendered_traceback(self):
        secret = "ghp_SECRET_TOKEN_SUPER_CONFIDENTIAL_12345"
        secret_path = f"/tmp/unreadable/{secret}/config.yaml"

        try:
            # Simulate adapter boundary catching an OS error with a secret path
            try:
                raise FileNotFoundError(f"Cannot access {secret_path}")
            except FileNotFoundError:
                raise SourceError(
                    SourceProvider.local_policy,
                    SourceErrorCategory.not_found,
                ) from None
        except SourceError as exc:
            rendered = traceback.format_exc()
            self.assertNotIn(secret, str(exc))
            self.assertNotIn(secret, repr(exc))
            self.assertNotIn(secret, rendered)
            self.assertIsNone(exc.__cause__)
            self.assertTrue(exc.__suppress_context__)

    def test_invalid_enum_constructor_does_not_leak_input_in_value_error(self):
        secret = "SECRET_TOKEN_SENTINEL_XYZ"
        with self.assertRaises(ValueError) as ctx:
            SourceError(secret, SourceErrorCategory.not_found)
        self.assertNotIn(secret, str(ctx.exception))

        with self.assertRaises(ValueError) as ctx2:
            SourceError(SourceProvider.trello, secret)
        self.assertNotIn(secret, str(ctx2.exception))


class TestPolicySourcePortAndFixtures(unittest.TestCase):
    """Tests for PolicySourcePort abstraction and FixtureSourcesAdapter conformance."""

    def test_abstract_port_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            PolicySourcePort()  # type: ignore

    def test_fixture_adapter_implements_policy_source_port(self):
        self.assertTrue(issubclass(FixtureSourcesAdapter, PolicySourcePort))
        self.assertTrue(issubclass(FixtureSourcesAdapter, TaskSourcePort))
        self.assertTrue(issubclass(FixtureSourcesAdapter, CodeSourcePort))
        self.assertTrue(issubclass(FixtureSourcesAdapter, CISourcePort))

        adapter = FixtureSourcesAdapter(fixtures_dir="fixtures")
        policies = adapter.get_policies("fixture_1_standard")
        self.assertIsInstance(policies, list)
        self.assertEqual(len(policies), 3)
        self.assertTrue(all(isinstance(p, QualityRule) for p in policies))

    def test_existing_fixture_snapshot_behavior_unchanged(self):
        adapter = FixtureSourcesAdapter(fixtures_dir="fixtures")
        tasks = adapter.get_tasks("fixture_1_standard")
        diffs = adapter.get_diffs("fixture_1_standard")
        runs = adapter.get_ci_results("fixture_1_standard")
        policies = adapter.get_policies("fixture_1_standard")

        now = datetime.now(timezone.utc)
        snapshot = WeeklySnapshot(
            id="snap-test-01",
            project_id="fixture_1_standard",
            cutoff_datetime=now,
            captured_at=now,
            tasks=tasks,
            code_diffs=diffs,
            ci_runs=runs,
            policies=policies,
            is_complete=True,
        )

        data = snapshot.model_dump()
        self.assertTrue(data["is_complete"])
        self.assertEqual(len(data["policies"]), 3)
        self.assertEqual(data["policies"][0]["id"], "Q1")


class TestSourceCollectionContracts(unittest.TestCase):
    """Tests for SourceCollectionPort and SourceCollectionResult consistency."""

    def test_abstract_collection_port(self):
        with self.assertRaises(TypeError):
            SourceCollectionPort()  # type: ignore

    def test_record_validation(self):
        # Valid execution record
        rec = SourceExecutionRecord(
            provider=SourceProvider.github,
            status=SourceExecutionStatus.success,
            duration_seconds=1.25,
            attempt_count=1,
        )
        self.assertEqual(rec.provider, SourceProvider.github)
        self.assertEqual(rec.status, SourceExecutionStatus.success)
        self.assertEqual(rec.duration_seconds, 1.25)
        self.assertEqual(rec.attempt_count, 1)
        self.assertIsNone(rec.error)

        # Negative duration rejected
        with self.assertRaises(ValueError):
            SourceExecutionRecord(
                provider=SourceProvider.github,
                status=SourceExecutionStatus.success,
                duration_seconds=-0.5,
                attempt_count=1,
            )

        # Infinite/NaN duration rejected
        with self.assertRaises(ValueError):
            SourceExecutionRecord(
                provider=SourceProvider.github,
                status=SourceExecutionStatus.success,
                duration_seconds=float("inf"),
                attempt_count=1,
            )

        # Attempt count < 1 rejected
        with self.assertRaises(ValueError):
            SourceExecutionRecord(
                provider=SourceProvider.github,
                status=SourceExecutionStatus.success,
                duration_seconds=0.1,
                attempt_count=0,
            )

    def test_record_errors_cannot_be_labeled_success(self):
        err_info = SourceErrorInfo(
            provider=SourceProvider.trello,
            category=SourceErrorCategory.timeout,
            message="Source 'trello' failed with error category 'timeout'.",
        )
        with self.assertRaises(ValueError):
            SourceExecutionRecord(
                provider=SourceProvider.trello,
                status=SourceExecutionStatus.success,
                duration_seconds=2.0,
                attempt_count=1,
                error=err_info,
            )

    def test_source_error_info_ignores_raw_message_and_canonicalizes(self):
        secret = "LEAK_RAW_SECRET_123"
        info = SourceErrorInfo(
            provider=SourceProvider.trello,
            category=SourceErrorCategory.timeout,
            message=secret,
        )
        self.assertNotIn(secret, info.message)
        self.assertEqual(info.message, "Source 'trello' failed with error category 'timeout'.")
        self.assertNotIn(secret, str(info.model_dump()))
        self.assertNotIn(secret, info.model_dump_json())

        with self.assertRaises(Exception):
            info.message = "MUTATED_SECRET"

    def test_record_provider_must_match_error_provider(self):
        mismatched_error = SourceErrorInfo(
            provider=SourceProvider.trello,
            category=SourceErrorCategory.timeout,
        )
        with self.assertRaises(ValueError):
            SourceExecutionRecord(
                provider=SourceProvider.github,
                status=SourceExecutionStatus.failed,
                duration_seconds=1.0,
                attempt_count=1,
                error=mismatched_error,
            )

    def test_failed_status_requires_error(self):
        with self.assertRaises(ValueError):
            SourceExecutionRecord(
                provider=SourceProvider.github,
                status=SourceExecutionStatus.failed,
                duration_seconds=1.0,
                attempt_count=1,
                error=None,
            )

    def test_collection_result_completeness_consistency(self):
        now = datetime.now(timezone.utc)
        complete_snap = WeeklySnapshot(
            id="snap-c1",
            project_id="proj",
            cutoff_datetime=now,
            captured_at=now,
            is_complete=True,
        )

        failed_record = SourceExecutionRecord(
            provider=SourceProvider.github_actions,
            status=SourceExecutionStatus.failed,
            duration_seconds=0.8,
            attempt_count=2,
            error=SourceErrorInfo(
                provider=SourceProvider.github_actions,
                category=SourceErrorCategory.network,
                message="Source 'github_actions' failed with error category 'network'.",
            ),
        )

        success_record = SourceExecutionRecord(
            provider=SourceProvider.trello,
            status=SourceExecutionStatus.success,
            duration_seconds=0.5,
            attempt_count=1,
        )

        # Incomplete/failed record cannot accompany a complete snapshot
        with self.assertRaises(ValueError):
            SourceCollectionResult(
                snapshot=complete_snap,
                evidence=[],
                records=[success_record, failed_record],
            )

        # Incomplete snapshot can accompany failed record
        incomplete_snap = WeeklySnapshot(
            id="snap-c2",
            project_id="proj",
            cutoff_datetime=now,
            captured_at=now,
            is_complete=False,
            incomplete_reason="github_actions failed",
        )
        valid_partial = SourceCollectionResult(
            snapshot=incomplete_snap,
            evidence=[],
            records=[success_record, failed_record],
        )
        self.assertFalse(valid_partial.snapshot.is_complete)
        self.assertEqual(len(valid_partial.records), 2)


class TestLocalYamlPolicyAdapter(unittest.TestCase):
    """Tests for LocalYamlPolicyAdapter and config/policies.yaml validation."""

    def test_load_default_config_policies_file(self):
        adapter = LocalYamlPolicyAdapter("config/policies.yaml")
        policies = adapter.get_policies("any_project")

        self.assertEqual(len(policies), 3)
        rule_map = {p.id: p for p in policies}
        self.assertIn("Q1", rule_map)
        self.assertIn("Q2", rule_map)
        self.assertIn("Q3", rule_map)

        # Verbatim semantic equivalence with baseline fixture
        q1 = rule_map["Q1"]
        self.assertEqual(q1.description, "Todas las pruebas unitarias deben pasar")
        self.assertEqual(q1.metric_key, "failed_tests")
        self.assertEqual(q1.operator, "==")
        self.assertEqual(q1.threshold, 0)
        self.assertEqual(q1.source, "ci")
        self.assertTrue(q1.mandatory)

        q2 = rule_map["Q2"]
        self.assertEqual(q2.description, "Cobertura de código mínima 80%")
        self.assertEqual(q2.metric_key, "coverage_percentage")
        self.assertEqual(q2.operator, ">=")
        self.assertEqual(float(q2.threshold), 80.0)
        self.assertEqual(q2.source, "ci")
        self.assertTrue(q2.mandatory)

        q3 = rule_map["Q3"]
        self.assertEqual(q3.description, "Cero hallazgos críticos abiertos")
        self.assertEqual(q3.metric_key, "open_critical_findings")
        self.assertEqual(q3.operator, "==")
        self.assertEqual(q3.threshold, 0)
        self.assertEqual(q3.source, "review")
        self.assertTrue(q3.mandatory)

    def test_missing_file_raises_safe_typed_error(self):
        adapter = LocalYamlPolicyAdapter("/nonexistent/policies_path_never_found.yaml")
        with self.assertRaises(SourceError) as ctx:
            adapter.get_policies("proj")
        self.assertEqual(ctx.exception.provider, SourceProvider.local_policy)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.not_found)
        self.assertIsNone(ctx.exception.__cause__)

    def test_malformed_yaml_raises_safe_typed_error(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tf:
            tf.write("policies: [invalid yaml: {:\n")
            temp_path = tf.name

        try:
            adapter = LocalYamlPolicyAdapter(temp_path)
            with self.assertRaises(SourceError) as ctx:
                adapter.get_policies("proj")
            self.assertEqual(ctx.exception.provider, SourceProvider.local_policy)
            self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)
            self.assertIsNone(ctx.exception.__cause__)
            self.assertNotIn(temp_path, str(ctx.exception))
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_empty_policies_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tf:
            tf.write("policies: []\n")
            temp_path = tf.name

        try:
            adapter = LocalYamlPolicyAdapter(temp_path)
            with self.assertRaises(SourceError) as ctx:
                adapter.get_policies("proj")
            self.assertEqual(ctx.exception.provider, SourceProvider.local_policy)
            self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_duplicate_rule_ids_rejected(self):
        content = """
policies:
  - id: Q1
    description: "Rule 1"
    metric_key: failed_tests
    operator: "=="
    threshold: 0
    source: ci
    mandatory: true
  - id: Q1
    description: "Duplicate Rule 1"
    metric_key: failed_tests
    operator: "=="
    threshold: 0
    source: ci
    mandatory: true
"""
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tf:
            tf.write(content)
            temp_path = tf.name

        try:
            adapter = LocalYamlPolicyAdapter(temp_path)
            with self.assertRaises(SourceError) as ctx:
                adapter.get_policies("proj")
            self.assertEqual(ctx.exception.provider, SourceProvider.local_policy)
            self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_invalid_rule_operator_rejected(self):
        content = """
policies:
  - id: Q1
    description: "Invalid operator"
    metric_key: failed_tests
    operator: "unsupported_operator"
    threshold: 0
    source: ci
    mandatory: true
"""
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tf:
            tf.write(content)
            temp_path = tf.name

        try:
            adapter = LocalYamlPolicyAdapter(temp_path)
            with self.assertRaises(SourceError) as ctx:
                adapter.get_policies("proj")
            self.assertEqual(ctx.exception.provider, SourceProvider.local_policy)
            self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_missing_required_field_rejected(self):
        content = """
policies:
  - id: Q1
    description: "Missing metric_key"
    operator: "=="
    threshold: 0
"""
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tf:
            tf.write(content)
            temp_path = tf.name

        try:
            adapter = LocalYamlPolicyAdapter(temp_path)
            with self.assertRaises(SourceError) as ctx:
                adapter.get_policies("proj")
            self.assertEqual(ctx.exception.provider, SourceProvider.local_policy)
            self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_boolean_threshold_rejected_for_numeric_comparator(self):
        content = """
policies:
  - id: Q2
    description: "Boolean threshold"
    metric_key: coverage_percentage
    operator: ">="
    threshold: true
    source: ci
    mandatory: true
"""
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tf:
            tf.write(content)
            temp_path = tf.name

        try:
            adapter = LocalYamlPolicyAdapter(temp_path)
            with self.assertRaises(SourceError) as ctx:
                adapter.get_policies("proj")
            self.assertEqual(ctx.exception.provider, SourceProvider.local_policy)
            self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_nonfinite_threshold_rejected_for_numeric_comparator(self):
        for bad_val in [".nan", ".inf", "-.inf"]:
            content = f"""
policies:
  - id: Q2
    description: "Nonfinite threshold"
    metric_key: coverage_percentage
    operator: ">="
    threshold: {bad_val}
    source: ci
    mandatory: true
"""
            with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tf:
                tf.write(content)
                temp_path = tf.name

            try:
                adapter = LocalYamlPolicyAdapter(temp_path)
                with self.assertRaises(SourceError) as ctx:
                    adapter.get_policies("proj")
                self.assertEqual(ctx.exception.provider, SourceProvider.local_policy)
                self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)


class TestEvaluatorMissingCoverageInvariance(unittest.TestCase):
    """Tests that missing coverage with Q2 results in unknown / evidencia_insuficiente."""

    def test_missing_coverage_percentage_results_in_unknown(self):
        adapter = FixtureSourcesAdapter(fixtures_dir="fixtures")
        policies = adapter.get_policies("fixture_1_standard")

        # CI facts where failed_tests=0 and open_critical_findings=0, but coverage is missing
        facts = {
            "failed_tests": 0,
            "open_critical_findings": 0,
        }

        result = evaluate_policies(policies, facts)
        self.assertIn("Q2", result.unknown_rule_ids)
        self.assertIn("Q2", result.mandatory_unknown_ids)
        self.assertEqual(result.recommended_action, "evidencia_insuficiente")


if __name__ == "__main__":
    unittest.main()
