"""Hermetic role selection and configured-window budgeting regressions."""
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from agente_gestion.agents.context import ContextBuilder, ContextBudgetExceededError, estimate_tokens_from_data
from agente_gestion.agents.monitoring import ProjectMonitoringAgent
from agente_gestion.agents.quality import CodeQualityAgent
from agente_gestion.agents.verifier import VerifierAgent
from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.adapters.llm.ollama import OllamaProvider
from agente_gestion.domain.tasks import TaskCard, TaskState, DeterministicTaskMetrics
from agente_gestion.domain.code import CodeDiffSnippet, CIRunResult
from agente_gestion.domain.findings import CandidateFinding
from agente_gestion.domain.report import MonitoringDraft
from agente_gestion.domain.policies import QualityRule
from agente_gestion.domain.llm import GenerationRequest, GenerationResult


class CaptureProvider(FakeProvider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return super().generate(request)


class RoleContextSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tasks = [TaskCard(id=f"T{i}", title=f"Task {i}", state=TaskState.completed,
                               list_id="irrelevant" * 80, list_name="Column" * 150,
                               detail="Details" * 1000, external_id=f"T{i}") for i in range(31)]
        self.tasks[1].state = TaskState.in_progress
        self.tasks[2].state = TaskState.unknown
        self.tasks[2].has_contradiction = True
        self.tasks[2].is_included = False
        self.tasks[1].due_date = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
        self.metrics = DeterministicTaskMetrics(total_cards=31, included_cards=30, completed_cards=29,
            progress_percentage=96.7, overdue_card_ids=["T1"], blocked_card_ids=[], unknown_card_ids=["T2"], contradictory_card_ids=["T2"])
        self.rule = QualityRule(id="Q3", description="Review", metric_key="open_critical_findings", source="review")
        self.snippet = CodeDiffSnippet(repository="r", commit_sha="sha", file_path="f.py", lines=[1], content="return 1 / total")
        self.finding = CandidateFinding(id="F1", rule_id="Q3", claim="Visible division by zero", severity="high",
            evidence_ids=["sha"], commit_sha="sha", file_path="f.py", line=1, confidence=.9)

    def test_large_monitoring_projects_tasks_without_code_and_preserves_metrics(self):
        provider = CaptureProvider(context_tokens=4096, max_output_tokens=768)
        ProjectMonitoringAgent(provider).run("p", self.metrics, self.tasks, evidence_ids=[t.id for t in self.tasks],
                                             source_is_complete=False, source_commit_sha="sha")
        request = provider.requests[0]
        self.assertEqual(request.max_generation_tokens, 512)
        payload = request.evidences
        self.assertNotIn("code_snippets", payload)
        self.assertEqual(payload["metrics"], self.metrics.model_dump())
        self.assertFalse(payload["source_context"]["is_complete"])
        self.assertEqual(payload["source_context"]["commit_sha"], "sha")
        selected = {row["id"]: row for row in payload["tasks"]}
        self.assertTrue({"T1", "T2"} <= selected.keys())
        self.assertNotIn("detail", selected["T1"])
        self.assertNotIn("list_name", selected["T1"])
        self.assertEqual(selected["T1"]["due_date"], self.tasks[1].due_date.isoformat())
        self.assertFalse(selected["T2"]["is_included"])
        self.assertTrue(selected["T2"]["has_contradiction"])
        scope = payload["selection"]
        self.assertEqual(set(scope["selected_task_ids"]), selected.keys())
        self.assertEqual(len(scope["selected_task_ids"]) + scope["omitted_task_count"], len(self.tasks))
        self.assertEqual(set(payload["available_evidence_ids"]), selected.keys())

    def test_compact_matching_ci_and_critical_signal_tasks_survive(self):
        self.tasks[3].state = TaskState.blocked
        self.metrics.blocked_card_ids = ["T3"]
        ci = CIRunResult(id="ci", workflow_name="tests", commit_sha="sha", status="failure", failed_tests=1, total_tests=10, coverage_percentage=70)
        provider = CaptureProvider(context_tokens=4096)
        ProjectMonitoringAgent(provider).run("p", self.metrics, self.tasks, ci, [*[t.id for t in self.tasks], "ci"],
                                             source_is_complete=True, source_commit_sha="sha")
        payload = provider.requests[0].evidences
        self.assertIn("T3", payload["selection"]["selected_task_ids"])
        self.assertEqual(payload["ci_result"]["commit_sha"], "sha")
        self.assertEqual(payload["ci_result"]["failed_tests"], 1)
        self.assertEqual(payload["ci_availability"], "reported")
        self.assertIn("ci", payload["available_evidence_ids"])

    def test_irreducible_critical_evidence_fails_without_provider(self):
        self.tasks[1].title = "Critical task " * 4000
        provider = CaptureProvider(context_tokens=4096)
        with self.assertRaises(ContextBudgetExceededError):
            ProjectMonitoringAgent(provider).run("p", self.metrics, self.tasks)
        self.assertEqual(provider.requests, [])

    def test_quality_keeps_whole_code_and_boundaries(self):
        provider = CaptureProvider()
        CodeQualityAgent(provider).run("p", MonitoringDraft(summary="draft"), [self.snippet], rules=[self.rule])
        self.assertEqual(provider.requests[0].evidences["code_snippets"], [self.snippet.model_dump()])

    def test_verifier_scopes_snippets_rules_and_evidence_to_findings(self):
        other = self.snippet.model_copy(update={"file_path": "unrelated.py", "content": "x" * 20000})
        disjoint = self.snippet.model_copy(update={"lines": [99], "start_line": 99, "end_line": 99, "content": "x" * 20000})
        other_rule = self.rule.model_copy(update={"id": "OTHER", "description": "x" * 10000})
        provider = CaptureProvider()
        VerifierAgent(provider).run("p", [self.finding], [self.snippet, other, disjoint], [self.rule, other_rule], ["sha", "unrelated"])
        payload = provider.requests[0].evidences
        self.assertEqual(payload["code_snippets"], [self.snippet.model_dump()])
        self.assertEqual(payload["rules"], [self.rule.model_dump()])
        self.assertEqual(payload["available_evidence_ids"], ["sha"])

    def test_complete_file_absence_proof_is_not_cut_by_selection(self):
        full = CodeDiffSnippet(repository="r", commit_sha="sha", file_path="f.py", lines=[1, 2],
            content="def f():\n    pass\n", evidence_scope="full_file", is_complete=True, total_lines=2)
        finding = self.finding.model_copy(update={"claim_basis": "file_absence", "claim": "The function is not implemented."})
        provider = CaptureProvider()
        VerifierAgent(provider).run("p", [finding], [full], [self.rule], ["sha"])
        self.assertEqual(provider.requests[0].evidences["code_snippets"], [full.model_dump()])

    def test_full_serialized_prompt_and_output_fit_effective_window(self):
        provider = CaptureProvider(context_tokens=4096)
        ProjectMonitoringAgent(provider).run("p", self.metrics, self.tasks)
        request = provider.requests[0]
        metadata = request.evidences["context_metadata"]
        wire = {"model": request.model, "messages": [{"role": "system", "content": request.instructions},
                {"role": "user", "content": json.dumps(request.evidences, default=str)}], "format": request.json_schema}
        self.assertLessEqual(estimate_tokens_from_data(wire) + request.max_generation_tokens, 4096)
        self.assertLessEqual(metadata["estimated_prompt_tokens"] + metadata["reserved_output_tokens"] + metadata["safety_margin_tokens"], 4096)
        self.assertLessEqual(metadata["estimated_tokens"], metadata["budget_tokens"])
        self.assertIn("estimate", metadata["estimation_method"])
        self.assertEqual(metadata["reserved_output_tokens"], request.max_generation_tokens)

    def test_unsupported_agent_output_reservation_fails_before_provider(self):
        provider = CaptureProvider(max_output_tokens=128)
        minimal_metrics = self.metrics.model_copy(update={"overdue_card_ids": [], "unknown_card_ids": [],
                                                          "contradictory_card_ids": []})
        task = TaskCard(id="T", title="Minimal task", state=TaskState.in_progress)
        with self.assertRaises(ContextBudgetExceededError):
            ProjectMonitoringAgent(provider).run("p", minimal_metrics, [task])
        self.assertEqual(provider.requests, [])

    def test_ollama_transport_receives_existing_window_and_exact_output(self):
        transport = Mock(return_value={"body": {"message": {"content": "{}"}, "done": True}})
        provider = OllamaProvider(context_tokens=3072, max_output_tokens=256, transport=transport)
        request = GenerationRequest(role="test", instructions="test", model="test", max_generation_tokens=128)
        self.assertEqual(provider.generate(request).status.value, "complete")
        body = transport.call_args.kwargs["payload"]
        self.assertEqual(body["options"], {"num_ctx": 3072, "num_predict": 128})
        transport.reset_mock()
        request.max_generation_tokens = 257
        self.assertEqual(provider.generate(request).status.value, "error")
        transport.assert_not_called()

    def test_monitoring_rejects_mentions_of_omitted_task_evidence(self):
        provider = CaptureProvider(context_tokens=3072)
        ProjectMonitoringAgent(provider).run("p", self.metrics, self.tasks)
        selected_ids = set(provider.requests[0].evidences["selection"]["selected_task_ids"])
        omitted = sorted({task.id for task in self.tasks} - selected_ids)
        self.assertTrue(omitted)
        title = next(task.title for task in self.tasks if task.id == omitted[0])
        provider = CaptureProvider(context_tokens=3072)
        provider.enqueue_responses([GenerationResult(status="complete", structured_data={"summary": f"{title} is listed."},
                                   provider="fake", model="fake", duration_seconds=0)] * 2)
        from agente_gestion.application.validation import ValidationError
        with self.assertRaises(ValidationError):
            ProjectMonitoringAgent(provider).run("p", self.metrics, self.tasks)

    def test_missing_required_verifier_location_fails_before_provider(self):
        provider = CaptureProvider()
        unrelated = self.snippet.model_copy(update={"file_path": "unrelated.py"})
        with self.assertRaises(ValueError):
            VerifierAgent(provider).run("p", [self.finding], [unrelated], [self.rule], ["sha"])
        self.assertEqual(provider.requests, [])

    def test_missing_cited_verifier_evidence_fails_before_provider(self):
        provider = CaptureProvider()
        with self.assertRaises(ValueError):
            VerifierAgent(provider).run("p", [self.finding], [self.snippet], [self.rule], ["unrelated"])
        self.assertEqual(provider.requests, [])

    def test_monitoring_through_ollama_fake_transport_has_no_window_expansion(self):
        bodies = []
        def transport(*args, payload=None, **kwargs):
            bodies.append(payload)
            return {"body": {"message": {"content": json.dumps({"summary": "Global progress summary"})}, "done": True}}
        provider = OllamaProvider(context_tokens=4096, max_output_tokens=768, transport=transport)
        ProjectMonitoringAgent(provider).run("p", self.metrics, self.tasks)
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0]["options"], {"num_ctx": 4096, "num_predict": 512})

    def test_mismatched_ci_is_not_selected_or_citable(self):
        ci = CIRunResult(id="wrong-ci", workflow_name="tests", commit_sha="other", status="success")
        provider = CaptureProvider(context_tokens=4096)
        ProjectMonitoringAgent(provider).run("p", self.metrics, self.tasks, ci, ["wrong-ci"], source_commit_sha="sha")
        payload = provider.requests[0].evidences
        self.assertIsNone(payload["ci_result"])
        self.assertEqual(payload["ci_availability"], "unavailable")
        self.assertNotIn("wrong-ci", payload["available_evidence_ids"])

    def test_verifier_ambiguous_diff_sides_fail_before_provider(self):
        provider = CaptureProvider()
        old = self.snippet.model_copy(update={"diff_side": "OLD"})
        with self.assertRaises(ValueError):
            VerifierAgent(provider).run("p", [self.finding], [self.snippet, old], [self.rule], ["sha"])
        self.assertEqual(provider.requests, [])

    def test_verifier_explicit_output_choice_matches_configured_capability(self):
        provider = CaptureProvider(context_tokens=4096, max_output_tokens=768)
        VerifierAgent(provider).run("p", [self.finding], [self.snippet], [self.rule], ["sha"])
        request = provider.requests[0]
        self.assertEqual(request.max_generation_tokens, 768)
        self.assertEqual(request.evidences["context_metadata"]["reserved_output_tokens"], 768)

    def test_monitoring_selection_is_relevance_driven_not_budget_filling(self):
        provider = CaptureProvider(context_tokens=4096)
        ProjectMonitoringAgent(provider).run("p", self.metrics, self.tasks)
        payload = provider.requests[0].evidences
        self.assertEqual({row["id"] for row in payload["tasks"]}, {"T1", "T2"})
        self.assertEqual(payload["selection"]["omitted_task_count"], 29)
        self.assertNotIn("omitted_task_ids", payload["selection"])
        self.assertEqual(payload["selection"]["reason"], "active_and_problem_cards")
        self.assertEqual(payload["metrics"], self.metrics.model_dump())
        self.assertEqual(set(payload["available_evidence_ids"]), {"T1", "T2"})

    def test_no_active_or_problem_tasks_use_only_one_explained_representative(self):
        tasks = [TaskCard(id="C1", title="Completed example", state=TaskState.completed),
                 TaskCard(id="P1", title="Pending example", state=TaskState.pending),
                 TaskCard(id="C2", title="Other completed item", state=TaskState.completed)]
        metrics = self.metrics.model_copy(update={"total_cards": 3, "included_cards": 3, "completed_cards": 2,
            "progress_percentage": 66.7, "overdue_card_ids": [], "unknown_card_ids": [], "contradictory_card_ids": []})
        provider = CaptureProvider(context_tokens=4096)
        ProjectMonitoringAgent(provider).run("p", metrics, tasks)
        payload = provider.requests[0].evidences
        self.assertEqual(len(payload["tasks"]), 1)
        self.assertEqual(payload["selection"]["reason"], "single_representative_for_global_progress")
        self.assertEqual(payload["selection"]["omitted_task_count"], 2)
