"""Deterministic End-to-End integration test suite for MultiRepo Quality and Verification.

Validates the complete pipeline:
  Quality -> EscalationGate -> Verification -> Persistence -> Report -> API -> React (contracts)
using sanitized historical candidate fixtures from run-20261018-165627-f049cb.
Zero external LLM or network calls; fully isolated in temporary storage.
"""

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from pydantic import SecretStr

from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.adapters.storage.artifacts import ArtifactStorageAdapter
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.agents.verifier import VerifierAgent
from agente_gestion.api.app import ApiSettings, ProjectConfig, create_app
from agente_gestion.application.config import Settings
from agente_gestion.application.escalation import EscalationGate, EscalationPolicy
from agente_gestion.application.generation import (
    GlobalBudgetTracker,
    ProviderInternalError,
    StageInvocationCoordinator,
)
from agente_gestion.application.orchestrator import OrchestratorConfig, ProjectOrchestrator
from agente_gestion.application.reporting import generate_report_markdown
from agente_gestion.application.repository_analysis import (
    _namespace_finding,
    analyze_repository_results,
)
from agente_gestion.domain.ci_metrics import CiArtifactOutcome, CiWorkflowStatus
from agente_gestion.domain.code import (
    CIRunResult,
    CiMetricsArtifactMetadata,
    CiMetricsArtifactValues,
    CodeDiffSnippet,
)
from agente_gestion.domain.findings import (
    CandidateFinding,
    FindingCategory,
    FindingVerification,
    SuggestedCodeValidationStatus,
    VerificationVerdict,
)
from agente_gestion.domain.llm import (
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
)
from agente_gestion.domain.policies import QualityRule
from agente_gestion.domain.report import MonitoringDraft
from agente_gestion.domain.repositories import (
    CommitResolutionStatus,
    RepositoryAccessStatus,
    RepositoryAnalysisState,
    RepositoryAvailabilityStatus,
    RepositoryConfiguration,
    RepositoryIdentity,
    RepositoryProvenance,
)
from agente_gestion.domain.run import ExecutionRun, RunStatus, StageStatus, compute_sha256
from agente_gestion.domain.snapshot import RepositorySnapshotResult, WeeklySnapshot
from agente_gestion.domain.tasks import TaskCard, TaskState


# ==============================================================================
# Sanitized Historical Fixtures (Derived from run-20261018-165627-f049cb)
# ==============================================================================

BACKEND_REPO_ID = "backend"
BACKEND_OWNER = "Josu-F1"
BACKEND_REPO_NAME = "hormigestion-backend"
BACKEND_SHA = "af6aa59757886d3f1f8437f0a10d9f1e89ed6345"

FRONTEND_REPO_ID = "frontend"
FRONTEND_OWNER = "Josu-F1"
FRONTEND_REPO_NAME = "hormigestion-frontend"
FRONTEND_SHA = "68e2b9a13af5193738bd6b4706b0c073918dab7c"

BACKEND_SNIPPET_CONTENT = (
    "  async obtenerConductoresDisponibles() {\n"
    "    const result = await this.pool.query<ConductorRow>(\n"
    "      `SELECT c.id, c.nombre_completo, c.telefono,\n"
    "         NOT EXISTS (\n"
    "           SELECT 1 FROM despachos d\n"
    "           WHERE d.conductor_id = c.id AND d.estado IN ('PENDIENTE', 'EN_TRANSITO', 'EN_OBRA')\n"
    "       WHERE c.activo\n"
    "       ORDER BY c.nombre_completo`\n"
    "    );\n"
    "    return result.rows;\n"
    "  }\n"
)

FRONTEND_SNIPPET_CONTENT = (
    "export function calculateVolume(detail: QuoteDetail): number {\n"
    "  const qty = Math.max(1, detail.cantidad || 1);\n"
    "  return qty * detail.unit_volume;\n"
    "}\n"
)


def _build_test_identities():
    backend_identity = RepositoryIdentity(
        id=BACKEND_REPO_ID, owner=BACKEND_OWNER, repo=BACKEND_REPO_NAME
    )
    frontend_identity = RepositoryIdentity(
        id=FRONTEND_REPO_ID, owner=FRONTEND_OWNER, repo=FRONTEND_REPO_NAME
    )
    return backend_identity, frontend_identity


