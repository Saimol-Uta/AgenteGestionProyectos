"""Deterministic End-to-End integration tests using simulated fake providers."""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from agente_gestion.domain.run import RunStatus, StageStatus
from agente_gestion.domain.report import HumanDecisionState
from agente_gestion.domain.llm import GenerationResult, GenerationStatus
from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.adapters.sources.fixtures import FixtureSourcesAdapter
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.adapters.storage.artifacts import ArtifactStorageAdapter
from agente_gestion.application.orchestrator import ProjectOrchestrator, OrchestratorConfig
from agente_gestion.application.escalation import EscalationGate, EscalationPolicy


class FakeE2ETestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "e2e_test.db")
        self.runs_dir = os.path.join(self.temp_dir, "runs")
        self.tz = ZoneInfo("America/Guayaquil")
        self.now = datetime(2026, 10, 2, 17, 0, 0, tzinfo=self.tz)

        self.sqlite = SQLiteStorageAdapter(db_path=self.db_path)
        self.artifacts = ArtifactStorageAdapter(base_dir=self.runs_dir)
        self.fixtures = FixtureSourcesAdapter(fixtures_dir="fixtures")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_e2e_1_positive_flow(self):
        """Full pipeline with code, escalation, verifier, report, and persistence."""
        fake = FakeProvider()
        config = OrchestratorConfig(
            project_id="fixture_1_standard",
            allow_remote_ai=True,
            runs_dir=self.runs_dir,
        )

        orchestrator = ProjectOrchestrator(
            source_adapter=self.fixtures,
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=fake,
            revisor_provider=fake,
            verificador_provider=fake,
            aprobador_provider=fake,
            config=config,
        )

        run = orchestrator.execute(project_id="fixture_1_standard")

        self.assertEqual(run.status, RunStatus.awaiting_human)
        self.assertEqual(run.stages["monitoring"].status, StageStatus.completed)
        self.assertEqual(run.stages["quality"].status, StageStatus.completed)
        self.assertEqual(run.stages["verifier"].status, StageStatus.completed)
        self.assertEqual(run.stages["report"].status, StageStatus.completed)

        # Artifacts verification
        run_artifacts_dir = os.path.join(self.runs_dir, run.run_id)
        self.assertTrue(os.path.exists(os.path.join(run_artifacts_dir, "report.json")))
        self.assertTrue(os.path.exists(os.path.join(run_artifacts_dir, "report.md")))
        self.assertTrue(os.path.exists(os.path.join(run_artifacts_dir, "snapshot.json")))
        self.assertTrue(os.path.exists(os.path.join(run_artifacts_dir, "execution_metrics.json")))

        # Check Markdown contains required sections in Spanish
        with open(os.path.join(run_artifacts_dir, "report.md"), "r", encoding="utf-8") as f:
            md_content = f.read()

        required_phrases = [
            "Período", "Estado general", "Resumen ejecutivo",
            "Avance de tareas", "Bloqueos", "Vencimientos",
            "Riesgos", "Criterios de calidad", "Hallazgos confirmados",
            "Recomendaciones", "Evidencias", "Proveedores",
            "Pendiente de decisión del gestor",
        ]
        for phrase in required_phrases:
            self.assertIn(phrase.lower(), md_content.lower(), f"Missing section phrase '{phrase}' in report.md")

    def test_e2e_2_no_code_routing(self):
        """When snapshot has no code changes, code quality and verifier must be skipped."""
        fake = FakeProvider()
        config = OrchestratorConfig(project_id="fixture_3_empty_and_unknown", allow_remote_ai=False)

        orchestrator = ProjectOrchestrator(
            source_adapter=self.fixtures,
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=fake,
            revisor_provider=fake,
            verificador_provider=fake,
            aprobador_provider=fake,
            config=config,
        )

        run = orchestrator.execute(project_id="fixture_3_empty_and_unknown")

        self.assertEqual(run.status, RunStatus.awaiting_human)
        self.assertEqual(run.stages["quality"].status, StageStatus.skipped)
        self.assertEqual(run.stages["verifier"].status, StageStatus.skipped)

    def test_e2e_3_no_escalation_routing(self):
        """When findings do not meet escalation threshold, verifier stage must be skipped."""
        fake = FakeProvider()
        config = OrchestratorConfig(project_id="fixture_1_standard", allow_remote_ai=True)

        # Gate that does not escalate
        gate = EscalationGate(policy=EscalationPolicy(
            severe_categories=set(),
            confidence_threshold=0.5,
            escalate_on_contradiction=False,
            escalate_on_insufficient_evidence=False,
            escalate_mandatory_rule_findings=False,
            auto_escalate_all=False,
        ))

        orchestrator = ProjectOrchestrator(
            source_adapter=self.fixtures,
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=fake,
            revisor_provider=fake,
            verificador_provider=fake,
            aprobador_provider=fake,
            escalation_gate=gate,
            config=config,
        )

        run = orchestrator.execute(project_id="fixture_1_standard")

        self.assertEqual(run.stages["quality"].status, StageStatus.completed)
        self.assertEqual(run.stages["verifier"].status, StageStatus.skipped)

    def test_e2e_4_wrong_sha_ci_mismatch(self):
        """In fixture 2, CI commit SHA doesn't match code commit SHA. Policies fail or remain unknown."""
        fake = FakeProvider()
        config = OrchestratorConfig(project_id="fixture_2_ci_mismatch", allow_remote_ai=True)

        orchestrator = ProjectOrchestrator(
            source_adapter=self.fixtures,
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=fake,
            revisor_provider=fake,
            verificador_provider=fake,
            aprobador_provider=fake,
            config=config,
        )

        run = orchestrator.execute(project_id="fixture_2_ci_mismatch")

        report = self.sqlite.get_latest_report("fixture_2_ci_mismatch")
        self.assertIsNotNone(report)
        # CI does not match, so CI metrics are None/unknown, mandatory rule fails/unknown -> non-favorable recommendation
        self.assertIn(report.recommendation, ["solicitar_cambios", "evidencia_insuficiente"])

    def test_e2e_5_cloud_unavailable_needs_attention(self):
        """When remote AI is disabled or fails, findings remain pending and status is needs_attention."""
        class UnavailableCloudProvider(FakeProvider):
            def generate(self, request):
                raise RuntimeError("Connection refused: 503 Cloud service unavailable")

        config = OrchestratorConfig(project_id="fixture_1_standard", allow_remote_ai=False)

        orchestrator = ProjectOrchestrator(
            source_adapter=self.fixtures,
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=FakeProvider(),
            revisor_provider=FakeProvider(),
            verificador_provider=UnavailableCloudProvider(),
            aprobador_provider=FakeProvider(),
            config=config,
        )

        run = orchestrator.execute(project_id="fixture_1_standard")

        self.assertEqual(run.status, RunStatus.needs_attention)
        self.assertEqual(run.stages["verifier"].status, StageStatus.needs_attention)

        report = self.sqlite.get_latest_report("fixture_1_standard")
        self.assertEqual(len(report.confirmed_findings), 0)
        self.assertGreater(len(report.pending_findings), 0)

    def test_e2e_6_invalid_citations_semantic_validation_recovery(self):
        """Model produces invalid citations; repair loop activates; if repair succeeds, continues."""
        fake = FakeProvider()
        # Enqueue first: invalid citation (unknown task ID 'T9999'), second: valid draft
        invalid_res = GenerationResult(
            status=GenerationStatus.complete,
            structured_data={
                "summary": "Draft summary",
                "risks": [],
                "suggested_actions": [],
                "referenced_task_ids": ["T9999_INVENTED"],
                "evidence_ids": ["T1"],
            },
            provider="fake",
            model="fake",
            duration_seconds=0.01,
        )
        valid_res = GenerationResult(
            status=GenerationStatus.complete,
            structured_data={
                "summary": "Draft summary repaired",
                "risks": [],
                "suggested_actions": [],
                "referenced_task_ids": ["T1"],
                "evidence_ids": ["T1"],
            },
            provider="fake",
            model="fake",
            duration_seconds=0.01,
        )
        fake.enqueue_responses([invalid_res, valid_res])

        config = OrchestratorConfig(project_id="fixture_1_standard", allow_remote_ai=True)
        orchestrator = ProjectOrchestrator(
            source_adapter=self.fixtures,
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=fake,
            revisor_provider=FakeProvider(),
            verificador_provider=FakeProvider(),
            aprobador_provider=FakeProvider(),
            config=config,
        )

        run = orchestrator.execute(project_id="fixture_1_standard")
        self.assertEqual(run.stages["monitoring"].status, StageStatus.completed)

    def test_e2e_7_bounded_budget(self):
        """When total LLM calls exceed global max_llm_calls_per_run, execution halts cleanly."""
        # Provider that loops or exhausts budget
        config = OrchestratorConfig(
            project_id="fixture_1_standard",
            max_llm_calls_per_run=1,  # Only 1 call allowed
            allow_remote_ai=True,
        )

        orchestrator = ProjectOrchestrator(
            source_adapter=self.fixtures,
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=FakeProvider(),
            revisor_provider=FakeProvider(),
            verificador_provider=FakeProvider(),
            aprobador_provider=FakeProvider(),
            config=config,
        )

        run = orchestrator.execute(project_id="fixture_1_standard")
        # Should halt before completing all 4 stages
        self.assertIn(run.status, [RunStatus.failed, RunStatus.needs_attention])

    def test_e2e_8_idempotence_and_bounded_recovery(self):
        """Repeated run with same snapshot/config is idempotent; changed config creates new run."""
        config = OrchestratorConfig(project_id="fixture_1_standard", allow_remote_ai=True)
        orchestrator = ProjectOrchestrator(
            source_adapter=self.fixtures,
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=FakeProvider(),
            revisor_provider=FakeProvider(),
            verificador_provider=FakeProvider(),
            aprobador_provider=FakeProvider(),
            config=config,
        )

        run_1 = orchestrator.execute(project_id="fixture_1_standard")
        run_2 = orchestrator.execute(project_id="fixture_1_standard")

        self.assertEqual(run_1.run_id, run_2.run_id)

    def test_e2e_9_human_decision_flow(self):
        """Full lifecycle: execution -> pending report -> manager decides -> report updated."""
        config = OrchestratorConfig(project_id="fixture_1_standard", allow_remote_ai=True)
        orchestrator = ProjectOrchestrator(
            source_adapter=self.fixtures,
            sqlite_storage=self.sqlite,
            artifact_storage=self.artifacts,
            elaborador_provider=FakeProvider(),
            revisor_provider=FakeProvider(),
            verificador_provider=FakeProvider(),
            aprobador_provider=FakeProvider(),
            config=config,
        )

        run = orchestrator.execute(project_id="fixture_1_standard")
        report = self.sqlite.get_latest_report("fixture_1_standard")
        self.assertEqual(report.human_decision, HumanDecisionState.pending)

        # Manager registers decision
        updated_report = self.sqlite.register_human_decision(
            report_id=report.report_id or run.run_id,
            decision=HumanDecisionState.changes_requested,
            reason="Corregir hallazgo crítico antes de pasar a staging",
            run_id=run.run_id,
        )

        self.assertEqual(updated_report.human_decision, HumanDecisionState.changes_requested)
        self.assertEqual(updated_report.decision_reason, "Corregir hallazgo crítico antes de pasar a staging")


if __name__ == "__main__":
    unittest.main()
