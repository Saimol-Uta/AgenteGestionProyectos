"""G3: deterministic risks linked to corrective proposals and manager decisions."""

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.agents.context import monitoring_choices
from agente_gestion.application.orchestrator import OrchestratorConfig, ProjectOrchestrator
from agente_gestion.domain.action_proposal import ActionProposal
from agente_gestion.domain.llm import GenerationResult, GenerationStatus
from agente_gestion.domain.report import DomainProposalRuleSet
from agente_gestion.domain.risks import (
    ProposalRiskRef,
    RiskKind,
    build_task_risks,
    risks_from_metrics,
)
from agente_gestion.domain.tasks import DeterministicTaskMetrics, TaskState
from tests import test_monitoring_proposals, test_orchestrator
from tests.web_fixtures import (
    CURRENT_SNAPSHOT_ID,
    PROJECT_ID,
    STORED_PROJECT_ID,
    TASK_ID,
    build_web_api_fixture,
)

# Identidad y resumen de una propuesta SIN riesgo, medidos con el código previo a
# G3 sobre el mismo escenario. Deben seguir siendo idénticos.
LEGACY_PROPOSAL_ID = "proposal-6022f2c87dc5764de353655b887154f573c71d3f419c9663770cd9b238747d4f"
LEGACY_STAGE_SUMMARY = {
    "failures": [], "limitations": [], "offered": 2, "saved": 1,
    "selected": 1, "technical_blocked": 1, "technical_ready": 0,
}


def _rule(**overrides) -> dict:
    rule = dict(rule_id="R", task_ids=["T"], field="state",
                allowed_values=["en_curso", "bloqueada"], rationale="Owner reason",
                expected_impact="Owner impact")
    rule.update(overrides)
    return rule


def _metrics(**buckets) -> DeterministicTaskMetrics:
    return DeterministicTaskMetrics(
        total_cards=1, included_cards=1, completed_cards=0, **buckets,
    )