def _build_test_snippets(backend_identity, frontend_identity):
    backend_snippet = CodeDiffSnippet(
        repository=f"{BACKEND_OWNER}/{BACKEND_REPO_NAME}",
        commit_sha=BACKEND_SHA,
        file_path="src/infrastructure/database/despacho-store.ts",
        diff_side="NEW",
        lines=list(range(70, 81)),
        content=BACKEND_SNIPPET_CONTENT,
        evidence_scope="snippet",
        is_complete=False,
        total_lines=150,
        source_sha256=hashlib.sha256(BACKEND_SNIPPET_CONTENT.encode("utf-8")).hexdigest(),
        repository_identity=backend_identity,
        provenance=RepositoryProvenance(
            repository_identity=backend_identity,
            commit_sha=BACKEND_SHA,
            source="github",
        ),
    )

    frontend_snippet = CodeDiffSnippet(
        repository=f"{FRONTEND_OWNER}/{FRONTEND_REPO_NAME}",
        commit_sha=FRONTEND_SHA,
        file_path="src/client/quoter-api.ts",
        diff_side="NEW",
        lines=list(range(190, 195)),
        content=FRONTEND_SNIPPET_CONTENT,
        evidence_scope="snippet",
        is_complete=False,
        total_lines=250,
        source_sha256=hashlib.sha256(FRONTEND_SNIPPET_CONTENT.encode("utf-8")).hexdigest(),
        repository_identity=frontend_identity,
        provenance=RepositoryProvenance(
            repository_identity=frontend_identity,
            commit_sha=FRONTEND_SHA,
            source="github",
        ),
    )
    return backend_snippet, frontend_snippet


def _build_backend_candidate_dict():
    """Raw structured output emitted by Quality agent for backend."""
    return {
        "id": "CQ-001",
        "rule_id": "Q3",
        "claim": "La consulta SQL en 'obtenerConductoresDisponibles' presenta un error de sintaxis al omitir el paréntesis de cierre en la subconsulta NOT EXISTS antes de la cláusula WHERE c.activo.",
        "severity": "high",
        "evidence_ids": [BACKEND_SHA],
        "commit_sha": BACKEND_SHA,
        "file_path": "src/infrastructure/database/despacho-store.ts",
        "line": 75,
        "confidence": 0.95,
        "requires_verification": True,
        "claim_basis": "visible_code",
        "category": "syntax_error",
        "evidence": "           SELECT 1 FROM despachos d\n           WHERE d.conductor_id = c.id AND d.estado IN ('PENDIENTE', 'EN_TRANSITO', 'EN_OBRA')\n       WHERE c.activo",
        "suggested_code": (
            "  async obtenerConductoresDisponibles() {\n"
            "    const result = await this.pool.query<ConductorRow>(\n"
            "      `SELECT c.id, c.nombre_completo, c.telefono,\n"
            "         NOT EXISTS (\n"
            "           SELECT 1 FROM despachos d\n"
            "           WHERE d.conductor_id = c.id AND d.estado IN ('PENDIENTE', 'EN_TRANSITO', 'EN_OBRA')\n"
            "         ) AS disponible\n"
            "       FROM conductores c\n"
            "       WHERE c.activo\n"
            "       ORDER BY c.nombre_completo`\n"
            "    );"
        ),
        "improvement_summary": "Corrige el error de sintaxis en la consulta SQL agregando el paréntesis de cierre faltante en la subconsulta NOT EXISTS y asegurando la estructura correcta de la sentencia.",
    }


def _build_frontend_candidate_dict():
    """Raw structured output emitted by Quality agent for frontend."""
    return {
        "id": "CQ-001",
        "rule_id": None,
        "claim": "El cálculo de volumen local fuerza una cantidad mínima de 1 cuando la cantidad es 0, lo que impide reflejar un volumen nulo para cantidades vacías o en cero.",
        "severity": "low",
        "evidence_ids": [FRONTEND_SHA],
        "commit_sha": FRONTEND_SHA,
        "file_path": "src/client/quoter-api.ts",
        "line": 192,
        "confidence": 0.85,
        "requires_verification": True,
        "claim_basis": "visible_code",
        "category": "otros",
        "evidence": "  const qty = Math.max(1, detail.cantidad || 1);",
        "suggested_code": "  const qty = Math.max(0, detail.cantidad ?? 0);",
        "improvement_summary": "Ajustar la validación de cantidad para permitir valores de cero mediante operador de coalescencia nula en lugar de forzar un mínimo de 1.",
    }


