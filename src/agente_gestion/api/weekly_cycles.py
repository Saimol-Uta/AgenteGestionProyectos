"""Explicit, bounded manual weekly-cycle jobs backed by the existing scheduler."""

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
from threading import RLock
import time
from typing import Callable, Any
from zoneinfo import ZoneInfo

from agente_gestion.adapters.outbox.hybrid_outbox import HybridNotificationOutboxAdapter
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.api.analysis_jobs import AnalysisJobLedger
from agente_gestion.api.control_plane import ControlPlaneRepository
from agente_gestion.api.control_schemas import (
    OutboxReceiptSummary,
    ScheduleCyclePreflight,
    ScheduleCyclePreparation,
    ScheduleView,
    WeeklyCycleJob,
    WeeklyWindowView,
)
from agente_gestion.api.errors import ApiProblem
from agente_gestion.api.settings import ApiSettings
from agente_gestion.application.weekly_scheduler import WeeklySchedulerService
from agente_gestion.domain.notification_outbox import OutboxReceipt
from agente_gestion.domain.weekly_schedule import WeeklySchedule


@dataclass(frozen=True)
class _ScheduleContext:
    schedule: WeeklySchedule | None
    preparation: ScheduleCyclePreflight
    fingerprint: str
    window: object | None
    last_receipt: OutboxReceipt | None
    outbox_history: str


@dataclass(frozen=True)
class _PreparedCycle:
    public: ScheduleCyclePreparation
    schedule: WeeklySchedule
    session_fingerprint: str
    expires_monotonic: float


class _ReadOnlyScheduleOutbox:
    """Injects only the existing stored receipt lookup; never creates files on GET."""

    def __init__(self, storage: SQLiteStorageAdapter):
        self.storage = storage

    def get_receipt_for_window(self, project_id: str, window_id: str):
        try:
            return self.storage.get_outbox_receipt_for_window(project_id, window_id)
        except sqlite3.OperationalError:
            return None


