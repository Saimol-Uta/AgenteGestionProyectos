"""Deterministic temporary data for the local web API HTTP tests."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from agente_gestion.adapters.outbox.hybrid_outbox import HybridNotificationOutboxAdapter
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.api.app import ApiSettings, ProjectConfig, create_app
from agente_gestion.application.reporting import generate_report_markdown
from agente_gestion.domain.action_proposal import ActionProposal
from agente_gestion.domain.code import CIRunResult, CodeDiffSnippet
from agente_gestion.domain.evidence import EvidenceItem, EvidenceProvenance, EvidenceSource
from agente_gestion.domain.findings import (
    CandidateFinding,
    FindingSeverity,
    FindingVerification,
    VerificationVerdict,
)
from agente_gestion.domain.llm import GenerationStatus, LLMCallAttempt
from agente_gestion.domain.policies import PolicyEvaluationResult, RuleEvaluation, RuleEvaluationStatus
from agente_gestion.domain.notification_outbox import OutboxMessage
from agente_gestion.domain.report import FinalReport, HumanDecisionState
from agente_gestion.domain.run import ExecutionRun, RunStatus, StageExecutionRecord, StageStatus
from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.domain.tasks import TaskCard, TaskState, calculate_task_metrics
from agente_gestion.domain.weekly_schedule import WeeklySchedule


PROJECT_ID = "H5-TEST"
TASK_ID = "task-001"
OPERATOR_TOKEN = "test-operator-token-not-for-logs"
EVIDENCE_SECRET = "fixture-evidence-secret-do-not-return"
RUN_ID = "run-current"
OLDER_RUN_ID = "run-older"
FINDING_ID = "finding-candidate-001"
CODE_EVIDENCE_ID = "evidence-code-001"
REPORT_ID = "report-current"
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"


@dataclass
class WebAPIFixture:
    client: TestClient
    database_path: Path
    job_ledger_path: Path | None = None
    configured_project_id: str = PROJECT_ID
    task_id: str = TASK_ID
    older_capture_id: str = ""
    current_capture_id: str = ""
    outbox_dir: Path | None = None


def _snapshot(snapshot_id: str, title: str, captured_at: datetime, project_id: str) -> WeeklySnapshot:
    task_id = TASK_ID if project_id == PROJECT_ID else "stored-task-1"
    code_diffs = []
    if project_id == PROJECT_ID and snapshot_id == "snapshot-current":
        code_diffs = [CodeDiffSnippet(
            repository="acme/h5-test",
            commit_sha=COMMIT_SHA,
            file_path="src/api/input.py",
            lines=[20, 21, 22],
            content=("def parse_input(value):\n"
                     f"    api_token = '{EVIDENCE_SECRET}'\n"
                     "    return value\n"),
            evidence_scope="diff",
            is_complete=False,
        )]
    return WeeklySnapshot(
        id=snapshot_id,
        project_id=project_id,
        cutoff_datetime=captured_at,
        captured_at=captured_at,
        task_source_identity="fixture:board:mapping-v1",
        tasks_complete=True,
        tasks=[TaskCard(
            id=task_id,
            title=title,
            state=TaskState.in_progress,
            list_id="list-in-progress",
            list_name="En curso",
            assignee_ids=["operator-1"],
            due_date=datetime(2026, 1, 10, 12, tzinfo=timezone.utc),
            detail=f"Descripción de fixture; api_token={EVIDENCE_SECRET}",
            observed_fields=["title", "state", "list_name", "assignee_ids", "due_date"],
        )],
        code_diffs=code_diffs,
    )


def create_web_fixture_db(database_path: Path) -> tuple[str, str]:
    """Create only an isolated temporary DB with two captures and safe test evidence."""
    storage = SQLiteStorageAdapter(str(database_path))
    first = _snapshot("snapshot-older", "Tarea anterior", datetime(2026, 1, 1, tzinfo=timezone.utc), PROJECT_ID)
    second = _snapshot(
        "snapshot-current", f"Tarea crítica api_token={EVIDENCE_SECRET}",
        datetime(2026, 1, 8, tzinfo=timezone.utc), PROJECT_ID,
    )
    older_capture = storage.save_snapshot_with_capture_receipt(first)
    current_capture = storage.save_snapshot_with_capture_receipt(second)

    # A stored-only project exercises actual database discovery beyond configured projects.
    stored_only = _snapshot("snapshot-stored-only", "Tarea almacenada", datetime(2026, 1, 9, tzinfo=timezone.utc), "STORED-ONLY")
    storage.save_snapshot_with_capture_receipt(stored_only)

    run_id = RUN_ID
    run_time = datetime(2026, 1, 8, tzinfo=timezone.utc)
    run = ExecutionRun(
        run_id=run_id,
        project_id=PROJECT_ID,
        status=RunStatus.completed,
        created_at=run_time,
        updated_at=run_time,
        snapshot_id=second.id,
        config_hash="config-hash",
        snapshot_hash="snapshot-hash",
        duration_seconds=4.5,
        report_id=REPORT_ID,
        is_simulated=True,
    )
    storage.save_run(run)
    storage.save_run(ExecutionRun(
        run_id=OLDER_RUN_ID,
        project_id=PROJECT_ID,
        status=RunStatus.failed,
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        snapshot_id=first.id,
        config_hash="older-config-hash",
        snapshot_hash="older-snapshot-hash",
        duration_seconds=1.25,
        is_simulated=True,
    ))

    code_evidence = EvidenceItem(
        provenance=EvidenceProvenance(
            id=CODE_EVIDENCE_ID,
            source=EvidenceSource.github,
            project_id=PROJECT_ID,
            repository="acme/h5-test",
            commit_sha=COMMIT_SHA,
            file_path="src/api/input.py",
            lines=[20, 21, 22],
            timestamp=second.captured_at,
            payload={"evidence_scope": "diff", "api_token": EVIDENCE_SECRET},
        ),
        category="diff",
        summary=f"Fragmento parcial de revisión; api_token={EVIDENCE_SECRET}",
    )
    evidence = EvidenceItem(
        provenance=EvidenceProvenance(
            id="evidence-task-001",
            source=EvidenceSource.trello,
            project_id=PROJECT_ID,
            external_id=TASK_ID,
            timestamp=second.captured_at,
            payload={
                "card_id": TASK_ID,
                "due_date": "2026-01-10T12:00:00+00:00",
                "observed_fields": ["due_date"],
                "api_token": EVIDENCE_SECRET,
            },
        ),
        category="task",
        summary=f"Cambio observado; api_token={EVIDENCE_SECRET}",
    )
    storage.save_evidence(run_id, [evidence, code_evidence])
    storage.save_action_proposal(ActionProposal(
        proposal_id="proposal-date-001",
        project_id=PROJECT_ID,
        task_id=TASK_ID,
        observed_capture_id=current_capture.capture_id,
        field="due_date",
        current_value=datetime(2026, 1, 10, 12, tzinfo=timezone.utc),
        proposed_value=datetime(2026, 1, 11, 12, tzinfo=timezone.utc),
        evidence_ids=["evidence-task-001"],
        rationale="Reprogramar el vencimiento tras la revisión del cronograma.",
        expected_impact="Disponer de un día adicional para completar la tarea.",
    ), [evidence])

    finding = CandidateFinding(
        id=FINDING_ID,
        rule_id="Q1",
        claim="Revisar validación de entrada",
        severity=FindingSeverity.high,
        evidence_ids=[CODE_EVIDENCE_ID],
        commit_sha=COMMIT_SHA,
        file_path="src/api/input.py",
        line=21,
        confidence=0.91,
        requires_verification=True,
        category="inyeccion_sql",
        evidence=f"api_token={EVIDENCE_SECRET}",
        suggested_fix="Validar el valor antes de procesarlo.",
    )
    storage.save_findings(run_id, [finding])
    storage.save_verifications(run_id, [FindingVerification(
        finding_id=FINDING_ID,
        verdict=VerificationVerdict.insufficient,
        reason="El fragmento parcial no permite confirmar la ausencia de validación.",
        verified_by="verifier-fixture",
        evidence_ids=[CODE_EVIDENCE_ID],
    )])
    for stage_name, status, duration in (
        ("source_collection", StageStatus.completed, 0.5),
        ("monitoring", StageStatus.completed, 1.0),
        ("quality", StageStatus.completed, 2.0),
        ("verifier", StageStatus.skipped, 0.0),
        ("report", StageStatus.completed, 1.0),
    ):
        storage.save_stage_result(run_id, StageExecutionRecord(
            stage_name=stage_name,
            status=status,
            duration_seconds=duration,
            output_summary=("Skipped: no escalated findings." if status == StageStatus.skipped
                            else f"{stage_name.title()} completed."),
            completed_at=run_time,
        ))
    storage.save_llm_attempts(run_id, [
        LLMCallAttempt(
            attempt_id="attempt-monitoring-001", stage="monitoring", provider="fake_simulated",
            model="fake-monitoring", status=GenerationStatus.complete, duration_seconds=1.0,
        ),
        LLMCallAttempt(
            attempt_id="attempt-quality-001", stage="quality", provider="fake_simulated",
            model="fake-quality", status=GenerationStatus.complete, duration_seconds=2.0,
        ),
        LLMCallAttempt(
            attempt_id="attempt-report-001", stage="report", provider="fake_simulated",
            model="fake-report", status=GenerationStatus.complete, duration_seconds=1.0,
        ),
    ])
    policies = PolicyEvaluationResult(
        evaluations=[
            RuleEvaluation(rule_id="Q1", description="Validación básica", status=RuleEvaluationStatus.pass_,
                           observed_value=True, mandatory=True, reason="Regla satisfecha."),
            RuleEvaluation(rule_id="Q2", description="Cobertura informada", status=RuleEvaluationStatus.unknown,
                           observed_value=None, mandatory=True, reason="No hay cobertura persistida."),
            RuleEvaluation(rule_id="Q3", description="Revisión de código", status=RuleEvaluationStatus.fail,
                           observed_value=1, mandatory=False, reason="Existe un candidato sin confirmación."),
        ],
        passed_rule_ids=["Q1"],
        failed_rule_ids=["Q3"],
        unknown_rule_ids=["Q2"],
        mandatory_failed_ids=[],
        mandatory_unknown_ids=["Q2"],
        recommended_action="evidencia_insuficiente",
    )
    report = FinalReport(
        project_id=PROJECT_ID,
        cutoff_datetime=second.cutoff_datetime,
        metrics=calculate_task_metrics(second.tasks, second.cutoff_datetime),
        ci_result=CIRunResult(
            id="ci-run-001", workflow_name="tests", commit_sha=COMMIT_SHA,
            status="success", total_tests=10, failed_tests=0, coverage_percentage=None,
            timestamp=run_time,
        ),
        insufficient_findings=[finding],
        policy_evaluations=policies,
        recommendation="evidencia_insuficiente",
        human_decision=HumanDecisionState.pending,
        summary="Informe de fixture basado en evidencia persistida.",
        recommendations=["Ampliar la evidencia antes de confirmar."],
        report_id=REPORT_ID,
        run_id=run_id,
        providers_used={"monitoring": "fake_simulated/fake-monitoring"},
        candidate_findings=[finding],
        all_evidence_ids=[CODE_EVIDENCE_ID],
    )
    report_markdown = generate_report_markdown(report)
    report_markdown += "\n\n<script>window.__h5_fixture_xss = true</script>\n"
    report_markdown += (
        "[enlace no seguro](javascript:alert(1))\n![beacon](https://attacker.invalid/pixel)\n"
        "![beacon de referencia][pixel]\n[pixel]: https://attacker.invalid/reference\n"
        "[enlace de referencia][bad]\n[bad]: javascript:alert(1)\n"
    )
    storage.save_report(report, report_id=REPORT_ID, report_md=report_markdown, run_id=run_id)
    outbox = HybridNotificationOutboxAdapter(storage, database_path.parent / "outbox")
    outbox.deliver(OutboxMessage(
        message_id="fixture-weekly-message",
        project_id=PROJECT_ID,
        window_id="a" * 64,
        report_id=REPORT_ID,
        channel="file",
        subject="Reporte semanal de prueba",
        summary="Resumen persistido para pruebas de reportería.",
        body_markdown=("Contenido semanal persistido.\n<script>window.__h5_outbox_xss=true</script>\n"
                       "![beacon](https://attacker.invalid/pixel)\n"
                       "![beacon de referencia][pixel]\n[pixel]: https://attacker.invalid/reference"),
        metrics=report.metrics.model_dump(mode="json"),
        risks=["Riesgo de fixture"],
        recipient="gestor_proyecto",
    ))
    return older_capture.capture_id, current_capture.capture_id


def build_web_api_fixture(
    root: Path,
    *,
    database_name: str = "web-fixture.sqlite",
    operator_token: str | None = OPERATOR_TOKEN,
    session_ttl_seconds: int = 3600,
    analysis_enabled: bool = False,
    analysis_executor=None,
    analysis_plan_loader=None,
    analysis_config_path: Path | None = None,
    job_ledger_name: str = "web-jobs.sqlite",
    analysis_queue_capacity: int = 3,
    governance_enabled: bool = False,
    report_decisions_enabled: bool = False,
    weekly_cycle_enabled: bool = False,
    weekly_schedule: WeeklySchedule | None = None,
    schedule_clock=None,
) -> WebAPIFixture:
    """Build an isolated app/client/DB triple. Never points at repository runtime data."""
    database_path = root / database_name
    older_capture, current_capture = create_web_fixture_db(database_path)
    if analysis_enabled and analysis_config_path is None:
        analysis_config_path = root / "analysis-config.yaml"
        analysis_config_path.write_text("{}\n", encoding="utf-8")
    job_ledger_path = root / job_ledger_name
    outbox_dir = root / "outbox"
    settings = ApiSettings(
        db_path=str(database_path),
        outbox_dir=str(outbox_dir),
        projects=[ProjectConfig(id=PROJECT_ID, name="Proyecto de prueba", source_mode="fixtures")],
        operator_token=operator_token,
        operator_actor="operador-local-de-prueba",
        operator_project_ids=[PROJECT_ID],
        allowed_origins=["http://testserver"],
        allowed_hosts=["testserver"],
        session_ttl_seconds=session_ttl_seconds,
        analysis_enabled=analysis_enabled,
        analysis_config_path=str(analysis_config_path) if analysis_config_path else None,
        job_ledger_path=str(job_ledger_path),
        analysis_queue_capacity=analysis_queue_capacity,
        governance_enabled=governance_enabled,
        report_decisions_enabled=report_decisions_enabled,
        weekly_cycle_enabled=weekly_cycle_enabled,
        weekly_schedule=weekly_schedule,
        weekly_schedule_status="configured" if weekly_schedule else "not_configured",
    )
    return WebAPIFixture(
        client=TestClient(create_app(
            settings,
            analysis_executor=analysis_executor,
            analysis_plan_loader=analysis_plan_loader,
            schedule_clock=schedule_clock,
        )),
        database_path=database_path,
        job_ledger_path=job_ledger_path,
        outbox_dir=outbox_dir,
        older_capture_id=older_capture,
        current_capture_id=current_capture,
    )
