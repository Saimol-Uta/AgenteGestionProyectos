import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from agente_gestion.agents.context import (
    ContextBuilder,
    BoundedContextType,
    ContextBudgetExceededError,
)
from agente_gestion.domain.tasks import TaskCard, TaskState, DeterministicTaskMetrics
from agente_gestion.domain.code import CodeDiffSnippet, CIRunResult
from agente_gestion.domain.report import MonitoringDraft
from agente_gestion.domain.findings import (
    CandidateFinding,
    FindingCategory,
    FindingVerification,
    VerificationVerdict,
)
from agente_gestion.domain.policies import QualityRule, PolicyEvaluationResult, RuleEvaluation, RuleEvaluationStatus
from agente_gestion.application.validation import (
    SemanticValidator,
    ValidationError,
)


class ContextAndValidationTestCase(unittest.TestCase):
    def setUp(self):
        self.tz = ZoneInfo("America/Guayaquil")
        self.cutoff = datetime(2026, 10, 2, 17, 0, 0, tzinfo=self.tz)
        self.metrics = DeterministicTaskMetrics(
            total_cards=4,
            included_cards=3,
            completed_cards=1,
            progress_percentage=33.3,
            overdue_card_ids=["T2"],
            blocked_card_ids=["T3"],
            unknown_card_ids=["T_UNK"],
            contradictory_card_ids=[],
        )
        self.tasks = [
            TaskCard(id="T1", title="Diseño", state=TaskState.completed, is_included=True),
            TaskCard(id="T2", title="Auth", state=TaskState.in_progress, is_included=True, due_date=datetime(2026, 10, 1, 10, 0, 0, tzinfo=self.tz)),
            TaskCard(id="T3", title="Sync", state=TaskState.blocked, is_included=True),
            TaskCard(id="T_UNK", title="Other", state=TaskState.unknown, is_included=False),
        ]
        self.code_snippets = [
            CodeDiffSnippet(
                repository="repo-main",
                commit_sha="c0ffee1",
                file_path="src/service.py",
                diff_side="NEW",
                lines=[14, 15, 16],
                content="def compute():\n    return 1 / total\n",
            )
        ]
        self.ci_result = CIRunResult(
            id="ci-1",
            workflow_name="test-and-lint",
            commit_sha="c0ffee1",
            status="failure",
            failed_tests=1,
            total_tests=10,
            coverage_percentage=75.0,
        )

    def test_context_builder_monitoring_bounded_context(self):
        builder = ContextBuilder(default_budget_tokens=2000)
        ctx = builder.build_monitoring_context(
            project_id="p1",
            metrics=self.metrics,
            tasks=self.tasks,
            ci_result=self.ci_result,
        )
        self.assertEqual(ctx.context_type, BoundedContextType.monitoring)
        self.assertIn("metrics", ctx.data)
        self.assertEqual(ctx.data["metrics"]["completed_cards"], 1)
        self.assertFalse(ctx.is_truncated)

    def test_context_builder_budget_exceeded_no_silent_truncation(self):
        # A very tiny token budget must raise an explicit exception or flag incomplete, NEVER silently truncate critical data
        builder = ContextBuilder(default_budget_tokens=20)
        with self.assertRaises(ContextBudgetExceededError):
            builder.build_monitoring_context(
                project_id="p1",
                metrics=self.metrics,
                tasks=self.tasks,
                ci_result=self.ci_result,
                strict_budget=True,
            )

    def test_context_builder_code_bounded_context(self):
        builder = ContextBuilder(default_budget_tokens=4000)
        draft = MonitoringDraft(
            summary="Avance 33.3%, 1 prueba fallida",
            risks=["Bloqueo en sync"],
            suggested_actions=["Revisar credencial"],
            referenced_task_ids=["T2", "T3"],
        )
        ctx = builder.build_code_context(
            project_id="p1",
            draft=draft,
            criteria=["Evitar division por cero", "Cerrar recursos"],
            code_snippets=self.code_snippets,
        )
        self.assertEqual(ctx.context_type, BoundedContextType.code)
        self.assertIn("code_snippets", ctx.data)
        self.assertEqual(len(ctx.data["code_snippets"]), 1)

    def test_semantic_validator_invented_task_ids(self):
        validator = SemanticValidator()
        draft_invalid = MonitoringDraft(
            summary="Resumen inventado",
            risks=[],
            suggested_actions=[],
            referenced_task_ids=["T2", "T_INVENTED_99"],
        )
        errors = validator.validate_monitoring_draft(
            draft=draft_invalid,
            available_task_ids={"T1", "T2", "T3", "T_UNK"},
            metrics=self.metrics,
            ci_result=self.ci_result,
        )
        self.assertTrue(any("T_INVENTED_99" in err for err in errors))

    def test_semantic_validator_invented_metrics(self):
        validator = SemanticValidator()
        # Draft claims 90% progress when actual is 33.3%
        draft_invented_metrics = MonitoringDraft(
            summary="El avance actual es 90.0% con 10 tareas completadas",
            risks=[],
            suggested_actions=[],
            referenced_task_ids=["T1"],
            claimed_progress_percentage=90.0,
            claimed_completed_cards=10,
        )
        errors = validator.validate_monitoring_draft(
            draft=draft_invented_metrics,
            available_task_ids={"T1", "T2", "T3", "T_UNK"},
            metrics=self.metrics,
            ci_result=self.ci_result,
        )
        self.assertTrue(any("progress" in err.lower() or "metric" in err.lower() for err in errors))

    def test_semantic_validator_code_findings_sha_and_location(self):
        validator = SemanticValidator()
        # Finding references nonexistent line or file
        invalid_finding = CandidateFinding(
            id="f-1",
            category="division_cero",
            file_path="src/other_file.py",  # Not in snippets!
            line=999,  # Line not in snippet!
            commit_sha="c0ffee1",
            evidence="1 / total",
            suggested_fix="validar total != 0",
        )
        errors = validator.validate_candidate_findings(
            findings=[invalid_finding],
            available_snippets=self.code_snippets,
            valid_rule_ids={"Q_DIV_ZERO"},
        )
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("other_file.py" in err or "line" in err.lower() for err in errors))

    def test_semantic_validator_duplicate_finding_ids(self):
        validator = SemanticValidator()
        f1 = CandidateFinding(
            id="f-dup",
            category="division_cero",
            file_path="src/service.py",
            line=15,
            commit_sha="c0ffee1",
            evidence="return 1 / total",
            suggested_fix="if total == 0: return 0",
        )
        f2 = CandidateFinding(
            id="f-dup",
            category="division_cero",
            file_path="src/service.py",
            line=15,
            commit_sha="c0ffee1",
            evidence="return 1 / total",
            suggested_fix="if total == 0: return 0",
        )
        errors = validator.validate_candidate_findings(
            findings=[f1, f2],
            available_snippets=self.code_snippets,
            valid_rule_ids=set(),
        )
        self.assertTrue(any("duplicate" in err.lower() for err in errors))

    def test_semantic_validator_approval_recommendation_must_not_approve_failing_rules(self):
        validator = SemanticValidator()
        policy_res = PolicyEvaluationResult(
            evaluations=[
                RuleEvaluation(
                    rule_id="Q1",
                    description="Tests must pass",
                    status=RuleEvaluationStatus.fail,
                    observed_value=2,
                    mandatory=True,
                )
            ],
            passed_rule_ids=[],
            failed_rule_ids=["Q1"],
            unknown_rule_ids=[],
            mandatory_failed_ids=["Q1"],
            mandatory_unknown_ids=[],
            recommended_action="solicitar_cambios",
        )
        # Attempting to recommend approval when mandatory rule failed must fail validation
        errors = validator.validate_approval_recommendation(
            claimed_recommendation="recomendar_aprobacion",
            actual_policy_result=policy_res,
        )
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any("favorable" in err.lower() or "mandatory" in err.lower() or "fail" in err.lower() for err in errors))

    def test_context_builder_verifier_and_report_contexts(self):
        builder = ContextBuilder(default_budget_tokens=4000)
        finding = CandidateFinding(
            id="f-1",
            category="division_cero",
            file_path="src/service.py",
            line=15,
            commit_sha="c0ffee1",
            evidence="1 / total",
            suggested_fix="if total == 0: return 0",
            rule_id="Q_DIV_ZERO",
        )
        rule = QualityRule(
            id="Q_DIV_ZERO",
            description="Sin divisiones por cero",
            metric_key="open_critical_findings",
            operator="==",
            threshold=0,
            mandatory=True,
        )
        # Test verifier bounded context
        v_ctx = builder.build_verifier_context(
            project_id="p1",
            findings=[finding],
            code_snippets=self.code_snippets,
            rules=[rule],
        )
        self.assertEqual(v_ctx.context_type, BoundedContextType.verifier)
        self.assertIn("findings", v_ctx.data)
        self.assertEqual(len(v_ctx.data["findings"]), 1)

        # Test report bounded context
        draft = MonitoringDraft(
            summary="Borrador de avance",
            risks=[],
            suggested_actions=[],
            referenced_task_ids=["T1"],
        )
        policy_res = PolicyEvaluationResult(
            evaluations=[],
            passed_rule_ids=["Q_DIV_ZERO"],
            failed_rule_ids=[],
            unknown_rule_ids=[],
            mandatory_failed_ids=[],
            mandatory_unknown_ids=[],
            recommended_action="recomendar_aprobacion",
        )
        r_ctx = builder.build_report_context(
            project_id="p1",
            draft=draft,
            findings=[finding],
            policy_evaluations=policy_res,
        )
        self.assertEqual(r_ctx.context_type, BoundedContextType.report)
        self.assertIn("policy_evaluations", r_ctx.data)

    def test_context_builder_non_strict_budget_flags_incomplete(self):
        builder = ContextBuilder(default_budget_tokens=10)
        ctx = builder.build_monitoring_context(
            project_id="p1",
            metrics=self.metrics,
            tasks=self.tasks,
            ci_result=self.ci_result,
            strict_budget=False,
        )
        self.assertTrue(ctx.is_truncated)
        self.assertEqual(ctx.status, "incomplete")
        self.assertIsNotNone(ctx.incomplete_reason)
        # Data was NOT silently discarded!
        self.assertIn("metrics", ctx.data)

    def test_semantic_validator_invalid_rule_id(self):
        validator = SemanticValidator()
        finding_bad_rule = CandidateFinding(
            id="f-bad-rule",
            category="otros",
            file_path="src/service.py",
            line=15,
            commit_sha="c0ffee1",
            evidence="code",
            suggested_fix="fix",
            rule_id="RULE_NONEXISTENT",
        )
        errors = validator.validate_candidate_findings(
            findings=[finding_bad_rule],
            available_snippets=self.code_snippets,
            valid_rule_ids={"Q1", "Q2"},
        )
        self.assertTrue(any("RULE_NONEXISTENT" in err for err in errors))

    def test_semantic_validator_empty_inputs(self):
        validator = SemanticValidator()
        # Empty draft, empty tasks, empty snippets
        draft_empty = MonitoringDraft(
            summary="Empty",
            risks=[],
            suggested_actions=[],
            referenced_task_ids=[],
        )
        metrics_empty = DeterministicTaskMetrics(
            total_cards=0,
            included_cards=0,
            completed_cards=0,
            progress_percentage=None,
            overdue_card_ids=[],
            blocked_card_ids=[],
            unknown_card_ids=[],
            contradictory_card_ids=[],
        )
        errors_draft = validator.validate_monitoring_draft(
            draft=draft_empty,
            available_task_ids=set(),
            metrics=metrics_empty,
            ci_result=None,
        )
        self.assertEqual(errors_draft, [])

        errors_findings = validator.validate_candidate_findings(
            findings=[],
            available_snippets=[],
            valid_rule_ids=set(),
        )
        self.assertEqual(errors_findings, [])

    def test_semantic_validator_finding_verifications_exact_id_and_evidence_subset(self):
        validator = SemanticValidator()
        candidate = CandidateFinding(
            id="f-100",
            category="division_cero",
            file_path="src/service.py",
            line=15,
            commit_sha="c0ffee1",
            evidence="return 1 / total",
            suggested_fix="if total == 0: return 0",
            evidence_ids=["ev-1", "ev-2"],
        )

        # 1. Verification with exact finding ID and subset of evidence passes
        valid_v = FindingVerification(
            finding_id="f-100",
            verdict=VerificationVerdict.confirmed,
            reason="Confirmed division by zero vulnerability",
            evidence_ids=["ev-1"],
        )
        errors = validator.validate_finding_verifications(
            verifications=[valid_v],
            candidate_findings=[candidate],
            available_evidence_ids={"ev-1", "ev-2", "ev-3"},
        )
        self.assertEqual(errors, [])

        # 2. Verification with nonexistent finding ID fails
        invalid_id_v = FindingVerification(
            finding_id="f-nonexistent",
            verdict=VerificationVerdict.confirmed,
            reason="Unknown finding",
            evidence_ids=["ev-1"],
        )
        errors_bad_id = validator.validate_finding_verifications(
            verifications=[invalid_id_v],
            candidate_findings=[candidate],
            available_evidence_ids={"ev-1", "ev-2"},
        )
        self.assertTrue(any("nonexistent finding_id" in e for e in errors_bad_id))

        # 3. Verification referencing evidence not associated with the candidate finding fails
        invalid_ev_subset = FindingVerification(
            finding_id="f-100",
            verdict=VerificationVerdict.confirmed,
            reason="Using unrelated evidence",
            evidence_ids=["ev-3"],  # ev-3 is in context, but NOT in candidate finding!
        )
        errors_bad_subset = validator.validate_finding_verifications(
            verifications=[invalid_ev_subset],
            candidate_findings=[candidate],
            available_evidence_ids={"ev-1", "ev-2", "ev-3"},
        )
        self.assertTrue(any("not associated" in e for e in errors_bad_subset))

    def test_semantic_validator_draft_and_findings_reject_invented_evidence_ids(self):
        validator = SemanticValidator()

        # Draft with invented evidence ID
        draft_bad_ev = MonitoringDraft(
            summary="Progress ok",
            referenced_task_ids=["T1"],
            evidence_ids=["ev-invented-99"],
        )
        errors_draft = validator.validate_monitoring_draft(
            draft=draft_bad_ev,
            available_task_ids={"T1"},
            metrics=self.metrics,
            available_evidence_ids={"ev-1", "ev-2"},
        )
        self.assertTrue(any("ev-invented-99" in e for e in errors_draft))

        # Finding with invented evidence ID
        finding_bad_ev = CandidateFinding(
            id="f-1",
            category="division_cero",
            file_path="src/service.py",
            line=15,
            commit_sha="c0ffee1",
            evidence="return 1 / total",
            suggested_fix="fix",
            evidence_ids=["ev-hallucinated"],
        )
        errors_finding = validator.validate_candidate_findings(
            findings=[finding_bad_ev],
            available_snippets=self.code_snippets,
            valid_rule_ids=set(),
            available_evidence_ids={"ev-1", "ev-2"},
        )
        self.assertTrue(any("ev-hallucinated" in e for e in errors_finding))