# ==============================================================================
# Custom Simulated Provider for MultiRepo Pipeline Scenarios
# ==============================================================================

class DeterministicMultiRepoProvider(FakeProvider):
    """Deterministic provider serving scripted Quality, Verification, and Report outputs."""

    def __init__(self, *, verifier_behavior="confirmed"):
        super().__init__()
        self.verifier_behavior = verifier_behavior
        self.requests = []

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)

        # 1. Monitoring stage (elaborador)
        if request.role == "elaborador":
            return GenerationResult(
                status=GenerationStatus.complete,
                structured_data={
                    "summary": "Monitoreo semanal determinista: avance en ambos repositorios con pruebas de CI ejecutadas.",
                    "task_state_claims": [],
                    "risks": ["Fallo previo en CI de backend registrado en snapshots."],
                    "suggested_actions": ["Revisar pruebas fallidas de CI."],
                    "referenced_task_ids": ["T-101"],
                    "evidence_ids": ["T-101"],
                },
                provider="simulated_gemini",
                model=self.model,
                duration_seconds=0.05,
                prompt_tokens=1000,
                completion_tokens=200,
            )

        # 2. Quality review stage (revisor)
        if request.role == "revisor":
            scope = request.evidences.get("repository_scope", {})
            repo_id = scope.get("identity", {}).get("id")
            if repo_id == "backend":
                findings = [_build_backend_candidate_dict()]
            else:
                findings = [_build_frontend_candidate_dict()]

            return GenerationResult(
                status=GenerationStatus.complete,
                structured_data={"findings": findings},
                provider="simulated_gemini",
                model=self.model,
                duration_seconds=0.1,
                prompt_tokens=1500,
                completion_tokens=300,
            )

        # 3. Verification stage (verificador)
        if request.role == "verificador":
            if self.verifier_behavior == "error_503":
                raise ProviderInternalError(
                    "Gemini API error 503: This model is currently experiencing high demand."
                )

            finding = request.evidences.get("findings", [{}])[0]
            finding_id = finding.get("id", "unknown")
            evidence_ids = finding.get("evidence_ids", [BACKEND_SHA])

            if self.verifier_behavior == "confirmed":
                verdict = "confirmed"
                reason = "Defecto confirmado: la subconsulta SQL carece de paréntesis de cierre en las líneas visibles."
            elif self.verifier_behavior == "rejected":
                verdict = "rejected"
                reason = "Afirmación contradicha: la sintaxis mostrada en las líneas visibles es válida según la especificación."
            elif self.verifier_behavior == "insufficient":
                verdict = "insufficient"
                reason = "Evidencia insuficiente: el contexto visible no permite determinar la estructura completa de la consulta."
            else:
                verdict = "confirmed"
                reason = "Verificación por defecto."

            return GenerationResult(
                status=GenerationStatus.complete,
                structured_data={
                    "verifications": [
                        {
                            "finding_id": finding_id,
                            "verdict": verdict,
                            "reason": reason,
                            "evidence_ids": evidence_ids[:1],
                        }
                    ]
                },
                provider="simulated_gemini",
                model=self.model,
                duration_seconds=0.08,
                prompt_tokens=800,
                completion_tokens=150,
            )

        # 4. Report synthesis stage (aprobador)
        if request.role == "aprobador":
            return GenerationResult(
                status=GenerationStatus.complete,
                structured_data={
                    "summary": "Resumen ejecutivo MultiRepo con candidatos evaluados deterministamente.",
                    "recommendation": "solicitar_cambios",
                    "recommendations": [
                        "Atender hallazgo CQ-001 en despacho-store.ts.",
                        "Subsanar la prueba fallida en el pipeline de CI del backend.",
                    ],
                },
                provider="simulated_gemini",
                model=self.model,
                duration_seconds=0.06,
                prompt_tokens=1200,
                completion_tokens=250,
            )

        return super().generate(request)


# ==============================================================================
# Comprehensive Test Suite
# ==============================================================================

