"""Focused enriched-card context and inactive-task corrective boundaries."""
from datetime import datetime, timezone
import unittest

from agente_gestion.agents.context import ContextBuilder, ContextBudgetExceededError, monitoring_choices
from agente_gestion.domain.tasks import TaskCard, TaskState, calculate_task_metrics
from agente_gestion.domain.report import DomainProposalRuleSet


class MonitoringCardScopeTest(unittest.TestCase):
    def context(self, *tasks):
        metrics = calculate_task_metrics(list(tasks), datetime(2026, 10, 20, tzinfo=timezone.utc))
        return ContextBuilder().build_monitoring_context("p", metrics, list(tasks)), metrics

    def test_selected_metadata_and_only_explicit_dependencies(self):
        task = TaskCard(id="active01", title="Active", state=TaskState.in_progress, priority="alta",
                        assignee_ids=["owner"], detail="Goal: deliver. depende de: prior001",
                        observed_fields=["priority", "assignee_ids", "detail"])
        prior = TaskCard(id="prior001", title="Prior", state=TaskState.completed)
        context, _ = self.context(task, prior)
        row = context.data["tasks"][0]
        self.assertEqual(row["priority"], "alta")
        self.assertEqual(row["assignee_ids"], ["owner"])
        self.assertEqual(row["detail"], task.detail)
        self.assertEqual(row["explicit_dependencies"], [prior.id])
        proposed = task.model_copy(update={"detail": "Predecesoras propuestas (no activadas): prior001"})
        self.assertEqual(self.context(proposed, prior)[0].data["tasks"][0]["explicit_dependencies"], [])

    def test_long_description_omission_is_explicit_not_a_clipped_quote(self):
        task = TaskCard(id="active01", title="Active", state=TaskState.in_progress, detail="x" * 10000)
        row = self.context(task)[0].data["tasks"][0]
        self.assertNotIn("detail", row)
        self.assertEqual(row["detail_availability"], "omitted_length_limit")
        self.assertEqual(row["detail_char_count"], 10000)

    def test_enriched_description_uses_whole_objective_and_scope_sections(self):
        detail = "### Objetivo\nDeliver dashboard.\n\n### Alcance\nReporting.\n\n### Actividades\n" + "x" * 1000
        task = TaskCard(id="active01", title="Active", state=TaskState.in_progress, detail=detail)
        row = self.context(task)[0].data["tasks"][0]
        self.assertEqual(row["detail_sections"], {"Objetivo": "Deliver dashboard.", "Alcance": "Reporting."})
        self.assertEqual(row["detail_availability"], "selected_sections")
        self.assertNotIn("detail", row)

    def test_archived_unknown_remains_raw_observation_not_active_context(self):
        archived = TaskCard(id="archive1", title="Archived", state=TaskState.unknown,
                            is_archived=True, is_included=False)
        active = TaskCard(id="active01", title="Active", state=TaskState.in_progress)
        context, metrics = self.context(archived, active)
        self.assertIn(archived.id, metrics.unknown_card_ids)
        self.assertEqual([t["id"] for t in context.data["tasks"]], [active.id])
        self.assertNotIn(archived.id, context.data["evidence_ids"])
        self.assertEqual(context.data["selection"]["inactive_observation_count"], 1)

    def test_inactive_cards_cannot_offer_corrective_choices_even_with_rules(self):
        rules = DomainProposalRuleSet(project_id="p", rules=[dict(rule_id="r", task_ids=["archive1"],
            field="priority", allowed_values=["alta"], rationale="Owner reason", expected_impact="Owner impact")])
        for metadata in ({"is_archived": True}, {"is_included": False}):
            card = TaskCard(id="archive1", title="Inactive", priority="baja", observed_fields=["priority"], **metadata)
            choices, limits = monitoring_choices("p", [card], [card.model_dump()], rules, [])
            self.assertEqual(choices, [])
            self.assertIn("inactive_task", limits)

    def test_budget_refuses_instead_of_clipping_metadata(self):
        tasks = [TaskCard(id="active01", title="Active", state=TaskState.in_progress)]
        metrics = calculate_task_metrics(tasks, datetime(2026, 10, 20, tzinfo=timezone.utc))
        with self.assertRaises(ContextBudgetExceededError):
            ContextBuilder().build_monitoring_context("p", metrics, tasks, budget_tokens=10, strict_budget=True)


if __name__ == "__main__":
    unittest.main()
