"""Offline deadline-window contracts: backend facts, never inferred task states."""
import io
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, mock_open, patch

from agente_gestion import cli
from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.agents.context import ContextBuilder, ContextBudgetExceededError
from agente_gestion.application.config import Settings
from agente_gestion.application.orchestrator import OrchestratorConfig, ProjectOrchestrator
from agente_gestion.application.reporting import generate_report_markdown
from agente_gestion.domain.policies import PolicyEvaluationResult
from agente_gestion.domain.report import FinalReport
from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.domain.tasks import DeterministicTaskMetrics, TaskCard, TaskState, calculate_task_metrics

CUTOFF = datetime.fromisoformat("2026-04-10T12:00:00-05:00")


def card(identifier, due=CUTOFF, **kwargs):
    return TaskCard(id=identifier, title=identifier, due_date=due,
                    state=kwargs.pop("state", TaskState.pending), **kwargs)


class DueSoonTests(unittest.TestCase):
    def test_inclusive_boundaries_offsets_naive_and_stable_ids(self):
        end = CUTOFF + timedelta(days=7)
        cards = [card("z-cutoff", CUTOFF.astimezone(timezone.utc)),
                 card("a-end", end.astimezone(timezone(timedelta(hours=3)))),
                 card("naive", CUTOFF.replace(tzinfo=None)),
                 card("overdue", CUTOFF - timedelta(microseconds=1)),
                 card("outside", end + timedelta(microseconds=1))]
        before = [c.model_dump() for c in cards]
        metrics = calculate_task_metrics(cards, CUTOFF)
        self.assertEqual(metrics.due_soon_card_ids, ["a-end", "naive", "z-cutoff"])
        self.assertEqual(metrics.overdue_card_ids, ["overdue"])
        self.assertEqual(calculate_task_metrics(list(reversed(cards)), CUTOFF).model_dump(), metrics.model_dump())
        self.assertEqual([c.model_dump() for c in cards], before)
        self.assertEqual(calculate_task_metrics(cards, CUTOFF.replace(tzinfo=None)).model_dump(), metrics.model_dump())

    def test_zero_window_and_custom_window(self):
        cards = [card("exact"), card("after", CUTOFF + timedelta(microseconds=1)),
                 card("day2", CUTOFF + timedelta(days=2))]
        self.assertEqual(calculate_task_metrics(cards, CUTOFF, due_soon_days=0).due_soon_card_ids, ["exact"])
        self.assertEqual(calculate_task_metrics(cards, CUTOFF, due_soon_days=2).due_soon_card_ids,
                         ["after", "day2", "exact"])

    def test_shared_deadline_eligibility_preserves_other_metrics(self):
        for due in (CUTOFF, CUTOFF - timedelta(days=1)):
            with self.subTest(due=due):
                cards = [card("pending", due), card("active", due, state=TaskState.in_progress),
                         card("blocked", due, state=TaskState.blocked),
                         card("done", due, state=TaskState.completed),
                         card("unknown", due, state=TaskState.unknown),
                         card("excluded", due, is_included=False),
                         card("archived", due, is_archived=True),
                         card("raw-complete", due, due_complete=True, has_contradiction=True),
                         card("contradiction", due, due_complete=False, has_contradiction=True),
                         card("missing", None)]
                metrics = calculate_task_metrics(cards, CUTOFF)
                expected = ["active", "blocked", "contradiction", "pending"]
                self.assertEqual(metrics.due_soon_card_ids, expected if due == CUTOFF else [])
                self.assertEqual(metrics.overdue_card_ids, expected if due < CUTOFF else [])
                self.assertEqual((metrics.total_cards, metrics.included_cards, metrics.completed_cards), (10, 9, 1))
                self.assertEqual(metrics.progress_percentage, 11.1)
                self.assertEqual(metrics.blocked_card_ids, ["blocked"])
                self.assertEqual(metrics.unknown_card_ids, ["unknown"])
                self.assertEqual(metrics.contradictory_card_ids, ["contradiction", "raw-complete"])
                self.assertEqual(cards[7].state, TaskState.pending)

    def test_empty_legacy_zero_denominator_and_duplicates(self):
        legacy = DeterministicTaskMetrics(total_cards=0, included_cards=0, completed_cards=0)
        self.assertEqual(legacy.due_soon_card_ids, [])
        for cards in ([], [card("excluded", is_included=False)]):
            metrics = calculate_task_metrics(cards, CUTOFF)
            self.assertIsNone(metrics.progress_percentage)
            self.assertEqual(metrics.due_soon_card_ids, [])
            self.assertEqual(metrics.overdue_card_ids, [])
        with self.assertRaisesRegex(ValueError, "Duplicate card ID"):
            calculate_task_metrics([card("same"), card("same")], CUTOFF)

    def test_invalid_windows_rejected_at_all_entry_points(self):
        with patch.dict(os.environ, {}, clear=True):
            for invalid in (-1, True, False, 1.0, 1.5, None, "bad"):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(ValueError):
                        calculate_task_metrics([], CUTOFF, due_soon_days=invalid)
                    with self.assertRaises(ValueError):
                        OrchestratorConfig(due_soon_days=invalid)
                    with self.assertRaises(ValueError):
                        Settings(_env_file=None, due_soon_days=invalid)

    def test_settings_yaml_env_override_precedence(self):
        with patch.dict(os.environ, {}, clear=True), patch("builtins.open", mock_open(read_data="execution:\n  due_soon_days: 3\n")), patch("agente_gestion.application.config.os.path.exists", return_value=True):
            self.assertEqual(Settings(_env_file=None).due_soon_days, 7)
            self.assertEqual(Settings(_env_file=None, _yaml_file="synthetic.yaml").due_soon_days, 3)
            with patch.dict(os.environ, {"DUE_SOON_DAYS": "2"}):
                self.assertEqual(Settings(_env_file=None, _yaml_file="synthetic.yaml").due_soon_days, 2)
                self.assertEqual(Settings(_env_file=None, _yaml_file="synthetic.yaml", due_soon_days=0).due_soon_days, 0)
            for invalid in ("-1", "true", "1.0"):
                with patch.dict(os.environ, {"DUE_SOON_DAYS": invalid}), self.assertRaises(ValueError):
                    Settings(_env_file=None)

    def test_cli_zero_override_and_absent_option(self):
        for option, expected in ((["--due-soon-days", "0"], 0), ([], 4)):
            with self.subTest(option=option), patch.dict(os.environ, {"DUE_SOON_DAYS": "4"}, clear=True):
                args = cli.build_parser().parse_args(["run", "--fake", *option])
                args.config = None
                args.env_file = "/dev/null"
                captured = []
                def stop_before_execution(config, **kwargs):
                    captured.append(config)
                    raise RuntimeError("stop: no storage or providers")
                with patch.object(cli, "_build_orchestrator", side_effect=stop_before_execution), patch("sys.stderr", new_callable=io.StringIO):
                    self.assertEqual(cli.run_command(args), 2)
                self.assertEqual(captured[0].due_soon_days, expected)

    def test_config_hash_and_cli_wiring(self):
        self.assertEqual(OrchestratorConfig().due_soon_days, 7)
        self.assertEqual(OrchestratorConfig().to_hash(), OrchestratorConfig(due_soon_days=7).to_hash())
        self.assertNotEqual(OrchestratorConfig(due_soon_days=0).to_hash(), OrchestratorConfig(due_soon_days=1).to_hash())
        with patch.dict(os.environ, {}, clear=True), patch.object(cli, "SQLiteStorageAdapter"), patch.object(cli, "ArtifactStorageAdapter"):
            config = Settings(_env_file=None, is_fake=True, due_soon_days=0)
            orchestrator, *_ = cli.initialize_pipeline(config)
        self.assertEqual(orchestrator.config.due_soon_days, 0)

    def test_orchestrator_uses_snapshot_cutoff_and_configured_window(self):
        storage, artifacts = MagicMock(), MagicMock()
        storage.find_run_by_fingerprint.return_value = None
        snapshot = WeeklySnapshot(id="snapshot", project_id="p", cutoff_datetime=CUTOFF,
                                  captured_at=CUTOFF, tasks=[card("exact"), card("tomorrow", CUTOFF + timedelta(days=1))])
        provider = FakeProvider()
        orchestrator = ProjectOrchestrator(storage, artifacts, provider, provider, provider, provider,
                                          config=OrchestratorConfig(due_soon_days=0, is_simulated=True))
        with patch("agente_gestion.application.orchestrator.calculate_task_metrics", wraps=calculate_task_metrics) as calculate:
            orchestrator.execute(snapshot=snapshot)
        self.assertEqual(calculate.call_args.kwargs["cutoff"], CUTOFF)
        self.assertEqual(calculate.call_args.kwargs["due_soon_days"], 0)
        self.assertEqual(calculate_task_metrics(**calculate.call_args.kwargs).due_soon_card_ids, ["exact"])

    def test_monitoring_requires_all_near_due_evidence_without_budget_growth(self):
        tasks = [card("a"), card("z")]
        metrics = calculate_task_metrics(tasks, CUTOFF)
        payload = ContextBuilder().build_monitoring_context("p", metrics, tasks)
        self.assertEqual(payload.data["selection"]["required_task_ids"], ["a", "z"])
        self.assertEqual(payload.data["selection"]["selected_task_ids"], ["a", "z"])
        self.assertEqual(payload.data["metrics"], metrics.model_dump())
        with self.assertRaisesRegex(ValueError, "lack snapshot evidence"):
            ContextBuilder().build_monitoring_context("p", metrics, tasks[:1])
        tasks[1].title = "Near due " * 4000
        with self.assertRaises(ContextBudgetExceededError):
            ContextBuilder().build_monitoring_context("p", metrics, tasks, budget_tokens=100, strict_budget=True)

    def test_markdown_renders_backend_due_soon_next_to_overdue(self):
        metrics = calculate_task_metrics([card("soon"), card("late", CUTOFF - timedelta(days=1))], CUTOFF)
        report = FinalReport(project_id="p", cutoff_datetime=CUTOFF, metrics=metrics,
                             policy_evaluations=PolicyEvaluationResult(evaluations=[], passed_rule_ids=[], failed_rule_ids=[],
                                 unknown_rule_ids=[], mandatory_failed_ids=[], mandatory_unknown_ids=[],
                                 recommended_action="evidencia_insuficiente"),
                             recommendation="evidencia_insuficiente", summary="Backend facts")
        markdown = generate_report_markdown(report)
        section = markdown.split("## Próximos vencimientos\n")[1].split("## Riesgos")[0]
        self.assertIn("**soon**", section)
        self.assertNotIn("**late**", section)
        self.assertLess(markdown.index("## Vencimientos"), markdown.index("## Próximos vencimientos"))
        report.metrics = calculate_task_metrics([], CUTOFF)
        self.assertIn("No se registran tareas próximas a vencer", generate_report_markdown(report))
