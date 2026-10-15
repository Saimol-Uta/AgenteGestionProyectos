"""Read-only project/task projections backed by the existing engine and SQLite adapter."""

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Iterator
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.application.tasks_query import TaskDetailView, TaskFilter, TaskPlanSummary, query_tasks_view
from agente_gestion.domain.code import CodeDiffSnippet
from agente_gestion.domain.code_action import CodeSourceSnapshot
from agente_gestion.domain.evidence import EvidenceItem
from agente_gestion.domain.findings import CandidateFinding
from agente_gestion.domain.policies import RuleEvaluationStatus
from agente_gestion.domain.report import FinalReport
from agente_gestion.domain.run import RunStatus, StageStatus
from agente_gestion.domain.snapshot import SnapshotCapture, WeeklySnapshot
from agente_gestion.domain.task_comparison import TaskChange, TaskSnapshotComparison, compare_task_snapshots
from agente_gestion.domain.tasks import TaskState, normalize_datetime_to_tz

from agente_gestion.api.schemas import (
    CIRunView,
    CaptureHistoryView,
    CaptureProgressPoint,
    CodeExcerpt,
    EvidenceCollection,
    EvidenceProvenanceView,
    EvidenceSummary,
    EvidenceView,
    ExecutiveReportSummary,
    ExecutiveSummary,
    FindingCounts,
    FindingDetail,
    FindingSummary,
    ProjectView,
    PoliciesSummary,
    PolicyResult,
    QualitySummary,
    RunDetail,
    RunProgressPoint,
    RunSeries,
    RunStages,
    RunSummary,
    StageView,
    TaskDetailResponse,
    TaskHistoryEntry,
    VerificationView,
    ProviderAttempt,
)
from agente_gestion.api.security import (
    safe_commit_sha,
    safe_identifier,
    safe_lines,
    safe_relative_path,
    safe_repository,
    sanitize_text,
)
from agente_gestion.api.settings import ApiSettings, ProjectConfig


class DataIntegrityError(RuntimeError):
    """A stored source record is invalid for a safe browser-facing projection."""


def _safe_finding_branch_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 1000 or any(ord(ch) < 32 for ch in value):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        return None
    if parsed.scheme == "sandbox" and parsed.netloc == "branches":
        parts = parsed.path.lstrip("/").split("/")
        if parts and all(part not in {"", ".", ".."} and "\\" not in part for part in parts):
            return value
        return None
    if parsed.scheme == "https" and parsed.netloc == "github.com":
        parts = parsed.path.strip("/").split("/")
        if (len(parts) >= 4 and safe_repository("/".join(parts[:2]))
                and parts[2] == "tree" and all(part and part not in {".", ".."} for part in parts[3:])):
            return value
    return None


