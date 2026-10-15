"""Deterministic temporary data for the local web API HTTP tests and demo."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
import uuid
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.adapters.outbox.hybrid_outbox import HybridNotificationOutboxAdapter
from agente_gestion.adapters.outbox.smtp import SMTPConfig
from agente_gestion.adapters.storage.artifacts import ArtifactStorageAdapter
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.api.app import ApiSettings, ProjectConfig, create_app
from agente_gestion.application.manager_notifications import ManagerNotificationService
from agente_gestion.application.orchestrator import OrchestratorConfig, ProjectOrchestrator
from agente_gestion.application.proposal_auto_review import ProposalAutoReviewService
from agente_gestion.domain.action_proposal import ActionProposal
from agente_gestion.domain.code import CodeDiffSnippet
from agente_gestion.domain.evidence import EvidenceItem, EvidenceProvenance, EvidenceSource
from agente_gestion.domain.policies import QualityRule
from agente_gestion.domain.run import RunStatus
from agente_gestion.domain.snapshot import SnapshotCapture, WeeklySnapshot
from agente_gestion.domain.tasks import TaskCard, TaskState
from agente_gestion.domain.weekly_schedule import WeeklySchedule


PROJECT_ID = "H5-TEST"
STORED_PROJECT_ID = "STORED-ONLY"
TASK_ID = "task-001"
OPERATOR_TOKEN = "test-operator-token-not-for-logs"
EVIDENCE_SECRET = "fixture-evidence-secret-do-not-return"
OLDER_SNAPSHOT_ID = "snapshot-older"
CURRENT_SNAPSHOT_ID = "snapshot-current"
OLDER_CAPTURED_AT = datetime(2026, 1, 8, 18, 0, tzinfo=timezone.utc)
CURRENT_CAPTURED_AT = datetime(2026, 1, 15, 18, 0, tzinfo=timezone.utc)
FIXTURE_CLOCK = datetime(2026, 1, 22, 18, 0, tzinfo=timezone.utc)
SOURCE_REPOSITORY = "acme/h5-test"
SOURCE_COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"
COMMIT_SHA = SOURCE_COMMIT_SHA
SOURCE_FILE_PATH = "src/metrics.py"
SOURCE_CONTENT = "def calculate_ratio(total): return 1 / total\n"
SOURCE_SUGGESTION = "return 0 if total == 0 else 1 / total"
FINDING_ID = "f-fake-1"
CODE_EVIDENCE_ID = SOURCE_COMMIT_SHA


def _stable_run_id(snapshot_id: str, captured_at: datetime) -> str:
    local_time = captured_at.astimezone(ZoneInfo("America/Guayaquil"))
    suffix = uuid.uuid5(uuid.NAMESPACE_URL, f"h5-unified-fixture:{snapshot_id}").hex[:6]
    return f"run-{local_time.strftime('%Y%m%d-%H%M%S')}-{suffix}"


OLDER_RUN_ID = _stable_run_id(OLDER_SNAPSHOT_ID, OLDER_CAPTURED_AT)
RUN_ID = _stable_run_id(CURRENT_SNAPSHOT_ID, CURRENT_CAPTURED_AT)
REPORT_ID = f"rep-{RUN_ID}"


@dataclass(frozen=True)
class UnifiedFixtureSeed:
    project_id: str
    capture_ids: tuple[str, str]
    run_ids: tuple[str, str]
    report_ids: tuple[str, str]
    proposal_id: str
    runs_path: Path
    outbox_path: Path

    @property
    def older_capture_id(self) -> str:
        return self.capture_ids[0]

    @property
    def current_capture_id(self) -> str:
        return self.capture_ids[1]

    @property
    def older_run_id(self) -> str:
        return self.run_ids[0]

    @property
    def current_run_id(self) -> str:
        return self.run_ids[1]

    @property
    def current_report_id(self) -> str:
        return self.report_ids[1]

    @property
    def report_id(self) -> str:
        return self.current_report_id


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
    run_ids: tuple[str, ...] = ()
    report_ids: tuple[str, ...] = ()
    proposal_id: str | None = None
    branches_dir: Path | None = None

    @property
    def current_run_id(self) -> str:
        if not self.run_ids:
            raise RuntimeError("Fixture has no generated pipeline run")
        return self.run_ids[-1]

    @property
    def current_report_id(self) -> str:
        if not self.report_ids:
            raise RuntimeError("Fixture has no generated pipeline report")
        return self.report_ids[-1]

    @property
    def report_id(self) -> str:
        return self.current_report_id


def _snapshot(
    snapshot_id: str,
    title: str,
    captured_at: datetime,
    *,
    due_date: datetime,
    state: TaskState = TaskState.in_progress,
    project_id: str = PROJECT_ID,
    include_source: bool = True,
) -> WeeklySnapshot:
    source = []
    if include_source:
        source = [CodeDiffSnippet(
            repository=SOURCE_REPOSITORY,
            commit_sha=SOURCE_COMMIT_SHA,
            file_path=SOURCE_FILE_PATH,
            lines=[1],
            content=SOURCE_CONTENT,
            evidence_scope="full_file",
            is_complete=True,
            total_lines=1,
            truncated=False,
            source_sha256=hashlib.sha256(SOURCE_CONTENT.encode("utf-8")).hexdigest(),
        )]
    tasks = [TaskCard(
        id=TASK_ID if project_id == PROJECT_ID else "stored-task-1",
        title=title,
        state=state,
        list_id="list-in-progress",
        list_name="En curso",
        assignee_ids=["operator-1"],
        due_date=due_date,
        detail=f"Descripción de fixture; api_token={EVIDENCE_SECRET}",
        priority="media",
        observed_fields=["title", "state", "list_name", "assignee_ids", "due_date", "priority"],
    )]
    policies = []
    if project_id == PROJECT_ID:
        policies = [
            QualityRule(
                id="Q1", description="La corrida debe producir un hallazgo verificable.",
                metric_key="total_findings", operator="==", threshold=1,
                source="review", mandatory=True,
            ),
            QualityRule(
                id="Q2", description=(
                    "La cobertura reportada debe alcanzar el mínimo. "
                    "<script>window.__h5_fixture_xss = true</script> "
                    "[enlace](javascript:alert(1)) ![beacon](https://attacker.invalid/pixel)"
                ),
                metric_key="coverage_percentage", operator=">=", threshold=80,
                source="ci", mandatory=True,
            ),
            QualityRule(
                id="Q3", description="No debe quedar un hallazgo crítico abierto.",
                metric_key="open_critical_findings", operator="<=", threshold=0,
                source="review", mandatory=False,
            ),
        ]
    return WeeklySnapshot(
        id=snapshot_id,
        project_id=project_id,
        cutoff_datetime=captured_at,
        captured_at=captured_at,
        task_source_identity=(
            "fixture:board:h5-unified-v1" if project_id == PROJECT_ID
            else f"fixture:board:{project_id.lower()}-unified-v1"
        ),
        tasks_complete=True,
        tasks=tasks,
        code_diffs=source,
        policies=policies,
        is_complete=True,
    )


class _FrozenDateTime(datetime):
    frozen_at: datetime

    @classmethod
    def now(cls, tz=None):
        value = cls.frozen_at
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        if tz is not None:
            value = value.astimezone(tz)
        return cls(value.year, value.month, value.day, value.hour, value.minute,
                   value.second, value.microsecond, tzinfo=value.tzinfo, fold=value.fold)


@contextmanager
def _pipeline_clock(value: datetime, snapshot_id: str):
    from agente_gestion.application import orchestrator as orchestrator_module

    frozen = type(f"FixtureClock_{snapshot_id.replace('-', '_')}", (_FrozenDateTime,), {"frozen_at": value})
    run_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"h5-unified-fixture:{snapshot_id}")
    with patch.object(orchestrator_module, "datetime", frozen), \
            patch.object(orchestrator_module.uuid, "uuid4", return_value=run_uuid):
        yield


def _task_evidence(snapshot: WeeklySnapshot, suffix: str) -> EvidenceItem:
    task = snapshot.tasks[0]
    assert task.due_date is not None
    return EvidenceItem(
        provenance=EvidenceProvenance(
            id=f"evidence-task-{suffix}",
            source=EvidenceSource.trello,
            project_id=snapshot.project_id,
            external_id=task.id,
            timestamp=snapshot.captured_at,
            payload={
                "card_id": task.id,
                "due_date": task.due_date.isoformat(),
                "observed_fields": ["due_date"],
            },
        ),
        category="task",
        summary="Vencimiento observado en la captura completa de tareas.",
    )


def _code_evidence(snapshot: WeeklySnapshot) -> EvidenceItem:
    source = snapshot.code_diffs[0]
    return EvidenceItem(
        provenance=EvidenceProvenance(
            id=source.commit_sha,
            source=EvidenceSource.github,
            project_id=snapshot.project_id,
            repository=source.repository,
            commit_sha=source.commit_sha,
            file_path=source.file_path,
            lines=source.lines,
            timestamp=snapshot.captured_at,
            payload={
                "evidence_scope": source.evidence_scope,
                "is_complete": source.is_complete,
                "truncated": source.truncated,
                "total_lines": source.total_lines,
                "content": source.content,
            },
        ),
        category="diff",
        summary="Archivo fuente completo capturado para revisión técnica.",
    )


def _execute_pipeline(database_path: Path, runs_path: Path, snapshot: WeeklySnapshot):
    storage = SQLiteStorageAdapter(str(database_path))
    pipeline = ProjectOrchestrator(
        sqlite_storage=storage,
        artifact_storage=ArtifactStorageAdapter(base_dir=str(runs_path)),
        elaborador_provider=FakeProvider(model="fixture-monitoring-v1"),
        revisor_provider=FakeProvider(model="fixture-quality-v1"),
        verificador_provider=FakeProvider(model="fixture-verifier-v1"),
        aprobador_provider=FakeProvider(model="fixture-report-v1"),
        source_adapter=None,
        config=OrchestratorConfig(
            project_id=snapshot.project_id,
            runs_dir=str(runs_path),
            db_path=str(database_path),
            is_simulated=True,
            allow_remote_ai=False,
            policy_version="h5-unified-fixture-v1",
        ),
    )
    with _pipeline_clock(snapshot.captured_at, snapshot.id):
        run = pipeline.execute(project_id=snapshot.project_id, snapshot=snapshot)
    if run.status == RunStatus.failed:
        raise RuntimeError(f"Deterministic fixture pipeline failed for {snapshot.id}")
    task_evidence = _task_evidence(snapshot, snapshot.id.removeprefix("snapshot-"))
    storage.save_evidence(run.run_id, [_code_evidence(snapshot), task_evidence])
    return run, task_evidence


def _validate_seed_targets(
    database_path: Path, runs_path: Path, outbox_path: Path,
) -> tuple[Path, Path, Path]:
    database = Path(database_path).expanduser().resolve(strict=False)
    runs = Path(runs_path).expanduser().resolve(strict=False)
    outbox = Path(outbox_path).expanduser().resolve(strict=False)
    targets = (database, runs, outbox)
    for index, target in enumerate(targets):
        for other in targets[index + 1:]:
            if target == other or target in other.parents or other in target.parents:
                raise ValueError("Database, run-artifact and outbox targets must be separate paths.")
    if database.exists():
        raise FileExistsError(f"Refusing to overwrite existing fixture database: {database}")
    for label, directory in (("runs", runs), ("outbox", outbox)):
        if directory.exists() and (not directory.is_dir() or next(directory.iterdir(), None) is not None):
            raise FileExistsError(f"Refusing to reuse non-empty fixture {label} directory: {directory}")
    return database, runs, outbox


def seed_unified_demo_data(
    database_path: Path, runs_path: Path, outbox_path: Path | None = None,
) -> UnifiedFixtureSeed:
    """Create a new isolated unified seed; never overwrite existing DB or artifact directories."""
    database_target = Path(database_path).expanduser().resolve(strict=False)
    default_outbox = database_target.with_name(f"{database_target.stem}-outbox")
    database, runs_dir, outbox_dir = _validate_seed_targets(
        database_target, runs_path, outbox_path or default_outbox,
    )
    database.parent.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)
    outbox_dir.mkdir(parents=True, exist_ok=True)
    storage = SQLiteStorageAdapter(str(database))

    older = _snapshot(
        OLDER_SNAPSHOT_ID,
        "Tarea previa de H5",
        OLDER_CAPTURED_AT,
        due_date=datetime(2026, 1, 20, 12, tzinfo=timezone.utc),
        state=TaskState.pending,
    )
    current = _snapshot(
        CURRENT_SNAPSHOT_ID,
        f"Tarea crítica api_token={EVIDENCE_SECRET}",
        CURRENT_CAPTURED_AT,
        due_date=datetime(2026, 1, 20, 12, tzinfo=timezone.utc),
    )
    older_run, _ = _execute_pipeline(database, runs_dir, older)
    current_run, current_task_evidence = _execute_pipeline(database, runs_dir, current)

    stored_only = _snapshot(
        "snapshot-stored-only",
        "Tarea almacenada sin configuración local",
        datetime(2026, 1, 16, 18, 0, tzinfo=timezone.utc),
        due_date=datetime(2026, 1, 24, 12, tzinfo=timezone.utc),
        project_id=STORED_PROJECT_ID,
    )
    storage.save_snapshot_with_capture_receipt(stored_only)

    captures = storage.list_snapshot_captures(PROJECT_ID)
    current_capture = next(item for item in captures if item.snapshot.id == current.id)
    proposal = ActionProposal(
        proposal_id="proposal-h5-due-date-unified",
        project_id=PROJECT_ID,
        task_id=TASK_ID,
        observed_capture_id=current_capture.capture_id,
        field="due_date",
        current_value=current.tasks[0].due_date,
        proposed_value=current.tasks[0].due_date + timedelta(days=1),
        evidence_ids=[current_task_evidence.provenance.id],
        rationale=(
            "Ajustar el vencimiento con base en la captura técnica actual. "
            "<script>window.__h5_outbox_xss = true</script> "
            "![beacon](https://attacker.invalid/reference)"
        ),
        expected_impact="Mantener el cronograma verificable para la aprobación del gestor.",
    )
    storage.save_action_proposal(proposal, [current_task_evidence])
    technical_review = ProposalAutoReviewService(storage).review_and_record(
        proposal,
        current_capture,
        [current_task_evidence],
        project_captures=captures,
    )
    if technical_review.technical_status != "ready" or technical_review.dry_run.status != "SIMULATED":
        raise RuntimeError("Unified fixture proposal did not pass automatic technical review")

    smtp_config = SMTPConfig()
    notifications = ManagerNotificationService(
        storage,
        HybridNotificationOutboxAdapter(storage, outbox_dir, smtp_config=smtp_config),
        smtp_config=smtp_config,
    )
    notifications.send_project_email(
        PROJECT_ID,
        recipient_email="gestor@example.test",
        idempotency_key="h5-unified-demo-seed",
    )

    return UnifiedFixtureSeed(
        project_id=PROJECT_ID,
        capture_ids=(next(item.capture_id for item in captures if item.snapshot.id == older.id),
                     current_capture.capture_id),
        run_ids=(older_run.run_id, current_run.run_id),
        report_ids=(older_run.report_id, current_run.report_id),
        proposal_id=proposal.proposal_id,
        runs_path=runs_dir,
        outbox_path=outbox_dir,
    )


def validate_unified_demo_database(
    database_path: Path, runs_path: Path | None = None, outbox_path: Path | None = None,
) -> None:
    """Reject legacy DBs and mismatched artifacts before exposing a writable fixture project."""
    database = Path(database_path).expanduser().resolve(strict=False)
    if not database.is_file():
        raise FileNotFoundError("No existe la base fixture regenerada indicada con --fixture-db.")
    runs_dir = Path(runs_path or database.with_name(f"{database.stem}-runs")).resolve(strict=False)
    outbox_dir = Path(outbox_path or database.with_name(f"{database.stem}-outbox")).resolve(strict=False)
    if not runs_dir.is_dir() or not outbox_dir.is_dir():
        raise ValueError("La base fixture requiere sus directorios de corridas y outbox generados.")
    storage = SQLiteStorageAdapter(str(database), read_only=True)
    captures = storage.list_snapshot_captures(PROJECT_ID)
    if len(captures) < 2:
        raise ValueError("La base fixture debe contener al menos dos capturas de H5-TEST.")
    capture_snapshot_ids = {capture.snapshot.id for capture in captures}
    for capture in captures:
        snapshot = capture.snapshot
        if (not snapshot.is_complete or snapshot.tasks_complete is not True
                or not snapshot.task_source_identity or not snapshot.code_diffs):
            raise ValueError("La base fixture contiene una captura de H5-TEST incompleta.")
        for source in snapshot.code_diffs:
            if (source.evidence_scope != "full_file" or not source.is_complete or source.truncated
                    or source.total_lines != len(source.content.splitlines())
                    or source.lines != list(range(1, source.total_lines + 1))):
                raise ValueError("La base fixture contiene código fuente parcial o inválido.")

    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        projects = {row[0] for row in connection.execute(
            "SELECT DISTINCT project_id FROM snapshots"
        )}
        run_rows = connection.execute(
            "SELECT run_id, snapshot_id, status, report_id FROM runs WHERE project_id = ?",
            (PROJECT_ID,),
        ).fetchall()
        review_count = connection.execute(
            "SELECT count(*) FROM proposal_technical_reviews WHERE project_id = ?", (PROJECT_ID,),
        ).fetchone()[0]
    if not projects.issubset({PROJECT_ID, STORED_PROJECT_ID}) or PROJECT_ID not in projects:
        raise ValueError("La base fixture contiene proyectos ajenos al conjunto local regenerado.")
    if len(run_rows) < 2 or any(
        status == RunStatus.failed.value or snapshot_id not in capture_snapshot_ids
        or report_id != f"rep-{run_id}"
        for run_id, snapshot_id, status, report_id in run_rows
    ):
        raise ValueError("La base fixture debe contener corridas válidas, source-complete y generadas por pipeline.")
    expected_run_ids = {row[0] for row in run_rows}
    artifact_run_ids = {item.name for item in runs_dir.iterdir() if item.is_dir()}
    if artifact_run_ids != expected_run_ids or any(
        not (runs_dir / run_id / "report.json").is_file() for run_id in expected_run_ids
    ):
        raise ValueError("Los artefactos de corridas no coinciden con el historial de la base fixture.")
    if not any(path.is_file() for path in outbox_dir.iterdir()):
        raise ValueError("El directorio outbox de la base fixture no contiene un artefacto generado.")
    if not storage.list_action_proposals(PROJECT_ID) or review_count < 1:
        raise ValueError("La base fixture carece de propuesta con revisión técnica automática.")


def run_fixture_analysis(project_id: str, engine_settings, captured_at: datetime = FIXTURE_CLOCK):
    """Execute one new deterministic fake-provider pipeline run against the configured fixture DB."""
    if (project_id != PROJECT_ID or engine_settings is None or not engine_settings.is_fake
            or engine_settings.allow_remote_ai or engine_settings.source_mode != "fixtures"):
        raise RuntimeError("fixture_executor_refused_nonlocal_plan")
    database = Path(engine_settings.db_path)
    runs_dir = Path(engine_settings.runs_dir)
    if not database.is_file() or not runs_dir.is_dir():
        raise RuntimeError("fixture_executor_requires_isolated_seed_paths")
    snapshot = _snapshot(
        "snapshot-analysis-confirmed",
        "Tarea actualizada por análisis local determinista",
        captured_at,
        due_date=captured_at + timedelta(days=5),
    )
    run, _ = _execute_pipeline(database, runs_dir, snapshot)
    return run


def create_web_fixture_db(
    database_path: Path, runs_path: Path | None = None, outbox_path: Path | None = None,
) -> tuple[str, str]:
    """Build fresh complete test data and keep the historical helper's capture-ID result."""
    database = Path(database_path)
    target_runs = runs_path or database.with_name(f"{database.stem}-runs")
    target_outbox = outbox_path or database.with_name(f"{database.stem}-outbox")
    seed = seed_unified_demo_data(database, target_runs, target_outbox)
    return seed.older_capture_id, seed.current_capture_id


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
    """Build an isolated app/client/DB triple, always seeded from the unified pipeline."""
    root = Path(root)
    database_path = root / database_name
    runs_path = root / f"{Path(database_name).stem}-runs"
    outbox_dir = root / f"{Path(database_name).stem}-outbox"
    seed = seed_unified_demo_data(database_path, runs_path, outbox_dir)
    if analysis_enabled and analysis_config_path is None:
        analysis_config_path = root / "analysis-config.yaml"
        analysis_config_path.write_text("is_fake: true\nallow_remote_ai: false\n", encoding="utf-8")
    job_ledger_path = root / job_ledger_name
    branches_dir = root / f"{Path(database_name).stem}-branches"
    settings = ApiSettings(
        db_path=str(database_path),
        runs_dir=str(runs_path),
        outbox_dir=str(outbox_dir),
        projects=(ProjectConfig(id=PROJECT_ID, name="Proyecto de prueba", source_mode="fixtures"),),
        operator_token=operator_token,
        operator_actor="operador-local-de-prueba",
        operator_project_ids=(PROJECT_ID,),
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
        branches_dir=str(branches_dir),
        external_writes_enabled=False,
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
        older_capture_id=seed.older_capture_id,
        current_capture_id=seed.current_capture_id,
        run_ids=seed.run_ids,
        report_ids=seed.report_ids,
        proposal_id=seed.proposal_id,
        branches_dir=branches_dir,
    )