class DeterministicMultiRepoE2ETestCase(unittest.TestCase):
    """End-to-End deterministic validation of Quality -> Escalation -> Verification -> Storage -> API."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.db_path = str(self.root / "analysis.sqlite")
        self.runs_dir = str(self.root / "runs")
        self.store = SQLiteStorageAdapter(self.db_path)
        self.artifacts = ArtifactStorageAdapter(self.runs_dir)

        self.backend_identity, self.frontend_identity = _build_test_identities()
        self.backend_snippet, self.frontend_snippet = _build_test_snippets(
            self.backend_identity, self.frontend_identity
        )

        self.mandatory_rule = QualityRule(
            id="Q3",
            description="Zero critical defects in database queries",
            metric_key="open_critical_findings",
            operator="==",
            threshold=0,
            mandatory=True,
        )

        self.now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

    def _build_snapshot(self) -> WeeklySnapshot:
        backend_state = RepositoryAnalysisState(
            identity=self.backend_identity,
            branch="develop",
            target_commit_sha=BACKEND_SHA,
            commit_resolution_status=CommitResolutionStatus.resolved,
            access_status=RepositoryAccessStatus.allowed,
            availability_status=RepositoryAvailabilityStatus.available,
        )
        frontend_state = RepositoryAnalysisState(
            identity=self.frontend_identity,
            branch="develop",
            target_commit_sha=FRONTEND_SHA,
            commit_resolution_status=CommitResolutionStatus.resolved,
            access_status=RepositoryAccessStatus.allowed,
            availability_status=RepositoryAvailabilityStatus.available,
        )

        backend_ci = CIRunResult(
            id="run-backend-ci",
            workflow_name="quality",
            workflow_path=".github/workflows/quality.yml",
            commit_sha=BACKEND_SHA,
            status="failure",
            total_tests=49,
            failed_tests=1,
            coverage_percentage=97.9,
            repository_identity=self.backend_identity,
            run_attempt=1,
            metrics_artifact=CiMetricsArtifactMetadata(
                outcome=CiArtifactOutcome.validated,
                certifies_metrics=True,
                schema_valid=True,
                semantics_valid=True,
                identity_bound=True,
                workflow_status_matches=True,
                artifact_name=f"ci-quality-{BACKEND_SHA}-run-backend-ci-1",
                metrics=CiMetricsArtifactValues(
                    repository=f"{BACKEND_OWNER}/{BACKEND_REPO_NAME}",
                    commit_sha=BACKEND_SHA,
                    workflow_run_id="run-backend-ci",
                    workflow_run_attempt="1",
                    status=CiWorkflowStatus.failure,
                    total_tests=49,
                    failed_tests=1,
                    coverage_percentage=97.9,
                    coverage_metric="lines",
                    coverage_basis="loaded TypeScript unit-test lines",
                ),
            ),
            provenance=RepositoryProvenance(
                repository_identity=self.backend_identity,
                commit_sha=BACKEND_SHA,
                source="github_actions",
                workflow_path=".github/workflows/quality.yml",
                run_id="run-backend-ci",
                run_attempt=1,
            ),
        )

        frontend_ci = CIRunResult(
            id="run-frontend-ci",
            workflow_name="quality",
            workflow_path=".github/workflows/quality.yml",
            commit_sha=FRONTEND_SHA,
            status="success",
            total_tests=31,
            failed_tests=0,
            coverage_percentage=92.73,
            repository_identity=self.frontend_identity,
            run_attempt=1,
            metrics_artifact=CiMetricsArtifactMetadata(
                outcome=CiArtifactOutcome.validated,
                certifies_metrics=True,
                schema_valid=True,
                semantics_valid=True,
                identity_bound=True,
                workflow_status_matches=True,
                artifact_name=f"ci-quality-{FRONTEND_SHA}-run-frontend-ci-1",
                metrics=CiMetricsArtifactValues(
                    repository=f"{FRONTEND_OWNER}/{FRONTEND_REPO_NAME}",
                    commit_sha=FRONTEND_SHA,
                    workflow_run_id="run-frontend-ci",
                    workflow_run_attempt="1",
                    status=CiWorkflowStatus.success,
                    total_tests=31,
                    failed_tests=0,
                    coverage_percentage=92.73,
                    coverage_metric="lines",
                    coverage_basis="loaded TypeScript lines excluding TSX and UI",
                ),
            ),
            provenance=RepositoryProvenance(
                repository_identity=self.frontend_identity,
                commit_sha=FRONTEND_SHA,
                source="github_actions",
                workflow_path=".github/workflows/quality.yml",
                run_id="run-frontend-ci",
                run_attempt=1,
            ),
        )

        backend_snapshot = RepositorySnapshotResult(
            repository_state=backend_state,
            code_diffs=[self.backend_snippet],
            ci_runs=[backend_ci],
            is_complete=False,
            incomplete_reason="Backend repository CI failed on commit.",
        )
        frontend_snapshot = RepositorySnapshotResult(
            repository_state=frontend_state,
            code_diffs=[self.frontend_snippet],
            ci_runs=[frontend_ci],
            is_complete=True,
            incomplete_reason=None,
        )

        return WeeklySnapshot(
            id="snapshot-historical-e2e",
            project_id="HORMIGESTION",
            cutoff_datetime=self.now,
            captured_at=self.now,
            tasks=[TaskCard(id="T-101", title="Modulo de despacho", state=TaskState.in_progress)],
            code_diffs=[],
            ci_runs=[],
            policies=[self.mandatory_rule],
            is_complete=False,
            repository_results=[backend_snapshot, frontend_snapshot],
        )

    def _build_orchestrator(self, provider):
        return ProjectOrchestrator(
            sqlite_storage=self.store,
            artifact_storage=self.artifacts,
            elaborador_provider=provider,
            revisor_provider=provider,
            verificador_provider=provider,
            aprobador_provider=provider,
            config=OrchestratorConfig(
                project_id="HORMIGESTION",
                allow_remote_ai=True,
                max_llm_calls_per_run=12,
                runs_dir=self.runs_dir,
                db_path=self.db_path,
                is_simulated=True,
            ),
        )

    # --------------------------------------------------------------------------
    # Fase B: Quality y EscalationGate
    # --------------------------------------------------------------------------
    def test_phase_b_quality_and_differential_escalation_gate(self):
        """Valida que Quality genera ambos candidatos y EscalationGate escala solo el de alta severidad."""
        provider = DeterministicMultiRepoProvider(verifier_behavior="confirmed")
        snapshot = self._build_snapshot()

        coordinator = StageInvocationCoordinator(budget_tracker=GlobalBudgetTracker(max_calls=10))
        outcome = analyze_repository_results(
            snapshot.repository_results,
            project_id="HORMIGESTION",
            draft=MonitoringDraft(summary="Draft", observed_progress=0.8, risks_detected=[]),
            rules=snapshot.policies,
            quality_provider=provider,
            verifier_provider=provider,
            coordinator=coordinator,
            escalation_gate=EscalationGate(),
            config=OrchestratorConfig(project_id="HORMIGESTION", is_simulated=True),
        )

        # 1. Ambos candidatos retenidos en candidate_findings
        self.assertEqual(len(outcome.candidate_findings), 2)
        candidates_by_repo = {f.repository_identity.id: f for f in outcome.candidate_findings}
        self.assertIn("backend", candidates_by_repo)
        self.assertIn("frontend", candidates_by_repo)

        # 2. Cada candidato pertenece exclusivamente a su repositorio y SHA
        backend_cand = candidates_by_repo["backend"]
        self.assertEqual(backend_cand.commit_sha, BACKEND_SHA)
        self.assertEqual(backend_cand.severity, "high")
        self.assertTrue(backend_cand.id.startswith(f"backend::{BACKEND_SHA}::"))

        frontend_cand = candidates_by_repo["frontend"]
        self.assertEqual(frontend_cand.commit_sha, FRONTEND_SHA)
        self.assertEqual(frontend_cand.severity, "low")
        self.assertTrue(frontend_cand.id.startswith(f"frontend::{FRONTEND_SHA}::"))

        # 3. Decisiones de EscalationGate
        decisions_by_id = {d.finding_id: d for d in outcome.escalation_decisions}
        self.assertTrue(decisions_by_id[backend_cand.id].should_escalate)
        self.assertGreater(len(decisions_by_id[backend_cand.id].reasons), 0)
        self.assertFalse(decisions_by_id[frontend_cand.id].should_escalate)

        # 4. El candidato frontend no desaparece: queda en pending
        self.assertIn(frontend_cand.id, [f.id for f in outcome.pending_findings])

    # --------------------------------------------------------------------------
    # Fase C: Escenarios de Verificación Independiente
    # --------------------------------------------------------------------------
    def test_phase_c1_confirmed_finding_with_unvalidated_code(self):
        """C1: Backend confirmado en base a snippet sin full_file; suggested_code permanece not_validated."""
        provider = DeterministicMultiRepoProvider(verifier_behavior="confirmed")
        snapshot = self._build_snapshot()
        orchestrator = self._build_orchestrator(provider)

        run = orchestrator.execute(snapshot=snapshot)
        self.assertIn(run.status, (RunStatus.awaiting_human, RunStatus.needs_attention))

        report = self.store.get_report(run.report_id)
        backend_result = next(r for r in report.repository_results if r.repository_state.identity.id == "backend")

        self.assertEqual(len(backend_result.confirmed_findings), 1)
        confirmed = backend_result.confirmed_findings[0]
        self.assertEqual(confirmed.verification_status, "confirmed")
        self.assertEqual(confirmed.suggested_code_validation_status, SuggestedCodeValidationStatus.not_validated)
        self.assertIn("subconsulta SQL carece de paréntesis", confirmed.verification_reason)

    def test_phase_c2_rejected_finding(self):
        """C2: Afirmación rechazada por el verificador."""
        provider = DeterministicMultiRepoProvider(verifier_behavior="rejected")
        snapshot = self._build_snapshot()
        orchestrator = self._build_orchestrator(provider)

        run = orchestrator.execute(snapshot=snapshot)
        report = self.store.get_report(run.report_id)
        backend_result = next(r for r in report.repository_results if r.repository_state.identity.id == "backend")

        self.assertEqual(len(backend_result.confirmed_findings), 0)
        self.assertEqual(len(backend_result.verifications), 1)
        self.assertEqual(backend_result.verifications[0].verdict, VerificationVerdict.rejected)
        # Sigue apareciendo en candidate_findings con su status rejected
        rejected_in_candidates = next(f for f in report.candidate_findings if f.id == backend_result.verifications[0].finding_id)
        self.assertEqual(rejected_in_candidates.verification_status, "rejected")
        self.assertEqual(rejected_in_candidates.suggested_code_validation_status, SuggestedCodeValidationStatus.not_validated)

    def test_phase_c3_insufficient_evidence(self):
        """C3: Verificación con evidencia insuficiente (e.g. afirmación que depende de código ausente)."""
        provider = DeterministicMultiRepoProvider(verifier_behavior="insufficient")
        snapshot = self._build_snapshot()
        orchestrator = self._build_orchestrator(provider)

        run = orchestrator.execute(snapshot=snapshot)
        report = self.store.get_report(run.report_id)
        backend_result = next(r for r in report.repository_results if r.repository_state.identity.id == "backend")

        self.assertEqual(len(backend_result.confirmed_findings), 0)
        self.assertEqual(len(backend_result.verifications), 1)
        self.assertEqual(backend_result.verifications[0].verdict, VerificationVerdict.insufficient)
        insufficient_in_candidates = next(f for f in report.candidate_findings if f.id == backend_result.verifications[0].finding_id)
        self.assertEqual(insufficient_in_candidates.verification_status, "insufficient")
        self.assertIn("Evidencia insuficiente", insufficient_in_candidates.verification_reason)

    def test_phase_c4_unescalated_candidate_remains_pending_zero_verifier_calls(self):
        """C4: Candidato frontend de baja severidad permanece pending y no llama al verificador."""
        provider = DeterministicMultiRepoProvider(verifier_behavior="confirmed")
        snapshot = self._build_snapshot()
        orchestrator = self._build_orchestrator(provider)

        run = orchestrator.execute(snapshot=snapshot)
        report = self.store.get_report(run.report_id)
        frontend_result = next(r for r in report.repository_results if r.repository_state.identity.id == "frontend")

        self.assertEqual(len(frontend_result.confirmed_findings), 0)
        self.assertEqual(len(frontend_result.verifications), 0)
        self.assertEqual(len(frontend_result.pending_findings), 1)
        pending_f = frontend_result.pending_findings[0]
        self.assertEqual(pending_f.verification_status, "pending")
        self.assertEqual(pending_f.suggested_code_validation_status, SuggestedCodeValidationStatus.not_validated)
        self.assertIsNotNone(pending_f.suggested_code)

        # Confirmar que Verifier fue llamado solo para el backend
        verifier_requests = [r for r in provider.requests if r.role == "verificador"]
        self.assertEqual(len(verifier_requests), 1)
        scope = verifier_requests[0].evidences.get("repository_scope", {})
        self.assertEqual(scope.get("identity", {}).get("id"), "backend")

    def test_phase_c5_provider_failure_does_not_lose_candidate_nor_evidence(self):
        """C5: Fallo de infraestructura (503) del Verificador mantiene estado trazable y preserva candidato."""
        provider = DeterministicMultiRepoProvider(verifier_behavior="error_503")
        snapshot = self._build_snapshot()
        orchestrator = self._build_orchestrator(provider)

        run = orchestrator.execute(snapshot=snapshot)
        # Fallo de verificador lleva la etapa a needs_attention
        self.assertEqual(run.stages["verifier:backend"].status, StageStatus.needs_attention)
        self.assertEqual(run.status, RunStatus.needs_attention)

        report = self.store.get_report(run.report_id)
        # El candidato no se confirmó
        self.assertEqual(len(report.confirmed_findings), 0)
        # El candidato sigue existiendo en candidate_findings y en pending
        self.assertEqual(len(report.candidate_findings), 2)
        backend_cand = next(f for f in report.candidate_findings if f.repository_identity.id == "backend")
        self.assertEqual(backend_cand.verification_status, "pending")
        self.assertIn("ProviderInternalError", backend_cand.verification_reason)

    # --------------------------------------------------------------------------
    # Fase D: Persistencia y Reportes Aislados
    # --------------------------------------------------------------------------
    def test_phase_d_isolated_persistence_and_report_integrity(self):
        """Valida que todos los artefactos y reportes se persisten íntegramente en storage aislado."""
        provider = DeterministicMultiRepoProvider(verifier_behavior="confirmed")
        snapshot = self._build_snapshot()
        orchestrator = self._build_orchestrator(provider)

        run = orchestrator.execute(snapshot=snapshot)

        # 1. Verificar artefactos en disco
        run_artifacts_dir = Path(self.runs_dir) / run.run_id
        self.assertTrue((run_artifacts_dir / "code_findings.json").exists())
        self.assertTrue((run_artifacts_dir / "report.json").exists())
        self.assertTrue((run_artifacts_dir / "report.md").exists())
        self.assertTrue((run_artifacts_dir / "execution_metrics.json").exists())

        # 2. Contenido de code_findings.json
        code_findings_disk = json.loads((run_artifacts_dir / "code_findings.json").read_text(encoding="utf-8"))
        self.assertEqual(len(code_findings_disk), 2)
        for item in code_findings_disk:
            self.assertEqual(item["suggested_code_validation_status"], "not_validated")
            self.assertIn("improvement_summary", item)
            self.assertIn("suggested_code", item)

        # 3. Contenido de report.json
        report_json = json.loads((run_artifacts_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report_json["candidate_findings"]), 2)
        self.assertEqual(len(report_json["repository_results"]), 2)

        # 4. Reporte Markdown contiene candidatos y advertencia de no validado
        report_md = (run_artifacts_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("despacho-store.ts", report_md)
        self.assertIn("No se calcula cobertura global", report_md)
        self.assertNotIn("Cobertura global: 9", report_md)

        # 5. Recarga desde nueva instancia de SQLiteStorageAdapter
        new_store = SQLiteStorageAdapter(self.db_path)
        persisted_findings = new_store.get_findings(run.run_id)
        self.assertEqual(len(persisted_findings), 2)
        persisted_report = new_store.get_report(run.report_id)
        self.assertEqual(len(persisted_report.candidate_findings), 2)

        # 6. Comprobar que bases históricas reales (.runtime/agente.db) NO fueron tocadas
        real_db = Path(".runtime/agente.db")
        if real_db.exists():
            # Run ID generado no debe existir en la base real
            import sqlite3
            conn = sqlite3.connect(str(real_db))
            count = conn.execute("SELECT COUNT(*) FROM runs WHERE run_id=?", (run.run_id,)).fetchone()[0]
            conn.close()
            self.assertEqual(count, 0)

    # --------------------------------------------------------------------------
    # Fase E: Contratos de la API HTTP (FastAPI TestClient)
    # --------------------------------------------------------------------------
    def test_phase_e_api_endpoints_expose_candidates_and_unvalidated_status(self):
        """Valida que los endpoints existentes exponen fielmente los candidatos y su estado not_validated."""
        provider = DeterministicMultiRepoProvider(verifier_behavior="confirmed")
        snapshot = self._build_snapshot()
        orchestrator = self._build_orchestrator(provider)
        run = orchestrator.execute(snapshot=snapshot)

        operator_token = "synthetic-e2e-operator-token-0000000000000000"
        api_settings = ApiSettings(
            db_path=self.db_path,
            job_ledger_path=str(self.root / "jobs.sqlite"),
            projects=(
                ProjectConfig(
                    id="HORMIGESTION",
                    name="Hormigestion MultiRepo E2E",
                    source_mode="real",
                    repositories=(
                        RepositoryConfiguration(
                            id="backend", owner=BACKEND_OWNER, repo=BACKEND_REPO_NAME, branch="develop",
                        ),
                        RepositoryConfiguration(
                            id="frontend", owner=FRONTEND_OWNER, repo=FRONTEND_REPO_NAME, branch="develop",
                        ),
                    ),
                ),
            ),
            operator_token=SecretStr(operator_token),
            operator_project_ids=("HORMIGESTION",),
            allowed_origins=("http://testserver",),
            allowed_hosts=("testserver",),
            external_writes_enabled=False,
        )

        app = create_app(api_settings)
        with TestClient(app) as client:
            # Login
            login_res = client.post(
                "/api/v1/auth/login",
                json={"token": operator_token},
                headers={"Origin": "http://testserver"},
            )
            self.assertEqual(login_res.status_code, 200)

            # 1. Endpoint /api/v1/projects/HORMIGESTION/repository-results
            res = client.get("/api/v1/projects/HORMIGESTION/repository-results")
            self.assertEqual(res.status_code, 200, res.text)
            body = res.json()
            self.assertEqual(body["project_id"], "HORMIGESTION")
            self.assertGreaterEqual(body["total"], 2)

            backend_item = next(i for i in body["items"] if i["repository"]["repository_id"] == "backend")
            self.assertEqual(len(backend_item["findings"]), 1)
            b_finding = backend_item["findings"][0]
            self.assertEqual(b_finding["verification_status"], "confirmed")
            self.assertEqual(b_finding["suggested_code_validation_status"], "not_validated")
            self.assertEqual(b_finding["severity"], "high")
            self.assertEqual(b_finding["file_path"], "src/infrastructure/database/despacho-store.ts")
            self.assertEqual(b_finding["line"], 75)
            self.assertIn("consulta SQL", b_finding["claim"])
            self.assertIn("obtenerConductoresDisponibles", b_finding["suggested_code"])

            # 2. Endpoint /api/v1/runs/{run_id}/findings filtrado por repository_id
            backend_findings_res = client.get(
                f"/api/v1/runs/{run.run_id}/findings",
                params={"repository_id": "backend"},
            )
            self.assertEqual(backend_findings_res.status_code, 200)
            b_page = backend_findings_res.json()
            self.assertEqual(b_page["total"], 1)
            self.assertEqual(b_page["items"][0]["suggested_code_validation_status"], "not_validated")

            frontend_findings_res = client.get(
                f"/api/v1/runs/{run.run_id}/findings",
                params={"repository_id": "frontend"},
            )
            self.assertEqual(frontend_findings_res.status_code, 200)
            f_page = frontend_findings_res.json()
            self.assertEqual(f_page["total"], 1)
            self.assertEqual(f_page["items"][0]["verification_status"], "pending")
            self.assertEqual(f_page["items"][0]["suggested_code_validation_status"], "not_validated")

            # 3. Endpoint detalle de hallazgo: /api/v1/runs/{run_id}/findings/{finding_id}
            detail_res = client.get(
                f"/api/v1/runs/{run.run_id}/findings/{b_finding['finding_id']}",
            )
            self.assertEqual(detail_res.status_code, 200)
            detail = detail_res.json()
            self.assertEqual(detail["suggested_code_validation_status"], "not_validated")
            self.assertEqual(detail["improvement_summary"], _build_backend_candidate_dict()["improvement_summary"])


if __name__ == "__main__":
    unittest.main()