if __name__ == "__main__":
    unittest.main()

class EvidenceScopeRegressionTests(unittest.TestCase):
    def snippet(self, **kwargs):
        return CodeDiffSnippet(repository="r", commit_sha="sha", file_path="store.ts",
                               lines=[151], content="return transaction(pool, async (client) => {", **kwargs)

    def test_partial_function_opener_is_not_file_end(self):
        snippet = self.snippet()
        self.assertEqual(snippet.evidence_scope, "snippet")
        self.assertFalse(snippet.is_complete)
        self.assertEqual(snippet.start_line, 151)
        self.assertEqual(snippet.end_line, 151)
        self.assertIsNone(snippet.total_lines)

    def test_full_file_requires_all_lines_and_explicit_completeness(self):
        snippet = CodeDiffSnippet(repository="r", commit_sha="sha", file_path="f",
                                 lines=[1, 2], content="def f():\n    pass\n",
                                 evidence_scope="full_file", is_complete=True, total_lines=2)
        self.assertTrue(snippet.is_complete)
        self.assertEqual(snippet.evidence_scope, "full_file")
        for overrides in ({"total_lines": 3}, {"lines": [2, 3]}, {"truncated": True},
                          {"evidence_scope": "diff"}, {"content": "only one line"}):
            data = snippet.model_dump()
            data.update(overrides)
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                CodeDiffSnippet.model_validate(data)

    def test_truncated_evidence_cannot_be_complete(self):
        snippet = self.snippet(evidence_scope="truncated", truncated=True)
        self.assertFalse(snippet.is_complete)
        with self.assertRaises(ValueError):
            self.snippet(is_complete=True)

    def test_budget_failure_preserves_boundaries_without_silent_omission(self):
        builder = ContextBuilder(default_budget_tokens=1)
        snippet = self.snippet()
        ctx = builder.build_code_context("p", MonitoringDraft(summary="draft"), [], [snippet])
        self.assertEqual(ctx.status, "incomplete")
        self.assertTrue(ctx.is_truncated)
        self.assertIn("Request additional context", ctx.incomplete_reason)
        self.assertEqual(ctx.data["code_snippets"][0]["content"], snippet.content)
        with self.assertRaises(ContextBudgetExceededError):
            builder.runtime_payload(ctx, "instructions", {}, 10, 100)

    def test_runtime_budget_reserves_instructions_schema_and_output(self):
        builder = ContextBuilder(default_budget_tokens=4096)
        ctx = builder.build_code_context("p", MonitoringDraft(summary="draft"), [], [self.snippet()])
        with self.assertRaises(ContextBudgetExceededError):
            builder.runtime_payload(ctx, "instructions" * 100, {}, 512, 600)

    def test_full_file_metadata_reaches_provider_payload(self):
        snippet = CodeDiffSnippet(repository="r", commit_sha="sha", file_path="f",
                                 diff_side="OLD", lines=[1], content="pass\n",
                                 evidence_scope="full_file", is_complete=True, total_lines=1)
        builder = ContextBuilder()
        ctx = builder.build_code_context("p", MonitoringDraft(summary="draft"), [], [snippet])
        payload = builder.runtime_payload(ctx, "instructions", {}, 512, 8192)
        self.assertTrue(payload["code_snippets"][0]["is_complete"])
        self.assertEqual(payload["code_snippets"][0]["diff_side"], "OLD")