class WeeklyCycleLedger:
    """Small durable ledger for confirmed scheduler jobs, in the existing web-job DB."""

    def __init__(self, settings: ApiSettings):
        validated = AnalysisJobLedger(settings.job_ledger_path, settings.db_path)
        self.path = validated.path

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _read(self):
        if not self.path.is_file():
            return None
        connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='weekly_cycle_jobs'"
        ).fetchone() is None:
            connection.close()
            return None
        return connection

    def create_job(
        self, project_id: str, idempotency_hash: str, request_fingerprint: str,
        preparation_id: str, configuration_fingerprint: str, window_id: str,
        queue_capacity: int,
    ) -> tuple[dict[str, Any], bool]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=2.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout = 2000")
            connection.execute("""CREATE TABLE IF NOT EXISTS weekly_cycle_jobs (
                job_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, outcome TEXT NOT NULL,
                window_id TEXT NOT NULL, delivery_id TEXT, error_code TEXT,
                idempotency_hash TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
                preparation_id TEXT NOT NULL, configuration_fingerprint TEXT NOT NULL,
                UNIQUE(project_id, idempotency_hash)
            )""")
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT * FROM weekly_cycle_jobs WHERE project_id=? AND idempotency_hash=?",
                (project_id, idempotency_hash),
            ).fetchone()
            if previous is not None:
                if previous["request_fingerprint"] != request_fingerprint:
                    connection.rollback()
                    raise ApiProblem(409, "idempotencia_conflictiva", "La clave de idempotencia ya se usó con otra solicitud.")
                connection.commit()
                return dict(previous), True
            active = connection.execute(
                "SELECT 1 FROM weekly_cycle_jobs WHERE project_id=? AND status IN ('queued','running') LIMIT 1",
                (project_id,),
            ).fetchone()
            if active is not None:
                connection.rollback()
                raise ApiProblem(409, "ciclo_semanal_en_curso", "Ya existe un ciclo semanal activo para este proyecto.")
            count = int(connection.execute(
                "SELECT count(*) FROM weekly_cycle_jobs WHERE status IN ('queued','running')"
            ).fetchone()[0])
            if count >= queue_capacity:
                connection.rollback()
                raise ApiProblem(429, "cola_de_ciclos_llena", "La cola de ciclos semanales está llena.")
            job_id = "webcycle-" + secrets.token_hex(12)
            now = self._timestamp()
            connection.execute(
                "INSERT INTO weekly_cycle_jobs VALUES (?, ?, 'queued', ?, ?, 'unknown', ?, NULL, NULL, ?, ?, ?, ?)",
                (job_id, project_id, now, now, window_id, idempotency_hash, request_fingerprint,
                 preparation_id, configuration_fingerprint),
            )
            row = connection.execute("SELECT * FROM weekly_cycle_jobs WHERE job_id=?", (job_id,)).fetchone()
            connection.commit()
            return dict(row), False
        finally:
            connection.close()

    def update(
        self, job_id: str, *, status: str, outcome: str | None = None,
        delivery_id: str | None = None, error_code: str | None = None,
    ) -> None:
        connection = sqlite3.connect(str(self.path), timeout=2.0)
        try:
            connection.execute(
                "UPDATE weekly_cycle_jobs SET status=?, updated_at=?, outcome=COALESCE(?, outcome), "
                "delivery_id=COALESCE(?, delivery_id), error_code=? WHERE job_id=?",
                (status, self._timestamp(), outcome, delivery_id, error_code, job_id),
            )
            connection.commit()
        finally:
            connection.close()

    def recover_orphans(self, active_job_ids: set[str]) -> None:
        if not self.path.is_file():
            return
        connection = sqlite3.connect(str(self.path), timeout=2.0, isolation_level=None)
        try:
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='weekly_cycle_jobs'"
            ).fetchone() is None:
                return
            placeholders = ",".join("?" for _ in active_job_ids)
            active_clause = f" AND job_id NOT IN ({placeholders})" if active_job_ids else ""
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE weekly_cycle_jobs SET status='interrupted', outcome='unknown', "
                "error_code='process_interrupted', updated_at=? "
                "WHERE status IN ('queued','running')" + active_clause,
                (self._timestamp(), *sorted(active_job_ids)),
            )
            connection.commit()
        finally:
            connection.close()

    def get(self, job_id: str) -> dict[str, Any] | None:
        connection = self._read()
        if connection is None:
            return None
        try:
            row = connection.execute("SELECT * FROM weekly_cycle_jobs WHERE job_id=?", (job_id,)).fetchone()
            return dict(row) if row is not None else None
        finally:
            connection.close()

    def get_idempotency(self, project_id: str, idempotency_hash: str) -> dict[str, Any] | None:
        connection = self._read()
        if connection is None:
            return None
        try:
            row = connection.execute(
                "SELECT * FROM weekly_cycle_jobs WHERE project_id=? AND idempotency_hash=?",
                (project_id, idempotency_hash),
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            connection.close()

    def list_for_project(self, project_id: str, page: int, page_size: int):
        connection = self._read()
        if connection is None:
            return [], 0, "unknown"
        try:
            total = int(connection.execute(
                "SELECT count(*) FROM weekly_cycle_jobs WHERE project_id=?", (project_id,),
            ).fetchone()[0])
            rows = connection.execute(
                "SELECT * FROM weekly_cycle_jobs WHERE project_id=? ORDER BY created_at DESC, job_id DESC LIMIT ? OFFSET ?",
                (project_id, page_size, (page - 1) * page_size),
            ).fetchall()
            return [dict(row) for row in rows], total, "available"
        finally:
            connection.close()


class WeeklyCycleService:
    """Session/configuration-bound preparation and a single bounded scheduler worker."""

    def __init__(
        self, settings: ApiSettings, control_plane: ControlPlaneRepository, *,
        clock: Callable[[], float] = time.monotonic,
        schedule_clock: Callable[[], datetime] | None = None,
    ):
        self.settings = settings
        self.control_plane = control_plane
        self.clock = clock
        self.schedule_clock = schedule_clock or (lambda: datetime.now(ZoneInfo(settings.timezone)))
        self.ledger = WeeklyCycleLedger(settings)
        self._prepared: dict[str, _PreparedCycle] = {}
        self._pool: ThreadPoolExecutor | None = None
        self._active_ids: set[str] = set()
        self._futures: dict[str, Future[Any]] = {}
        self._lock = RLock()

    @staticmethod
    def _session_fingerprint(cookie: str | None) -> str:
        return hashlib.sha256((cookie or "").encode("utf-8")).hexdigest()

    def _read_storage(self) -> SQLiteStorageAdapter | None:
        if not self.control_plane.queries.database_exists:
            return None
        try:
            return SQLiteStorageAdapter(self.settings.db_path, read_only=True)
        except (OSError, sqlite3.Error):
            return None

    def _context(self, project_id: str) -> _ScheduleContext:
        schedule = self.settings.weekly_schedule
        schedule_matches = schedule is not None and schedule.project_id == project_id
        if not schedule_matches:
            status = "disabled" if not self.settings.weekly_cycle_enabled else (
                "invalid" if self.settings.weekly_schedule_status == "invalid" else "incomplete"
            )
            preflight = ScheduleCyclePreflight(
                schedule_status=status, window_id=None, has_captures=None,
                outbox_history="unknown", report_binding="unknown", can_run=False,
            )
            fingerprint = self._fingerprint(project_id, None, None, None)
            return _ScheduleContext(None, preflight, fingerprint, None, None, "unknown")

        storage = self._read_storage()
        if storage is None:
            preflight = ScheduleCyclePreflight(
                schedule_status="configured", window_id=None, has_captures=None,
                outbox_history="unknown", report_binding="unknown", can_run=False,
            )
            return _ScheduleContext(schedule, preflight,
                                    self._fingerprint(project_id, schedule, None, None), None, None, "unknown")
        try:
            captures = self.control_plane.queries.captures(project_id)
            latest_capture = captures[-1] if captures else None
            reports, _total, report_availability = self.control_plane.reports(project_id, 1, 1)
            report_record = (self.control_plane.report_record(project_id, reports[0].report_id)
                             if reports else None)
            report_binding = "unknown"
            if report_record is not None:
                run_id = report_record["run_id"]
                valid = self.control_plane._report_run_is_bound(project_id, report_record["report_id"], run_id)
                if valid and latest_capture is not None:
                    with self.control_plane._connect() as connection:
                        run_row = connection.execute(
                            "SELECT snapshot_id FROM runs WHERE run_id=? AND project_id=?",
                            (run_id, project_id),
                        ).fetchone() if connection is not None else None
                    valid = run_row is not None and run_row["snapshot_id"] == latest_capture.snapshot.id
                report_binding = "available" if valid else "mismatch"
            elif report_availability == "available":
                report_binding = "unknown"
            with self.control_plane._connect() as connection:
                tables = self.control_plane._tables(connection)
            outbox_history = "available" if connection is not None and "notification_outbox" in tables else "unknown"
            scheduler = WeeklySchedulerService(storage, outbox=_ReadOnlyScheduleOutbox(storage))
            status = scheduler.check_schedule(project_id, schedule, eval_time=self.schedule_clock())
            has_captures = latest_capture is not None
            can_run = bool(
                self.settings.weekly_cycle_enabled and has_captures
                and outbox_history == "available" and report_binding == "available"
                and status.can_run_scheduled
            )
            preflight = ScheduleCyclePreflight(
                schedule_status="configured", window_id=status.window.window_id,
                has_captures=has_captures, outbox_history=outbox_history,
                report_binding=report_binding, can_run=can_run,
            )
            fingerprint = self._fingerprint(
                project_id, schedule, latest_capture.capture_id if latest_capture else None,
                report_record["report_id"] if report_record else None, status.window.window_id,
                report_version=report_record["report"].report_version if report_record else None,
                report_decision=report_record["decision"] if report_record else None,
            )
            return _ScheduleContext(
                schedule, preflight, fingerprint, status.window, status.last_receipt, outbox_history,
            )
        except (OSError, ValueError, sqlite3.Error, LookupError):
            preflight = ScheduleCyclePreflight(
                schedule_status="configured", window_id=None, has_captures=None,
                outbox_history="unknown", report_binding="unknown", can_run=False,
            )
            return _ScheduleContext(schedule, preflight,
                                    self._fingerprint(project_id, schedule, None, None), None, None, "unknown")

    def _fingerprint(
        self, project_id: str, schedule: WeeklySchedule | None,
        capture_id: str | None, report_id: str | None, window_id: str | None = None,
        *, report_version: int | None = None, report_decision: str | None = None,
    ) -> str:
        raw = json.dumps({
            "project_id": project_id,
            "schedule": schedule.model_dump(mode="json") if schedule else None,
            "enabled": self.settings.weekly_cycle_enabled,
            "db_path": str(Path(self.settings.db_path).absolute()),
            "outbox_dir": str(Path(self.settings.outbox_dir).absolute()),
            "due_soon_days": self.settings.due_soon_days,
            "capture_id": capture_id,
            "report_id": report_id,
            "report_version": report_version,
            "report_decision": report_decision,
            "window_id": window_id,
            "remote_consent": False,
            "channel": "sqlite",
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def schedule_view(self, project_id: str) -> ScheduleView:
        context = self._context(project_id)
        schedule_view = self.control_plane.schedule_settings_view(project_id)
        if context.schedule is None:
            return ScheduleView(
                project_id=project_id, availability="unknown", enabled=self.settings.weekly_cycle_enabled,
                schedule=None, planned_window=None, is_due=None, is_dispatched=None,
                outbox_history="unknown", can_run_manually=False, last_receipt=None,
            )
        window_view = None
        if context.window is not None:
            window = context.window
            window_view = WeeklyWindowView(
                window_id=window.window_id,
                window_start_local=window.window_start_local,
                window_start_utc=window.window_start_utc,
                window_end_local=window.window_end_local,
                window_end_utc=window.window_end_utc,
                cutoff_local=window.cutoff_local,
                cutoff_utc=window.cutoff_utc,
                next_boundary_local=window.next_boundary_local,
                next_boundary_utc=window.next_boundary_utc,
            )
        is_dispatched = (
            None if context.outbox_history == "unknown" else context.last_receipt is not None
        )
        is_due = (
            self._is_due(project_id, context.schedule, context.window)
            if context.window is not None else None
        )
        receipt_view = None
        if context.last_receipt is not None:
            rows, _total, _availability = self.control_plane._outbox_rows(project_id, page=1, page_size=10000)
            match = next(((receipt, message) for receipt, message in rows
                          if receipt.delivery_id == context.last_receipt.delivery_id), None)
            if match is not None:
                receipt_view = self.control_plane._outbox_view(*match)
        return ScheduleView(
            project_id=project_id, availability="configured", enabled=self.settings.weekly_cycle_enabled,
            schedule=schedule_view, planned_window=window_view, is_due=is_due,
            is_dispatched=is_dispatched, outbox_history=context.outbox_history,
            can_run_manually=context.preparation.can_run,
            last_receipt=receipt_view,
        )

    def _is_due(self, project_id: str, schedule: WeeklySchedule, window) -> bool | None:
        storage = self._read_storage()
        if storage is None:
            return None
        try:
            status = WeeklySchedulerService(storage, outbox=_ReadOnlyScheduleOutbox(storage)).check_schedule(
                project_id, schedule, eval_time=self.schedule_clock(),
            )
            return status.is_due
        except (OSError, ValueError, sqlite3.Error):
            return None

    def prepare(self, project_id: str, session_cookie: str | None) -> ScheduleCyclePreparation:
        with self._lock:
            self.ledger.recover_orphans(set(self._active_ids))
        context = self._context(project_id)
        now = self.clock()
        zone = ZoneInfo(self.settings.timezone)
        preparation_id = secrets.token_urlsafe(24)
        status = context.preparation.schedule_status
        eligible = bool(
            self.settings.weekly_cycle_enabled and context.schedule is not None
            and context.preparation.can_run
        )
        public = ScheduleCyclePreparation(
            preparation_id=preparation_id,
            project_id=project_id,
            expires_at=datetime.now(zone) + timedelta(seconds=self.settings.analysis_prepare_ttl_seconds),
            configuration_fingerprint=context.fingerprint,
            eligible=eligible,
            capabilities=["weekly_cycle:prepare", "weekly_cycle:confirm"],
            preflight=context.preparation,
        )
        if context.schedule is not None:
            with self._lock:
                self._prepared[preparation_id] = _PreparedCycle(
                    public=public, schedule=context.schedule,
                    session_fingerprint=self._session_fingerprint(session_cookie),
                    expires_monotonic=now + self.settings.analysis_prepare_ttl_seconds,
                )
        with self._lock:
            for key, item in tuple(self._prepared.items()):
                if item.expires_monotonic <= now:
                    self._prepared.pop(key, None)
        return public

    def confirm(
        self, project_id: str, preparation_id: str, configuration_fingerprint: str,
        session_cookie: str | None, actor_id: str, idempotency_key: str,
    ) -> tuple[WeeklyCycleJob, bool]:
        session_fingerprint = self._session_fingerprint(session_cookie)
        idempotency_hash = hashlib.sha256((session_fingerprint + "|" + idempotency_key).encode()).hexdigest()
        # Same-key replay is checked before preparation expiry, but remains session-bound.
        previous = self.ledger.get_idempotency(project_id, idempotency_hash)
        request_fingerprint = hashlib.sha256(json.dumps({
            "project_id": project_id, "configuration_fingerprint": configuration_fingerprint,
            "remote_consent": False, "session_fingerprint": session_fingerprint,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if previous is not None:
            if previous["request_fingerprint"] != request_fingerprint:
                raise ApiProblem(409, "idempotencia_conflictiva", "La clave de idempotencia ya se usó con otra solicitud.")
            return self._job_view(previous), True
        with self._lock:
            prepared = self._prepared.get(preparation_id)
        if prepared is None or prepared.public.project_id != project_id:
            raise ApiProblem(409, "preparacion_no_disponible", "La preparación expiró o no está disponible.")
        if prepared.expires_monotonic <= self.clock():
            with self._lock:
                self._prepared.pop(preparation_id, None)
            raise ApiProblem(409, "preparacion_expirada", "La preparación expiró.")
        if prepared.session_fingerprint != session_fingerprint:
            raise ApiProblem(403, "preparacion_sesion_distinta", "La preparación pertenece a otra sesión local.")
        if prepared.public.configuration_fingerprint != configuration_fingerprint:
            raise ApiProblem(409, "configuracion_cambiada", "La programación o los datos vinculados cambiaron.")
        context = self._context(project_id)
        if context.fingerprint != configuration_fingerprint:
            raise ApiProblem(409, "configuracion_cambiada", "La programación o los datos vinculados cambiaron.")
        if not self.settings.weekly_cycle_enabled:
            raise ApiProblem(503, "ciclo_semanal_no_habilitado", "El servidor no habilitó el ciclo semanal manual.")
        if not prepared.public.eligible or not context.preparation.can_run:
            raise ApiProblem(409, "ciclo_semanal_no_preparado", "El ciclo semanal no está listo para ejecutarse.")
        if context.schedule is None or context.window is None:
            raise ApiProblem(409, "programacion_no_disponible", "No hay una programación semanal válida.")
        with self._lock:
            row, reused = self.ledger.create_job(
                project_id=project_id,
                idempotency_hash=idempotency_hash,
                request_fingerprint=request_fingerprint,
                preparation_id=preparation_id,
                configuration_fingerprint=context.fingerprint,
                window_id=context.window.window_id,
                queue_capacity=self.settings.analysis_queue_capacity,
            )
            if not reused:
                job_id = row["job_id"]
                self._submit(
                    job_id, project_id, context.schedule, actor_id, session_fingerprint,
                    idempotency_key, request_fingerprint, context.fingerprint,
                )
                row = self.ledger.get(job_id) or row
        return self._job_view(row), reused

    def _job_view(self, row: dict[str, Any]) -> WeeklyCycleJob:
        job_id = str(row.get("job_id", ""))
        project_id = str(row.get("project_id", ""))
        if not re.fullmatch(r"webcycle-[a-f0-9]{24}", job_id):
            raise ApiProblem(500, "registro_de_ciclo_invalido", "No se pudo validar el ciclo semanal almacenado.")
        status = str(row.get("status", ""))
        if status not in {"queued", "running", "completed", "failed", "interrupted"}:
            raise ApiProblem(500, "registro_de_ciclo_invalido", "No se pudo validar el ciclo semanal almacenado.")
        with self._lock:
            active = job_id in self._active_ids
        if status in {"queued", "running"} and not active:
            status, error_code = "interrupted", "process_interrupted"
        else:
            error_code = row.get("error_code")
        try:
            created = datetime.fromisoformat(str(row["created_at"]))
            updated = datetime.fromisoformat(str(row["updated_at"]))
        except (KeyError, ValueError):
            raise ApiProblem(500, "registro_de_ciclo_invalido", "No se pudo validar el ciclo semanal almacenado.") from None
        outcome = str(row.get("outcome", "unknown"))
        if outcome not in {"EXECUTED", "SKIPPED_NOT_DUE", "SKIPPED_ALREADY_DISPATCHED", "FAILED", "unknown"}:
            outcome = "unknown"
        delivery_id = row.get("delivery_id")
        if delivery_id is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", str(delivery_id)):
            delivery_id = None
        error = error_code if error_code in {
            "process_interrupted", "configuration_changed", "scheduler_failed", "worker_unavailable",
            "invalid_scheduler_result",
        } else ("cycle_error" if error_code else None)
        return WeeklyCycleJob(
            job_id=job_id, project_id=project_id, status=status,
            created_at=created.astimezone(ZoneInfo(self.settings.timezone)) if created.tzinfo else created.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(self.settings.timezone)),
            updated_at=updated.astimezone(ZoneInfo(self.settings.timezone)) if updated.tzinfo else updated.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(self.settings.timezone)),
            outcome=outcome, window_id=row.get("window_id"), delivery_id=delivery_id,
            error_code=error, remote_consent=False,
        )

    def _submit(
        self, job_id: str, project_id: str, schedule: WeeklySchedule,
        actor_id: str, session_fingerprint: str, idempotency_key: str,
        request_fingerprint: str, configuration_fingerprint: str,
    ) -> None:
        with self._lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="h5-weekly-cycle")
            self._active_ids.add(job_id)
            try:
                future = self._pool.submit(
                    self._run_job, job_id, project_id, schedule, actor_id, session_fingerprint,
                    idempotency_key, request_fingerprint, configuration_fingerprint,
                )
            except RuntimeError:
                self._active_ids.discard(job_id)
                self.ledger.update(job_id, status="failed", outcome="FAILED", error_code="worker_unavailable")
                return
            self._futures[job_id] = future
            if future.done():
                self._futures.pop(job_id, None)

    def _run_job(
        self, job_id: str, project_id: str, schedule: WeeklySchedule,
        actor_id: str, session_fingerprint: str, idempotency_key: str,
        request_fingerprint: str, configuration_fingerprint: str,
    ) -> None:
        try:
            self.ledger.update(job_id, status="running")
            context = self._context(project_id)
            if context.fingerprint != configuration_fingerprint:
                self.ledger.update(job_id, status="failed", outcome="FAILED", error_code="configuration_changed")
                return
            if context.schedule is None or not context.preparation.can_run:
                self.ledger.update(job_id, status="completed", outcome="SKIPPED_NOT_DUE")
                return
            storage = SQLiteStorageAdapter(self.settings.db_path)
            # This existing confirmation explicitly authorizes a local SQLite-only cycle.
            # Manager email is sent through its separate protected confirmation route.
            scheduler = WeeklySchedulerService(
                storage,
                outbox=HybridNotificationOutboxAdapter(storage, Path(self.settings.outbox_dir)),
            )
            result = scheduler.run_scheduled_cycle(
                project_id, schedule, eval_time=self.schedule_clock(), force=False,
                channel="sqlite", due_soon_days=self.settings.due_soon_days,
            )
            if result.status not in {"EXECUTED", "SKIPPED_NOT_DUE", "SKIPPED_ALREADY_DISPATCHED", "FAILED"}:
                self.ledger.update(job_id, status="failed", outcome="FAILED", error_code="invalid_scheduler_result")
                return
            delivery_id = result.receipt.delivery_id if result.receipt is not None else None
            self.ledger.update(
                job_id, status="failed" if result.status == "FAILED" else "completed",
                outcome=result.status, delivery_id=delivery_id,
                error_code="scheduler_failed" if result.status == "FAILED" else None,
            )
            self.control_plane._ensure_web_audit(
                storage, project_id=project_id, actor_id=actor_id,
                session_cookie=session_fingerprint,
                permission="web:weekly_cycle", resource_id=result.window_id,
                idempotency_key=idempotency_key, request_fingerprint=request_fingerprint,
            )
        except Exception:
            try:
                self.ledger.update(job_id, status="failed", outcome="FAILED", error_code="scheduler_failed")
            except Exception:
                pass
        finally:
            with self._lock:
                self._active_ids.discard(job_id)
                self._futures.pop(job_id, None)

    def get_job(self, job_id: str) -> WeeklyCycleJob:
        row = self.ledger.get(job_id)
        if row is None:
            raise LookupError("weekly_cycle_job_not_found")
        return self._job_view(row)

    def list_jobs(self, project_id: str, page: int, page_size: int):
        rows, total, availability = self.ledger.list_for_project(project_id, page, page_size)
        return [self._job_view(row) for row in rows], total, availability

    def shutdown(self) -> None:
        with self._lock:
            pool = self._pool
            self._pool = None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=False)
