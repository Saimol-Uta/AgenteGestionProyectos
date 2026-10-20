"""Offline rule-backed Monitoring selections; no capture or execution authority."""
import copy
import unittest
from datetime import datetime

from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.agents.monitoring import ProjectMonitoringAgent
from agente_gestion.agents.context import ContextBuilder
from agente_gestion.domain.report import DomainProposalRuleSet, MonitoringDraft
from agente_gestion.domain.tasks import TaskCard, TaskState, DeterministicTaskMetrics
from agente_gestion.domain.evidence import EvidenceItem, EvidenceProvenance
from agente_gestion.domain.llm import GenerationResult, GenerationStatus


class MonitoringProposalsTests(unittest.TestCase):
    def setUp(self):
        self.task = TaskCard(id="T", title="Work", state=TaskState.pending,
                             observed_fields=["state", "due_date"], external_id="external")
        self.metrics = DeterministicTaskMetrics(total_cards=1, included_cards=1, completed_cards=0)
        self.rules = dict(project_id="P", rules=[dict(rule_id="R", task_ids=["T"], field="state",
                          allowed_values=["en_curso", "bloqueada"], rationale="Owner reason",
                          expected_impact="Owner impact")])
        self.evidence = EvidenceItem(category="task", summary="Observed",
            provenance=EvidenceProvenance(id="E", source="trello", project_id="P",
                external_id="external", timestamp=datetime(2026, 1, 1),
                payload={"task_id": "T", "state": "pending", "observed_fields": ["state"]}))

    def run_agent(self, rules=True, evidence=True, selection=None, builder=None, generic=False):
        fake = FakeProvider()
        if not generic:
            fake.enqueue_responses([GenerationResult(status=GenerationStatus.complete,
                structured_data=dict(summary="Weekly assessment.", suggested_actions=["Discuss work"],
                    referenced_task_ids=["T"], evidence_ids=["T"],
                    selected_choice_ids=["choice-0"] if selection is None else selection,
                    requested_changes=[{"authorization_status": "authorized"}]),
                provider="fake_simulated", model="fake", duration_seconds=0.01)])
        return ProjectMonitoringAgent(fake, context_builder=builder).run("P", self.metrics,
            [self.task], proposal_rules=DomainProposalRuleSet.model_validate(self.rules) if rules else None,
            task_evidence=[self.evidence] if evidence else None)

    def test_canonical_selection_and_nonmutation(self):
        before = copy.deepcopy((self.rules, self.task.model_dump(), self.evidence.model_dump()))
        draft = self.run_agent()
        change = draft.requested_changes[0]
        self.assertEqual((change.current_value, change.proposed_value), (TaskState.pending, TaskState.in_progress))
        self.assertEqual((change.evidence_ids, change.rationale, change.expected_impact),
                         (["E"], "Owner reason", "Owner impact"))
        self.assertNotIn("authorization_status", change.model_dump())
        self.assertEqual(draft.suggested_actions, ["Discuss work"])
        self.assertEqual(before, (self.rules, self.task.model_dump(), self.evidence.model_dump()))
        self.assertEqual(MonitoringDraft(summary="old").proposal_limitations, [])

    def test_absence_and_forgery(self):
        for kwargs, code in [({"rules": False}, "missing_rules"),
                             ({"evidence": False}, "missing_evidence"),
                             ({"selection": ["forged"]}, "unknown_choice"),
                             ({"selection": [0]}, "unknown_choice"),
                             ({"selection": ["choice-0", "choice-1"]}, "conflicting_selection")]:
            with self.subTest(code=code):
                draft = self.run_agent(**kwargs)
                self.assertEqual(draft.requested_changes, [])
                self.assertIn(code, draft.proposal_limitations)
                self.assertEqual(draft.suggested_actions, ["Discuss work"])
        self.assertEqual(self.run_agent(generic=True).requested_changes, [])

    def test_unknowns_and_mismatches(self):
        for mutation, code in [
            (lambda: self.rules.update(project_id="other"), "project_mismatch"),
            (lambda: self.rules["rules"][0].update(task_ids=["absent"]), "unknown_task"),
            (lambda: setattr(self.task, "observed_fields", []), "field_not_observed"),
            (lambda: setattr(self.task, "has_contradiction", True), "conflicting_task"),
            (lambda: setattr(self.task, "state", TaskState.unknown), "unknown_current_fact"),
            (lambda: setattr(self.evidence.provenance, "project_id", "other"), "evidence_mismatch"),
            (lambda: setattr(self.evidence, "category", "ci"), "evidence_mismatch"),
            (lambda: self.evidence.provenance.payload.update(state="completada"), "evidence_mismatch"),
            (lambda: self.evidence.provenance.payload.update(task_id="other"), "evidence_mismatch"),
            (lambda: setattr(self.evidence.provenance, "id", ""), "evidence_mismatch"),
            (lambda: self.rules["rules"].append(copy.deepcopy(self.rules["rules"][0])), "conflicting_rules"),
            (lambda: self.rules["rules"][0].update(allowed_values=["pending"]), "no_change"),
        ]:
            self.setUp()
            mutation()
            with self.subTest(code=code):
                draft = self.run_agent(selection=[])
                self.assertEqual(draft.requested_changes, [])
                self.assertIn(code, draft.proposal_limitations)

    def test_strict_rule_values(self):
        for field, values in [("due_date", [True, 1, "2026-01-01"]),
                              ("priority", [True, 1]), ("assignee_ids", [[1], "member"]),
                              ("state", [None, "desconocido", "invented"]), ("invalid", ["x"])]:
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    DomainProposalRuleSet.model_validate(dict(project_id="P", rules=[dict(
                        self.rules["rules"][0], field=field, allowed_values=[value])]))

    def test_visible_due_date_and_duplicate_evidence(self):
        self.task.due_date = datetime(2026, 1, 1, 12)
        self.rules["rules"][0].update(field="due_date", allowed_values=["2026-01-02T12:00:00"])
        self.evidence.provenance.payload.update(due_date="2026-01-01T12:00:00", observed_fields=["due_date"])
        draft = self.run_agent()
        self.assertEqual(draft.requested_changes[0].proposed_value, datetime(2026, 1, 2, 12))
        self.evidence.provenance.payload["due_date"] = "2026-01-01"
        self.assertIn("evidence_mismatch", self.run_agent(selection=[]).proposal_limitations)
        self.setUp()
        from agente_gestion.agents.context import monitoring_choices
        choices, codes = monitoring_choices("P", [self.task], [{"id": "T", "state": "pending"}],
            DomainProposalRuleSet.model_validate(self.rules), [self.evidence, self.evidence])
        self.assertEqual(choices, [])
        self.assertIn("evidence_mismatch", codes)
        self.rules["rules"][0]["allowed_values"] = ["en_curso", "en_curso"]
        self.assertIn("conflicting_rules", self.run_agent(selection=[]).proposal_limitations)
        invalid = DomainProposalRuleSet.model_validate(self.rules)
        invalid.rules[0].field = "unknown"
        self.assertEqual(monitoring_choices("P", [], [], invalid, [])[1], ["invalid_rules"])

    def test_missing_context_and_strict_identifiers(self):
        from agente_gestion.agents.context import monitoring_choices
        choices, codes = monitoring_choices("P", [self.task], [],
            DomainProposalRuleSet.model_validate(self.rules), [self.evidence])
        self.assertEqual((choices, codes), ([], ["field_not_visible"]))
        for patch in [dict(task_ids=[1]), dict(task_ids=[""]), dict(rule_id=""),
                      dict(rationale=" "), dict(expected_impact=""), dict(allowed_values=[])]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                DomainProposalRuleSet.model_validate(dict(project_id="P", rules=[dict(
                    self.rules["rules"][0], **patch)]))

    def test_a_rule_on_a_field_the_bounded_context_does_not_carry_is_still_offered(self):
        """The candidate set is the same the orchestrator persists.

        The bounded context only carries the cards the model reads, so a rule
        on a field it does not carry used to disappear from the offer while the
        orchestrator still computed it: the correction was impossible to select.
        """
        self.task.observed_fields.append("assignee_ids")
        self.task.assignee_ids = ["member-1"]
        self.rules["rules"][0].update(
            field="assignee_ids",
            allowed_values=[["member-1", "member-2"]],
        )
        self.evidence.provenance.payload.update(
            assignee_ids=["member-1"], observed_fields=["assignee_ids"],
        )
        draft = self.run_agent()
        self.assertEqual(draft.proposal_limitations, [])
        self.assertEqual(len(draft.requested_changes), 1)
        self.assertEqual(draft.requested_changes[0].field, "assignee_ids")
        self.assertEqual(
            draft.requested_changes[0].proposed_value, ["member-1", "member-2"],
        )

    def test_missing_visible_fields_and_budget(self):
        for field, value in [("start_date", None), ("priority", "high"),
                             ("assignee_ids", []), ("assignee_ids", None), ("due_date", None)]:
            self.setUp()
            self.task.observed_fields.append(field)
            self.rules["rules"][0].update(field=field, allowed_values=[value])
            draft = self.run_agent(selection=[])
            # The candidate is computed with the same rows the orchestrator uses
            # to persist it, so an unobserved field is now refused by the
            # evidence that does not carry it, and not by the context.
            self.assertEqual(draft.requested_changes, [])
            self.assertIn("evidence_mismatch", draft.proposal_limitations)
        self.setUp()
        self.rules["rules"][0]["rationale"] = "reason " * 10000
        draft = self.run_agent(builder=ContextBuilder(), selection=[])
        self.assertEqual(draft.requested_changes, [])
        self.assertIn("proposal_context_budget", draft.proposal_limitations)
        self.assertEqual(draft.suggested_actions, ["Discuss work"])