class SQLiteQueryRepository:
    """Queries captured data using adapter read-only mode; never migrates or writes."""

    def __init__(self, settings: ApiSettings):
        self.settings = settings
        self.db_path = Path(settings.db_path)

    @property
    def database_exists(self) -> bool:
        try:
            return self.db_path.is_file()
        except OSError:
            return False

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection | None]:
        if not self.database_exists:
            yield None
            return
        connection = None
        try:
            uri = self.db_path.resolve().as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=1.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            yield connection
        except sqlite3.OperationalError:
            raise
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _tables(connection: sqlite3.Connection) -> set[str]:
        return {str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}

    def _adapter(self) -> SQLiteStorageAdapter | None:
        if not self.database_exists:
            return None
        return SQLiteStorageAdapter(str(self.db_path), read_only=True)

    def project_ids(self) -> set[str]:
        ids = {project.id for project in self.settings.projects}
        with self._connect() as connection:
            if connection is None:
                return ids
            tables = self._tables(connection)
            for table in ("snapshot_captures", "snapshots", "runs", "reports", "action_proposals"):
                if table not in tables:
                    continue
                columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
                if "project_id" not in columns:
                    continue
                for row in connection.execute(f"SELECT project_id FROM {table} WHERE project_id IS NOT NULL"):
                    value = safe_identifier(row[0])
                    if value:
                        ids.add(value)
        return ids

    def _legacy_captures(self, project_id: str) -> list[SnapshotCapture]:
        with self._connect() as connection:
            if connection is None or "snapshots" not in self._tables(connection):
                return []
            rows = connection.execute(
                "SELECT data_json, snapshot_id, project_id FROM snapshots WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        captures: list[SnapshotCapture] = []
        for row in rows:
            raw = row["data_json"]
            try:
                metadata = SQLiteStorageAdapter._capture_metadata(raw)
                snapshot = WeeklySnapshot.model_validate_json(raw)
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                raise DataIntegrityError("Stored snapshot data is invalid") from None
            if (metadata[1] != row["snapshot_id"] or metadata[2] != project_id
                    or snapshot.project_id != project_id):
                raise DataIntegrityError("Stored snapshot metadata is inconsistent")
            captures.append(SnapshotCapture(
                capture_id=metadata[0], content_hash=metadata[0], snapshot=snapshot,
            ))
        timezone = ZoneInfo(self.settings.timezone)
        return sorted(captures, key=lambda item: (
            normalize_datetime_to_tz(item.snapshot.captured_at, timezone), item.capture_id,
        ))

    def captures(self, project_id: str) -> list[SnapshotCapture]:
        adapter = self._adapter()
        if adapter is None:
            return []
        try:
            captures = adapter.list_snapshot_captures(project_id)
        except NotImplementedError:
            captures = self._legacy_captures(project_id)
        for capture in captures:
            self._validate_snapshot(capture.snapshot)
        return captures

    @staticmethod
    def _validate_snapshot(snapshot: WeeklySnapshot) -> None:
        if (not safe_identifier(snapshot.id) or not safe_identifier(snapshot.project_id)
                or any(not safe_identifier(task.id) for task in snapshot.tasks)):
            raise DataIntegrityError("Stored project or task identifiers are unsafe")

    def project_config(self, project_id: str) -> ProjectConfig | None:
        return next((project for project in self.settings.projects if project.id == project_id), None)

    def latest_run(self, project_id: str) -> tuple[str | None, str | None]:
        with self._connect() as connection:
            if connection is None or "runs" not in self._tables(connection):
                return None, None
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(runs)")}
            if not {"project_id", "run_id", "status"}.issubset(columns):
                return None, None
            ordering = [name for name in ("updated_at", "created_at", "run_id") if name in columns]
            order_sql = ", ".join(f"{name} DESC" for name in ordering) or "run_id DESC"
            row = connection.execute(
                f"SELECT run_id, status FROM runs WHERE project_id = ? ORDER BY {order_sql} LIMIT 1",
                (project_id,),
            ).fetchone()
            if not row:
                return None, None
            run_id = safe_identifier(row["run_id"])
            status = sanitize_text(
                row["status"], secrets_to_redact=self._configured_secrets(), limit=80,
            ) if row["status"] is not None else None
            return run_id, status

    def project_views(self, page: int, page_size: int) -> tuple[list[ProjectView], int]:
        project_ids = sorted(self.project_ids(), key=str.casefold)
        total = len(project_ids)
        selected = project_ids[(page - 1) * page_size:page * page_size]
        views: list[ProjectView] = []
        for project_id in selected:
            config = self.project_config(project_id)
            captures = self.captures(project_id)
            latest = captures[-1] if captures else None
            latest_run_id, latest_run_status = self.latest_run(project_id)
            summary = self.summary(latest) if latest else None
            views.append(ProjectView(
                id=project_id,
                name=sanitize_text(
                    config.name, secrets_to_redact=self._configured_secrets(), limit=160,
                ) if config else project_id,
                source_mode=config.source_mode if config else "stored",
                latest_capture_at=self._normalize_datetime(latest.snapshot.captured_at) if latest else None,
                latest_run_id=latest_run_id,
                latest_run_status=latest_run_status,
                summary=summary,
            ))
        return views, total

    def _ensure_project(self, project_id: str) -> None:
        if not safe_identifier(project_id):
            raise ValueError("invalid_project_id")
        if project_id not in self.project_ids():
            raise LookupError("project_not_found")

    def require_project(self, project_id: str) -> ProjectView:
        self._ensure_project(project_id)
        config = self.project_config(project_id)
        captures = self.captures(project_id)
        latest = captures[-1] if captures else None
        latest_run_id, latest_run_status = self.latest_run(project_id)
        return ProjectView(
            id=project_id,
            name=sanitize_text(
                config.name, secrets_to_redact=self._configured_secrets(), limit=160,
            ) if config else project_id,
            source_mode=config.source_mode if config else "stored",
            latest_capture_at=self._normalize_datetime(latest.snapshot.captured_at) if latest else None,
            latest_run_id=latest_run_id,
            latest_run_status=latest_run_status,
            summary=self.summary(latest) if latest else None,
        )

    def captures_for_project(self, project_id: str) -> list[SnapshotCapture]:
        self._ensure_project(project_id)
        return self.captures(project_id)

    def selected_capture(self, project_id: str, capture_id: str | None = None) -> SnapshotCapture:
        captures = self.captures_for_project(project_id)
        if not captures:
            raise LookupError("capture_not_found")
        if capture_id is None:
            return captures[-1]
        for capture in captures:
            if capture.capture_id == capture_id:
                return capture
        raise LookupError("capture_not_found")

    def history_item(self, capture: SnapshotCapture) -> CaptureHistoryView:
        return CaptureHistoryView(
            capture_id=capture.capture_id,
            captured_at=self._normalize_datetime(capture.snapshot.captured_at),
            summary=self.summary(capture),
        )

    def summary(self, capture: SnapshotCapture) -> TaskPlanSummary:
        summary = query_tasks_view(
            capture.snapshot,
            capture.capture_id,
            filters=TaskFilter(due_soon_days=self.settings.due_soon_days),
            timezone_name=self.settings.timezone,
        )[0]
        return summary.model_copy(update={
            "captured_at": self._normalize_datetime(summary.captured_at),
            "cutoff_datetime": self._normalize_datetime(summary.cutoff_datetime),
        })

    def tasks(
        self,
        project_id: str,
        *,
        capture_id: str | None,
        q: str | None,
        state: TaskState | None,
        assignee_id: str | None,
        list_name: str | None,
        critical_only: bool,
        overdue_only: bool,
        due_soon_only: bool,
        sort: str,
        direction: str,
        page: int,
        page_size: int,
    ) -> tuple[list[TaskDetailView], int]:
        if overdue_only and due_soon_only:
            raise ValueError("incompatible_due_filters")
        if capture_id is None:
            self._ensure_project(project_id)
            available_captures = self.captures(project_id)
            if not available_captures:
                return [], 0
            capture = available_captures[-1]
        else:
            capture = self.selected_capture(project_id, capture_id)
        _, views = query_tasks_view(
            capture.snapshot,
            capture.capture_id,
            filters=TaskFilter(
                state=state,
                critical_only=critical_only,
                assignee_id=assignee_id,
                list_name=list_name,
                due_soon_days=self.settings.due_soon_days,
            ),
            timezone_name=self.settings.timezone,
        )
        text_query = q.casefold().strip() if q else ""
        rows = []
        for view in views:
            if overdue_only and not view.is_overdue:
                continue
            if due_soon_only and not view.is_due_soon:
                continue
            searchable = " ".join((
                view.id, view.title, view.detail or "", view.list_name or "",
                " ".join(view.labels), " ".join(view.assignee_ids),
            )).casefold()
            if text_query and text_query not in searchable:
                continue
            rows.append(view)
        if sort == "title":
            rows.sort(key=lambda item: (item.title.casefold(), item.id), reverse=direction == "desc")
        else:
            dated = [item for item in rows if item.due_date is not None]
            undated = [item for item in rows if item.due_date is None]
            task_timezone = ZoneInfo(self.settings.timezone)
            dated.sort(
                key=lambda item: (normalize_datetime_to_tz(item.due_date, task_timezone), item.id),
                reverse=direction == "desc",
            )
            rows = dated + undated
        total = len(rows)
        selected = rows[(page - 1) * page_size:page * page_size]
        return [self._sanitize_task_view(view) for view in selected], total

    def task_detail(self, project_id: str, task_id: str, capture_id: str | None) -> TaskDetailResponse:
        capture = self.selected_capture(project_id, capture_id)
        _, views = query_tasks_view(
            capture.snapshot,
            capture.capture_id,
            filters=TaskFilter(due_soon_days=self.settings.due_soon_days),
            timezone_name=self.settings.timezone,
        )
        view = next((item for item in views if item.id == task_id), None)
        if view is None:
            raise LookupError("task_not_found")
        return self._task_detail(project_id, capture, view)

    def _sanitize_task_view(self, view: TaskDetailView) -> TaskDetailView:
        fields = view.model_dump(mode="python")
        secrets_to_redact = self._configured_secrets()
        for name in ("title", "list_id", "list_name", "priority", "detail"):
            if fields.get(name) is not None:
                fields[name] = sanitize_text(
                    fields[name], secrets_to_redact=secrets_to_redact, limit=2000,
                )
        for name in ("labels", "assignee_ids", "observed_fields", "explicit_dependencies", "dependency_impacts"):
            if fields.get(name) is not None:
                fields[name] = [
                    sanitize_text(value, secrets_to_redact=secrets_to_redact, limit=500) or ""
                    for value in fields[name]
                ]
        fields["list_id"] = safe_identifier(fields["list_id"])
        fields["assignee_ids"] = [
            identifier for value in fields["assignee_ids"]
            if (identifier := safe_identifier(value)) is not None
        ]
        for name in ("start_date", "due_date", "last_activity_at"):
            if fields[name] is not None:
                fields[name] = self._normalize_datetime(fields[name])
        return TaskDetailView(**fields)

    def _task_detail(
        self, project_id: str, capture: SnapshotCapture, view: TaskDetailView,
    ) -> TaskDetailResponse:
        sanitized = self._sanitize_task_view(view)
        return TaskDetailResponse(
            **sanitized.model_dump(mode="python"),
            evidence=self.task_evidence(project_id, capture.snapshot.id, view.id),
            history=self.task_history(project_id, capture, view.id),
        )

    def task_evidence(self, project_id: str, snapshot_id: str, task_id: str) -> list[EvidenceView]:
        run_ids: list[str] = []
        with self._connect() as connection:
            if connection is None:
                return []
            tables = self._tables(connection)
            if "runs" not in tables or "evidence" not in tables:
                return []
            rows = connection.execute(
                "SELECT run_id FROM runs WHERE project_id = ? AND snapshot_id = ? ORDER BY run_id LIMIT 100",
                (project_id, snapshot_id),
            )
            run_ids = [value for row in rows if (value := safe_identifier(row["run_id"]))]
        adapter = self._adapter()
        if adapter is None:
            return []
        results: list[EvidenceView] = []
        for run_id in run_ids:
            for raw_item in adapter.get_evidence(run_id):
                try:
                    item = raw_item if isinstance(raw_item, EvidenceItem) else EvidenceItem.model_validate(raw_item)
                except (ValueError, TypeError):
                    continue
                provenance = item.provenance
                payload = provenance.payload if isinstance(provenance.payload, dict) else {}
                related = provenance.external_id == task_id or any(
                    payload.get(key) == task_id for key in ("card_id", "task_id")
                )
                evidence_id = safe_identifier(provenance.id)
                if (not related or item.category != "task" or provenance.project_id != project_id
                        or evidence_id is None or safe_identifier(provenance.project_id) is None):
                    continue
                view_provenance = EvidenceProvenanceView(
                    id=evidence_id,
                    source=provenance.source,
                    project_id=project_id,
                    repository=safe_repository(provenance.repository),
                    commit_sha=safe_commit_sha(provenance.commit_sha),
                    file_path=safe_relative_path(provenance.file_path),
                    diff_side=provenance.diff_side,
                    lines=safe_lines(provenance.lines),
                    external_id=safe_identifier(provenance.external_id),
                    timestamp=self._normalize_datetime(provenance.timestamp),
                )
                results.append(EvidenceView(
                    provenance=view_provenance,
                    category=sanitize_text(item.category, limit=80) or "evidencia",
                    summary=sanitize_text(
                        item.summary, secrets_to_redact=self._configured_secrets(), limit=2000,
                    ) or "",
                ))
                if len(results) >= 100:
                    return results
        return results

    def task_history(self, project_id: str, current: SnapshotCapture, task_id: str) -> list[TaskHistoryEntry]:
        captures = self.captures(project_id)
        index = next((pos for pos, capture in enumerate(captures)
                      if capture.capture_id == current.capture_id), None)
        if index is None or index == 0:
            return []
        history: list[TaskHistoryEntry] = []
        for previous, observed in zip(captures[:index], captures[1:index + 1]):
            comparison = compare_task_snapshots(previous.snapshot, observed.snapshot)
            for change in comparison.changes:
                if change.task_id != task_id:
                    continue
                history.append(TaskHistoryEntry(
                    **self._sanitize_change(change).model_dump(mode="python"),
                    observed_at=self._normalize_datetime(observed.snapshot.captured_at),
                    capture_id=observed.capture_id,
                    previous_capture_id=previous.capture_id,
                    current_capture_id=observed.capture_id,
                ))
        return history

    def compare(self, project_id: str, before: str, after: str) -> TaskSnapshotComparison:
        previous = self.selected_capture(project_id, before)
        current = self.selected_capture(project_id, after)
        try:
            comparison = compare_task_snapshots(previous.snapshot, current.snapshot)
        except ValueError:
            raise LookupError("comparison_unavailable") from None
        return comparison.model_copy(update={
            "previous_captured_at": self._normalize_datetime(comparison.previous_captured_at),
            "current_captured_at": self._normalize_datetime(comparison.current_captured_at),
            "changes": [self._sanitize_change(change) for change in comparison.changes],
        })

    def _sanitize_change(self, change: TaskChange) -> TaskChange:
        values = change.model_dump(mode="python")
        secrets_to_redact = self._configured_secrets()
        for name in ("before", "after"):
            value = values[name]
            if isinstance(value, str):
                values[name] = sanitize_text(value, secrets_to_redact=secrets_to_redact, limit=2000)
            elif isinstance(value, list):
                values[name] = [
                    sanitize_text(item, secrets_to_redact=secrets_to_redact, limit=500) or ""
                    for item in value
                ]
            elif isinstance(value, datetime):
                values[name] = self._normalize_datetime(value)
        values["previous_evidence_ids"] = [
            item for value in values["previous_evidence_ids"]
            if (item := safe_identifier(value)) is not None
        ]
        values["current_evidence_ids"] = [
            item for value in values["current_evidence_ids"]
            if (item := safe_identifier(value)) is not None
        ]
        return TaskChange(**values)

    def _normalize_datetime(self, value: datetime) -> datetime:
        return normalize_datetime_to_tz(value, ZoneInfo(self.settings.timezone))

    def _configured_secrets(self) -> tuple[str, ...]:
        values = []
        for name in ("operator_token", "github_token", "trello_api_key", "trello_token", "smtp_user", "smtp_password"):
            secret = getattr(self.settings, name, None)
            if secret is not None and hasattr(secret, "get_secret_value"):
                values.append(secret.get_secret_value())
        return tuple(values)

    _RUN_STAGES = ("source_collection", "monitoring", "quality", "verifier", "report")
    _RUN_STATUSES = {status.value for status in RunStatus}
    _STAGE_STATUSES = {status.value for status in StageStatus}

    @staticmethod
    def _safe_duration(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            return None
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None

    @staticmethod
    def _safe_model_name(value: object) -> str:
        if (isinstance(value, str) and len(value) <= 80 and "://" not in value
                and not re.search(r"(?i)(?:api[_-]?key|token|secret|password|bearer|credential)", value)
                and sanitize_text(value, limit=80) == value
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}", value)):
            return value
        return "unknown"

    @staticmethod
    def _safe_nonnegative_int(value: object) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    @staticmethod
    def _safe_percentage(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        return number if math.isfinite(number) and 0 <= number <= 100 else None

    def _run_rows(self, project_id: str) -> list[dict[str, object]]:
        self._ensure_project(project_id)
        with self._connect() as connection:
            if connection is None or "runs" not in self._tables(connection):
                return []
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(runs)")}
            if not {"run_id", "project_id", "status", "created_at", "updated_at", "snapshot_id"}.issubset(columns):
                return []
            rows = connection.execute(
                "SELECT * FROM runs WHERE project_id = ? ORDER BY created_at DESC, run_id DESC",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _run_row(self, run_id: str) -> dict[str, object]:
        if not safe_identifier(run_id):
            raise LookupError("run_not_found")
        with self._connect() as connection:
            if connection is None or "runs" not in self._tables(connection):
                raise LookupError("run_not_found")
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise LookupError("run_not_found")
        record = dict(row)
        if not safe_identifier(record.get("project_id")):
            raise DataIntegrityError("Stored run project identifier is unsafe")
        return record

    def _run_summary(self, row: dict[str, object]) -> RunSummary:
        run_id = safe_identifier(row.get("run_id"))
        project_id = safe_identifier(row.get("project_id"))
        snapshot_id = safe_identifier(row.get("snapshot_id"))
        if run_id is None or project_id is None or snapshot_id is None:
            raise DataIntegrityError("Stored run identifiers are unsafe")
        raw_status = str(row.get("status") or "")
        status = raw_status if raw_status in self._RUN_STATUSES else "unknown"
        try:
            created_at = datetime.fromisoformat(str(row["created_at"]))
            updated_at = datetime.fromisoformat(str(row["updated_at"]))
        except (KeyError, TypeError, ValueError):
            raise DataIntegrityError("Stored run timestamps are invalid") from None
        raw_simulated = row.get("is_simulated")
        if isinstance(raw_simulated, bool):
            is_simulated = raw_simulated
        elif isinstance(raw_simulated, int) and raw_simulated in {0, 1}:
            is_simulated = raw_simulated == 1
        else:
            is_simulated = None
        report_id = safe_identifier(row.get("report_id")) if row.get("report_id") else None
        return RunSummary(
            run_id=run_id,
            project_id=project_id,
            status=status,
            created_at=self._normalize_datetime(created_at),
            updated_at=self._normalize_datetime(updated_at),
            snapshot_id=snapshot_id,
            duration_seconds=self._safe_duration(row.get("duration_seconds")),
            is_simulated=is_simulated,
            report_id=report_id,
        )

    def runs(self, project_id: str, page: int, page_size: int) -> tuple[list[RunSummary], int]:
        rows = self._run_rows(project_id)
        total = len(rows)
        start = (page - 1) * page_size
        return [self._run_summary(row) for row in rows[start:start + page_size]], total

    def run_detail(self, run_id: str) -> RunDetail:
        row = self._run_row(run_id)
        summary = self._run_summary(row)
        raw_error = row.get("error_message")
        error_code = None
        if raw_error:
            error_code = "run_failed" if summary.status == "failed" else "run_error"
        return RunDetail(**summary.model_dump(), error_code=error_code)

    def _stage_records(self, run_id: str) -> dict[str, dict[str, object]]:
        with self._connect() as connection:
            if connection is None or "stage_results" not in self._tables(connection):
                return {}
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(stage_results)")}
            if not {"run_id", "stage_name", "status"}.issubset(columns):
                return {}
            rows = connection.execute(
                "SELECT * FROM stage_results WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        return {str(row["stage_name"]): dict(row) for row in rows
                if row["stage_name"] in self._RUN_STAGES}

    def _provider_attempts(self, run_id: str) -> dict[str, list[ProviderAttempt]]:
        with self._connect() as connection:
            if connection is None or "llm_calls" not in self._tables(connection):
                return {}
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(llm_calls)")}
            required = {"run_id", "stage", "provider", "model", "status", "duration_seconds"}
            if not required.issubset(columns):
                return {}
            rows = connection.execute(
                "SELECT stage, provider, model, status, duration_seconds FROM llm_calls "
                "WHERE run_id = ? ORDER BY stage, provider, model, status",
                (run_id,),
            ).fetchall()
        grouped: dict[tuple[str, str, str, str], list[float | None]] = {}
        safe_providers = {"gemini", "groq", "fake_simulated"}
        safe_statuses = {"complete", "incomplete", "refused", "error"}
        for row in rows:
            stage = str(row["stage"] or "")
            if stage not in self._RUN_STAGES:
                continue
            raw_provider = str(row["provider"] or "")
            provider = raw_provider if raw_provider in safe_providers else "other"
            model = self._safe_model_name(row["model"])
            status_value = str(row["status"] or "")
            status = status_value if status_value in safe_statuses else "unknown"
            duration = self._safe_duration(row["duration_seconds"])
            key = (stage, provider, model, status)
            grouped.setdefault(key, []).append(duration)
        result: dict[str, list[ProviderAttempt]] = {}
        for (stage, provider, model, status), durations in grouped.items():
            result.setdefault(stage, []).append(ProviderAttempt(
                provider=provider,
                model=model,
                status=status,
                attempt_count=len(durations),
                duration_seconds=(sum(value for value in durations if value is not None)
                                  if all(value is not None for value in durations) else None),
            ))
        return result

    def run_stages(self, run_id: str) -> RunStages:
        self._run_row(run_id)
        records = self._stage_records(run_id)
        provider_attempts = self._provider_attempts(run_id)
        items: list[StageView] = []
        for name in self._RUN_STAGES:
            row = records.get(name)
            if row is None:
                items.append(StageView(stage_name=name, status="unknown", recorded=False))
                continue
            raw_status = str(row.get("status") or "")
            status = raw_status if raw_status in self._STAGE_STATUSES else "unknown"
            try:
                completed_at = datetime.fromisoformat(str(row["completed_at"])) if row.get("completed_at") else None
            except ValueError:
                completed_at = None
            raw_summary = row.get("output_summary")
            summary = None
            if (name != "source_collection" and isinstance(raw_summary, str)
                    and raw_summary.strip() and not raw_summary.lstrip().startswith(("{", "["))):
                summary = sanitize_text(
                    raw_summary, secrets_to_redact=self._configured_secrets(), limit=500,
                )
            raw_error = row.get("error_message")
            items.append(StageView(
                stage_name=name,
                status=status,
                recorded=True,
                duration_seconds=self._safe_duration(row.get("duration_seconds")),
                completed_at=self._normalize_datetime(completed_at) if completed_at else None,
                providers=provider_attempts.get(name),
                result_summary=summary,
                error_code="stage_failed" if raw_error else None,
            ))
        return RunStages(run_id=run_id, items=items)

    def _finding_storage_available(self) -> bool:
        with self._connect() as connection:
            if connection is None or "findings" not in self._tables(connection):
                return False
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(findings)")}
        return "run_id" in columns and bool({"id", "finding_id"}.intersection(columns))

    def _finding_rows(self, run_id: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            if connection is None or "findings" not in self._tables(connection):
                return []
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(findings)")}
            identifier_column = "id" if "id" in columns else "finding_id"
            if "run_id" not in columns or identifier_column not in columns:
                return []
            rows = connection.execute(
                f"SELECT * FROM findings WHERE run_id = ? ORDER BY {identifier_column}",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _report_for_run(self, row: dict[str, object]) -> FinalReport | None:
        run_id = safe_identifier(row.get("run_id"))
        project_id = safe_identifier(row.get("project_id"))
        if run_id is None or project_id is None:
            return None
        with self._connect() as connection:
            if connection is None or "reports" not in self._tables(connection):
                return None
            columns = {item["name"] for item in connection.execute("PRAGMA table_info(reports)")}
            if not {"data_json", "report_id", "run_id", "project_id", "created_at"}.issubset(columns):
                return None
            report_id = safe_identifier(row.get("report_id")) if row.get("report_id") else None
            if report_id:
                stored = connection.execute(
                    "SELECT data_json FROM reports WHERE report_id = ? AND run_id = ?",
                    (report_id, run_id),
                ).fetchone()
            else:
                stored = connection.execute(
                    "SELECT data_json FROM reports WHERE project_id = ? AND run_id = ? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (project_id, run_id),
                ).fetchone()
        if stored is None:
            return None
        try:
            report = FinalReport.model_validate_json(stored["data_json"])
        except (ValueError, TypeError):
            raise DataIntegrityError("Stored report data is invalid") from None
        if report.project_id != project_id or (report.run_id and report.run_id != run_id):
            raise DataIntegrityError("Stored report association is inconsistent")
        return report

    @staticmethod
    def _report_findings(report: FinalReport | None) -> dict[str, CandidateFinding]:
        result: dict[str, CandidateFinding] = {}
        if report is None:
            return result
        collections = (
            report.candidate_findings, report.confirmed_findings, report.rejected_findings,
            report.insufficient_findings, report.pending_findings,
        )
        for collection in collections:
            for finding in collection:
                result[finding.id] = finding
        return result

    def _verification_rows(self, run_id: str) -> dict[str, dict[str, object]]:
        with self._connect() as connection:
            if connection is None or "verifications" not in self._tables(connection):
                return {}
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(verifications)")}
            if not {"run_id", "finding_id", "verdict"}.issubset(columns):
                return {}
            rows = connection.execute(
                "SELECT * FROM verifications WHERE run_id = ? ORDER BY finding_id",
                (run_id,),
            ).fetchall()
        return {str(row["finding_id"]): dict(row) for row in rows}

    def _verification_view(self, row: dict[str, object] | None) -> VerificationView | None:
        if row is None:
            return None
        verdict = str(row.get("verdict") or "")
        if verdict not in {"confirmed", "rejected", "insufficient"}:
            return None
        try:
            raw_ids = json.loads(str(row.get("evidence_ids") or "[]"))
        except (ValueError, TypeError):
            raw_ids = []
        evidence_ids = [value for item in raw_ids if (value := safe_identifier(item))] if isinstance(raw_ids, list) else []
        return VerificationView(
            verdict=verdict,
            reason=sanitize_text(str(row.get("reason") or ""),
                                 secrets_to_redact=self._configured_secrets(), limit=1200) or "",
            verified_by=sanitize_text(str(row.get("verified_by") or ""), limit=120) or "unknown",
            evidence_ids=evidence_ids,
        )

    def _evidence_items(self, run_id: str) -> tuple[bool, list[EvidenceSummary], dict[str, EvidenceItem]]:
        with self._connect() as connection:
            if connection is None or "evidence" not in self._tables(connection):
                return False, [], {}
        adapter = self._adapter()
        if adapter is None:
            return False, [], {}
        summaries: list[EvidenceSummary] = []
        items_by_id: dict[str, EvidenceItem] = {}
        for raw in adapter.get_evidence(run_id):
            try:
                item = raw if isinstance(raw, EvidenceItem) else EvidenceItem.model_validate(raw)
            except (ValueError, TypeError):
                continue
            provenance = item.provenance
            evidence_id = safe_identifier(provenance.id)
            if evidence_id is None or safe_identifier(provenance.project_id) is None:
                continue
            summary = EvidenceSummary(
                id=evidence_id,
                source=provenance.source.value,
                timestamp=self._normalize_datetime(provenance.timestamp),
                repository=safe_repository(provenance.repository),
                commit_sha=safe_commit_sha(provenance.commit_sha),
                file_path=safe_relative_path(provenance.file_path),
                lines=safe_lines(provenance.lines),
                category=sanitize_text(item.category, limit=80),
                summary=sanitize_text(
                    item.summary, secrets_to_redact=self._configured_secrets(), limit=1200,
                ) or "",
            )
            summaries.append(summary)
            items_by_id[evidence_id] = item
        summaries.sort(key=lambda item: item.id)
        return True, summaries, items_by_id

    def evidence_page(self, run_id: str, page: int, page_size: int) -> tuple[list[EvidenceSummary], int]:
        self._run_row(run_id)
        _available, summaries, _items = self._evidence_items(run_id)
        total = len(summaries)
        start = (page - 1) * page_size
        return summaries[start:start + page_size], total

    def findings(self, run_id: str, page: int, page_size: int) -> tuple[list[FindingSummary], int]:
        run_row = self._run_row(run_id)
        rows = self._finding_rows(run_id)
        report_findings = self._report_findings(self._report_for_run(run_row))
        verifications = self._verification_rows(run_id)
        views = [self._finding_summary(run_id, row, report_findings, verifications) for row in rows]
        total = len(views)
        start = (page - 1) * page_size
        return views[start:start + page_size], total

    def _finding_summary(
        self,
        run_id: str,
        row: dict[str, object],
        report_findings: dict[str, CandidateFinding],
        verifications: dict[str, dict[str, object]],
    ) -> FindingSummary:
        finding_id = safe_identifier(row.get("finding_id", row.get("id")))
        if finding_id is None:
            raise DataIntegrityError("Stored finding identifier is unsafe")
        candidate = report_findings.get(finding_id)
        verification = self._verification_view(verifications.get(finding_id))
        if verification is not None:
            verification_status = verification.verdict
        elif candidate is not None and candidate.requires_verification:
            verification_status = "pending"
        else:
            verification_status = "unknown"
        category_raw = candidate.category if candidate is not None else row.get("category")
        if hasattr(category_raw, "value"):
            category_raw = category_raw.value
        category = sanitize_text(str(category_raw), limit=100) if category_raw is not None else None
        severity = candidate.severity.value if candidate is not None else None
        rule_id = safe_identifier(candidate.rule_id) if candidate is not None and candidate.rule_id else (
            safe_identifier(row.get("rule_id")) if row.get("rule_id") else None
        )
        file_path = safe_relative_path(candidate.file_path if candidate is not None else row.get("file_path"))
        line_value = candidate.line if candidate is not None else row.get("line")
        line = line_value if isinstance(line_value, int) and not isinstance(line_value, bool) and line_value > 0 else None
        commit_value = candidate.commit_sha if candidate is not None else row.get("commit_sha")
        confidence_value = candidate.confidence if candidate is not None else row.get("confidence")
        confidence = None
        if isinstance(confidence_value, (int, float)) and not isinstance(confidence_value, bool):
            confidence = float(confidence_value) if 0 <= float(confidence_value) <= 1 else None
        suggested_code_raw = row.get("suggested_code")
        if not isinstance(suggested_code_raw, str):
            suggested_code_raw = ""
        improvement_raw = row.get("improvement_summary")
        if not isinstance(improvement_raw, str):
            improvement_raw = ""
        row_status = row.get("decision_status")
        decision_status = (
            str(row_status) if row_status in {"pending", "approved", "rejected", "changes_requested", "failed"}
            else "pending"
        )
        branch_name = row.get("branch_name")
        branch_url = row.get("branch_url")
        commit_sha_applied = row.get("commit_sha_applied")
        return FindingSummary(
            finding_id=finding_id,
            run_id=run_id,
            category=category,
            severity=severity,
            rule_id=rule_id,
            file_path=file_path,
            line=line,
            commit_sha=safe_commit_sha(commit_value),
            confidence=confidence,
            suggested_code=sanitize_text(suggested_code_raw, secrets_to_redact=self._configured_secrets(), limit=65536) or "",
            improvement_summary=sanitize_text(improvement_raw, secrets_to_redact=self._configured_secrets(), limit=1200) or "",
            decision_status=decision_status,
            branch_name=sanitize_text(str(branch_name), limit=240) if branch_name else None,
            branch_url=_safe_finding_branch_url(branch_url),
            commit_sha_applied=safe_commit_sha(commit_sha_applied),
            verification_status=verification_status,
        )

    def run_project_id(self, run_id: str) -> str:
        row = self._run_row(run_id)
        project_id = safe_identifier(row.get("project_id"))
        if project_id is None:
            raise DataIntegrityError("Stored run project identifier is unsafe")
        return project_id

    def finding_action_context(
        self, run_id: str, finding_id: str,
    ) -> tuple[CandidateFinding, str, CodeSourceSnapshot | None, str | None]:
        """Return a stored candidate and its exact captured patch context, never a guessed source."""
        run_row = self._run_row(run_id)
        if not safe_identifier(finding_id):
            raise LookupError("finding_not_found")
        row = next((item for item in self._finding_rows(run_id)
                    if item.get("id", item.get("finding_id")) == finding_id), None)
        if row is None:
            raise LookupError("finding_not_found")
        candidate = self._report_findings(self._report_for_run(run_row)).get(finding_id)
        if candidate is None:
            raise LookupError("finding_not_actionable")
        if (safe_relative_path(candidate.file_path) != candidate.file_path
                or safe_commit_sha(candidate.commit_sha) is None):
            raise LookupError("finding_source_unavailable")

        suggestion = row.get("suggested_code")
        summary = row.get("improvement_summary")
        status = row.get("decision_status")
        candidate = candidate.model_copy(update={
            "suggested_code": suggestion if isinstance(suggestion, str) else "",
            "improvement_summary": summary if isinstance(summary, str) else "",
            "decision_status": status if status in {"pending", "approved", "rejected", "changes_requested", "failed"} else "pending",
            "branch_name": row.get("branch_name"),
            "branch_url": row.get("branch_url"),
            "commit_sha_applied": row.get("commit_sha_applied"),
        })

        source = None
        repository = None
        adapter = self._adapter()
        snapshot_id = safe_identifier(run_row.get("snapshot_id"))
        if adapter is not None and snapshot_id is not None:
            snapshot = adapter.get_snapshot(snapshot_id)
            if snapshot is not None and snapshot.project_id == run_row.get("project_id"):
                matching: list[CodeDiffSnippet] = []
                for raw in snapshot.code_diffs:
                    try:
                        snippet = raw if isinstance(raw, CodeDiffSnippet) else CodeDiffSnippet.model_validate(raw)
                    except (ValueError, TypeError):
                        continue
                    if snippet.commit_sha == candidate.commit_sha and snippet.file_path == candidate.file_path:
                        matching.append(snippet)
                complete = [snippet for snippet in matching
                            if snippet.evidence_scope == "full_file" and snippet.is_complete and not snippet.truncated]
                if len(complete) > 1:
                    raise LookupError("finding_source_ambiguous")
                if len(complete) == 1:
                    chosen = complete[0]
                    source_repository = safe_repository(chosen.repository)
                    source_hash = chosen.source_sha256
                    if (source_repository and source_hash
                            and hashlib.sha256(chosen.content.encode("utf-8")).hexdigest() == source_hash
                            and chosen.total_lines == len(chosen.content.splitlines())):
                        try:
                            source = CodeSourceSnapshot(
                                content=chosen.content,
                                is_complete=True,
                                repository=source_repository,
                                commit_sha=chosen.commit_sha,
                                file_path=chosen.file_path,
                                source_sha256=source_hash,
                                source_blob_sha=chosen.source_blob_sha,
                                total_lines=chosen.total_lines,
                            )
                            repository = source_repository
                        except ValueError:
                            source = None
                            repository = None

        excerpt = candidate.evidence or ""
        return candidate, excerpt, source, repository

    def finding_detail(self, run_id: str, finding_id: str) -> FindingDetail:
        run_row = self._run_row(run_id)
        if not safe_identifier(finding_id):
            raise LookupError("finding_not_found")
        rows = self._finding_rows(run_id)
        row = next((item for item in rows if item.get("id") == finding_id), None)
        if row is None:
            raise LookupError("finding_not_found")
        report_findings = self._report_findings(self._report_for_run(run_row))
        candidate = report_findings.get(finding_id)
        verification_row = self._verification_rows(run_id).get(finding_id)
        summary = self._finding_summary(run_id, row, report_findings,
                                        {finding_id: verification_row} if verification_row else {})
        evidence_available, evidence, evidence_items = self._evidence_items(run_id)
        evidence_ids: list[str] | None = None
        if candidate is not None:
            evidence_ids = [value for item in candidate.evidence_ids
                            if (value := safe_identifier(item)) is not None]
        verification = self._verification_view(verification_row)
        code_excerpt = self._code_excerpt(run_row, candidate, evidence_items) if candidate else None
        return FindingDetail(
            **summary.model_dump(),
            claim=sanitize_text(candidate.claim, secrets_to_redact=self._configured_secrets(), limit=2000)
            if candidate else None,
            suggested_fix=sanitize_text(candidate.suggested_fix,
                                        secrets_to_redact=self._configured_secrets(), limit=1200)
            if candidate else None,
            evidence_ids=evidence_ids,
            verification=verification,
            evidence=EvidenceCollection(
                availability="available" if evidence_available else "unknown",
                items=[item for item in evidence if evidence_ids is None or item.id in evidence_ids],
            ),
            code_excerpt=code_excerpt,
        )

    def _code_excerpt(
        self,
        run_row: dict[str, object],
        candidate: CandidateFinding,
        evidence_items: dict[str, EvidenceItem],
    ) -> CodeExcerpt | None:
        evidence_id = next((safe_identifier(item) for item in candidate.evidence_ids
                            if safe_identifier(item) in evidence_items), None)
        if evidence_id is None:
            return None
        evidence = evidence_items[evidence_id]
        provenance = evidence.provenance
        file_path = safe_relative_path(candidate.file_path)
        commit_sha = safe_commit_sha(candidate.commit_sha)
        finding_line = candidate.line
        if (file_path is None or commit_sha is None or provenance.commit_sha != commit_sha
                or safe_relative_path(provenance.file_path) != file_path
                or not safe_lines(provenance.lines) or finding_line not in provenance.lines):
            return None
        snapshot_id = safe_identifier(run_row.get("snapshot_id"))
        if snapshot_id is None:
            return None
        adapter = self._adapter()
        if adapter is None:
            return None
        try:
            snapshot = adapter.get_snapshot(snapshot_id)
        except (ValueError, TypeError, KeyError):
            return None
        if snapshot is None or snapshot.project_id != run_row.get("project_id"):
            return None
        for raw_snippet in snapshot.code_diffs:
            try:
                snippet = raw_snippet if isinstance(raw_snippet, CodeDiffSnippet) else CodeDiffSnippet.model_validate(raw_snippet)
            except (ValueError, TypeError):
                continue
            if (snippet.commit_sha != commit_sha or safe_relative_path(snippet.file_path) != file_path
                    or finding_line not in snippet.lines):
                continue
            content_lines = snippet.content.splitlines()
            if len(content_lines) != len(snippet.lines):
                continue
            index = snippet.lines.index(finding_line)
            complete_source = (snippet.evidence_scope == "full_file" and snippet.is_complete
                               and not snippet.truncated)
            if complete_source and len(content_lines) <= 21:
                selected_numbers = snippet.lines
                selected_content = content_lines
                scope = "full_file"
                is_partial = False
            else:
                start = max(0, index - 5)
                end = min(len(content_lines), index + 6)
                selected_numbers = snippet.lines[start:end]
                selected_content = content_lines[start:end]
                scope = "truncated" if snippet.truncated or snippet.evidence_scope == "truncated" else (
                    snippet.evidence_scope if snippet.evidence_scope in {"snippet", "diff"} else "snippet"
                )
                is_partial = True
            original = "\n".join(selected_content)
            cleaned = sanitize_text(
                original, secrets_to_redact=self._configured_secrets(), limit=4000,
            ) or ""
            if len(cleaned) > 4000:
                cleaned = cleaned[:4000]
                scope, is_partial = "truncated", True
            return CodeExcerpt(
                evidence_id=evidence_id,
                file_path=file_path,
                lines=selected_numbers,
                content=cleaned,
                evidence_scope=scope,
                is_partial=is_partial,
                redacted=cleaned != original,
            )
        return None

    def _report_for_project(self, project_id: str) -> tuple[FinalReport, str, str | None] | None:
        with self._connect() as connection:
            if connection is None or "reports" not in self._tables(connection):
                return None
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(reports)")}
            required = {"data_json", "report_id", "run_id", "created_at", "project_id"}
            if not required.issubset(columns):
                return None
            row = connection.execute(
                "SELECT data_json, report_id, run_id FROM reports WHERE project_id = ? "
                "ORDER BY created_at DESC, report_id DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            report = FinalReport.model_validate_json(row["data_json"])
        except (ValueError, TypeError):
            raise DataIntegrityError("Stored report data is invalid") from None
        report_id = safe_identifier(row["report_id"])
        raw_run_id = row["run_id"]
        run_id = safe_identifier(raw_run_id) if raw_run_id else None
        if (report_id is None or report.project_id != project_id
                or (raw_run_id is not None and run_id is None)
                or (report.run_id and report.run_id != run_id)):
            raise DataIntegrityError("Stored report association is inconsistent")
        return report, report_id, run_id

    def policies_summary(self, project_id: str) -> PoliciesSummary:
        self._ensure_project(project_id)
        stored = self._report_for_project(project_id)
        if stored is None:
            return PoliciesSummary(project_id=project_id, run_id=None, availability="unknown", items=[])
        report, _report_id, run_id = stored
        items: list[PolicyResult] = []
        for evaluation in report.policy_evaluations.evaluations:
            rule_id = safe_identifier(evaluation.rule_id)
            if rule_id is None:
                continue
            status_value = evaluation.status.value if hasattr(evaluation.status, "value") else str(evaluation.status)
            status = status_value if status_value in {member.value for member in RuleEvaluationStatus} else "unknown"
            observed = evaluation.observed_value
            if not isinstance(observed, (str, int, float, bool, type(None))):
                observed = None
            if isinstance(observed, str):
                observed = sanitize_text(observed, secrets_to_redact=self._configured_secrets(), limit=500)
            items.append(PolicyResult(
                rule_id=rule_id,
                description=sanitize_text(evaluation.description,
                                          secrets_to_redact=self._configured_secrets(), limit=500) or "",
                status=status,
                mandatory=bool(evaluation.mandatory),
                observed_value=observed,
                reason=sanitize_text(evaluation.reason,
                                     secrets_to_redact=self._configured_secrets(), limit=1000)
                if evaluation.reason is not None else None,
            ))
        return PoliciesSummary(
            project_id=project_id,
            run_id=run_id,
            availability="available" if items else "unknown",
            items=items,
        )

    def _latest_run_row(self, project_id: str) -> dict[str, object] | None:
        rows = self._run_rows(project_id)
        return rows[0] if rows else None

    def quality_summary(self, project_id: str) -> QualitySummary:
        self._ensure_project(project_id)
        row = self._latest_run_row(project_id)
        if row is None:
            return QualitySummary(
                project_id=project_id, run_id=None, stage_status=None,
                availability="unknown", finding_counts=None, ci=None,
            )
        run_id = safe_identifier(row.get("run_id"))
        if run_id is None:
            raise DataIntegrityError("Stored run identifier is unsafe")
        stage = self._stage_records(run_id).get("quality")
        findings = self._finding_rows(run_id)
        report = self._report_for_run(row)
        status_value = str(stage.get("status") or "") if stage else ""
        stage_status = status_value if status_value in self._STAGE_STATUSES else ("unknown" if stage else None)
        available = stage is not None or bool(findings)
        counts = None
        if available and self._finding_storage_available():
            report_findings = self._report_findings(report)
            verification_rows = self._verification_rows(run_id)
            status_counts = {"candidate": 0, "confirmed": 0, "rejected": 0,
                             "insufficient": 0, "pending": 0, "unknown": 0}
            for finding_row in findings:
                view = self._finding_summary(run_id, finding_row, report_findings, verification_rows)
                status_counts["candidate"] += 1
                status_counts[view.verification_status] += 1
            counts = FindingCounts(**status_counts)
        ci = None
        if report is not None and report.ci_result is not None:
            ci_record = report.ci_result
            raw_ci_status = sanitize_text(ci_record.status, limit=80)
            ci_status = raw_ci_status.lower() if raw_ci_status else "unknown"
            if ci_status not in {"success", "failure", "completed", "in_progress", "queued", "cancelled", "unknown"}:
                ci_status = "unknown"
            ci = CIRunView(
                id=safe_identifier(ci_record.id) or "unknown",
                workflow_name=sanitize_text(ci_record.workflow_name, limit=120) or "unknown",
                commit_sha=safe_commit_sha(ci_record.commit_sha) or "unknown",
                status=ci_status,
                total_tests=self._safe_nonnegative_int(ci_record.total_tests),
                failed_tests=self._safe_nonnegative_int(ci_record.failed_tests),
                coverage_percentage=self._safe_percentage(ci_record.coverage_percentage),
                timestamp=self._normalize_datetime(ci_record.timestamp) if ci_record.timestamp else None,
            )
        return QualitySummary(
            project_id=project_id,
            run_id=run_id,
            stage_status=stage_status,
            availability="available" if available else "unknown",
            finding_counts=counts,
            ci=ci,
        )

    def _latest_report_view(self, project_id: str) -> ExecutiveReportSummary | None:
        stored = self._report_for_project(project_id)
        if stored is None:
            return None
        report, report_id, run_id = stored
        recommendation = report.recommendation
        if recommendation not in {"recomendar_aprobacion", "solicitar_cambios", "evidencia_insuficiente"}:
            recommendation = "unknown"
        decision = report.human_decision.value if hasattr(report.human_decision, "value") else str(report.human_decision)
        if decision not in {"pending", "approved", "rejected", "changes_requested", "postponed"}:
            decision = "unknown"
        return ExecutiveReportSummary(
            report_id=report_id,
            run_id=run_id,
            cutoff_datetime=self._normalize_datetime(report.cutoff_datetime),
            recommendation=recommendation,
            human_decision=decision,
            summary=sanitize_text(report.summary, secrets_to_redact=self._configured_secrets(), limit=2000) or "",
        )

    def _run_series(self, project_id: str, limit: int = 100) -> RunSeries:
        rows = self._run_rows(project_id)
        counts: dict[str, int] = {}
        for row in rows:
            status = str(row.get("status") or "")
            key = status if status in self._RUN_STATUSES else "unknown"
            counts[key] = counts.get(key, 0) + 1
        series: list[RunProgressPoint] = []
        for row in reversed(rows[:limit]):
            item = self._run_summary(row)
            series.append(RunProgressPoint(
                run_id=item.run_id,
                created_at=item.created_at,
                status=item.status,
                duration_seconds=item.duration_seconds,
            ))
        return RunSeries(total=len(rows), by_status=counts, series=series)

    def executive_summary(self, project_id: str) -> ExecutiveSummary:
        captures = self.captures_for_project(project_id)
        latest = captures[-1] if captures else None
        capture_series = []
        for capture in captures[-100:]:
            summary = self.summary(capture)
            capture_series.append(CaptureProgressPoint(
                capture_id=capture.capture_id,
                captured_at=summary.captured_at,
                progress_percentage=summary.progress_percentage,
                total_tasks=summary.total_tasks,
                completed_tasks=summary.completed_tasks,
                state_counts=summary.state_counts,
            ))
        return ExecutiveSummary(
            project_id=project_id,
            capture_id=latest.capture_id if latest else None,
            task_summary=self.summary(latest) if latest else None,
            capture_series=capture_series,
            quality=self.quality_summary(project_id),
            policies=self.policies_summary(project_id),
            latest_report=self._latest_report_view(project_id),
            runs=self._run_series(project_id),
        )
