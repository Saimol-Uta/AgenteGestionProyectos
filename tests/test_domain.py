import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from agente_gestion.domain.tasks import (
    TaskCard,
    TaskState,
    TaskMappingConfig,
    calculate_task_metrics,
)
from agente_gestion.domain.evidence import (
    EvidenceProvenance,
    EvidenceSource,
    DiffSide,
)
from agente_gestion.domain.code import (
    CodeDiffSnippet,
    CIRunResult,
    match_ci_for_commit,
)
from agente_gestion.domain.policies import (
    QualityRule,
    RuleEvaluationStatus,
    evaluate_policies,
)
from agente_gestion.domain.llm import (
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
    ModelCapabilities,
)
from agente_gestion.adapters.sources.fixtures import FixtureSourcesAdapter


class DomainMetricsAndPoliciesTestCase(unittest.TestCase):
    def setUp(self):
        self.tz = ZoneInfo("America/Guayaquil")
        self.cutoff = datetime(2026, 10, 2, 17, 0, 0, tzinfo=self.tz)

    def test_timezone_aware_cutoff_and_overdue(self):
        # Card with due date before cutoff in America/Guayaquil is overdue if pending/in_progress/blocked
        card_pending = TaskCard(
            id="T1",
            title="Tarea pendiente",
            state=TaskState.pending,
            due_date=datetime(2026, 10, 1, 12, 0, 0, tzinfo=self.tz),
            is_included=True,
        )
        # Card with due date after cutoff is NOT overdue
        card_future = TaskCard(
            id="T2",
            title="Tarea futura",
            state=TaskState.pending,
            due_date=datetime(2026, 10, 5, 12, 0, 0, tzinfo=self.tz),
            is_included=True,
        )
        metrics = calculate_task_metrics([card_pending, card_future], cutoff=self.cutoff)
        self.assertEqual(metrics.overdue_card_ids, ["T1"])
        self.assertEqual(metrics.total_cards, 2)
        self.assertEqual(metrics.included_cards, 2)

    def test_unknown_states_not_overdue(self):
        # Unknown state must NOT be marked as overdue even if due date is prior to cutoff
        card_unknown = TaskCard(
            id="T_UNK",
            title="Tarea en lista no mapeada",
            state=TaskState.unknown,
            due_date=datetime(2026, 9, 20, 10, 0, 0, tzinfo=self.tz),
            is_included=True,
        )
        metrics = calculate_task_metrics([card_unknown], cutoff=self.cutoff)
        self.assertNotIn("T_UNK", metrics.overdue_card_ids)
        self.assertIn("T_UNK", metrics.unknown_card_ids)

    def test_completed_cards_not_overdue(self):
        # Completed cards must NOT be marked as overdue even if due date < cutoff
        card_done = TaskCard(
            id="T_DONE",
            title="Tarea finalizada",
            state=TaskState.completed,
            due_date=datetime(2026, 9, 25, 10, 0, 0, tzinfo=self.tz),
            is_included=True,
        )
        metrics = calculate_task_metrics([card_done], cutoff=self.cutoff)
        self.assertNotIn("T_DONE", metrics.overdue_card_ids)
        self.assertEqual(metrics.completed_cards, 1)

    def test_zero_progress_none(self):
        # When included denominator is zero, progress is None (not 0.0)
        card_excluded = TaskCard(
            id="T_EXCL",
            title="Tarea no incluida en avance",
            state=TaskState.completed,
            is_included=False,
        )
        metrics = calculate_task_metrics([card_excluded], cutoff=self.cutoff)
        self.assertEqual(metrics.included_cards, 0)
        self.assertIsNone(metrics.progress_percentage)

        # Empty list also produces progress None
        metrics_empty = calculate_task_metrics([], cutoff=self.cutoff)
        self.assertEqual(metrics_empty.included_cards, 0)
        self.assertIsNone(metrics_empty.progress_percentage)

    def test_included_denominator_progress(self):
        # 1 completed out of 3 included => 33.3%
        cards = [
            TaskCard(id="C1", title="1", state=TaskState.completed, is_included=True),
            TaskCard(id="C2", title="2", state=TaskState.in_progress, is_included=True),
            TaskCard(id="C3", title="3", state=TaskState.pending, is_included=True),
            TaskCard(id="C4", title="4", state=TaskState.completed, is_included=False),
        ]
        metrics = calculate_task_metrics(cards, cutoff=self.cutoff)
        self.assertEqual(metrics.total_cards, 4)
        self.assertEqual(metrics.included_cards, 3)
        self.assertEqual(metrics.completed_cards, 1)
        self.assertAlmostEqual(metrics.progress_percentage, 33.3, places=1)

    def test_duplicate_card_ids_detection(self):
        cards = [
            TaskCard(id="DUP", title="D1", state=TaskState.pending, is_included=True),
            TaskCard(id="DUP", title="D2", state=TaskState.completed, is_included=True),
        ]
        with self.assertRaises(ValueError) as ctx:
            calculate_task_metrics(cards, cutoff=self.cutoff)
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_contradictory_card_signals(self):
        # Card with contradictory status detail or flags
        card = TaskCard(
            id="T_CONTR",
            title="Tarea contradictoria",
            state=TaskState.completed,
            detail="Pendiente de revision, no terminada",
            is_included=True,
            has_contradiction=True,
        )
        metrics = calculate_task_metrics([card], cutoff=self.cutoff)
        self.assertIn("T_CONTR", metrics.contradictory_card_ids)

    def test_exact_matching_commit_ci(self):
        target_sha = "abc1234"
        ci_runs = [
            CIRunResult(id="run-1", workflow_name="CI", commit_sha="other999", status="success"),
            CIRunResult(id="run-2", workflow_name="CI", commit_sha=target_sha, status="failure", failed_tests=2, total_tests=20),
        ]
        matched = match_ci_for_commit(ci_runs, target_sha)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.commit_sha, target_sha)
        self.assertEqual(matched.id, "run-2")

        # When no matching commit sha exists
        no_match = match_ci_for_commit(ci_runs, "nonexistent_sha")
        self.assertIsNone(no_match)

    def test_evidence_provenance_contract(self):
        prov = EvidenceProvenance(
            id="ev-1",
            source=EvidenceSource.github,
            project_id="proj-1",
            repository="org/repo",
            commit_sha="abc1234",
            file_path="src/main.py",
            diff_side=DiffSide.NEW,
            lines=[10, 11, 12],
            external_id="pr-42",
            timestamp=datetime(2026, 10, 2, 10, 0, 0, tzinfo=self.tz),
            payload={"patch": "@@ -10,3 +10,3 @@"},
        )
        self.assertEqual(prov.id, "ev-1")
        self.assertEqual(prov.repository, "org/repo")
        self.assertEqual(prov.diff_side, DiffSide.NEW)

    def test_policy_evaluations_pass_fail_unknown(self):
        rules = [
            QualityRule(
                id="Q1",
                description="Todas las pruebas deben pasar",
                metric_key="failed_tests",
                operator="==",
                threshold=0,
                mandatory=True,
            ),
            QualityRule(
                id="Q2",
                description="Cobertura mínima 80%",
                metric_key="coverage_percentage",
                operator=">=",
                threshold=80.0,
                mandatory=True,
            ),
            QualityRule(
                id="Q3",
                description="Revisión documental del gestor",
                metric_key="documentation_reviewed",
                operator="is_true",
                threshold=True,
                mandatory=True,
            ),
        ]

        # Case 1: Pass all
        facts_all_pass = {
            "failed_tests": 0,
            "coverage_percentage": 85.0,
            "documentation_reviewed": True,
        }
        res1 = evaluate_policies(rules, facts_all_pass)
        self.assertEqual(res1.passed_rule_ids, ["Q1", "Q2", "Q3"])
        self.assertEqual(res1.failed_rule_ids, [])
        self.assertEqual(res1.unknown_rule_ids, [])
        self.assertEqual(res1.recommended_action, "recomendar_aprobacion")

        # Case 2: Mandatory rule fails
        facts_fail = {
            "failed_tests": 2,
            "coverage_percentage": 85.0,
            "documentation_reviewed": True,
        }
        res2 = evaluate_policies(rules, facts_fail)
        self.assertIn("Q1", res2.failed_rule_ids)
        self.assertEqual(res2.recommended_action, "solicitar_cambios")

        # Case 3: Mandatory rule missing / unknown evidence
        facts_unknown = {
            "failed_tests": 0,
            "coverage_percentage": 85.0,
            "documentation_reviewed": None,  # Missing evidence
        }
        res3 = evaluate_policies(rules, facts_unknown)
        self.assertIn("Q3", res3.unknown_rule_ids)
        self.assertIn("Q3", res3.mandatory_unknown_ids)
        # Non-favorable mandatory unknown must not recommend approval
        self.assertEqual(res3.recommended_action, "evidencia_insuficiente")

    def test_llm_contract_nullable_usage(self):
        req = GenerationRequest(
            role="elaborador",
            instructions="Generar resumen semanal",
            evidences={"metrics": {"total": 5}},
            schema_name="DraftSchema",
            model="qwen3.5:2b-q4_K_M",
            max_generation_tokens=500,
        )
        res = GenerationResult(
            structured_data={"summary": "Avance 50%"},
            status=GenerationStatus.complete,
            finish_reason="stop",
            provider="ollama",
            model="qwen3.5:2b-q4_K_M",
            duration_seconds=1.2,
            prompt_tokens=None,
            completion_tokens=None,
            cost_estimate=None,
            request_ref=None,
        )
        caps = ModelCapabilities(
            context_tokens=4096,
            max_output_tokens=768,
            supports_json_schema=True,
            supports_tools=False,
            supports_streaming=True,
            reasoning_options=False,
        )
        self.assertIsNone(res.prompt_tokens)
        self.assertIsNone(res.cost_estimate)
        self.assertEqual(caps.context_tokens, 4096)

    def test_fixtures_adapter_loads_four_marked_fixtures(self):
        adapter = FixtureSourcesAdapter("fixtures")
        fixtures = adapter.list_available_fixtures()
        self.assertGreaterEqual(len(fixtures), 4)

        for fix_id in fixtures:
            project_data = adapter.load_fixture(fix_id)
            self.assertIn("project_id", project_data)
            self.assertIn("tasks", project_data)
            self.assertIn("code_diffs", project_data)
            self.assertIn("ci_runs", project_data)
            self.assertIn("policies", project_data)

    def test_fixture_ci_mismatch_behavior(self):
        adapter = FixtureSourcesAdapter("fixtures")
        diffs = adapter.get_diffs("fixture_2_ci_mismatch")
        self.assertTrue(len(diffs) > 0)
        feature_sha = diffs[0].commit_sha

        all_ci = adapter.get_ci_results("fixture_2_ci_mismatch")
        self.assertTrue(len(all_ci) > 0)
        # CI exists for another SHA, but NOT for feature_sha
        matched = match_ci_for_commit(all_ci, feature_sha)
        self.assertIsNone(matched)

    def test_fixture_empty_and_unknown_metrics(self):
        adapter = FixtureSourcesAdapter("fixtures")
        tasks = adapter.get_tasks("fixture_3_empty_and_unknown")
        metrics = calculate_task_metrics(tasks, cutoff=self.cutoff)
        # In fixture 3, no tasks are included => progress must be None
        self.assertEqual(metrics.included_cards, 0)
        self.assertIsNone(metrics.progress_percentage)
        # Unknown cards must not be marked overdue
        self.assertEqual(metrics.overdue_card_ids, [])
        self.assertGreaterEqual(len(metrics.unknown_card_ids), 2)

    def test_fixture_mandatory_unknown_policy_evaluation(self):
        adapter = FixtureSourcesAdapter("fixtures")
        policies = adapter.get_policies("fixture_4_mandatory_unknown_policies")
        ci_runs = adapter.get_ci_results("fixture_4_mandatory_unknown_policies")
        matched_ci = match_ci_for_commit(ci_runs, "d3f4a5b6c7d8")

        from agente_gestion.quality.evaluator import QualityEvaluator
        # architecture_signoff is not in extracted facts => unknown
        result = QualityEvaluator.evaluate(rules=policies, ci_run=matched_ci)
        self.assertIn("Q4_MANDATORY", result.mandatory_unknown_ids)
        self.assertEqual(result.recommended_action, "evidencia_insuficiente")

    def test_core_clean_architecture_no_forbidden_imports(self):
        import ast
        from pathlib import Path

        core_paths = [
            Path("src/agente_gestion/domain"),
            Path("src/agente_gestion/ports"),
            Path("src/agente_gestion/quality"),
            Path("src/agente_gestion/agents/context.py"),
            Path("src/agente_gestion/application/validation.py"),
        ]

        forbidden = {"sqlite3", "sqlite", "adapters", "agente_gestion.adapters", "cli"}

        for p in core_paths:
            py_files = [p] if p.is_file() else list(p.rglob("*.py"))
            for py_file in py_files:
                with open(py_file, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=str(py_file))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertNotIn(
                                alias.name.split(".")[0],
                                forbidden,
                                f"Forbidden import '{alias.name}' in core file {py_file}",
                            )
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            self.assertNotIn(
                                node.module.split(".")[0],
                                forbidden,
                                f"Forbidden from-import '{node.module}' in core file {py_file}",
                            )
                            if "adapters" in node.module:
                                self.fail(f"Forbidden adapter import in core file {py_file}")

    def test_quality_evaluator_seguridad_severity_semantics(self):
        from agente_gestion.quality.evaluator import QualityEvaluator
        from agente_gestion.domain.findings import CandidateFinding

        rule = QualityRule(
            id="Q_NO_CRIT",
            description="Zero open critical or security findings",
            metric_key="open_critical_findings",
            operator="==",
            threshold=0,
            mandatory=True,
        )

        for cat in ["Seguridad", "  Seguridad  ", "SEGURIDAD", "High", " HIGH ", "CRITICAL", " critical "]:
            finding = CandidateFinding(
                id=f"f-{cat.strip()}",
                category=cat,
                file_path="src/login.py",
                line=42,
                commit_sha="c0ffee1",
                evidence="eval(user_input)",
                suggested_fix="do not use eval",
                evidence_ids=["c0ffee1"],
            )

            # Facts extraction: unresolved severe finding MUST force open_critical_findings to None (never 0)
            facts = QualityEvaluator.extract_facts(
                ci_run=None,
                findings=[],
                unresolved_findings=[finding],
            )
            self.assertIsNone(
                facts.get("open_critical_findings"),
                f"Expected open_critical_findings to be None (unknown) for unresolved category '{cat}'",
            )

            # Evaluation: mandatory rule must NOT pass as 0; must be unknown -> evidencia_insuficiente
            eval_res = QualityEvaluator.evaluate(
                rules=[rule],
                unresolved_findings=[finding],
            )
            self.assertIn(
                "Q_NO_CRIT",
                eval_res.mandatory_unknown_ids,
                f"Rule Q_NO_CRIT should be mandatory unknown for category '{cat}'",
            )
            self.assertEqual(
                eval_res.recommended_action,
                "evidencia_insuficiente",
                f"Recommended action must be evidencia_insuficiente for unresolved category '{cat}'",
            )


if __name__ == "__main__":
    unittest.main()
