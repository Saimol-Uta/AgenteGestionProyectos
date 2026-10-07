"""Offline adversarial evidence grounding regressions."""
import unittest

from agente_gestion.application.validation import SemanticValidator, ValidationError
from agente_gestion.domain.code import CodeDiffSnippet
from agente_gestion.domain.findings import CandidateFinding, FindingVerification
from agente_gestion.domain.policies import QualityRule
from agente_gestion.domain.report import MonitoringDraft
from agente_gestion.domain.tasks import TaskCard, TaskState, DeterministicTaskMetrics
from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.domain.llm import GenerationResult
from agente_gestion.agents.quality import CodeQualityAgent
from agente_gestion.agents.monitoring import ProjectMonitoringAgent
from agente_gestion.agents.verifier import VerifierAgent


class EvidenceGroundingTests(unittest.TestCase):
    def setUp(self):
        self.snippet = CodeDiffSnippet(repository="r", commit_sha="sha", file_path="store.ts",
                                      lines=[151], content="return transaction(pool, async (client) => {")
        self.rule = QualityRule(id="Q3", description="Review", source="review", metric_key="open_critical_findings")
        self.coverage = QualityRule(id="Q2", description="Coverage >= 80%", source="ci", metric_key="coverage_percentage", operator=">=", threshold=80)
        self.metrics = DeterministicTaskMetrics(total_cards=1, included_cards=1, completed_cards=0,
                                               progress_percentage=0, overdue_card_ids=[], blocked_card_ids=[],
                                               unknown_card_ids=[], contradictory_card_ids=[])

    def finding(self, **kwargs):
        data = dict(id="C1", rule_id="Q3", claim="Visible SQL concatenation", severity="high",
                    evidence_ids=["sha"], commit_sha="sha", file_path="store.ts", line=151, confidence=.9)
        data.update(kwargs)
        return CandidateFinding(**data)

    def validate(self, finding, snippets=None, **kwargs):
        return SemanticValidator.validate_candidate_findings([finding], snippets or [self.snippet],
                                                             {"Q2", "Q3"}, {"sha"}, **kwargs)

    def queued(self, payload):
        provider = FakeProvider()
        provider.enqueue_responses([GenerationResult(structured_data=payload, status="complete", provider="fake", model="fake", duration_seconds=0)] * 2)
        return provider

    def test_partial_absence_claims_fail_in_actual_quality_agent(self):
        for claim in ("No existe implementación dentro de changeQuotationState.",
                      "El método se corta abruptamente y no existe lógica de actualización.",
                      "The function is not implemented.", "The implementation ends abruptly.",
                      "The method has an incomplete implementation."):
            with self.subTest(claim=claim), self.assertRaises(ValidationError):
                provider = self.queued({"findings": [self.finding(claim=claim).model_dump(mode="json")]})
                CodeQualityAgent(provider).run("p", MonitoringDraft(summary="draft"), [self.snippet], rules=[self.rule])

    def test_structured_absence_cannot_disguise_itself_as_visible_code(self):
        self.assertTrue(self.validate(self.finding(claim="Missing behavior", claim_basis="file_absence")))

    def test_partial_visible_defect_remains_valid(self):
        self.assertEqual(self.validate(self.finding()), [])

    def test_full_file_positive_control(self):
        full = CodeDiffSnippet(repository="r", commit_sha="sha", file_path="store.ts", lines=[1],
                               content="function changeQuotationState() {}", evidence_scope="full_file",
                               is_complete=True, total_lines=1)
        self.assertEqual(self.validate(self.finding(line=1, claim="The function is not implemented.", claim_basis="file_absence"), [full]), [])

    def test_verifier_cannot_confirm_partial_absence(self):
        finding = self.finding(claim="No existe implementación dentro del método.")
        provider = self.queued({"verifications": [{"finding_id": "C1", "verdict": "confirmed", "reason": "No code after opener", "evidence_ids": ["sha"]}]})
        with self.assertRaises(ValidationError):
            VerifierAgent(provider).run("p", [finding], [self.snippet], rules=[self.rule], evidence_ids=["sha"])
        provider = self.queued({"verifications": [{"finding_id": "C1", "verdict": "insufficient", "reason": "Request more context", "evidence_ids": ["sha"]}]})
        self.assertEqual(VerifierAgent(provider).run("p", [finding], [self.snippet], evidence_ids=["sha"])[0].verdict.value, "insufficient")

    def test_coverage_rule_cannot_containerize_implementation_claim(self):
        provider = self.queued({"findings": [self.finding(rule_id="Q2").model_dump(mode="json")]})
        with self.assertRaises(ValidationError):
            CodeQualityAgent(provider).run("p", MonitoringDraft(summary="draft"), [self.snippet], rules=[self.coverage])

    def test_policy_metric_needs_matching_reported_fact(self):
        finding = self.finding(rule_id="Q2", claim="Coverage is 70%, below 80%", claim_basis="policy_metric", metric_key="coverage_percentage", observed_value=70)
        self.assertTrue(self.validate(finding, rules=[self.coverage], metric_facts={"coverage_percentage": None}))
        from agente_gestion.domain.code import CIRunResult
        ci = CIRunResult(id="sha", workflow_name="tests", commit_sha="sha", status="success", coverage_percentage=70)
        self.assertEqual(self.validate(finding, rules=[self.coverage], ci_result=ci), [])
        self.assertTrue(self.validate(finding, rules=[self.coverage], metric_facts={"coverage_percentage": 90}))

    def test_explicit_unassociated_finding_is_valid_but_missing_key_is_not(self):
        finding = self.finding(rule_id=None)
        self.assertEqual(self.validate(finding, rules=[self.coverage]), [])
        data = finding.model_dump()
        del data["rule_id"]
        from agente_gestion.agents.quality import GeneratedFindingSchema
        with self.assertRaises(ValueError):
            GeneratedFindingSchema.model_validate(data)
        with self.assertRaises(ValueError):
            self.finding(rule_id="")

    def test_monitoring_rejects_non_in_progress_task_claims(self):
        for state in ("completed", "pending", "unknown"):
            task = TaskCard(id="T1", title="Levantamiento Técnico de Datos Históricos de Despacho", state=TaskState[state])
            for summary in (f"El único elemento en curso es '{task.title}'.", "T1 is in progress."):
                with self.subTest(state=state, summary=summary), self.assertRaises(ValidationError):
                    provider = self.queued({"summary": summary, "referenced_task_ids": ["T1"], "evidence_ids": ["T1"]})
                    ProjectMonitoringAgent(provider).run("p", self.metrics, [task], evidence_ids=["T1"])

    def test_monitoring_exact_mentions_need_both_citation_lists(self):
        task = TaskCard(id="T1", title="Build migration", state=TaskState.in_progress)
        for summary in ("Build migration is in progress.", "T1 is in progress."):
            for refs, evs in (([], ["T1"]), (["T1"], [])):
                with self.subTest(summary=summary, refs=refs), self.assertRaises(ValidationError):
                    provider = self.queued({"summary": summary, "referenced_task_ids": refs, "evidence_ids": evs})
                    ProjectMonitoringAgent(provider).run("p", self.metrics, [task], evidence_ids=["T1"])
        provider = self.queued({"summary": "T1 is in progress.", "referenced_task_ids": ["T1"], "evidence_ids": ["T1"]})
        self.assertEqual(ProjectMonitoringAgent(provider).run("p", self.metrics, [task], evidence_ids=["T1"]).referenced_task_ids, ["T1"])

    def test_structured_task_state_is_checked_independently_of_prose(self):
        task = TaskCard(id="T1", title="Task", state=TaskState.completed)
        provider = self.queued({"summary": "Weekly summary", "referenced_task_ids": ["T1"], "evidence_ids": ["T1"],
                                "task_state_claims": [{"task_id": "T1", "state": TaskState.in_progress.value}]})
        with self.assertRaises(ValidationError):
            ProjectMonitoringAgent(provider).run("p", self.metrics, [task], evidence_ids=["T1"])

    def test_unknown_coverage_does_not_prove_missing_tests(self):
        for claim in ("Coverage is unavailable, therefore tests are missing.",
                      "No hay cobertura disponible, por tanto faltan pruebas.",
                      "Sin CI, el fragmento demuestra falta de pruebas."):
            with self.subTest(claim=claim):
                self.assertTrue(self.validate(self.finding(rule_id=None, claim=claim)))

    def test_complete_file_must_match_cited_commit_file_and_line(self):
        finding = self.finding(claim="The function is not implemented.")
        full = CodeDiffSnippet(repository="r", commit_sha="sha", file_path="store.ts", lines=[1],
                               content="function f() {}", evidence_scope="full_file", is_complete=True, total_lines=1)
        for changed in ({"commit_sha": "other"}, {"file_path": "other.ts"}, {}):
            with self.subTest(changed=changed):
                self.assertTrue(self.validate(finding, [self.snippet, full.model_copy(update=changed)]))
        finding = self.finding(line=1, claim="The function is not implemented.", evidence_ids=["uncited"])
        self.assertTrue(SemanticValidator.validate_candidate_findings([finding], [full], {"Q3"}, {"uncited", "sha"}))

    def test_metric_claim_requires_cited_matching_ci_not_unbound_fact(self):
        from agente_gestion.domain.code import CIRunResult
        finding = self.finding(rule_id="Q2", claim="Coverage is 70%, below 80%", claim_basis="policy_metric",
                               metric_key="coverage_percentage", observed_value=70, evidence_ids=["sha", "ci"])
        ci = CIRunResult(id="ci", workflow_name="tests", commit_sha="sha", status="success", coverage_percentage=70)
        self.assertTrue(self.validate(finding, rules=[self.coverage], metric_facts={"coverage_percentage": 70}))
        self.assertEqual(SemanticValidator.validate_candidate_findings([finding], [self.snippet], {"Q2"}, {"sha", "ci"}, rules=[self.coverage], ci_result=ci), [])
        for changed in ({"commit_sha": "other"}, {"coverage_percentage": None}, {"id": "uncited"}):
            with self.subTest(changed=changed):
                self.assertTrue(SemanticValidator.validate_candidate_findings([finding], [self.snippet], {"Q2"}, {"sha", "ci"}, rules=[self.coverage], ci_result=ci.model_copy(update=changed)))

    def test_fake_provider_uses_null_without_an_applicable_review_rule(self):
        findings = CodeQualityAgent(FakeProvider()).run("p", MonitoringDraft(summary="draft"), [self.snippet], rules=[self.coverage])
        self.assertIsNone(findings[0].rule_id)
        findings = CodeQualityAgent(FakeProvider()).run("p", MonitoringDraft(summary="draft"), [self.snippet], rules=[])
        self.assertIsNone(findings[0].rule_id)

    def test_runtime_monitoring_uses_actual_domain_state_representation(self):
        class Capture(FakeProvider):
            def generate(self, request):
                self.request = request
                return super().generate(request)
        provider = Capture()
        task = TaskCard(id="T1", title="Build migration", state=TaskState.in_progress)
        ProjectMonitoringAgent(provider).run("p", self.metrics, [task], evidence_ids=["T1"])
        self.assertEqual(provider.request.evidences["tasks"][0]["state"], "en_curso")
        self.assertIn("en_curso", provider.request.instructions)

    def test_policy_metric_without_policy_definition_is_unverifiable(self):
        finding = self.finding(rule_id="Q2", claim_basis="policy_metric", metric_key="coverage_percentage", observed_value=70)
        self.assertTrue(self.validate(finding))

    def test_full_file_absence_confirmation_must_cite_its_commit(self):
        full = CodeDiffSnippet(repository="r", commit_sha="sha", file_path="store.ts", lines=[1], content="function f() {}",
                               evidence_scope="full_file", is_complete=True, total_lines=1)
        finding = self.finding(line=1, claim="The function is not implemented.")
        verdict = FindingVerification(finding_id="C1", verdict="confirmed", reason="Checked file", verified_by="test", evidence_ids=[])
        self.assertTrue(SemanticValidator.validate_finding_verifications([verdict], [finding], {"sha"}, available_snippets=[full]))
        verdict.evidence_ids = ["sha"]
        self.assertEqual(SemanticValidator.validate_finding_verifications([verdict], [finding], {"sha"}, available_snippets=[full]), [])

    def test_unassociated_rule_survives_sqlite_and_report_roundtrip(self):
        import tempfile
        from pathlib import Path
        from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
        from agente_gestion.domain.report import FinalReport
        from agente_gestion.domain.policies import PolicyEvaluationResult
        from datetime import datetime, timezone
        finding = self.finding(rule_id=None)
        with tempfile.TemporaryDirectory() as directory:
            storage = SQLiteStorageAdapter(str(Path(directory) / "db.sqlite"))
            storage.save_findings("run", [finding])
            self.assertIsNone(storage.get_findings("run")[0]["rule_id"])
        report = FinalReport(summary="Report", project_id="p", cutoff_datetime=datetime.now(timezone.utc), metrics=self.metrics,
                             policy_evaluations=PolicyEvaluationResult(evaluations=[], passed_rule_ids=[], failed_rule_ids=[],
                             unknown_rule_ids=[], mandatory_failed_ids=[], mandatory_unknown_ids=[], recommended_action="evidencia_insuficiente"),
                             pending_findings=[finding], recommendation="evidencia_insuficiente")
        self.assertIsNone(FinalReport.model_validate_json(report.model_dump_json()).pending_findings[0].rule_id)

    def test_structured_metric_cannot_bypass_review_rule_applicability(self):
        finding = self.finding(claim_basis="policy_metric", metric_key="coverage_percentage", observed_value=70)
        self.assertTrue(self.validate(finding, rules=[self.rule]))

    def test_verifier_cannot_confirm_incompatible_ci_policy_association(self):
        finding = self.finding(rule_id="Q2")
        provider = self.queued({"verifications": [{"finding_id": "C1", "verdict": "confirmed", "reason": "Visible defect", "evidence_ids": ["sha"]}]})
        with self.assertRaises(ValidationError):
            VerifierAgent(provider).run("p", [finding], [self.snippet], rules=[self.coverage], evidence_ids=["sha"])

    def test_adjacent_task_status_cannot_bypass_ground_truth(self):
        task = TaskCard(id="T1", title="Build migration", state=TaskState.completed)
        for summary in ("Build migration is listed. It is in progress.",
                        "Build migration figura en el listado. Está en curso."):
            with self.subTest(summary=summary), self.assertRaises(ValidationError):
                provider = self.queued({"summary": summary, "referenced_task_ids": ["T1"], "evidence_ids": ["T1"],
                                        "task_state_claims": [{"task_id": "T1", "state": TaskState.completed.value}]})
                ProjectMonitoringAgent(provider).run("p", self.metrics, [task], evidence_ids=["T1"])

    def test_adjacent_completed_state_and_unrelated_summary_remain_valid(self):
        task = TaskCard(id="T1", title="Build migration", state=TaskState.completed)
        for summary in ("Build migration is listed. It is completed.",
                        "Build migration figura en el listado. Está completada.",
                        "Build migration is listed. Project progress is 0%. Delivery risk is low."):
            with self.subTest(summary=summary):
                provider = self.queued({"summary": summary, "referenced_task_ids": ["T1"], "evidence_ids": ["T1"]})
                self.assertEqual(ProjectMonitoringAgent(provider).run("p", self.metrics, [task], evidence_ids=["T1"]).summary, summary)

    def test_ambiguous_followup_state_requests_attribution(self):
        # Both are active and selected: ambiguity must still fail attribution,
        # independently of the separate omitted-task assertion guard.
        tasks = [TaskCard(id="T1", title="Build migration", state=TaskState.in_progress),
                 TaskCard(id="T2", title="Deploy service", state=TaskState.in_progress)]
        provider = self.queued({"summary": "Build migration and Deploy service are listed. It is in progress.",
                                "referenced_task_ids": ["T1", "T2"], "evidence_ids": ["T1", "T2"]})
        with self.assertRaises(ValidationError) as caught:
            ProjectMonitoringAgent(provider).run("p", self.metrics, tasks, evidence_ids=["T1", "T2"])
        self.assertIn("attribution", str(caught.exception))
