"""Tests for explicit agents, stage invocation, budget, and retries."""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from agente_gestion.domain.tasks import TaskCard, TaskState, DeterministicTaskMetrics
from agente_gestion.domain.code import CodeDiffSnippet, CIRunResult
from agente_gestion.domain.policies import (
    QualityRule,
    PolicyEvaluationResult,
    RuleEvaluation,
    RuleEvaluationStatus,
)
from agente_gestion.domain.findings import (
    CandidateFinding,
    FindingCategory,
    FindingVerification,
    SuggestedCodeValidationStatus,
    VerificationVerdict,
)
from agente_gestion.domain.report import MonitoringDraft
from agente_gestion.domain.llm import GenerationStatus, GenerationResult
from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.agents.monitoring import ProjectMonitoringAgent
from agente_gestion.agents.quality import CodeQualityAgent
from agente_gestion.agents.verifier import VerifierAgent
from agente_gestion.agents.report import ReportAgent
from agente_gestion.application.generation import (
    StageInvocationCoordinator,
    GlobalBudgetTracker,
    GlobalBudgetExceededError,
)
from agente_gestion.application.validation import ValidationError


class AgentsAndGenerationTestCase(unittest.TestCase):
    def setUp(self):
        self.tz = ZoneInfo("America/Guayaquil")
        self.cutoff = datetime(2026, 10, 2, 17, 0, 0, tzinfo=self.tz)
        self.metrics = DeterministicTaskMetrics(
            total_cards=3,
            included_cards=3,
            completed_cards=1,
            progress_percentage=33.3,
            overdue_card_ids=["T2"],
            blocked_card_ids=["T3"],
            unknown_card_ids=[],
            contradictory_card_ids=[],
        )
        self.tasks = [
            TaskCard(id="T1", title="Feature A", state=TaskState.completed, is_included=True),
            TaskCard(id="T2", title="Feature B", state=TaskState.in_progress, is_included=True),
            TaskCard(id="T3", title="Feature C", state=TaskState.blocked, is_included=True),
        ]
        self.snippets = [
            CodeDiffSnippet(
                repository="repo-main",
                commit_sha="c0ffee1",
                file_path="src/service.py",
                diff_side="NEW",
                lines=[14, 15, 16],
                content="def compute():\n    return 1 / total\n",
            )
        ]
        self.policy_result = PolicyEvaluationResult(
            evaluations=[
                RuleEvaluation(
                    rule_id="Q_PASS",
                    description="Passing rule",
                    status=RuleEvaluationStatus.pass_,
                    observed_value=0,
                    mandatory=True,
                )
            ],
            passed_rule_ids=["Q_PASS"],
            failed_rule_ids=[],
            unknown_rule_ids=[],
            mandatory_failed_ids=[],
            mandatory_unknown_ids=[],
            recommended_action="recomendar_aprobacion",
        )

    # --- Agent Implementations and Defaults ---

    def test_project_monitoring_agent_uses_gemini_default(self):
        fake = FakeProvider()
        agent = ProjectMonitoringAgent(provider=fake)
        self.assertEqual(agent.model, "gemini-3.5-flash-lite")
        draft = agent.run(
            project_id="proj-1",
            metrics=self.metrics,
            tasks=self.tasks,
            evidence_ids=["ev-1"],
        )
        self.assertIsInstance(draft, MonitoringDraft)
        # Priority order exposes active/blocked signals before completed tasks.
        self.assertIn("T2", draft.referenced_task_ids)

    def test_code_quality_agent_uses_groq_default(self):
        fake = FakeProvider()
        agent = CodeQualityAgent(provider=fake)
        self.assertEqual(agent.model, "llama-3.3-70b-versatile")
        draft = MonitoringDraft(
            summary="Progress steady",
            risks=[],
            suggested_actions=[],
            referenced_task_ids=["T1"],
            evidence_ids=["ev-1"],
        )
        rule = QualityRule(
            id="Q3",
            description="Zero critical findings",
            metric_key="open_critical_findings",
            operator="==",
            threshold=0,
            mandatory=True,
        )
        findings = agent.run(
            project_id="proj-1",
            draft=draft,
            snippets=self.snippets,
            criteria=["No zero division"],
            rules=[rule],
            evidence_ids=["c0ffee1"],
        )
        self.assertIsInstance(findings, list)
        self.assertTrue(len(findings) > 0)
        # Findings must be CandidateFinding and NOT automatically confirmed!
        self.assertIsInstance(findings[0], CandidateFinding)
        self.assertEqual(findings[0].rule_id, "Q3")
        self.assertIn("c0ffee1", findings[0].evidence_ids)

    def test_invalid_empty_or_null_finding_fails_through_agent_coordinator(self):
        """RED assertion: invalid empty/null findings fail closed through actual agent coordinator."""
        fake = FakeProvider()
        # Enqueue 1st response with null rule_id and empty evidence_ids, then repair with empty rule_id
        fake.enqueue_responses([
            GenerationResult(
                status=GenerationStatus.complete,
                structured_data={
                    "findings": [
                        {
                            "id": "F-001",
                            "category": "Seguridad",
                            "file_path": "src/service.py",
                            "line": 15,
                            "commit_sha": "c0ffee1",
                            "evidence": "1 / total",
                            "suggested_fix": "fix",
                            "rule_id": None,
                            "evidence_ids": [],
                            "confidence": 0.95,
                        }
                    ]
                },
                provider="fake_simulated",
                model="qwen3.5:4b-q4_K_M",
                duration_seconds=0.01,
            ),
            GenerationResult(
                status=GenerationStatus.complete,
                structured_data={
                    "findings": [
                        {
                            "id": "F-001",
                            "category": "Seguridad",
                            "file_path": "src/service.py",
                            "line": 15,
                            "commit_sha": "c0ffee1",
                            "evidence": "1 / total",
                            "suggested_fix": "fix",
                            "rule_id": "",
                            "evidence_ids": [],
                            "confidence": 0.95,
                        }
                    ]
                },
                provider="fake_simulated",
                model="qwen3.5:4b-q4_K_M",
                duration_seconds=0.01,
            ),
        ])
        coordinator = StageInvocationCoordinator(
            budget_tracker=GlobalBudgetTracker(max_calls=8),
        )
        agent = CodeQualityAgent(provider=fake, coordinator=coordinator)
        draft = MonitoringDraft(
            summary="Progress steady",
            risks=[],
            suggested_actions=[],
            referenced_task_ids=["T1"],
            evidence_ids=["ev-1"],
        )
        rule = QualityRule(
            id="Q3",
            description="Zero critical findings",
            metric_key="open_critical_findings",
            operator="==",
            threshold=0,
            mandatory=True,
        )
        with self.assertRaises(ValidationError) as ctx:
            agent.run(
                project_id="proj-1",
                draft=draft,
                snippets=self.snippets,
                rules=[rule],
                evidence_ids=["c0ffee1"],
            )
        err_msg = str(ctx.exception).lower()
        self.assertTrue(
            "rule_id" in err_msg or "evidence_ids" in err_msg or "malformed" in err_msg
        )

    def test_verifier_agent_selective_claims_verification(self):
        fake = FakeProvider()
        agent = VerifierAgent(provider=fake, model="gemini-2.5-flash")
        finding = CandidateFinding(
            id="f-1",
            rule_id=None,  # No policy supplied: explicitly unassociated.
            category="division_cero",
            file_path="src/service.py",
            line=15,
            commit_sha="c0ffee1",
            evidence="1 / total",
            suggested_fix="check total != 0",
            evidence_ids=["ev-1"],
        )
        verifications = agent.run(
            project_id="proj-1",
            findings=[finding],
            snippets=self.snippets,
            rules=[],
            evidence_ids=["ev-1"],
        )
        self.assertEqual(len(verifications), 1)
        self.assertIsInstance(verifications[0], FindingVerification)
        self.assertEqual(verifications[0].finding_id, "f-1")
        # Verification references subset of associated evidence
        for eid in verifications[0].evidence_ids:
            self.assertIn(eid, finding.evidence_ids)

    def test_verifier_agent_default_timeout_is_120_and_forwarded_to_request(self):
        """VerifierAgent must default to 120.0 and forward it to GenerationRequest.timeout_seconds."""
        class CapturingFakeProvider(FakeProvider):
            def __init__(self):
                super().__init__()
                self.captured_requests = []
            def generate(self, request):
                self.captured_requests.append(request)
                return super().generate(request)

        provider = CapturingFakeProvider()
        agent = VerifierAgent(provider=provider, model="gemini-2.5-flash")
        self.assertEqual(agent.timeout_seconds, 120.0)

        finding = CandidateFinding(
            id="f-1",
            rule_id=None,  # No policy supplied: explicitly unassociated.
            category="division_cero",
            file_path="src/service.py",
            line=15,
            commit_sha="c0ffee1",
            evidence="1 / total",
            suggested_fix="check total != 0",
            evidence_ids=["ev-1"],
        )
        agent.run(
            project_id="proj-1",
            findings=[finding],
            snippets=self.snippets,
            rules=[],
            evidence_ids=["ev-1"],
        )
        self.assertEqual(len(provider.captured_requests), 1)
        self.assertEqual(provider.captured_requests[0].timeout_seconds, 120.0)

    def test_verifier_agent_custom_timeout_forwarded_to_request(self):
        """VerifierAgent with custom timeout_seconds (e.g. 220.0 or 88.0) forwards it to GenerationRequest."""
        class CapturingFakeProvider(FakeProvider):
            def __init__(self):
                super().__init__()
                self.captured_requests = []
            def generate(self, request):
                self.captured_requests.append(request)
                return super().generate(request)

        # Legacy 220.0 override
        provider_220 = CapturingFakeProvider()
        agent_220 = VerifierAgent(provider=provider_220, timeout_seconds=220.0)
        self.assertEqual(agent_220.timeout_seconds, 220.0)

        # Custom sentinel 88.0
        provider_88 = CapturingFakeProvider()
        agent_88 = VerifierAgent(provider=provider_88, timeout_seconds=88.0)
        self.assertEqual(agent_88.timeout_seconds, 88.0)

        finding = CandidateFinding(
            id="f-1",
            rule_id=None,  # No policy supplied: explicitly unassociated.
            category="division_cero",
            file_path="src/service.py",
            line=15,
            commit_sha="c0ffee1",
            evidence="1 / total",
            suggested_fix="check total != 0",
            evidence_ids=["ev-1"],
        )
        agent_88.run(
            project_id="proj-1",
            findings=[finding],
            snippets=self.snippets,
            rules=[],
            evidence_ids=["ev-1"],
        )
        self.assertEqual(len(provider_88.captured_requests), 1)
        self.assertEqual(provider_88.captured_requests[0].timeout_seconds, 88.0)

    def test_verifier_agent_rejects_non_positive_timeout(self):
        """VerifierAgent must reject non-positive timeout_seconds."""
        fake = FakeProvider()
        with self.assertRaises(ValueError):
            VerifierAgent(provider=fake, timeout_seconds=0.0)
        with self.assertRaises(ValueError):
            VerifierAgent(provider=fake, timeout_seconds=-5.0)

    def test_report_agent_uses_gemini_and_respects_deterministic_policy(self):
        fake = FakeProvider()
        agent = ReportAgent(provider=fake)
        self.assertEqual(agent.model, "gemini-3.5-flash-lite")
        draft = MonitoringDraft(
            summary="Progress 33.3%",
            risks=[],
            suggested_actions=[],
            referenced_task_ids=["T1"],
            evidence_ids=["ev-1"],
        )
        report = agent.run(
            project_id="proj-1",
            cutoff_datetime=self.cutoff,
            metrics=self.metrics,
            draft=draft,
            confirmed_findings=[],
            rejected_findings=[],
            insufficient_findings=[],
            pending_findings=[],
            policy_evaluations=self.policy_result,
        )
        self.assertIn("aprobacion", report.recommendation.lower())

    # --- Coordination: Budget, Retries, Repair, and Recording ---

    def test_global_budget_shared_max_8_calls(self):
        budget = GlobalBudgetTracker(max_calls=8)
        # Consume 8 calls
        for _ in range(8):
            budget.consume_call()
        self.assertEqual(budget.total_calls_made, 8)
        # 9th call must raise GlobalBudgetExceededError
        with self.assertRaises(GlobalBudgetExceededError):
            budget.consume_call()

    def test_repair_attempt_for_invalid_output_max_one(self):
        fake = FakeProvider()
        # T2 is selected active evidence; completed T1 is intentionally omitted.
        # Enqueue: 1st call returns invalid data, 2nd call (repair) returns valid data
        fake.enqueue_responses([
            GenerationResult(
                status=GenerationStatus.complete,
                structured_data={"summary": "Invented", "referenced_task_ids": ["T_UNKNOWN_99"],
                                 "evidence_ids": ["T_UNKNOWN_99"]},
                provider="fake_simulated",
                model="qwen3.5:2b-q4_K_M",
                duration_seconds=0.1,
            ),
            GenerationResult(
                status=GenerationStatus.complete,
                structured_data={"summary": "Fixed", "referenced_task_ids": ["T2"], "evidence_ids": ["T2"]},
                provider="fake_simulated",
                model="qwen3.5:2b-q4_K_M",
                duration_seconds=0.1,
            ),
        ])
        coordinator = StageInvocationCoordinator(
            budget_tracker=GlobalBudgetTracker(max_calls=8),
        )
        agent = ProjectMonitoringAgent(provider=fake, coordinator=coordinator)
        draft = agent.run(
            project_id="proj-1",
            metrics=self.metrics,
            tasks=self.tasks,
            evidence_ids=["ev-1"],
        )
        self.assertEqual(draft.summary, "Fixed")
        # Exactly 2 calls were made (1 initial + 1 repair)
        self.assertEqual(coordinator.budget_tracker.total_calls_made, 2)
        # Both attempts recorded
        self.assertEqual(len(coordinator.recorded_attempts), 2)
        self.assertFalse(coordinator.recorded_attempts[0].validation_passed)
        self.assertTrue(coordinator.recorded_attempts[1].validation_passed)

    def test_repair_attempt_fails_if_repair_also_invalid_no_unbounded_loop(self):
        fake = FakeProvider()
        # Enqueue 3 invalid responses, but coordinator must stop after 1 repair (total 2 attempts)
        fake.enqueue_responses([
            GenerationResult(
                status=GenerationStatus.complete,
                structured_data={"summary": "Invented 1", "referenced_task_ids": ["T_BAD_1"]},
                provider="fake_simulated",
                model="qwen3.5:2b-q4_K_M",
                duration_seconds=0.1,
            ),
            GenerationResult(
                status=GenerationStatus.complete,
                structured_data={"summary": "Invented 2", "referenced_task_ids": ["T_BAD_2"]},
                provider="fake_simulated",
                model="qwen3.5:2b-q4_K_M",
                duration_seconds=0.1,
            ),
            GenerationResult(
                status=GenerationStatus.complete,
                structured_data={"summary": "Invented 3", "referenced_task_ids": ["T_BAD_3"]},
                provider="fake_simulated",
                model="qwen3.5:2b-q4_K_M",
                duration_seconds=0.1,
            ),
        ])
        coordinator = StageInvocationCoordinator(
            budget_tracker=GlobalBudgetTracker(max_calls=8),
        )
        agent = ProjectMonitoringAgent(provider=fake, coordinator=coordinator)
        with self.assertRaises(ValidationError):
            agent.run(
                project_id="proj-1",
                metrics=self.metrics,
                tasks=self.tasks,
                evidence_ids=["ev-1"],
            )
        # Stopped at 2 calls! At most ONE repair attempt!
        self.assertEqual(coordinator.budget_tracker.total_calls_made, 2)

    def test_transient_error_retry_succeeds(self):
        fake = FakeProvider()
        # T2 is selected active evidence; completed T1 is intentionally omitted.
        # 1st call transient error, 2nd call succeeds
        fake.enqueue_responses([
            GenerationResult(
                status=GenerationStatus.error,
                error_message="503 Service Unavailable",
                provider="fake_simulated",
                model="qwen3.5:2b-q4_K_M",
                duration_seconds=0.05,
            ),
            GenerationResult(
                status=GenerationStatus.complete,
                structured_data={"summary": "Recovered", "referenced_task_ids": ["T2"], "evidence_ids": ["T2"]},
                provider="fake_simulated",
                model="qwen3.5:2b-q4_K_M",
                duration_seconds=0.1,
            ),
        ])
        coordinator = StageInvocationCoordinator(
            budget_tracker=GlobalBudgetTracker(max_calls=8),
        )
        agent = ProjectMonitoringAgent(provider=fake, coordinator=coordinator)
        draft = agent.run(
            project_id="proj-1",
            metrics=self.metrics,
            tasks=self.tasks,
            evidence_ids=["ev-1"],
        )
        self.assertEqual(draft.summary, "Recovered")
        self.assertEqual(coordinator.budget_tracker.total_calls_made, 2)

    def test_no_auth_or_config_retries(self):
        fake = FakeProvider()
        # HTTP 401 Unauthorized must abort immediately without retry
        fake.enqueue_responses([
            GenerationResult(
                status=GenerationStatus.error,
                error_message="401 Unauthorized: invalid API key",
                provider="fake_simulated",
                model="gemini-2.5-flash",
                duration_seconds=0.05,
            ),
            GenerationResult(
                status=GenerationStatus.complete,
                structured_data={"verifications": []},
                provider="fake_simulated",
                model="gemini-2.5-flash",
                duration_seconds=0.05,
            ),
        ])
        coordinator = StageInvocationCoordinator(
            budget_tracker=GlobalBudgetTracker(max_calls=8),
        )
        agent = VerifierAgent(provider=fake, coordinator=coordinator)
        sample_finding = CandidateFinding(
            id="f-1",
            category="division_cero",
            file_path="src/service.py",
            line=15,
            commit_sha="c0ffee1",
            evidence="1 / total",
            suggested_fix="check total != 0",
            evidence_ids=["ev-1"],
        )
        with self.assertRaises(RuntimeError):
            agent.run(
                project_id="proj-1",
                findings=[sample_finding],
                snippets=self.snippets,
                rules=[],
                evidence_ids=["ev-1"],
            )
        # Exactly 1 call was made (no retry on auth error)
        self.assertEqual(coordinator.budget_tracker.total_calls_made, 1)

    def test_no_silent_fallback_to_cloud(self):
        # When local provider fails, the coordinator does NOT silently invoke Gemini
        fake = FakeProvider()
        fake.enqueue_responses([
            GenerationResult(
                status=GenerationStatus.error,
                error_message="Connection refused to Ollama",
                provider="ollama",
                model="qwen3.5:2b-q4_K_M",
                duration_seconds=0.05,
            ),
            GenerationResult(
                status=GenerationStatus.error,
                error_message="Connection refused to Ollama",
                provider="ollama",
                model="qwen3.5:2b-q4_K_M",
                duration_seconds=0.05,
            ),
        ])
        coordinator = StageInvocationCoordinator()
        agent = ProjectMonitoringAgent(provider=fake, coordinator=coordinator)
        with self.assertRaises(RuntimeError):
            agent.run(
                project_id="proj-1",
                metrics=self.metrics,
                tasks=self.tasks,
                evidence_ids=["ev-1"],
            )
        # Provider remained fake/ollama, never switched to cloud
        self.assertEqual(coordinator.recorded_attempts[0].provider, "ollama")

    def test_empty_evidence_ids_rejects_invented_citations(self):
        fake = FakeProvider()
        fake.enqueue_responses([
            GenerationResult(
                status=GenerationStatus.complete,
                structured_data={
                    "summary": "Valid summary",
                    "risks": [],
                    "suggested_actions": [],
                    "referenced_task_ids": ["T1"],
                    "evidence_ids": ["fabricated_ev_123"],
                },
                provider="fake_simulated",
                model="qwen3.5:2b-q4_K_M",
                duration_seconds=0.05,
            ),
            GenerationResult(
                status=GenerationStatus.complete,
                structured_data={
                    "summary": "Valid summary 2",
                    "risks": [],
                    "suggested_actions": [],
                    "referenced_task_ids": ["T1"],
                    "evidence_ids": ["fabricated_ev_123"],
                },
                provider="fake_simulated",
                model="qwen3.5:2b-q4_K_M",
                duration_seconds=0.05,
            ),
        ])
        coordinator = StageInvocationCoordinator(
            budget_tracker=GlobalBudgetTracker(max_calls=8),
        )
        agent = ProjectMonitoringAgent(provider=fake, coordinator=coordinator)
        # Empty evidence_ids supplied
        with self.assertRaises(ValidationError) as ctx:
            agent.run(
                project_id="proj-1",
                metrics=self.metrics,
                tasks=self.tasks,
                evidence_ids=[],
            )
        self.assertIn("fabricated_ev_123", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


class RuntimeContextRegressionTests(unittest.TestCase):
    setUp = AgentsAndGenerationTestCase.setUp
    def test_partial_evidence_keeps_other_roles_bounded_and_verifier_fails_closed(self):
        from agente_gestion.agents.context import ContextBuilder
        from unittest.mock import Mock
        builder = ContextBuilder()
        for method in ("build_monitoring_context", "build_code_context", "build_verifier_context"):
            setattr(builder, method, Mock(wraps=getattr(builder, method)))
        class Capture(FakeProvider):
            def __init__(self):
                super().__init__()
                self.requests = []
            def generate(self, request):
                self.request = request
                self.requests.append(request)
                return super().generate(request)
        provider = Capture()
        draft = ProjectMonitoringAgent(provider, context_builder=builder).run("p", self.metrics, self.tasks, evidence_ids=["ev-1"])
        rule = QualityRule(id="Q3", description="Review", source="review", metric_key="open_critical_findings", operator="==", threshold=0)
        findings = CodeQualityAgent(provider, context_builder=builder).run("p", draft, self.snippets, rules=[rule])
        quality_request = provider.request
        verifier = VerifierAgent(provider, context_builder=builder)
        verifications = verifier.run("p", findings, self.snippets, rules=[rule], evidence_ids=["c0ffee1"])
        self.assertEqual(len(verifications), len(findings))
        self.assertEqual(len(provider.requests), 3)
        self.assertTrue(all(item.suggested_code_validation_status == SuggestedCodeValidationStatus.not_validated for item in verifications))
        self.assertIs(provider.request, provider.requests[-1])
        builder.build_monitoring_context.assert_called_once()
        builder.build_code_context.assert_called_once()
        builder.build_verifier_context.assert_called_once()
        for request in (quality_request, provider.request):
            self.assertEqual(request.evidences["context_metadata"]["status"], "complete")
            self.assertFalse(request.evidences["code_snippets"][0]["is_complete"])
            self.assertIn("fin del fragmento no es el fin del archivo", request.instructions)
            self.assertIn("full_file", request.instructions)

    def test_context_overflow_stops_all_roles_before_provider(self):
        from agente_gestion.agents.context import ContextBuilder, ContextBudgetExceededError
        from unittest.mock import Mock
        provider = FakeProvider()
        provider.generate = Mock(side_effect=AssertionError("provider must not be invoked"))
        builder = ContextBuilder(default_budget_tokens=1)
        draft = MonitoringDraft(summary="draft")
        finding = CandidateFinding(id="C1", rule_id="Q3", claim="claim", severity="high",
                                   evidence_ids=["c0ffee1"], commit_sha="c0ffee1", file_path="src/service.py", line=14, confidence=.8)
        calls = [lambda: ProjectMonitoringAgent(provider, context_builder=builder).run("p", self.metrics, self.tasks),
                 lambda: CodeQualityAgent(provider, context_builder=builder).run("p", draft, self.snippets),
                 lambda: VerifierAgent(provider, context_builder=builder).run("p", [finding], self.snippets, evidence_ids=["c0ffee1"])]
        for call in calls:
            with self.assertRaises(ContextBudgetExceededError) as caught:
                call()
            self.assertIn("Request additional context", str(caught.exception))
        provider.generate.assert_not_called()
