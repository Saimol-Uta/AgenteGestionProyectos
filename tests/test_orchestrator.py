"""Unit tests for modular Orchestrator routing, stage sequencing, and invariants."""

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.domain.tasks import TaskCard, TaskState
from agente_gestion.domain.code import CodeDiffSnippet, CIRunResult
from agente_gestion.domain.policies import QualityRule
from agente_gestion.domain.findings import CandidateFinding, FindingCategory
from agente_gestion.domain.run import RunStatus, StageStatus
from agente_gestion.domain.report import HumanDecisionState
from agente_gestion.domain.llm import GenerationResult, GenerationStatus
from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.adapters.sources.fixtures import FixtureSourcesAdapter
from agente_gestion.ports.source_collection import (
    SourceCollectionResult,
    SourceExecutionRecord,
    SourceExecutionStatus,
)
from agente_gestion.ports.source_errors import SourceProvider
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.adapters.storage.artifacts import ArtifactStorageAdapter
from agente_gestion.application.orchestrator import ProjectOrchestrator, OrchestratorConfig
from agente_gestion.application.escalation import EscalationGate, EscalationPolicy


SAMPLE_COMMIT_SHA = "c" * 40
SAMPLE_SOURCE = "def calculate_average(total): return 1 / total"


class OrchestratorTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.runs_dir = os.path.join(self.temp_dir, "runs")
        self.tz = ZoneInfo("America/Guayaquil")
        self.now = datetime(2026, 10, 2, 17, 0, 0, tzinfo=self.tz)

        self.sqlite = SQLiteStorageAdapter(db_path=self.db_path)
        self.artifacts = ArtifactStorageAdapter(base_dir=self.runs_dir)

        # Reusable Fake Provider
        self.fake_provider = FakeProvider()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _complete_code_diff(self) -> CodeDiffSnippet:
        return CodeDiffSnippet(
            repository="org/repo",
            commit_sha=SAMPLE_COMMIT_SHA,
            file_path="src/service.py",
            lines=[1],
            content=SAMPLE_SOURCE,
            evidence_scope="full_file",
            is_complete=True,
            start_line=1,
            end_line=1,
            total_lines=1,
            source_sha256=hashlib.sha256(SAMPLE_SOURCE.encode("utf-8")).hexdigest(),
        )

    def _sample_snapshot_with_code(self) -> WeeklySnapshot:
        return WeeklySnapshot(
            id="snap-code",
            project_id="test-proj",
            cutoff_datetime=self.now,
            captured_at=self.now,
            tasks=[
                TaskCard(id="T1", title="Task 1", state=TaskState.completed, is_included=True),
                TaskCard(id="T2", title="Task 2", state=TaskState.in_progress, is_included=True),
            ],
            code_diffs=[self._complete_code_diff()],
            ci_runs=[
                CIRunResult(
                    id="ci-1",
                    workflow_name="tests",
                    commit_sha=SAMPLE_COMMIT_SHA,
                    status="success",
                    total_tests=10,
                    failed_tests=0,
                )
            ],
            policies=[
                QualityRule(id="Q1", description="Tests pass", metric_key="failed_tests", operator="==", threshold=0)
            ],
            is_complete=True,
        )

    def _sample_snapshot_no_code(self) -> WeeklySnapshot:
        return WeeklySnapshot(
            id="snap-no-code",
            project_id="test-no-code",
            cutoff_datetime=self.now,
            captured_at=self.now,
            tasks=[
                TaskCard(id="T1", title="Task 1", state=TaskState.completed, is_included=True),
            ],
            code_diffs=[],  # No code diffs
            ci_runs=[],
            policies=[],
            is_complete=True,
        )

    def test_singleton_source_summary_keeps_legacy_shape_when_diagnostics_are_absent(self):
        snapshot = WeeklySnapshot(
            id="singleton-source",
            project_id="source-legacy",
            cutoff_datetime=self.now,
            captured_at=self.now,
            tasks=[],
            code_diffs=[],
            ci_runs=[],
            policies=[],
            is_complete=True,
        )
        result = SourceCollectionResult(
            snapshot=snapshot,
            target_commit_sha=None,
            records=[SourceExecutionRecord(
                provider=SourceProvider.local_policy,
                status=SourceExecutionStatus.success,
                duration_seconds=0.25,
            )],
        )

        class FixedSource:
            def collect_snapshot(self, project_id, cutoff_datetime, commit_sha=None):
                return result

        orchestrator = ProjectOrchestrator(
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=self.fake_provider,
            revisor_provider=self.fake_provider,
            verificador_provider=self.fake_provider,
            aprobador_provider=self.fake_provider,
            source_adapter=FixedSource(),
            config=OrchestratorConfig(project_id="source-legacy", allow_remote_ai=False),
        )
        run = orchestrator.execute()
        stage = self.sqlite.get_stage_results(run.run_id)["source_collection"]
        summary = json.loads(stage.output_summary)
        metrics_path = os.path.join(self.runs_dir, run.run_id, "execution_metrics.json")
        with open(metrics_path, encoding="utf-8") as artifact_file:
            execution_metrics = json.load(artifact_file)

        # Differential compatibility with the singleton serializer at baseline 8e7d970f.
        baseline_summary = json.loads(json.dumps({
            "target_commit_sha": result.target_commit_sha,
            "is_complete": result.snapshot.is_complete,
            "records": [record.model_dump() for record in result.records],
        }))
        self.assertEqual(summary, baseline_summary)
        from agente_gestion.domain.run import compute_sha256
        self.assertEqual(
            stage.data_hash, compute_sha256([record.model_dump() for record in result.records]),
        )
        self.assertEqual(set(summary), {"target_commit_sha", "is_complete", "records"})
        self.assertEqual(execution_metrics["source_collection"], summary)

        with open(metrics_path, "rb") as artifact_file:
            historical_artifact = artifact_file.read()
        repeated_run = orchestrator.execute()
        with open(metrics_path, "rb") as artifact_file:
            self.assertEqual(artifact_file.read(), historical_artifact)
        self.assertEqual(repeated_run.run_id, run.run_id)
        self.assertEqual(
            self.sqlite.get_stage_results(run.run_id)["source_collection"].output_summary,
            stage.output_summary,
        )

    def test_orchestrator_happy_path_positive_flow(self):
        snapshot = self._sample_snapshot_with_code()
        config = OrchestratorConfig(project_id="test-proj", allow_remote_ai=True)

        orchestrator = ProjectOrchestrator(
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=self.fake_provider,
            revisor_provider=self.fake_provider,
            verificador_provider=self.fake_provider,
            aprobador_provider=self.fake_provider,
            config=config,
        )

        run = orchestrator.execute(snapshot=snapshot)

        self.assertEqual(run.status, RunStatus.awaiting_human)
        self.assertIn("monitoring", run.stages)
        self.assertEqual(run.stages["monitoring"].status, StageStatus.completed)
        self.assertIn("quality", run.stages)
        self.assertEqual(run.stages["quality"].status, StageStatus.completed)
        self.assertIn("verifier", run.stages)
        self.assertEqual(run.stages["verifier"].status, StageStatus.completed)
        self.assertIn("report", run.stages)
        self.assertEqual(run.stages["report"].status, StageStatus.completed)

        # Authoritative metrics: progress_percentage computed by engine (50.0%), completed_cards = 1
        report = self.sqlite.get_latest_report("test-proj")
        self.assertIsNotNone(report)
        self.assertEqual(report.metrics.completed_cards, 1)
        self.assertEqual(report.metrics.progress_percentage, 50.0)
        self.assertEqual(report.human_decision, HumanDecisionState.pending)

    def test_orchestrator_skips_code_quality_and_verifier_when_no_code(self):
        snapshot = self._sample_snapshot_no_code()
        config = OrchestratorConfig(project_id="test-no-code", allow_remote_ai=False)

        orchestrator = ProjectOrchestrator(
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=self.fake_provider,
            revisor_provider=self.fake_provider,
            verificador_provider=self.fake_provider,
            aprobador_provider=self.fake_provider,
            config=config,
        )

        run = orchestrator.execute(snapshot=snapshot)

        self.assertEqual(run.status, RunStatus.awaiting_human)
        self.assertEqual(run.stages["quality"].status, StageStatus.skipped)
        self.assertEqual(run.stages["verifier"].status, StageStatus.skipped)
        self.assertEqual(run.stages["monitoring"].status, StageStatus.completed)
        self.assertEqual(run.stages["report"].status, StageStatus.completed)

    def test_orchestrator_skips_verifier_when_no_findings_escalate(self):
        # Configure EscalationGate that does not escalate routine findings
        snapshot = self._sample_snapshot_with_code()
        config = OrchestratorConfig(project_id="test-proj", allow_remote_ai=True)

        gate_policy = EscalationPolicy(
            severe_categories=set(),  # No category severe
            confidence_threshold=0.5,  # Fake provider gives 0.85 > 0.5
            escalate_on_contradiction=False,
            escalate_on_insufficient_evidence=False,
            escalate_mandatory_rule_findings=False,
            auto_escalate_all=False,
        )
        gate = EscalationGate(policy=gate_policy)

        orchestrator = ProjectOrchestrator(
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=self.fake_provider,
            revisor_provider=self.fake_provider,
            verificador_provider=self.fake_provider,
            aprobador_provider=self.fake_provider,
            escalation_gate=gate,
            config=config,
        )

        run = orchestrator.execute(snapshot=snapshot)

        self.assertEqual(run.status, RunStatus.awaiting_human)
        self.assertEqual(run.stages["quality"].status, StageStatus.completed)
        # Verifier should be skipped because no findings escalated
        self.assertEqual(run.stages["verifier"].status, StageStatus.skipped)
        self.assertEqual(run.stages["report"].status, StageStatus.completed)

    def test_orchestrator_cloud_unavailable_sets_needs_attention(self):
        """
        When verifier provider fails or is unavailable:
        run status => needs_attention with pending verification,
        never invent confirmed verdict, do not mark mandatory failed stages complete.
        """
        snapshot = self._sample_snapshot_with_code()
        # Set config without remote AI enabled or failing provider
        config = OrchestratorConfig(project_id="test-proj", allow_remote_ai=False)

        class FailingCloudProvider(FakeProvider):
            def generate(self, request):
                raise RuntimeError("Cloud provider unavailable: network connection refused")

        orchestrator = ProjectOrchestrator(
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=self.fake_provider,
            revisor_provider=self.fake_provider,
            verificador_provider=FailingCloudProvider(),
            aprobador_provider=self.fake_provider,
            config=config,
        )

        run = orchestrator.execute(snapshot=snapshot)

        self.assertEqual(run.status, RunStatus.needs_attention)
        self.assertEqual(run.stages["verifier"].status, StageStatus.needs_attention)

        report = self.sqlite.get_latest_report("test-proj")
        self.assertIsNotNone(report)
        # Escalated finding must remain in pending_findings, never confirmed
        self.assertEqual(len(report.confirmed_findings), 0)
        self.assertGreater(len(report.pending_findings), 0)

    def test_orchestrator_single_run_idempotence_and_bounded_resume(self):
        snapshot = self._sample_snapshot_with_code()
        config = OrchestratorConfig(project_id="test-proj", allow_remote_ai=True)

        orchestrator = ProjectOrchestrator(
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=self.fake_provider,
            revisor_provider=self.fake_provider,
            verificador_provider=self.fake_provider,
            aprobador_provider=self.fake_provider,
            config=config,
        )

        run_1 = orchestrator.execute(snapshot=snapshot)
        run_1_id = run_1.run_id

        # Second execute with identical snapshot and config -> idempotent return of existing completed run
        run_2 = orchestrator.execute(snapshot=snapshot)
        self.assertEqual(run_2.run_id, run_1_id)
        self.assertEqual(run_2.status, RunStatus.awaiting_human)

        # Modifying config hash creates a new run version
        config_changed = OrchestratorConfig(project_id="test-proj", allow_remote_ai=False)
        orchestrator_changed = ProjectOrchestrator(
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=self.fake_provider,
            revisor_provider=self.fake_provider,
            verificador_provider=self.fake_provider,
            aprobador_provider=self.fake_provider,
            config=config_changed,
        )
        run_3 = orchestrator_changed.execute(snapshot=snapshot)
        self.assertNotEqual(run_3.run_id, run_1_id)

    def test_orchestrator_unresolved_severe_findings_forces_mandatory_unknown_and_insufficient(self):
        # Snapshot has mandatory Q3 rule
        snapshot = self._sample_snapshot_with_code()
        snapshot.policies = [
            QualityRule(
                id="Q1",
                description="Tests pass",
                metric_key="failed_tests",
                operator="==",
                threshold=0,
                mandatory=True,
            ),
            QualityRule(
                id="Q3",
                description="Zero open critical findings",
                metric_key="open_critical_findings",
                operator="==",
                threshold=0,
                mandatory=True,
            ),
        ]

        # Revisor outputs a severe SQL injection finding
        revisor_fake = FakeProvider()
        revisor_fake.enqueue_responses([
            GenerationResult(
                status=GenerationStatus.complete,
                structured_data={
                    "findings": [
                        {
                            "id": "f-sql-1",
                            "rule_id": "Q3",
                            "claim": "Inyeccion SQL detectada en concatenacion de consulta",
                            "severity": "critical",
                            "evidence_ids": [SAMPLE_COMMIT_SHA],
                            "commit_sha": SAMPLE_COMMIT_SHA,
                            "file_path": "src/service.py",
                            "line": 1,
                            "confidence": 0.9,
                            "requires_verification": True,
                            "category": "inyeccion_sql",
                            "evidence": "SELECT * FROM users WHERE id = " + "x",
                            "suggested_fix": "Use parameterized queries",
                            "suggested_code": "cursor.execute(\"SELECT * FROM users WHERE id = ?\", (x,))",
                            "improvement_summary": "Parametriza el valor de entrada.",
                        }
                    ]
                },
                provider="fake_simulated",
                model="qwen3.5:4b-q4_K_M",
                duration_seconds=0.05,
            )
        ])

        # Verifier returns insufficient verdict
        verifier_fake = FakeProvider()
        verifier_fake.enqueue_responses([
            GenerationResult(
                status=GenerationStatus.complete,
                structured_data={
                    "verifications": [
                        {
                            "finding_id": "f-sql-1",
                            "verdict": "insufficient",
                            "reason": "Needs further human security audit",
                            "evidence_ids": [SAMPLE_COMMIT_SHA],
                        }
                    ]
                },
                provider="fake_simulated",
                model="fake-gemini-flash",
                duration_seconds=0.05,
            )
        ])

        config = OrchestratorConfig(project_id="test-proj", allow_remote_ai=True)
        orchestrator = ProjectOrchestrator(
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=self.fake_provider,
            revisor_provider=revisor_fake,
            verificador_provider=verifier_fake,
            aprobador_provider=self.fake_provider,
            config=config,
        )

        run = orchestrator.execute(snapshot=snapshot)

        # Insufficient verification => needs_attention
        self.assertEqual(run.stages["verifier"].status, StageStatus.needs_attention)
        self.assertEqual(run.status, RunStatus.needs_attention)

        report = self.sqlite.get_latest_report("test-proj")
        self.assertIsNotNone(report)
        self.assertEqual(len(report.confirmed_findings), 0)
        self.assertEqual(len(report.insufficient_findings), 1)

        # Policy Q3 must NOT be evaluated as pass / 0 critical findings!
        q3_eval = next((e for e in report.policy_evaluations.evaluations if e.rule_id == "Q3"), None)
        self.assertIsNotNone(q3_eval)
        self.assertEqual(q3_eval.status.value, "unknown")
        self.assertIn("Q3", report.policy_evaluations.mandatory_unknown_ids)
        self.assertEqual(report.policy_evaluations.recommended_action, "evidencia_insuficiente")
        self.assertIn("insuficiente", report.recommendation.lower())

    def test_no_key_or_default_disabled_not_invoking_local_verifier(self):
        """
        Regression RED/GREEN:
        When remote AI is disabled or no key provided, local verifier must NOT be invoked,
        explicit reason recorded, findings remain pending, and status is needs_attention.
        """
        snapshot = self._sample_snapshot_with_code()
        # Revisor outputs a finding that triggers escalation
        revisor_fake = FakeProvider()
        revisor_fake.enqueue_responses([
            GenerationResult(
                status=GenerationStatus.complete,
                structured_data={
                    "findings": [
                        {
                            "id": "f-sql-esc",
                            "suggested_code": "return 1 / total if total else 0",
                            "improvement_summary": "Comprueba el divisor.",
                            # No review policy exists here: do not misuse CI test-count Q1.
                            "rule_id": None,
                            "claim": "Vulnerabilidad de inyeccion SQL en servicio",
                            "severity": "critical",
                            "evidence_ids": [SAMPLE_COMMIT_SHA],
                            "commit_sha": SAMPLE_COMMIT_SHA,
                            "file_path": "src/service.py",
                            "line": 1,
                            "confidence": 0.9,
                            "requires_verification": True,
                            "category": "inyeccion_sql",
                            "evidence": "return 1 / total",
                            "suggested_fix": "fix",
                        }
                    ]
                },
                provider="fake_simulated",
                model="qwen3.5:4b-q4_K_M",
                duration_seconds=0.01,
            )
        ])

        # Verifier is None (local fallback completely removed) and allow_remote_ai=False
        config = OrchestratorConfig(project_id="test-proj", allow_remote_ai=False)
        orchestrator = ProjectOrchestrator(
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=self.fake_provider,
            revisor_provider=revisor_fake,
            verificador_provider=None,  # No local fallback!
            aprobador_provider=self.fake_provider,
            config=config,
        )

        run = orchestrator.execute(snapshot=snapshot)

        self.assertEqual(run.status, RunStatus.needs_attention)
        self.assertEqual(run.stages["verifier"].status, StageStatus.needs_attention)
        self.assertIn(
            "Remote AI verifier disabled by project configuration",
            run.stages["verifier"].error_message,
        )

        report = self.sqlite.get_latest_report("test-proj")
        self.assertIsNotNone(report)
        # Findings must remain pending, NOT confirmed or invented
        self.assertEqual(len(report.confirmed_findings), 0)
        self.assertEqual(len(report.pending_findings), 1)
        self.assertEqual(report.pending_findings[0].id, "f-sql-esc")
        # Models used reflects unavailable
        self.assertEqual(report.providers_used.get("verificador"), "unavailable")

    def test_explicit_cloud_path_configured_model(self):
        """
        Regression RED/GREEN:
        When remote AI is enabled, VerifierAgent must use the actual configured
        provider model instead of hardcoding default gemini-2.5-flash.
        """
        snapshot = self._sample_snapshot_with_code()
        revisor_fake = FakeProvider()
        revisor_fake.enqueue_responses([
            GenerationResult(
                status=GenerationStatus.complete,
                structured_data={
                    "findings": [
                        {
                            "id": "f-sql-cloud",
                            "suggested_code": "return 1 / total if total else 0",
                            "improvement_summary": "Comprueba el divisor.",
                            # No review policy exists here: do not misuse CI test-count Q1.
                            "rule_id": None,
                            "claim": "Vulnerabilidad critica en servicio",
                            "severity": "critical",
                            "evidence_ids": [SAMPLE_COMMIT_SHA],
                            "commit_sha": SAMPLE_COMMIT_SHA,
                            "file_path": "src/service.py",
                            "line": 1,
                            "confidence": 0.9,
                            "requires_verification": True,
                            "category": "inyeccion_sql",
                            "evidence": "return 1 / total",
                            "suggested_fix": "fix",
                        }
                    ]
                },
                provider="fake_simulated",
                model="qwen3.5:4b-q4_K_M",
                duration_seconds=0.01,
            )
        ])

        # Custom configured cloud provider
        class MockConfiguredCloudProvider(FakeProvider):
            def __init__(self):
                super().__init__(model="gemini-1.5-pro-custom")
                self.api_key = "test-api-key"
                self.captured_requests = []

            def generate(self, request):
                self.captured_requests.append(request)
                return GenerationResult(
                    status=GenerationStatus.complete,
                    structured_data={
                        "verifications": [
                            {
                                "finding_id": "f-sql-cloud",
                                "verdict": "confirmed",
                                "reason": "Verified security flaw",
                                "evidence_ids": [SAMPLE_COMMIT_SHA],
                            }
                        ]
                    },
                    provider="gemini",
                    model=request.model or self.model,
                    duration_seconds=0.05,
                )

        cloud_provider = MockConfiguredCloudProvider()
        config = OrchestratorConfig(
            project_id="test-proj",
            allow_remote_ai=True,
            quality_timeout_seconds=120.0,
        )

        orchestrator = ProjectOrchestrator(
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=self.fake_provider,
            revisor_provider=revisor_fake,
            verificador_provider=cloud_provider,
            aprobador_provider=self.fake_provider,
            config=config,
        )

        run = orchestrator.execute(snapshot=snapshot)

        self.assertEqual(run.stages["verifier"].status, StageStatus.completed)
        self.assertEqual(len(cloud_provider.captured_requests), 1)
        # VerifierAgent must have used configured model "gemini-1.5-pro-custom"
        self.assertEqual(cloud_provider.captured_requests[0].model, "gemini-1.5-pro-custom")

        report = self.sqlite.get_latest_report("test-proj")
        self.assertIsNotNone(report)
        self.assertEqual(len(report.confirmed_findings), 1)
        self.assertEqual(report.providers_used.get("verificador"), "gemini-1.5-pro-custom")

    def test_monitoring_cites_matching_ci_accepted_and_wrong_sha_rejected(self):
        """
        Regression RED/GREEN:
        Monitoring draft citing exact matching CI is accepted as valid evidence,
        while citing wrong-SHA unrelated CI is rejected by semantic validation.
        """
        class FakeMonitoringCitingProvider(FakeProvider):
            def __init__(self, cited_ci_id: str):
                super().__init__()
                self.cited_ci_id = cited_ci_id

            def generate(self, request):
                role = request.role.lower()
                if "elaborador" in role or "monitoring" in role:
                    structured = {
                        "summary": f"Resumen citando CI {self.cited_ci_id}",
                        "risks": [],
                        "suggested_actions": [],
                        "referenced_task_ids": ["T1"],
                        "evidence_ids": ["T1", self.cited_ci_id],
                    }
                    return GenerationResult(
                        status=GenerationStatus.complete,
                        structured_data=structured,
                        raw_text=json.dumps(structured),
                        finish_reason="stop",
                        provider=self.provider_name,
                        model=request.model or self.model,
                        duration_seconds=0.01,
                    )
                return super().generate(request)

        # Snapshot with a code commit, matching CI, and an unrelated CI with wrong SHA
        snapshot = WeeklySnapshot(
            id="snap-ci-test",
            project_id="test-ci-proj",
            cutoff_datetime=self.now,
            captured_at=self.now,
            tasks=[
                TaskCard(id="T1", title="Task 1", state=TaskState.completed, is_included=True),
            ],
            code_diffs=[self._complete_code_diff()],
            ci_runs=[
                CIRunResult(
                    id="run-ci-101",
                    workflow_name="tests",
                    commit_sha=SAMPLE_COMMIT_SHA,
                    status="success",
                    total_tests=10,
                    failed_tests=0,
                ),
                CIRunResult(
                    id="run-ci-wrong-sha",
                    workflow_name="tests",
                    commit_sha="deadbeef",
                    status="failure",
                    total_tests=10,
                    failed_tests=2,
                ),
            ],
            policies=[
                QualityRule(id="Q1", description="Tests pass", metric_key="failed_tests", operator="==", threshold=0)
            ],
            is_complete=True,
        )

        config_matching = OrchestratorConfig(project_id="test-ci-proj", allow_remote_ai=True)

        # Case 1: Elaborador cites matching CI ("run-ci-101") -> must be accepted
        elaborador_matching = FakeMonitoringCitingProvider("run-ci-101")
        orchestrator_matching = ProjectOrchestrator(
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=elaborador_matching,
            revisor_provider=self.fake_provider,
            verificador_provider=self.fake_provider,
            aprobador_provider=self.fake_provider,
            config=config_matching,
        )
        run_matching = orchestrator_matching.execute(snapshot=snapshot)
        self.assertEqual(run_matching.stages["monitoring"].status, StageStatus.completed)
        self.assertEqual(run_matching.status, RunStatus.awaiting_human)

        # Case 2: Elaborador cites wrong-SHA unrelated CI ("run-ci-wrong-sha") -> must be rejected
        elaborador_wrong = FakeMonitoringCitingProvider("run-ci-wrong-sha")
        config_wrong = OrchestratorConfig(project_id="test-ci-proj-wrong", allow_remote_ai=True)
        snapshot_wrong = WeeklySnapshot(
            id="snap-ci-test-wrong",
            project_id="test-ci-proj-wrong",
            cutoff_datetime=self.now,
            captured_at=self.now,
            tasks=[
                TaskCard(id="T1", title="Task 1", state=TaskState.completed, is_included=True),
            ],
            code_diffs=[self._complete_code_diff()],
            ci_runs=[
                CIRunResult(
                    id="run-ci-101",
                    workflow_name="tests",
                    commit_sha=SAMPLE_COMMIT_SHA,
                    status="success",
                    total_tests=10,
                    failed_tests=0,
                ),
                CIRunResult(
                    id="run-ci-wrong-sha",
                    workflow_name="tests",
                    commit_sha="deadbeef",
                    status="failure",
                    total_tests=10,
                    failed_tests=2,
                ),
            ],
            policies=[
                QualityRule(id="Q1", description="Tests pass", metric_key="failed_tests", operator="==", threshold=0)
            ],
            is_complete=True,
        )
        orchestrator_wrong = ProjectOrchestrator(
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=elaborador_wrong,
            revisor_provider=self.fake_provider,
            verificador_provider=self.fake_provider,
            aprobador_provider=self.fake_provider,
            config=config_wrong,
        )
        run_wrong = orchestrator_wrong.execute(snapshot=snapshot_wrong)
        self.assertEqual(run_wrong.stages["monitoring"].status, StageStatus.failed)
        self.assertEqual(run_wrong.status, RunStatus.failed)
        self.assertIn("run-ci-wrong-sha", run_wrong.stages["monitoring"].error_message)

    def test_orchestrator_config_independent_timeouts_and_forwarding_to_verifier(self):
        """OrchestratorConfig maintains independent timeouts and forwards them to Quality and Verifier agents."""
        # Non-positive validation
        with self.assertRaises(ValueError):
            OrchestratorConfig(verifier_timeout_seconds=0.0)
        with self.assertRaises(ValueError):
            OrchestratorConfig(verifier_timeout_seconds=-10.0)

        # Default verifier_timeout_seconds is 120.0
        cfg_default = OrchestratorConfig()
        self.assertEqual(cfg_default.verifier_timeout_seconds, 120.0)
        self.assertEqual(cfg_default.quality_timeout_seconds, 120.0)

        # Custom independent sentinel budgets
        class CapturingRevisorProvider(FakeProvider):
            def __init__(self):
                super().__init__(model="qwen3.5:4b-q4_K_M")
                self.captured_requests = []
            def generate(self, request):
                self.captured_requests.append(request)
                return super().generate(request)

        class CapturingVerifierProvider(FakeProvider):
            def __init__(self):
                super().__init__(model="gemini-2.5-flash")
                self.api_key = "test-fake-key"
                self.captured_requests = []
            def generate(self, request):
                self.captured_requests.append(request)
                return super().generate(request)

        revisor = CapturingRevisorProvider()
        verificador = CapturingVerifierProvider()

        config = OrchestratorConfig(
            project_id="test-proj",
            allow_remote_ai=True,
            quality_timeout_seconds=45.0,
            verifier_timeout_seconds=88.0,
        )
        self.assertEqual(config.quality_timeout_seconds, 45.0)
        self.assertEqual(config.verifier_timeout_seconds, 88.0)

        snapshot = self._sample_snapshot_with_code()
        orchestrator = ProjectOrchestrator(
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=self.fake_provider,
            revisor_provider=revisor,
            verificador_provider=verificador,
            aprobador_provider=self.fake_provider,
            config=config,
        )

        run = orchestrator.execute(snapshot=snapshot)
        self.assertEqual(run.stages["quality"].status, StageStatus.completed)
        self.assertEqual(run.stages["verifier"].status, StageStatus.completed)

        # Quality request received independent quality_timeout_seconds (45.0)
        self.assertEqual(len(revisor.captured_requests), 1)
        self.assertEqual(revisor.captured_requests[0].timeout_seconds, 45.0)

        # Verifier request received independent verifier_timeout_seconds (88.0)
        self.assertEqual(len(verificador.captured_requests), 1)
        self.assertEqual(verificador.captured_requests[0].timeout_seconds, 88.0)


    def test_large_diffs_do_not_enter_monitoring_and_snapshot_scope_is_preserved(self):
        snapshot = self._sample_snapshot_with_code()
        snapshot.is_complete = False
        for task in snapshot.tasks:
            task.detail = "irrelevant detail" * 5000
        snapshot.code_diffs[0].content = "unrelated large diff" * 10000
        class Capture(FakeProvider):
            def __init__(self):
                super().__init__(context_tokens=4096)
                self.requests = []
            def generate(self, request):
                self.requests.append(request)
                return super().generate(request)
        provider = Capture()
        orchestrator = ProjectOrchestrator(sqlite_storage=self.sqlite, artifact_storage=self.artifacts,
            elaborador_provider=provider, revisor_provider=provider, verificador_provider=provider,
            aprobador_provider=provider, config=OrchestratorConfig(project_id="test-proj"))
        run = orchestrator.execute(snapshot=snapshot)
        self.assertEqual(run.stages["monitoring"].status, StageStatus.completed)
        self.assertEqual(run.stages["quality"].status, StageStatus.failed)
        self.assertEqual(len(provider.requests), 1)
        payload = provider.requests[0].evidences
        self.assertNotIn("code_snippets", payload)
        self.assertFalse(payload["source_context"]["is_complete"])
        self.assertEqual(payload["source_context"]["commit_sha"], SAMPLE_COMMIT_SHA)


if __name__ == "__main__":
    unittest.main()
