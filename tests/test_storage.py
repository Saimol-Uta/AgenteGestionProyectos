"""Unit tests for SQLite and filesystem persistence in storage adapters."""

import os
import shutil
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from agente_gestion.domain.run import (
    ExecutionRun,
    RunStatus,
    StageExecutionRecord,
    StageStatus,
)
from agente_gestion.domain.snapshot import WeeklySnapshot
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
    VerificationVerdict,
)
from agente_gestion.domain.report import (
    FinalReport,
    HumanDecisionState,
    MonitoringDraft,
)
from agente_gestion.domain.llm import LLMCallAttempt, GenerationStatus
from agente_gestion.domain.evidence import EvidenceItem, EvidenceSource
from agente_gestion.application.escalation import EscalationDecision
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.adapters.storage.artifacts import ArtifactStorageAdapter


class StorageAdapterTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_agente.db")
        self.runs_dir = os.path.join(self.temp_dir, "runs")
        self.tz = ZoneInfo("America/Guayaquil")
        self.now = datetime(2026, 10, 2, 17, 0, 0, tzinfo=self.tz)

        self.sqlite_storage = SQLiteStorageAdapter(db_path=self.db_path)
        self.artifact_storage = ArtifactStorageAdapter(base_dir=self.runs_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_sample_snapshot(self) -> WeeklySnapshot:
        return WeeklySnapshot(
            id="snap-100",
            project_id="test-project",
            cutoff_datetime=self.now,
            captured_at=self.now,
            tasks=[
                TaskCard(id="T1", title="Task 1", state=TaskState.completed, is_included=True),
                TaskCard(id="T2", title="Task 2", state=TaskState.in_progress, is_included=True),
            ],
            code_diffs=[
                CodeDiffSnippet(
                    repository="org/repo",
                    commit_sha="c0ffee1",
                    file_path="src/main.py",
                    lines=[1, 2],
                    content="print('hello')",
                )
            ],
            ci_runs=[
                CIRunResult(
                    id="ci-1",
                    workflow_name="tests",
                    commit_sha="c0ffee1",
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

    def _create_sample_report(self) -> FinalReport:
        metrics = DeterministicTaskMetrics(
            total_cards=2,
            included_cards=2,
            completed_cards=1,
            progress_percentage=50.0,
            overdue_card_ids=[],
            blocked_card_ids=[],
            unknown_card_ids=[],
            contradictory_card_ids=[],
        )
        policy_eval = PolicyEvaluationResult(
            evaluations=[
                RuleEvaluation(
                    rule_id="Q1",
                    description="Tests pass",
                    status=RuleEvaluationStatus.pass_,
                    observed_value=0,
                    mandatory=True,
                )
            ],
            passed_rule_ids=["Q1"],
            failed_rule_ids=[],
            unknown_rule_ids=[],
            mandatory_failed_ids=[],
            mandatory_unknown_ids=[],
            recommended_action="recomendar_aprobacion",
        )
        return FinalReport(
            project_id="test-project",
            cutoff_datetime=self.now,
            metrics=metrics,
            confirmed_findings=[],
            rejected_findings=[],
            insufficient_findings=[],
            pending_findings=[],
            policy_evaluations=policy_eval,
            recommendation="recomendar_aprobacion",
            human_decision=HumanDecisionState.pending,
            summary="Proyecto al 50% con calidad verificada.",
            recommendations=["Continuar sprint"],
        )

    def test_sqlite_save_and_get_run(self):
        run = ExecutionRun(
            run_id="run-1",
            project_id="test-project",
            status=RunStatus.analyzing,
            created_at=self.now,
            updated_at=self.now,
            snapshot_id="snap-100",
            config_hash="cfg-hash-1",
            snapshot_hash="snap-hash-1",
            policy_version="pol-v1",
            duration_seconds=1.5,
        )
        self.sqlite_storage.save_run(run)
        retrieved = self.sqlite_storage.get_run("run-1")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.run_id, "run-1")
        self.assertEqual(retrieved.status, RunStatus.analyzing)
        self.assertEqual(retrieved.snapshot_hash, "snap-hash-1")

    def test_sqlite_find_by_fingerprint_idempotence(self):
        run = ExecutionRun(
            run_id="run-idem",
            project_id="test-project",
            status=RunStatus.completed,
            created_at=self.now,
            updated_at=self.now,
            snapshot_id="snap-100",
            config_hash="cfg-abc",
            snapshot_hash="snap-xyz",
            policy_version="pol-v1",
        )
        self.sqlite_storage.save_run(run)

        found = self.sqlite_storage.find_run_by_fingerprint(
            snapshot_hash="snap-xyz", config_hash="cfg-abc"
        )
        self.assertIsNotNone(found)
        self.assertEqual(found.run_id, "run-idem")

        not_found = self.sqlite_storage.find_run_by_fingerprint(
            snapshot_hash="snap-xyz", config_hash="cfg-different"
        )
        self.assertIsNone(not_found)

    def test_sqlite_save_and_retrieve_snapshot(self):
        snap = self._create_sample_snapshot()
        self.sqlite_storage.save_snapshot(snap, content_hash="snap-hash-100")

        retrieved = self.sqlite_storage.get_snapshot("snap-100")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.id, "snap-100")
        self.assertEqual(len(retrieved.tasks), 2)
        self.assertEqual(len(retrieved.code_diffs), 1)

    def test_sqlite_stage_results_tracking(self):
        run = ExecutionRun(
            run_id="run-stage",
            project_id="test-project",
            status=RunStatus.analyzing,
            created_at=self.now,
            updated_at=self.now,
            snapshot_id="snap-100",
            config_hash="c1",
            snapshot_hash="s1",
            policy_version="v1",
        )
        self.sqlite_storage.save_run(run)

        stg = StageExecutionRecord(
            stage_name="monitoring",
            status=StageStatus.completed,
            duration_seconds=0.42,
            output_summary="Draft prepared",
            data_hash="hash-monitoring-data",
        )
        self.sqlite_storage.save_stage_result("run-stage", stg)

        stages = self.sqlite_storage.get_stage_results("run-stage")
        self.assertIn("monitoring", stages)
        self.assertEqual(stages["monitoring"].status, StageStatus.completed)
        self.assertEqual(stages["monitoring"].data_hash, "hash-monitoring-data")

    def test_sqlite_report_and_human_decision_registration(self):
        run = ExecutionRun(
            run_id="run-rep",
            project_id="test-project",
            status=RunStatus.awaiting_human,
            created_at=self.now,
            updated_at=self.now,
            snapshot_id="snap-100",
            config_hash="c1",
            snapshot_hash="s1",
            policy_version="v1",
        )
        self.sqlite_storage.save_run(run)

        report = self._create_sample_report()
        report_id = "rep-101"
        self.sqlite_storage.save_report(report, report_id=report_id, run_id="run-rep")

        latest = self.sqlite_storage.get_latest_report("test-project")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.human_decision, HumanDecisionState.pending)

        # Register human manager decision
        updated_report = self.sqlite_storage.register_human_decision(
            report_id=report_id,
            decision=HumanDecisionState.approved,
            reason="Avance validado por el gestor técnico",
            run_id="run-rep",
        )
        self.assertEqual(updated_report.human_decision, HumanDecisionState.approved)

        # Verify persistence of decision
        latest_after = self.sqlite_storage.get_latest_report("test-project")
        self.assertEqual(latest_after.human_decision, HumanDecisionState.approved)

    def test_sqlite_llm_attempts_and_findings_persistence(self):
        attempt = LLMCallAttempt(
            attempt_id="att-1",
            request_ref="draft_v1",
            stage="monitoring",
            attempt_number=1,
            provider="ollama",
            model="qwen3.5:2b-q4_K_M",
            status=GenerationStatus.complete,
            duration_seconds=0.3,
            prompt_tokens=150,
            completion_tokens=40,
            validation_passed=True,
        )
        self.sqlite_storage.save_llm_attempts("run-findings", [attempt])

        finding = CandidateFinding(
            id="find-1",
            category="division_cero",
            file_path="src/main.py",
            line=2,
            commit_sha="c0ffee1",
            evidence="return 1 / total",
            suggested_fix="check zero",
            evidence_ids=["ev-1"],
            confidence=0.8,
        )
        escalation_dec = EscalationDecision(
            finding_id="find-1",
            should_escalate=True,
            reasons=["Severe category detected"],
        )
        self.sqlite_storage.save_findings(
            run_id="run-findings",
            findings=[finding],
            escalation_decisions=[escalation_dec],
        )

        verification = FindingVerification(
            finding_id="find-1",
            verdict=VerificationVerdict.confirmed,
            reason="Confirmed division by zero vulnerability",
            verified_by="gemini-2.5-flash",
            evidence_ids=["ev-1"],
        )
        self.sqlite_storage.save_verifications("run-findings", [verification])

        # Retrieve stored records
        stored_findings = self.sqlite_storage.get_findings("run-findings")
        self.assertEqual(len(stored_findings), 1)
        self.assertEqual(stored_findings[0]["finding_id"], "find-1")
        self.assertTrue(stored_findings[0]["should_escalate"])

        stored_verifs = self.sqlite_storage.get_verifications("run-findings")
        self.assertEqual(len(stored_verifs), 1)
        self.assertEqual(stored_verifs[0]["verdict"], "confirmed")

    def test_artifact_storage_writes_all_required_files(self):
        run_id = "run-artifacts-ok"
        snapshot = self._create_sample_snapshot()
        metrics = DeterministicTaskMetrics(
            total_cards=2,
            included_cards=2,
            completed_cards=1,
            progress_percentage=50.0,
            overdue_card_ids=[],
            blocked_card_ids=[],
            unknown_card_ids=[],
            contradictory_card_ids=[],
        )
        monitoring_draft = MonitoringDraft(
            summary="Borrador de avance",
            risks=[],
            suggested_actions=["Accion 1"],
            referenced_task_ids=["T1"],
            evidence_ids=["ev-1"],
        )
        code_findings = [
            CandidateFinding(
                id="f1",
                category="otros",
                file_path="src/main.py",
                line=1,
                commit_sha="c0ffee1",
                evidence="x = 1",
                suggested_fix="y = 1",
            )
        ]
        verifications = [
            FindingVerification(
                finding_id="f1",
                verdict=VerificationVerdict.rejected,
                reason="Not a defect",
                evidence_ids=[],
            )
        ]
        report = self._create_sample_report()
        report_md = "# Reporte Semanal\nContenido de prueba."
        exec_metrics = {
            "duration_seconds": 2.1,
            "models_used": {"elaborador": "qwen3.5:2b-q4_K_M"},
            "provider_errors": [],
            "escalations": {"f1": "Severe category"},
        }

        self.artifact_storage.save_run_artifacts(
            run_id=run_id,
            snapshot=snapshot,
            metrics=metrics,
            monitoring=monitoring_draft,
            code_findings=code_findings,
            verifications=verifications,
            report=report,
            report_md=report_md,
            execution_metrics=exec_metrics,
        )

        run_path = os.path.join(self.runs_dir, run_id)
        expected_files = [
            "snapshot.json",
            "metrics.json",
            "monitoring.json",
            "code_findings.json",
            "verification.json",
            "report.json",
            "report.md",
            "execution_metrics.json",
        ]
        for fname in expected_files:
            target = os.path.join(run_path, fname)
            self.assertTrue(os.path.exists(target), f"Missing expected artifact {fname} at {target}")

    def test_artifact_storage_partial_artifacts_on_failure(self):
        run_id = "run-artifacts-failed"
        snapshot = self._create_sample_snapshot()
        exec_metrics = {
            "status": "failed",
            "error": "Simulated failure during stage",
            "duration_seconds": 0.5,
        }

        self.artifact_storage.save_partial_artifacts(
            run_id=run_id,
            snapshot=snapshot,
            execution_metrics=exec_metrics,
        )

        run_path = os.path.join(self.runs_dir, run_id)
        self.assertTrue(os.path.exists(os.path.join(run_path, "snapshot.json")))
        self.assertTrue(os.path.exists(os.path.join(run_path, "execution_metrics.json")))
        # Full report should not be present
        self.assertFalse(os.path.exists(os.path.join(run_path, "report.json")))


if __name__ == "__main__":
    unittest.main()