class RuleSelectorTests(unittest.TestCase):
    """The owner declares a risk TYPE, never a capture-scoped risk identifier."""

    def test_rule_without_selector_keeps_the_previous_serialization(self):
        rules = DomainProposalRuleSet.model_validate({
            "project_id": "P", "rules": [_rule()],
        })
        dumped = rules.model_dump(mode="json")
        self.assertNotIn("risk_kind", dumped["rules"][0])
        self.assertEqual(dumped["rules"][0], {
            "rule_id": "R", "task_ids": ["T"], "field": "state",
            "allowed_values": ["en_curso", "bloqueada"],
            "rationale": "Owner reason", "expected_impact": "Owner impact",
        })

    def test_rule_with_selector_is_validated_and_serialized(self):
        rules = DomainProposalRuleSet.model_validate({
            "project_id": "P", "rules": [_rule(risk_kind="overdue")],
        })
        self.assertEqual(rules.rules[0].risk_kind, RiskKind.overdue)
        self.assertEqual(rules.model_dump(mode="json")["rules"][0]["risk_kind"], "overdue")

    def test_unknown_risk_selector_is_rejected(self):
        for value in ("invented", "", 3, "OVERDUE"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DomainProposalRuleSet.model_validate({
                    "project_id": "P", "rules": [_rule(risk_kind=value)],
                })
        # An absent selector is valid and means "this rule declares no risk".
        rules = DomainProposalRuleSet.model_validate({
            "project_id": "P", "rules": [_rule(risk_kind=None)],
        })
        self.assertIsNone(rules.rules[0].risk_kind)


class RiskResolutionTests(unittest.TestCase):
    """The concrete risk is resolved against the same capture and evidence."""

    def setUp(self):
        fixture = test_monitoring_proposals.MonitoringProposalsTests()
        fixture.setUp()
        self.task = fixture.task
        self.evidence = fixture.evidence
        self.rules = fixture.rules

    def _risks(self, *kinds) -> list:
        buckets = {f"{kind}_card_ids": ["T"] for kind in kinds}
        return risks_from_metrics(_metrics(**buckets), "P", "cap-1")

    def _choices(self, rule: dict, risks: list, project_id: str = "P"):
        rules = DomainProposalRuleSet.model_validate({"project_id": "P", "rules": [rule]})
        return monitoring_choices(
            project_id, [self.task], [{"id": "T", "state": "pending"}], rules,
            [self.evidence], risks,
        )

    def test_rule_links_the_matching_risk(self):
        risks = self._risks("overdue")
        choices, codes = self._choices(_rule(risk_kind="overdue"), risks)
        self.assertEqual(codes, [])
        self.assertTrue(choices)
        reference = choices[0].risk_refs[0]
        self.assertEqual(reference.kind, RiskKind.overdue)
        self.assertEqual(reference.task_id, "T")
        self.assertEqual(reference.capture_id, "cap-1")
        self.assertEqual(reference.risk_id, risks[0].risk_id)

    def test_rule_without_matching_risk_produces_no_candidate(self):
        choices, codes = self._choices(_rule(risk_kind="blocked"), self._risks("overdue"))
        self.assertEqual(choices, [])
        self.assertIn("risk_not_present", codes)

    def test_multiple_risks_on_one_task_select_the_declared_kind(self):
        risks = self._risks("overdue", "blocked")
        self.assertEqual(len(risks), 2)
        for kind, expected in (("overdue", RiskKind.overdue), ("blocked", RiskKind.blocked)):
            with self.subTest(kind=kind):
                choices, codes = self._choices(_rule(risk_kind=kind), risks)
                self.assertEqual(codes, [])
                self.assertEqual([ref.kind for ref in choices[0].risk_refs], [expected])
                self.assertEqual(choices[0].risk_refs[0].risk_id,
                                 next(risk.risk_id for risk in risks if risk.kind == expected))

    def test_rule_from_another_project_is_rejected(self):
        choices, codes = self._choices(
            _rule(risk_kind="overdue"), self._risks("overdue"), project_id="other",
        )
        self.assertEqual(choices, [])
        self.assertEqual(codes, ["project_mismatch"])

    def test_rule_without_selector_produces_no_reference(self):
        choices, codes = self._choices(_rule(), self._risks("overdue"))
        self.assertEqual(codes, [])
        self.assertEqual(choices[0].risk_refs, [])
        self.assertNotIn("risk_refs", choices[0].model_dump(mode="json"))


class ProposalContractTests(unittest.TestCase):
    """The stored proposal keeps the concrete risk it addresses."""

    def setUp(self):
        self.base = test_orchestrator.OrchestratorTestCase()
        self.base.setUp()
        self.addCleanup(self.base.tearDown)
        fixture = test_monitoring_proposals.MonitoringProposalsTests()
        fixture.setUp()
        self.fixture = fixture
        self.snapshot = self.base._sample_snapshot_no_code()
        self.snapshot.project_id = "P"
        self.snapshot.tasks = [fixture.task]
        self.evidence = [fixture.evidence]
        self.collection = SimpleNamespace(
            snapshot=self.snapshot, evidence=self.evidence, target_commit_sha=None, records=[],
        )
        self.source = SimpleNamespace(collect_snapshot=Mock(return_value=self.collection))
        self.store = self.base.sqlite
        self.orch = ProjectOrchestrator(
            self.store, self.base.artifacts, self.base.fake_provider, self.base.fake_provider,
            aprobador_provider=self.base.fake_provider, source_adapter=self.source,
            config=OrchestratorConfig(project_id="P"),
        )

    def _rules(self, **overrides) -> DomainProposalRuleSet:
        return DomainProposalRuleSet.model_validate({
            "project_id": "P", "rules": [dict(_rule(), **overrides)],
        })

    def _execute(self, rules: DomainProposalRuleSet):
        self.base.fake_provider.enqueue_responses([GenerationResult(
            status=GenerationStatus.complete,
            structured_data=dict(summary="Weekly assessment.", referenced_task_ids=["T"],
                                  evidence_ids=["T"], selected_choice_ids=["choice-0"]),
            provider="fake_simulated", model="fake", duration_seconds=0.01)])
        return self.orch.execute(proposal_rules=rules)

    def _stored(self) -> list:
        return self.store.list_action_proposals("P")

    def test_risk_free_proposal_keeps_its_legacy_identifier_and_body(self):
        run = self._execute(self._rules())
        stored = self._stored()
        self.assertEqual(len(stored), 1)
        proposal = stored[0][0]
        self.assertEqual(proposal.proposal_id, LEGACY_PROPOSAL_ID)
        self.assertEqual(proposal.schema_version, "action_proposal_v1")
        self.assertEqual(proposal.risk_refs, [])
        self.assertNotIn("risk_refs", proposal.model_dump(mode="json"))
        summary = json.loads(run.stages["proposals"].output_summary)
        self.assertEqual(summary, LEGACY_STAGE_SUMMARY)

    def test_risk_bound_proposal_keeps_the_reference_and_uses_the_v2_schema(self):
        self.fixture.task.due_date = self.snapshot.cutoff_datetime.replace(year=2020)
        self.snapshot.tasks = [self.fixture.task]
        run = self._execute(self._rules(risk_kind="overdue"))
        stored = self._stored()
        self.assertEqual(len(stored), 1)
        proposal = stored[0][0]
        self.assertEqual(proposal.schema_version, "action_proposal_v2")
        self.assertEqual(len(proposal.risk_refs), 1)
        reference = proposal.risk_refs[0]
        self.assertEqual(reference.kind, RiskKind.overdue)
        self.assertEqual(reference.task_id, "T")
        self.assertEqual(reference.capture_id, proposal.observed_capture_id)
        self.assertTrue(reference.risk_id.startswith("risk-"))
        self.assertNotEqual(proposal.proposal_id, LEGACY_PROPOSAL_ID)
        summary = json.loads(run.stages["proposals"].output_summary)
        self.assertEqual(summary["saved"], 1)
        self.assertEqual(summary["failures"], [])

    def test_risk_bound_proposal_is_saved_once_and_stays_stable(self):
        self.fixture.task.due_date = self.snapshot.cutoff_datetime.replace(year=2020)
        self.snapshot.tasks = [self.fixture.task]
        self._execute(self._rules(risk_kind="overdue"))
        first = self._stored()
        self._execute(self._rules(risk_kind="overdue"))
        second = self._stored()
        self.assertEqual(len(second), 1)
        self.assertEqual(first[0][0].proposal_id, second[0][0].proposal_id)
        self.assertEqual(first[0][0].model_dump(mode="json"), second[0][0].model_dump(mode="json"))

    def test_rule_without_matching_risk_persists_no_proposal(self):
        run = self._execute(self._rules(risk_kind="blocked"))
        self.assertEqual(self._stored(), [])
        summary = json.loads(run.stages["proposals"].output_summary)
        self.assertEqual(summary["saved"], 0)
        self.assertIn("risk_not_present", summary["limitations"])

    def test_narrative_risks_never_create_proposals_or_references(self):
        self.fixture.task.due_date = self.snapshot.cutoff_datetime.replace(year=2020)
        self.snapshot.tasks = [self.fixture.task]
        self._execute(self._rules(risk_kind="overdue"))
        proposal = self._stored()[0][0]
        self.assertEqual(len(proposal.risk_refs), 1)
        self.assertNotIn("Riesgo potencial", json.dumps(proposal.model_dump(mode="json")))


class RiskLinkReadModelTests(unittest.TestCase):
    """The read model exposes the proposals that really answer each risk."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = build_web_api_fixture(Path(self.temp.name))
        self.client = self.fixture.client
        self.storage = SQLiteStorageAdapter(str(self.fixture.database_path))
        self.capture = next(
            item for item in self.storage.list_snapshot_captures(PROJECT_ID)
            if item.snapshot.id == CURRENT_SNAPSHOT_ID
        )
        self.evidence = next(
            item for item in self.storage.get_evidence(self.fixture.run_ids[-1])
            if item.category == "task"
        )
        self.risks = build_task_risks(
            self.capture.snapshot, self.capture.capture_id,
            due_soon_days=7, timezone_name="America/Guayaquil",
        )
        self.risk = next(risk for risk in self.risks if risk.kind == RiskKind.due_soon)

    def _store_proposal(self, proposal_id: str, risk_refs: list) -> None:
        due = self.capture.snapshot.tasks[0].due_date
        proposal = ActionProposal(
            proposal_id=proposal_id,
            project_id=PROJECT_ID,
            task_id=TASK_ID,
            observed_capture_id=self.capture.capture_id,
            field="due_date",
            current_value=due,
            proposed_value=due + timedelta(days=1),
            evidence_ids=[self.evidence.provenance.id],
            rationale="Ajustar el vencimiento con base en la captura actual.",
            expected_impact="Mantener el cronograma verificable.",
            schema_version="action_proposal_v2" if risk_refs else "action_proposal_v1",
            risk_refs=risk_refs,
        )
        self.storage.save_action_proposal(proposal, [self.evidence])

    def _risk(self, kind: RiskKind = RiskKind.due_soon) -> dict:
        body = self.client.get(f"/api/v1/projects/{PROJECT_ID}/summary").json()
        return next(risk for risk in body["risks"] if risk["kind"] == kind.value)

    def test_summary_links_the_proposal_that_answers_the_risk(self):
        self._store_proposal("proposal-g3-risk-bound", [ProposalRiskRef(
            risk_id=self.risk.risk_id, kind=self.risk.kind, task_id=TASK_ID,
            capture_id=self.capture.capture_id,
        )])
        risk = self._risk()
        self.assertEqual(risk["risk_id"], self.risk.risk_id)
        self.assertEqual(len(risk["proposals"]), 1)
        link = risk["proposals"][0]
        self.assertEqual(link["proposal_id"], "proposal-g3-risk-bound")
        self.assertEqual(link["task_id"], TASK_ID)
        self.assertEqual(link["capture_id"], self.capture.capture_id)
        self.assertEqual(link["field"], "due_date")
        self.assertEqual(link["review_status"], "pending")
        self.assertEqual(link["execution_status"], "not_executed")

    def test_proposal_without_risk_is_never_linked_to_a_risk(self):
        self._store_proposal("proposal-g3-risk-bound", [ProposalRiskRef(
            risk_id=self.risk.risk_id, kind=self.risk.kind, task_id=TASK_ID,
            capture_id=self.capture.capture_id,
        )])
        risk = self._risk()
        linked = {link["proposal_id"] for link in risk["proposals"]}
        self.assertNotIn("proposal-h5-due-date-unified", linked)
        self.assertEqual(len(linked), 1)

    def test_task_detail_carries_the_capture_and_the_linked_proposals(self):
        self._store_proposal("proposal-g3-risk-bound", [ProposalRiskRef(
            risk_id=self.risk.risk_id, kind=self.risk.kind, task_id=TASK_ID,
            capture_id=self.capture.capture_id,
        )])
        detail = self.client.get(f"/api/v1/projects/{PROJECT_ID}/tasks/{TASK_ID}").json()
        self.assertEqual(detail["capture_id"], self.capture.capture_id)
        risk = next(item for item in detail["risks"] if item["kind"] == RiskKind.due_soon.value)
        self.assertEqual([link["proposal_id"] for link in risk["proposals"]],
                         ["proposal-g3-risk-bound"])

    def test_proposal_exposes_the_risk_that_motivated_it(self):
        self._store_proposal("proposal-g3-risk-bound", [ProposalRiskRef(
            risk_id=self.risk.risk_id, kind=self.risk.kind, task_id=TASK_ID,
            capture_id=self.capture.capture_id,
        )])
        body = self.client.get(f"/api/v1/projects/{PROJECT_ID}/proposals").json()
        bound = next(item for item in body["items"]
                     if item["proposal_id"] == "proposal-g3-risk-bound")
        self.assertEqual(len(bound["risk_refs"]), 1)
        reference = bound["risk_refs"][0]
        self.assertEqual(reference["risk_id"], self.risk.risk_id)
        self.assertEqual(reference["kind"], RiskKind.due_soon.value)
        self.assertTrue(reference["resolved"])
        legacy = next(item for item in body["items"]
                      if item["proposal_id"] == "proposal-h5-due-date-unified")
        self.assertEqual(legacy["risk_refs"], [])

    def test_unresolvable_risk_reference_is_declared_not_verified(self):
        self._store_proposal("proposal-g3-fake-risk", [ProposalRiskRef(
            risk_id="risk-" + "0" * 24, kind=RiskKind.due_soon, task_id=TASK_ID,
            capture_id=self.capture.capture_id,
        )])
        body = self.client.get(f"/api/v1/projects/{PROJECT_ID}/proposals").json()
        bound = next(item for item in body["items"]
                     if item["proposal_id"] == "proposal-g3-fake-risk")
        self.assertFalse(bound["risk_refs"][0]["resolved"])

    def test_links_never_cross_projects(self):
        self._store_proposal("proposal-g3-risk-bound", [ProposalRiskRef(
            risk_id=self.risk.risk_id, kind=self.risk.kind, task_id=TASK_ID,
            capture_id=self.capture.capture_id,
        )])
        body = self.client.get(f"/api/v1/projects/{STORED_PROJECT_ID}/summary").json()
        for risk in body["risks"]:
            self.assertEqual(risk["proposals"], [])
        other = self.client.get(f"/api/v1/projects/{STORED_PROJECT_ID}/proposals").json()
        self.assertEqual(other["total"], 0)

    def test_summary_exposes_the_persisted_diagnostic_context(self):
        body = self.client.get(f"/api/v1/projects/{PROJECT_ID}/summary").json()
        context = body["proposals_context"]
        self.assertIsNotNone(context)
        self.assertEqual(context["diagnostic"]["outcome"], "not_configured")
        self.assertIn("missing_rules", context["diagnostic"]["limitation_codes"])

    def test_risk_without_proposal_reports_no_linked_proposal(self):
        risk = self._risk()
        self.assertEqual(risk["proposals"], [])


if __name__ == "__main__":
    unittest.main()
