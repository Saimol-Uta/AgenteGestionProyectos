"""Read-only projections and bounded, session-authorized web governance operations."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import re
import sqlite3
from typing import Any, Iterator, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.api.analysis_jobs import AnalysisRuntimePlan, load_analysis_plan
from agente_gestion.api.control_schemas import (
    AuditCollection,
    AuditEvent,
    AuditEventDetail,
    OutboxCollection,
    OutboxDetail,
    OutboxMessageView,
    OutboxMetrics,
    OutboxReceiptSummary,
    PoliciesConfiguration,
    ProposalCollection,
    ProposalDetail,
    ProposalEvidence,
    ProposalSummary,
    ProposalTechnicalReviewView,
    ProposalTechnicalDryRun,
    ProjectConfiguration,
    RepositoryConfiguration,
    ReportCollection,
    ReportDecisionEvent,
    ReportDetail,
    ReportMetrics,
    ReportSummary,
    ScheduleView,
    TrelloConfiguration,
    WeeklyScheduleView,
    WeeklyWindowView,
)
from agente_gestion.api.errors import ApiProblem
from agente_gestion.api.repository import DataIntegrityError, SQLiteQueryRepository
from agente_gestion.api.security import (
    safe_commit_sha,
    safe_identifier,
    safe_repository,
    sanitize_text,
)
from agente_gestion.api.settings import ApiSettings
from agente_gestion.domain.action_proposal import ActionProposal
from agente_gestion.domain.proposal_review import assess_proposal
from agente_gestion.domain.notification_outbox import OutboxMessage, OutboxReceipt
from agente_gestion.domain.report import FinalReport, HumanDecisionState
from agente_gestion.domain.run import ExecutionRun, RunStatus
from agente_gestion.domain.security_rbac import SecurityAuditEntry, hash_token_fingerprint
from agente_gestion.domain.weekly_schedule import WeeklySchedule


_DECISION_VALUES = {item.value for item in HumanDecisionState}
_WEB_PERMISSIONS = {
    "report_decision": "web:report_decision",
    "weekly_cycle": "web:weekly_cycle",
    "manager_email": "web:manager_email",
    "finding_decision": "web:finding_decision",
    "proposal_execution": "web:proposal_execute",
}
_HTML_BLOCKS = re.compile(r"(?is)<\s*(script|style|iframe|object|svg|math|form)\b[^>]*>.*?<\s*/\s*\1\s*>")
_HTML_TAG = re.compile(r"(?is)<[^>]{0,4096}>")
_HTML_COMMENT = re.compile(r"(?s)<!--.*?-->")
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MARKDOWN_REFERENCE_IMAGE = re.compile(r"!\[[^\]]*\](?:\[[^\]]*\])?")
_MARKDOWN_REFERENCE_DEFINITION = re.compile(r"(?m)^\s*\[[^\]]+\]:\s*\S+.*$")
_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _safe_markdown(value: str | None, *, secrets_to_redact: tuple[str, ...] = ()) -> str | None:
    """Return bounded Markdown with raw HTML, active links and remote image loads removed."""
    cleaned = sanitize_text(value, secrets_to_redact=secrets_to_redact, limit=100_000)
    if cleaned is None:
        return None
    cleaned = _HTML_BLOCKS.sub("", cleaned)
    cleaned = _HTML_COMMENT.sub("", cleaned)
    cleaned = _HTML_TAG.sub("", cleaned)
    cleaned = _MARKDOWN_IMAGE.sub("", cleaned)
    cleaned = _MARKDOWN_REFERENCE_IMAGE.sub("", cleaned)
    cleaned = _MARKDOWN_REFERENCE_DEFINITION.sub("", cleaned)

    def safe_link(match: re.Match[str]) -> str:
        label, destination = match.group(1), match.group(2).strip().strip("<>")
        try:
            scheme = urlsplit(destination).scheme.lower()
        except ValueError:
            scheme = "unsafe"
        if scheme not in {"", "http", "https", "mailto"} or destination.startswith("//"):
            return f"[{label}](#)"
        return f"[{label}]({destination})"

    return _MARKDOWN_LINK.sub(safe_link, cleaned)


def _normalized_datetime(value: datetime, timezone_name: str) -> datetime:
    zone = ZoneInfo(timezone_name)
    if value.tzinfo is None:
        value = value.replace(tzinfo=zone)
    return value.astimezone(zone)


def _utc_key(value: datetime, timezone_name: str) -> datetime:
    return _normalized_datetime(value, timezone_name).astimezone(timezone.utc)


def _json_text(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _scalar(value: object) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return None


def _safe_component(value: object) -> str | None:
    if isinstance(value, str) and _SAFE_COMPONENT.fullmatch(value) and value not in {".", ".."}:
        return value
    return None


class ControlPlaneRepository:
    """Read-only access to existing persisted contracts; never initializes a database."""

    def __init__(self, settings: ApiSettings):
        self.settings = settings
        self.queries = SQLiteQueryRepository(settings)
        self._secrets: tuple[str, ...] = tuple(
            ([settings.operator_token.get_secret_value()] if settings.operator_token else [])
            + list(self.queries._configured_secrets())
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection | None]:
        with self.queries._connect() as connection:
            yield connection

    @staticmethod
    def _tables(connection: sqlite3.Connection | None) -> set[str]:
        return SQLiteQueryRepository._tables(connection) if connection is not None else set()

    def _read_storage(self) -> SQLiteStorageAdapter | None:
        if not self.queries.database_exists:
            return None
        return SQLiteStorageAdapter(str(self.queries.db_path), read_only=True)

    def _write_storage(self) -> SQLiteStorageAdapter:
        # Core migrations and writes occur only on an explicitly authorized POST.
        return SQLiteStorageAdapter(self.settings.db_path)

    def _safe_text(self, value: str | None, limit: int = 2000) -> str | None:
        return sanitize_text(value, secrets_to_redact=self._secrets, limit=limit)

    def _report_row(self, row: sqlite3.Row, project_id: str) -> dict[str, Any]:
        try:
            report = FinalReport.model_validate_json(row["data_json"])
            report_id = str(row["report_id"])
            run_id = str(row["run_id"]) if row["run_id"] is not None else None
            created_at = datetime.fromisoformat(str(row["created_at"]))
            cutoff = datetime.fromisoformat(str(row["cutoff_datetime"]))
            if (safe_identifier(report_id) is None or report.project_id != project_id
                    or str(row["project_id"]) != project_id or report.report_id != report_id
                    or report.run_id != run_id or (run_id is not None and safe_identifier(run_id) is None)
                    or report.report_version != int(row["version"])):
                raise ValueError
            decision = str(row["human_decision"])
            if decision not in _DECISION_VALUES or report.human_decision.value != decision:
                raise ValueError
            if report.cutoff_datetime != cutoff:
                raise ValueError
            decision_timestamp = (
                datetime.fromisoformat(str(row["decision_timestamp"]))
                if row["decision_timestamp"] is not None else None
            )
        except (KeyError, ValueError, TypeError, ValidationError, AttributeError):
            raise DataIntegrityError("Stored report binding is invalid") from None
        return {
            "report": report,
            "report_id": report_id,
            "run_id": run_id,
            "created_at": created_at,
            "cutoff_datetime": cutoff,
            "decision": decision,
            "decision_reason": row["decision_reason"],
            "decision_timestamp": decision_timestamp,
            "markdown_text": row["markdown_text"],
        }

    def _report_summary(self, record: dict[str, Any]):
        report: FinalReport = record["report"]
        metrics = report.metrics
        policy_results = []
        for evaluation in report.policy_evaluations.evaluations:
            status = evaluation.status.value if hasattr(evaluation.status, "value") else str(evaluation.status)
            if status not in {"pass", "fail", "unknown"}:
                status = "unknown"
            policy_results.append({
                "rule_id": self._safe_text(evaluation.rule_id, 128) or "unknown",
                "description": _safe_markdown(
                    evaluation.description, secrets_to_redact=self._secrets,
                ) or "",
                "status": status,
                "mandatory": bool(evaluation.mandatory),
                "observed_value": _scalar(evaluation.observed_value),
                "reason": self._safe_text(evaluation.reason, 1000),
            })
        risk_ids = list(dict.fromkeys(
            metrics.overdue_card_ids + metrics.due_soon_card_ids + metrics.blocked_card_ids
            + metrics.unknown_card_ids + metrics.contradictory_card_ids
        ))
        return ReportSummary(
            report_id=record["report_id"],
            project_id=report.project_id,
            run_id=record["run_id"],
            report_version=report.report_version,
            created_at=_normalized_datetime(record["created_at"], self.settings.timezone),
            cutoff_datetime=_normalized_datetime(record["cutoff_datetime"], self.settings.timezone),
            recommendation=self._safe_text(report.recommendation, 160) or "unknown",
            human_decision=record["decision"],
            decision_reason=self._safe_text(record["decision_reason"], 1000),
            decision_timestamp=(_normalized_datetime(record["decision_timestamp"], self.settings.timezone)
                                if record["decision_timestamp"] else None),
            summary=self._safe_text(report.summary, 5000) or "",
            metrics=ReportMetrics(
                total_cards=metrics.total_cards,
                included_cards=metrics.included_cards,
                completed_cards=metrics.completed_cards,
                progress_percentage=metrics.progress_percentage,
                overdue_card_ids=[self._safe_text(item, 128) or "unknown" for item in metrics.overdue_card_ids],
                due_soon_card_ids=[self._safe_text(item, 128) or "unknown" for item in metrics.due_soon_card_ids],
                blocked_card_ids=[self._safe_text(item, 128) or "unknown" for item in metrics.blocked_card_ids],
                unknown_card_ids=[self._safe_text(item, 128) or "unknown" for item in metrics.unknown_card_ids],
                contradictory_card_ids=[self._safe_text(item, 128) or "unknown" for item in metrics.contradictory_card_ids],
            ),
            risk_task_ids=[self._safe_text(item, 128) or "unknown" for item in risk_ids],
            recommendations=[self._safe_text(item, 2000) or "" for item in report.recommendations],
            policy_results=policy_results,
        )

    def _report_history(self, record: dict[str, Any], tables: set[str]) -> tuple[list[ReportDecisionEvent], str]:
        if "human_decisions" not in tables:
            return [], "unknown"
        report_id, run_id = record["report_id"], record["run_id"]
        with self._connect() as connection:
            if connection is None:
                return [], "unknown"
            rows = connection.execute(
                "SELECT id, report_id, run_id, decision, reason, recorded_at "
                "FROM human_decisions WHERE report_id=? ORDER BY recorded_at, id LIMIT 1000",
                (report_id,),
            ).fetchall()
        events = []
        for row in rows:
            decision = str(row["decision"])
            row_run = str(row["run_id"]) if row["run_id"] is not None else None
            event_id = safe_identifier(str(row["id"]))
            if (event_id is None or decision not in _DECISION_VALUES
                    or (row_run is not None and row_run != run_id)):
                raise DataIntegrityError("Stored report decision binding is invalid")
            events.append(ReportDecisionEvent(
                decision_id=event_id,
                report_id=report_id,
                run_id=row_run,
                decision=decision,
                reason=self._safe_text(row["reason"], 1000),
                recorded_at=_normalized_datetime(datetime.fromisoformat(str(row["recorded_at"])), self.settings.timezone),
                actor_id=None,
                actor_verification="unknown",
            ))
        return events, "available"

    def reports(
        self, project_id: str, page: int, page_size: int, decision: str | None = None,
    ) -> tuple[list[ReportSummary], int, str]:
        with self._connect() as connection:
            tables = self._tables(connection)
            if connection is None or "reports" not in tables:
                return [], 0, "unknown"
            where = "project_id=?"
            params: list[object] = [project_id]
            if decision is not None:
                where += " AND human_decision=?"
                params.append(decision)
            total = int(connection.execute(f"SELECT count(*) FROM reports WHERE {where}", params).fetchone()[0])
            rows = connection.execute(
                f"SELECT report_id, run_id, project_id, version, created_at, cutoff_datetime, recommendation, "
                f"human_decision, decision_reason, decision_timestamp, data_json, markdown_text "
                f"FROM reports WHERE {where} ORDER BY created_at DESC, report_id DESC LIMIT ? OFFSET ?",
                (*params, page_size, (page - 1) * page_size),
            ).fetchall()
        records = [self._report_row(row, project_id) for row in rows]
        for record in records:
            if record["run_id"] is not None and not self._report_run_is_bound(
                project_id, record["report_id"], record["run_id"],
            ):
                raise DataIntegrityError("Stored report run binding is invalid")
        return [self._report_summary(record) for record in records], total, "available"

    def report_detail(self, project_id: str, report_id: str) -> ReportDetail:
        with self._connect() as connection:
            tables = self._tables(connection)
            if connection is None or "reports" not in tables:
                raise LookupError("report_not_found")
            row = connection.execute(
                "SELECT report_id, run_id, project_id, version, created_at, cutoff_datetime, recommendation, "
                "human_decision, decision_reason, decision_timestamp, data_json, markdown_text "
                "FROM reports WHERE project_id=? AND report_id=?",
                (project_id, report_id),
            ).fetchone()
        if row is None:
            raise LookupError("report_not_found")
        record = self._report_row(row, project_id)
        if record["run_id"] is not None and not self._report_run_is_bound(
            project_id, record["report_id"], record["run_id"],
        ):
            raise DataIntegrityError("Stored report run binding is invalid")
        summary = self._report_summary(record)
        history, _availability = self._report_history(record, tables)
        markdown = _safe_markdown(record["markdown_text"], secrets_to_redact=self._secrets)
        return ReportDetail(**summary.model_dump(), decision_history=history, markdown_text=markdown)

    def report_record(self, project_id: str, report_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            tables = self._tables(connection)
            if connection is None or "reports" not in tables:
                return None
            row = connection.execute(
                "SELECT report_id, run_id, project_id, version, created_at, cutoff_datetime, recommendation, "
                "human_decision, decision_reason, decision_timestamp, data_json, markdown_text "
                "FROM reports WHERE project_id=? AND report_id=?",
                (project_id, report_id),
            ).fetchone()
        if row is None:
            return None
        record = self._report_row(row, project_id)
        if record["run_id"] is not None and not self._report_run_is_bound(
            project_id, record["report_id"], record["run_id"],
        ):
            raise DataIntegrityError("Stored report run binding is invalid")
        return record

    def report_download(self, project_id: str, report_id: str, format_name: Literal["markdown", "json"]):
        detail = self.report_detail(project_id, report_id)
        if format_name == "markdown":
            if detail.markdown_text is None:
                raise LookupError("report_markdown_unavailable")
            return "text/markdown; charset=utf-8", f"report-{report_id}.md", detail.markdown_text
        body = _json_text(detail.model_dump(mode="json"))
        return "application/json; charset=utf-8", f"report-{report_id}.json", body

    def _decision_timestamps(self, project_id: str, proposal_id: str) -> tuple[dict[str, datetime], str]:
        with self._connect() as connection:
            if connection is None or "proposal_decisions" not in self._tables(connection):
                return {}, "unknown"
            rows = connection.execute(
                "SELECT event_id, recorded_at FROM proposal_decisions "
                "WHERE project_id=? AND proposal_id=? ORDER BY ordered_version LIMIT 1000",
                (project_id, proposal_id),
            ).fetchall()
        result: dict[str, datetime] = {}
        for row in rows:
            event_id = safe_identifier(str(row["event_id"]))
            if event_id is None:
                raise DataIntegrityError("Stored proposal event identifier is invalid")
            result[event_id] = _normalized_datetime(
                datetime.fromisoformat(str(row["recorded_at"])), self.settings.timezone,
            )
        return result, "available"

    def _technical_review_view(self, project_id: str, proposal: ActionProposal, digest: str):
        """Read a bound technical result without granting or fabricating human approval."""
        from agente_gestion.application.proposal_auto_review import ProposalTechnicalReview

        with self._connect() as connection:
            if connection is None or "proposal_technical_reviews" not in self._tables(connection):
                return None
            row = connection.execute(
                "SELECT review_json, review_sha256 FROM proposal_technical_reviews "
                "WHERE project_id=? AND proposal_id=? AND proposal_body_sha256=? AND capture_id=?",
                (project_id, proposal.proposal_id, digest, proposal.observed_capture_id),
            ).fetchone()
        if row is None:
            return None
        if hashlib.sha256(row["review_json"].encode("utf-8")).hexdigest() != row["review_sha256"]:
            raise DataIntegrityError("Stored technical review integrity mismatch")
        try:
            review = ProposalTechnicalReview.model_validate_json(row["review_json"])
        except (ValidationError, ValueError, TypeError):
            raise DataIntegrityError("Stored technical review is invalid") from None
        if (review.project_id, review.proposal_id, review.proposal_body_sha256, review.capture_id) != (
            project_id, proposal.proposal_id, digest, proposal.observed_capture_id,
        ):
            raise DataIntegrityError("Stored technical review binding mismatch")
        return ProposalTechnicalReviewView(
            review_id=safe_identifier(review.review_id) or "unknown",
            technical_status=review.technical_status,
            technical_errors=[self._safe_text(item, 256) or "unknown" for item in review.technical_errors],
            dry_run=ProposalTechnicalDryRun(
                status=review.dry_run.status,
                reasons=[self._safe_text(item, 256) or "unknown" for item in review.dry_run.reasons],
                field=review.dry_run.field,
                before=_safe_proposal_value(review.dry_run.before, self._safe_text),
                after=_safe_proposal_value(review.dry_run.after, self._safe_text),
            ),
            recorded_at=_normalized_datetime(review.recorded_at, self.settings.timezone),
        )

    def _proposal_data(self, project_id: str, proposal: ActionProposal, evidence_items, storage):
        review = storage.get_proposal_review_state(project_id, proposal.proposal_id)
        if review is None:
            raise DataIntegrityError("Stored proposal review state is unavailable")
        digest, _events, projection = review
        try:
            capture = self.queries.selected_capture(project_id, proposal.observed_capture_id)
            assessment = assess_proposal(proposal, capture, evidence_items)
            technical = "eligible" if assessment.eligible else "ineligible"
            errors = list(assessment.errors)
        except (LookupError, ValueError, DataIntegrityError):
            capture = None
            technical = "unknown"
            errors = []
        try:
            captures = self.queries.captures(project_id)
        except (ValueError, DataIntegrityError, sqlite3.Error):
            captures = []
        freshness = "unknown"
        if captures and capture is not None:
            capture_times = [(_utc_key(item.snapshot.captured_at, self.settings.timezone), item.capture_id)
                             for item in captures]
            latest_time = max(item[0] for item in capture_times)
            newest = [capture_id for timestamp, capture_id in capture_times if timestamp == latest_time]
            if len(newest) == 1:
                freshness = "current" if newest[0] == proposal.observed_capture_id else "stale"
        evidence_by_id = {item.provenance.id: item for item in evidence_items}
        public_evidence = []
        for evidence_id in proposal.evidence_ids:
            item = evidence_by_id.get(evidence_id)
            if item is None:
                continue
            public_evidence.append(ProposalEvidence(
                evidence_id=safe_identifier(item.provenance.id) or "unknown",
                category=self._safe_text(item.category, 80) or "unknown",
                summary=self._safe_text(item.summary, 1000) or "",
            ))
        executions = []
        execution_table_available = False
        with self._connect() as connection:
            execution_table_available = connection is not None and "proposal_executions" in self._tables(connection)
        if execution_table_available:
            try:
                executions = storage.list_proposal_executions(project_id, proposal.proposal_id)
            except (sqlite3.Error, ValueError):
                execution_table_available = False
        execution_status = (
            str(executions[-1].status) if executions else ("not_executed" if execution_table_available else "unknown")
        )
        if execution_status not in {"not_executed", "EXECUTED", "ALREADY_APPLIED", "DENIED", "CONFLICT", "FAILED", "unknown"}:
            execution_status = "unknown"
        version = projection.version if projection is not None else None
        review_status = projection.review_status if projection is not None else "unknown"
        authorization_status = projection.authorization_status if projection is not None else "unknown"
        return {
            "summary": ProposalSummary(
                proposal_id=proposal.proposal_id,
                project_id=proposal.project_id,
                task_id=proposal.task_id,
                capture_id=proposal.observed_capture_id,
                field=proposal.field,
                current_value=_safe_proposal_value(proposal.current_value, self._safe_text),
                proposed_value=_safe_proposal_value(proposal.proposed_value, self._safe_text),
                rationale=self._safe_text(proposal.rationale, 2000) or "",
                expected_impact=self._safe_text(proposal.expected_impact, 2000) or "",
                evidence=public_evidence,
                proposal_body_sha256=digest,
                version=version,
                technical_eligibility=technical,
                technical_errors=errors,
                technical_review=self._technical_review_view(project_id, proposal, digest),
                freshness=freshness,
                review_status=review_status,
                authorization_status=authorization_status,
                execution_status=execution_status,
            ),
            "projection": projection,
            "digest": digest,
        }

    def proposals(
        self, project_id: str, page: int, page_size: int, review_status: str | None = None,
    ) -> tuple[list[ProposalSummary], int, str]:
        storage = self._read_storage()
        if storage is None:
            return [], 0, "unknown"
        with self._connect() as connection:
            if "action_proposals" not in self._tables(connection):
                return [], 0, "unknown"
        proposals = storage.list_action_proposals(project_id)
        views = [self._proposal_data(project_id, proposal, evidence, storage)["summary"]
                 for proposal, evidence in proposals]
        if review_status is not None:
            views = [item for item in views if item.review_status == review_status]
        total = len(views)
        return views[(page - 1) * page_size:page * page_size], total, "available"

    def proposal_detail(self, project_id: str, proposal_id: str) -> ProposalDetail:
        storage = self._read_storage()
        if storage is None:
            raise LookupError("proposal_not_found")
        with self._connect() as connection:
            tables = self._tables(connection)
            if "action_proposals" not in tables:
                raise LookupError("proposal_not_found")
        item = storage.get_action_proposal(proposal_id)
        if item is None or item[0].project_id != project_id:
            raise LookupError("proposal_not_found")
        proposal, evidence = item
        data = self._proposal_data(project_id, proposal, evidence, storage)
        return ProposalDetail(**data["summary"].model_dump())

    def _web_audit_entry(
        self, *, project_id: str, actor_id: str, session_cookie: str | None,
        permission: str, resource_id: str, idempotency_key: str,
        request_fingerprint: str, decision: Literal["granted", "denied"] = "granted",
    ) -> SecurityAuditEntry:
        session_fingerprint = hashlib.sha256((session_cookie or "").encode("utf-8")).hexdigest()
        identity = "|".join((session_fingerprint, project_id, resource_id, permission, idempotency_key))
        return SecurityAuditEntry(
            event_id="web-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32],
            project_id=project_id,
            actor_id=actor_id,
            role="local_operator",
            permission_checked=permission,
            resource_id=resource_id,
            decision=decision,
            reason="request_sha256=" + request_fingerprint,
            token_fingerprint=hash_token_fingerprint(session_cookie or ""),
        )

    def _ensure_web_audit(
        self, storage: SQLiteStorageAdapter, *, project_id: str, actor_id: str,
        session_cookie: str | None, permission: str, resource_id: str,
        idempotency_key: str, request_fingerprint: str, decision: Literal["granted", "denied"] = "granted",
    ) -> str:
        expected = self._web_audit_entry(
            project_id=project_id, actor_id=actor_id, session_cookie=session_cookie,
            permission=permission, resource_id=resource_id, idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint, decision=decision,
        )
        event_id = expected.event_id
        with self._connect() as connection:
            if connection is not None and "security_audit_log" in self._tables(connection):
                row = connection.execute(
                    "SELECT event_id, project_id, actor_id, role, permission_checked, resource_id, decision, reason, "
                    "token_fingerprint, recorded_at FROM security_audit_log WHERE event_id=?", (event_id,),
                ).fetchone()
                if row is not None:
                    if (row["project_id"], row["actor_id"], row["role"], row["permission_checked"],
                            row["resource_id"], row["decision"], row["reason"]) != (
                            expected.project_id, expected.actor_id, expected.role, expected.permission_checked,
                            expected.resource_id, expected.decision, expected.reason):
                        raise ApiProblem(409, "idempotencia_conflictiva", "La clave de idempotencia ya se usó con otra solicitud.")
                    return event_id
        try:
            storage.append_security_audit_event(expected)
        except sqlite3.IntegrityError:
            with self._connect() as connection:
                row = (connection.execute(
                    "SELECT project_id, actor_id, role, permission_checked, resource_id, decision, reason "
                    "FROM security_audit_log WHERE event_id=?", (event_id,),
                ).fetchone() if connection is not None and "security_audit_log" in self._tables(connection) else None)
            if row is None or tuple(row) != (
                expected.project_id, expected.actor_id, expected.role, expected.permission_checked,
                expected.resource_id, expected.decision, expected.reason,
            ):
                raise
        return event_id

    @staticmethod
    def _operation_fingerprint(values: dict[str, Any]) -> str:
        return hashlib.sha256(_json_text(values).encode("utf-8")).hexdigest()

    @staticmethod
    def _operation_event_id(
        project_id: str, resource_id: str, permission: str,
        session_cookie: str | None, idempotency_key: str,
    ) -> str:
        session_hash = hashlib.sha256((session_cookie or "").encode("utf-8")).hexdigest()
        value = "|".join((session_hash, project_id, resource_id, permission, idempotency_key))
        return "web-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]

    def _web_audit_event(self, event_id: str) -> SecurityAuditEntry | None:
        with self._connect() as connection:
            if connection is None or "security_audit_log" not in self._tables(connection):
                return None
            row = connection.execute(
                "SELECT event_id, project_id, actor_id, role, permission_checked, resource_id, decision, reason, "
                "token_fingerprint, recorded_at FROM security_audit_log WHERE event_id=?", (event_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return SecurityAuditEntry(
                event_id=row["event_id"], project_id=row["project_id"], actor_id=row["actor_id"],
                role=row["role"], permission_checked=row["permission_checked"], resource_id=row["resource_id"],
                decision=row["decision"], reason=row["reason"], token_fingerprint=row["token_fingerprint"],
                recorded_at=datetime.fromisoformat(row["recorded_at"]),
            )
        except (TypeError, ValueError, ValidationError):
            raise DataIntegrityError("Stored web audit event is invalid") from None

    def _report_run_is_bound(self, project_id: str, report_id: str, run_id: str | None) -> bool:
        if run_id is None:
            return False
        with self._connect() as connection:
            tables = self._tables(connection)
            if connection is None or "runs" not in tables or "reports" not in tables:
                return False
            run = connection.execute(
                "SELECT project_id FROM runs WHERE run_id=?", (run_id,),
            ).fetchone()
            collision = connection.execute(
                "SELECT 1 FROM reports WHERE run_id=? AND report_id<>? LIMIT 1",
                (report_id, report_id),
            ).fetchone()
        return run is not None and run["project_id"] == project_id and collision is None

    def decide_report(
        self, project_id: str, report_id: str, *, expected_version: int,
        expected_human_decision: str, decision: str, reason: str | None,
        actor_id: str, session_cookie: str | None, idempotency_key: str,
    ) -> tuple[ReportDetail, ReportDecisionEvent, bool, str]:
        normalized_reason = self._safe_text(reason, 1000)
        if normalized_reason is not None:
            normalized_reason = normalized_reason.strip() or None
        if decision != "approved" and not normalized_reason:
            raise ApiProblem(422, "motivo_requerido", "Indique el motivo de esta decisión humana.")
        request_fingerprint = self._operation_fingerprint({
            "expected_version": expected_version,
            "expected_human_decision": expected_human_decision,
            "decision": decision,
            "reason": normalized_reason,
        })
        permission = _WEB_PERMISSIONS["report_decision"]
        audit_event_id = self._operation_event_id(
            project_id, report_id, permission, session_cookie, idempotency_key,
        )
        previous_audit = self._web_audit_event(audit_event_id)
        if previous_audit is not None:
            if (previous_audit.project_id != project_id or previous_audit.actor_id != actor_id
                    or previous_audit.permission_checked != permission or previous_audit.resource_id != report_id
                    or previous_audit.reason != "request_sha256=" + request_fingerprint
                    or previous_audit.decision != "granted"):
                raise ApiProblem(409, "idempotencia_conflictiva", "La clave de idempotencia ya se usó con otra solicitud.")
            detail = self.report_detail(project_id, report_id)
            matching = [item for item in detail.decision_history
                        if item.decision == decision and item.reason == normalized_reason]
            if detail.human_decision != decision or not matching:
                raise ApiProblem(409, "decision_no_reconciliable", "No se pudo verificar el resultado previo de la decisión.")
            return detail, matching[-1], True, audit_event_id

        record = self.report_record(project_id, report_id)
        if record is None:
            raise LookupError("report_not_found")
        summary = self._report_summary(record)
        if summary.report_version != expected_version or summary.human_decision != expected_human_decision:
            raise ApiProblem(409, "version_de_informe_obsoleta", "El informe cambió desde que fue revisado.")
        if not self._report_run_is_bound(project_id, report_id, record["run_id"]):
            raise ApiProblem(409, "vinculo_de_informe_invalido", "El informe no tiene una ejecución válida del proyecto.")
        with self._connect() as connection:
            tables = self._tables(connection)
        history, history_availability = self._report_history(record, tables)
        if history_availability != "available" or history:
            raise ApiProblem(409, "decision_humana_existente", "El informe ya tiene historial de decisión o su auditoría no está disponible.")
        try:
            domain_decision = HumanDecisionState(decision)
            storage = self._write_storage()
            audit_entry = self._web_audit_entry(
                project_id=project_id, actor_id=actor_id, session_cookie=session_cookie,
                permission=permission, resource_id=report_id, idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )
            stored, reused = storage.register_human_decision_with_audit(
                report_id, domain_decision, normalized_reason, record["run_id"],
                project_id=project_id, expected_version=expected_version,
                expected_human_decision=expected_human_decision, audit_entry=audit_entry,
            )
        except ValueError as exc:
            failure = exc.args[0] if exc.args else "decision_no_aplicada"
            if failure == "report_not_found":
                raise LookupError("report_not_found") from None
            problems = {
                "report_version_obsolete": (409, "version_de_informe_obsoleta", "El informe cambió desde que fue revisado."),
                "report_binding_invalid": (409, "vinculo_de_informe_invalido", "El informe no tiene una ejecución válida del proyecto."),
                "human_decision_exists": (409, "decision_humana_existente", "El informe ya tiene historial de decisión."),
                "idempotency_conflict": (409, "idempotencia_conflictiva", "La clave de idempotencia ya se usó con otra solicitud."),
                "decision_not_reconciled": (409, "decision_no_reconciliable", "No se pudo verificar el resultado previo de la decisión."),
            }
            status_code, code, message = problems.get(
                failure, (409, "decision_no_aplicada", "No fue posible aplicar la decisión al informe actual."),
            )
            raise ApiProblem(status_code, code, message) from None
        except sqlite3.Error:
            raise ApiProblem(503, "base_no_disponible", "La consulta local no está disponible en este momento.") from None
        except (TypeError, ValidationError):
            raise ApiProblem(409, "decision_no_aplicada", "No fue posible aplicar la decisión al informe actual.") from None
        detail = self.report_detail(project_id, report_id)
        matching = [item for item in detail.decision_history
                    if item.decision == decision and item.reason == normalized_reason]
        if (stored.report_id != report_id or detail.human_decision != decision or not matching):
            raise DataIntegrityError("Human report decision did not preserve report binding")
        decision_event = matching[-1]
        return detail, decision_event, reused, audit_entry.event_id

    def _outbox_rows(self, project_id: str, *, page: int = 1, page_size: int = 100):
        with self._connect() as connection:
            if connection is None or "notification_outbox" not in self._tables(connection):
                return [], 0, "unknown"
            total = int(connection.execute(
                "SELECT count(*) FROM notification_outbox WHERE project_id=?", (project_id,),
            ).fetchone()[0])
            rows = connection.execute(
                "SELECT delivery_id, message_json, receipt_json, file_path FROM notification_outbox "
                "WHERE project_id=? ORDER BY dispatched_at DESC, delivery_id DESC LIMIT ? OFFSET ?",
                (project_id, page_size, (page - 1) * page_size),
            ).fetchall()
        parsed = []
        for row in rows:
            try:
                receipt = OutboxReceipt.model_validate_json(row["receipt_json"])
                message = OutboxMessage.model_validate_json(row["message_json"])
                if (receipt.delivery_id != row["delivery_id"] or receipt.project_id != project_id
                        or message.project_id != project_id or receipt.message_id != message.message_id
                        or receipt.window_id != message.window_id or receipt.channel != message.channel
                        or receipt.status not in {"DELIVERED", "SKIPPED_DUPLICATE", "FAILED"}
                        or receipt.file_path != row["file_path"]):
                    raise ValueError
            except (ValueError, TypeError, ValidationError):
                raise DataIntegrityError("Stored outbox receipt binding is invalid") from None
            parsed.append((receipt, message))
        return parsed, total, "available"

    @staticmethod
    def _read_regular_file_beneath(root: Path, relative: Path) -> bytes | None:
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            return None
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        file_flags = os.O_RDONLY | os.O_NOFOLLOW
        directory_fd = None
        file_fd = None
        try:
            directory_fd = os.open(os.sep, directory_flags)
            for part in root.parts[1:]:
                next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            if not relative.parts or relative.is_absolute() or any(part in {".", ".."} for part in relative.parts):
                return None
            for part in relative.parts[:-1]:
                next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            file_fd = os.open(relative.parts[-1], file_flags, dir_fd=directory_fd)
            metadata = os.fstat(file_fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 2_000_000:
                return None
            chunks: list[bytes] = []
            remaining = 2_000_001
            while remaining > 0:
                chunk = os.read(file_fd, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            return content if len(content) <= 2_000_000 else None
        except (OSError, ValueError):
            return None
        finally:
            if file_fd is not None:
                os.close(file_fd)
            if directory_fd is not None:
                os.close(directory_fd)

    def _safe_outbox_file(self, receipt: OutboxReceipt, message: OutboxMessage, suffix: str = ".json") -> bytes | None:
        if not receipt.file_path or receipt.channel not in {"file", "all"}:
            return None
        if suffix not in {".json", ".md"}:
            return None
        root = Path(self.settings.outbox_dir).absolute()
        source = Path(receipt.file_path)
        if not source.is_absolute():
            source = root / source
        try:
            relative = source.absolute().relative_to(root)
        except ValueError:
            return None
        expected_name = f"{message.project_id}_{message.window_id[:16]}_{receipt.delivery_id}.json"
        if (source.name != expected_name or not relative.parts
                or any(part in {".", ".."} for part in relative.parts)):
            return None
        try:
            stored_json = self._read_regular_file_beneath(root, relative)
            if stored_json is None:
                return None
            stored_message = OutboxMessage.model_validate_json(stored_json)
            if (stored_message.project_id != message.project_id
                    or stored_message.message_id != message.message_id
                    or stored_message.window_id != message.window_id
                    or stored_message.report_id != message.report_id
                    or stored_message.channel != message.channel):
                return None
            if suffix == ".json":
                return stored_json
            markdown_relative = relative.with_suffix(".md")
            markdown = self._read_regular_file_beneath(root, markdown_relative)
            if markdown is None:
                return None
            expected_markdown = (
                f"# {message.subject}\n\n"
                f"**Fecha de despacho:** {receipt.dispatched_at.isoformat()}\n"
                f"**Proyecto:** {message.project_id}\n"
                f"**Ventana semanal:** `{message.window_id}`\n\n"
                f"## Resumen Ejecutivo\n\n{message.summary}\n\n"
                f"## Contenido Detallado\n\n{message.body_markdown}\n"
            ).encode("utf-8")
            return markdown if markdown == expected_markdown else None
        except (TypeError, ValueError, ValidationError):
            return None

    def _report_binding(self, project_id: str, report_id: str) -> dict[str, Any] | None:
        if safe_identifier(report_id) is None:
            return None
        record = self.report_record(project_id, report_id)
        if record is None or not self._report_run_is_bound(project_id, report_id, record["run_id"]):
            return None
        return record

    def _outbox_view(self, receipt: OutboxReceipt, message: OutboxMessage) -> OutboxReceiptSummary:
        report = self._report_binding(receipt.project_id, message.report_id)
        report_id = message.report_id if report is not None else None
        artifact_available = report is not None
        if message.channel in {"file", "all"}:
            artifact_available = artifact_available and self._safe_outbox_file(receipt, message, ".json") is not None
        return OutboxReceiptSummary(
            delivery_id=safe_identifier(receipt.delivery_id) or "unknown",
            message_id=safe_identifier(receipt.message_id) or "unknown",
            project_id=receipt.project_id,
            window_id=safe_identifier(receipt.window_id) or "unknown",
            channel=receipt.channel,
            delivery_channel=receipt.delivery_channel,
            status=receipt.status,
            dispatched_at=_normalized_datetime(receipt.dispatched_at, self.settings.timezone),
            artifact_available=artifact_available,
            report_id=report_id,
        )

    def _outbox_message_view(self, message: OutboxMessage, report_id: str | None) -> OutboxMessageView:
        raw_metrics = message.metrics if isinstance(message.metrics, dict) else {}
        metrics = OutboxMetrics(
            total_tasks=_int_or_none(raw_metrics.get("total_tasks", raw_metrics.get("total_cards"))),
            included_tasks=_int_or_none(raw_metrics.get("included_tasks", raw_metrics.get("included_cards"))),
            completed_tasks=_int_or_none(raw_metrics.get("completed_tasks", raw_metrics.get("completed_cards"))),
            progress_percentage=_float_or_none(raw_metrics.get("progress_percentage")),
            overdue_count=_int_or_none(raw_metrics.get("overdue_count", raw_metrics.get("overdue_count"))),
            due_soon_count=_int_or_none(raw_metrics.get("due_soon_count")),
            blocked_count=_int_or_none(raw_metrics.get("blocked_count")),
            unknown_count=_int_or_none(raw_metrics.get("unknown_count")),
            contradictory_count=_int_or_none(raw_metrics.get("contradictory_count")),
        )
        return OutboxMessageView(
            message_id=safe_identifier(message.message_id) or "unknown",
            project_id=message.project_id,
            window_id=safe_identifier(message.window_id) or "unknown",
            report_id=report_id,
            channel=message.channel,
            subject=self._safe_text(message.subject, 300) or "",
            summary=self._safe_text(message.summary, 5000) or "",
            body_markdown=_safe_markdown(message.body_markdown, secrets_to_redact=self._secrets) or "",
            metrics=metrics,
            risks=[self._safe_text(item, 1000) or "" for item in message.risks],
            recipient=self._safe_text(message.recipient, 128),
            created_at=_normalized_datetime(message.created_at, self.settings.timezone),
        )

    def outbox(
        self, project_id: str, page: int, page_size: int,
    ) -> tuple[list[OutboxReceiptSummary], int, str]:
        rows, total, availability = self._outbox_rows(project_id, page=page, page_size=page_size)
        return [self._outbox_view(receipt, message) for receipt, message in rows], total, availability

    def outbox_detail(self, project_id: str, delivery_id: str) -> OutboxDetail:
        rows, _total, availability = self._outbox_rows(project_id, page=1, page_size=10000)
        if availability == "unknown":
            raise LookupError("outbox_not_found")
        for receipt, message in rows:
            if receipt.delivery_id == delivery_id:
                summary = self._outbox_view(receipt, message)
                return OutboxDetail(
                    receipt=summary,
                    message=self._outbox_message_view(message, summary.report_id),
                )
        raise LookupError("outbox_not_found")

    def outbox_download(self, project_id: str, delivery_id: str, format_name: Literal["markdown", "json"]):
        rows, _total, availability = self._outbox_rows(project_id, page=1, page_size=10000)
        if availability == "unknown":
            raise LookupError("outbox_not_found")
        for receipt, message in rows:
            if receipt.delivery_id != delivery_id:
                continue
            summary = self._outbox_view(receipt, message)
            if summary.report_id is None or not summary.artifact_available:
                raise LookupError("outbox_artifact_unavailable")
            view = self._outbox_message_view(message, summary.report_id)
            if format_name == "json":
                return "application/json; charset=utf-8", f"outbox-{delivery_id}.json", _json_text(view.model_dump(mode="json"))
            markdown_content = self._safe_outbox_file(receipt, message, ".md")
            if markdown_content is not None:
                try:
                    raw = markdown_content.decode("utf-8")
                except UnicodeError:
                    raise LookupError("outbox_artifact_unavailable") from None
                body = _safe_markdown(raw, secrets_to_redact=self._secrets) or ""
            else:
                if message.channel in {"file", "all"}:
                    raise LookupError("outbox_artifact_unavailable")
                body = _safe_markdown(
                    f"# {view.subject}\n\n{view.summary}\n\n{view.body_markdown}",
                    secrets_to_redact=self._secrets,
                ) or ""
            return "text/markdown; charset=utf-8", f"outbox-{delivery_id}.md", body
        raise LookupError("outbox_not_found")

    def audit_records(self, project_id: str) -> tuple[list[tuple[AuditEvent, dict[str, str | int | float | bool | None]]], str]:
        records: list[tuple[AuditEvent, dict[str, str | int | float | bool | None]]] = []
        execution_human_reasons: dict[str, str] = {}
        security_human_reasons: dict[str, str] = {}
        with self._connect() as connection:
            tables = self._tables(connection)
            if connection is None:
                return [], "unknown"
            if "proposal_executions" in tables:
                columns = {row["name"] for row in connection.execute(
                    "PRAGMA table_info(proposal_executions)")}
                if "human_reason" in columns:
                    execution_human_reasons = {
                        str(row["execution_id"]): str(row["human_reason"])
                        for row in connection.execute(
                            "SELECT execution_id, human_reason FROM proposal_executions "
                            "WHERE project_id=? AND human_reason IS NOT NULL", (project_id,),
                        ).fetchall()
                    }
            if "security_audit_log" in tables:
                columns = {row["name"] for row in connection.execute(
                    "PRAGMA table_info(security_audit_log)")}
                if "human_reason" in columns:
                    security_human_reasons = {
                        str(row["event_id"]): str(row["human_reason"])
                        for row in connection.execute(
                            "SELECT event_id, human_reason FROM security_audit_log "
                            "WHERE project_id=? AND human_reason IS NOT NULL", (project_id,),
                        ).fetchall()
                    }
            for row in (connection.execute(
                "SELECT run_id, project_id, status, created_at, report_id FROM runs "
                "WHERE project_id=? ORDER BY created_at DESC LIMIT 10000", (project_id,),
            ).fetchall() if "runs" in tables else []):
                run_id = safe_identifier(str(row["run_id"]))
                if run_id is None:
                    continue
                status = self._safe_text(str(row["status"]), 80) or "unknown"
                report_id = safe_identifier(row["report_id"])
                occurred = _normalized_datetime(datetime.fromisoformat(str(row["created_at"])), self.settings.timezone)
                records.append((AuditEvent(
                    event_id=f"run-{run_id}", project_id=project_id, event_type="analysis_run",
                    occurred_at=occurred, resource_type="run", resource_id=run_id, outcome=status,
                    summary=f"Ejecución {status}.", actor_id=None, actor_verification="unknown", source="engine",
                ), {"status": status, "report_id": report_id}))
            if "human_decisions" in tables and "reports" in tables:
                decision_rows = connection.execute(
                    "SELECT d.id, d.report_id, d.run_id, d.decision, d.reason, d.recorded_at "
                    "FROM human_decisions d JOIN reports r ON r.report_id=d.report_id "
                    "WHERE r.project_id=? ORDER BY d.recorded_at DESC LIMIT 10000", (project_id,),
                ).fetchall()
            else:
                decision_rows = []
            for row in decision_rows:
                event_id = safe_identifier(str(row["id"]))
                report_id = safe_identifier(str(row["report_id"]))
                run_id = safe_identifier(row["run_id"])
                decision = str(row["decision"])
                if event_id is None or report_id is None or decision not in _DECISION_VALUES:
                    continue
                occurred = _normalized_datetime(datetime.fromisoformat(str(row["recorded_at"])), self.settings.timezone)
                reason = self._safe_text(row["reason"], 1000)
                records.append((AuditEvent(
                    event_id=f"decision-{event_id}", project_id=project_id, event_type="report_decision",
                    occurred_at=occurred, resource_type="report", resource_id=report_id, outcome=decision,
                    summary=f"Decisión humana registrada: {decision}.", actor_id=None,
                    actor_verification="unknown", source="engine",
                ), {"decision": decision, "run_id": run_id, "reason": reason}))

        storage = self._read_storage()
        if storage is not None:
            with self._connect() as connection:
                tables = self._tables(connection)
            if "action_proposals" in tables:
                try:
                    for proposal, evidence in storage.list_action_proposals(project_id):
                        state = storage.get_proposal_review_state(project_id, proposal.proposal_id)
                        if state is None:
                            raise DataIntegrityError("Stored proposal review state is unavailable")
                        times, _availability = self._decision_timestamps(project_id, proposal.proposal_id)
                        for event in state[1]:
                            stamp = times.get(event.event_id)
                            if stamp is None:
                                continue
                            event_id = safe_identifier(event.event_id)
                            proposal_id = safe_identifier(event.proposal_id)
                            if event_id is None or proposal_id is None:
                                continue
                            records.append((AuditEvent(
                                event_id="proposal-" + hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:24],
                                project_id=project_id, event_type="proposal_decision",
                                occurred_at=stamp, resource_type="proposal", resource_id=proposal_id,
                                outcome=event.decision, summary=f"Decisión de propuesta: {event.decision}.",
                                actor_id=None, actor_verification="unknown", source="engine",
                            ), {"decision": event.decision, "version": event.expected_version + 1}))
                        if "proposal_executions" in tables:
                            for execution in storage.list_proposal_executions(project_id, proposal.proposal_id):
                                execution_id = safe_identifier(execution.execution_id)
                                if execution_id is None:
                                    continue
                                records.append((AuditEvent(
                                    event_id=execution_id, project_id=project_id, event_type="proposal_execution",
                                    occurred_at=_normalized_datetime(execution.recorded_at, self.settings.timezone),
                                    resource_type="proposal", resource_id=proposal.proposal_id,
                                    outcome=execution.status, summary=f"Intento de ejecución registrado: {execution.status}.",
                                    actor_id=None, actor_verification="unknown", source="engine",
                                ), {"status": execution.status, "verification_status": execution.verification_status,
                                    "field": execution.field,
                                    "human_reason": execution_human_reasons.get(execution_id)}))
                except (sqlite3.Error, ValueError, DataIntegrityError):
                    raise DataIntegrityError("Stored governance audit records are invalid") from None
            if "security_audit_log" in tables:
                try:
                    entries = storage.list_security_audit_events(project_id)
                except (sqlite3.Error, ValueError):
                    raise DataIntegrityError("Stored security audit records are invalid") from None
                for entry in entries:
                    event_id = safe_identifier(entry.event_id)
                    if event_id is None:
                        continue
                    verified_web = (
                        entry.event_id.startswith("web-")
                        and entry.role == "local_operator"
                        and entry.permission_checked in set(_WEB_PERMISSIONS.values())
                        and entry.reason.startswith("request_sha256=")
                        and bool(re.fullmatch(r"sha256:[0-9a-f]{16}", entry.token_fingerprint))
                    )
                    actor = self._safe_text(entry.actor_id, 128) if verified_web else None
                    permission = self._safe_text(entry.permission_checked, 128) or "unknown"
                    records.append((AuditEvent(
                        event_id=event_id, project_id=project_id, event_type="security",
                        occurred_at=_normalized_datetime(entry.recorded_at, self.settings.timezone),
                        resource_type="security", resource_id=safe_identifier(entry.resource_id),
                        outcome=entry.decision, summary=f"Evento de seguridad: {permission} ({entry.decision}).",
                        actor_id=actor, actor_verification=("verified_local_session" if verified_web else "unknown"),
                        source=("web" if verified_web else "engine"),
                    ), {"permission": permission, "decision": entry.decision,
                        "human_reason": security_human_reasons.get(entry.event_id)}))

        outbox_rows, _total, _availability = self._outbox_rows(project_id, page=1, page_size=10000)
        for receipt, message in outbox_rows:
            event_id = safe_identifier(receipt.delivery_id)
            if event_id is None:
                continue
            report = self._report_binding(project_id, message.report_id)
            records.append((AuditEvent(
                event_id=event_id, project_id=project_id, event_type="outbox_delivery",
                occurred_at=_normalized_datetime(receipt.dispatched_at, self.settings.timezone),
                resource_type="delivery", resource_id=event_id, outcome=receipt.status,
                summary=f"Entrega semanal registrada: {receipt.status}.", actor_id=None,
                actor_verification="unknown", source="outbox",
            ), {"status": receipt.status, "channel": receipt.channel,
                "report_id": message.report_id if report is not None else None,
                "window_id": safe_identifier(message.window_id)}))
        with self._connect() as connection:
            available_tables = self._tables(connection)
        available = bool(records) or bool(available_tables & {
            "runs", "human_decisions", "action_proposals", "proposal_decisions",
            "proposal_executions", "notification_outbox", "security_audit_log",
        })
        return sorted(records, key=lambda item: _utc_key(item[0].occurred_at, self.settings.timezone), reverse=True), (
            "available" if available else "unknown"
        )

    def audit_page(
        self, project_id: str, *, page: int, page_size: int, event_type: str | None = None,
        since: datetime | None = None, until: datetime | None = None,
    ) -> tuple[list[AuditEvent], int, str]:
        records, availability = self.audit_records(project_id)
        selected = [event for event, _details in records]
        if event_type is not None:
            selected = [item for item in selected if item.event_type == event_type]
        if since is not None:
            selected = [item for item in selected if _utc_key(item.occurred_at, self.settings.timezone)
                        >= _utc_key(since, self.settings.timezone)]
        if until is not None:
            selected = [item for item in selected if _utc_key(item.occurred_at, self.settings.timezone)
                        <= _utc_key(until, self.settings.timezone)]
        total = len(selected)
        return selected[(page - 1) * page_size:page * page_size], total, availability

    def audit_detail(self, project_id: str, event_id: str) -> AuditEventDetail:
        records, _availability = self.audit_records(project_id)
        for event, details in records:
            if event.event_id == event_id:
                return AuditEventDetail(**event.model_dump(), details=details)
        raise LookupError("audit_event_not_found")

    def _weekly_schedule_view(self, schedule: WeeklySchedule | None):
        if schedule is None:
            return None
        return WeeklyScheduleView(
            project_id=schedule.project_id, weekday=schedule.weekday, local_time=schedule.local_time,
            timezone=schedule.timezone, window_rule=schedule.window_rule,
        )

    def configuration(self, project_id: str, capabilities: list[str] | None = None) -> ProjectConfiguration:
        configured = self.queries.project_config(project_id)
        if configured is None:
            if project_id not in self.queries.project_ids():
                raise LookupError("project_not_found")
            return ProjectConfiguration(
                project_id=project_id, availability="unknown", source_mode="stored",
                timezone=None, due_soon_days=None,
                repository=RepositoryConfiguration(configured=False),
                trello=TrelloConfiguration(configured=False, credential_status="unknown"),
                providers=[], policies=PoliciesConfiguration(configured=None, version=None),
                schedule=None, schedule_status="not_configured",
                capabilities=capabilities or [], editable_fields=[],
            )
        try:
            plan = load_analysis_plan(self.settings, project_id)
        except Exception:
            plan = None
        engine = plan.engine_settings if plan is not None else None
        providers = plan.remote.providers if plan is not None else []
        owner = _safe_component(getattr(engine, "github_owner", None)) if engine is not None else None
        repository_name = _safe_component(
            getattr(engine, "github_repo", None) or getattr(engine, "github_repository", None)
        ) if engine is not None else None
        branch = getattr(engine, "github_branch", None) if engine is not None else None
        if not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9._/-]{1,128}", branch) or ".." in branch.split("/"):
            branch = None
        commit_sha = safe_commit_sha(getattr(engine, "github_commit_sha", None)) if engine is not None else None
        board_id = safe_identifier(getattr(engine, "trello_board_id", None)) if engine is not None else None
        has_trello_credentials = bool(
            engine is not None and getattr(engine, "trello_api_key", None)
            and getattr(engine, "trello_token", None)
        )
        trello_status = "unknown" if engine is None else ("configured" if has_trello_credentials else "missing")
        policy_check = next((item for item in (plan.preflight.checks if plan is not None else [])
                             if item.code == "policy_configuration"), None)
        policies_configured = (
            policy_check.status == "ready" if policy_check is not None
            else (False if engine is not None and configured.source_mode == "real" else None)
        )
        policy_version = getattr(engine, "policy_version", None) if engine is not None else None
        if not isinstance(policy_version, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", policy_version):
            policy_version = None
        configured_schedule = (
            self._weekly_schedule_view(self.settings.weekly_schedule)
            if self.settings.weekly_schedule is not None
            and self.settings.weekly_schedule.project_id == project_id else None
        )
        schedule_status = (
            self.settings.weekly_schedule_status
            if self.settings.weekly_schedule is None or self.settings.weekly_schedule.project_id == project_id
            else "not_configured"
        )
        return ProjectConfiguration(
            project_id=project_id, availability="available" if engine is not None else "unknown",
            source_mode=configured.source_mode, timezone=self.settings.timezone,
            due_soon_days=self.settings.due_soon_days,
            repository=RepositoryConfiguration(
                configured=bool(owner and repository_name), owner=owner, name=repository_name,
                branch=branch, commit_sha=commit_sha,
            ),
            trello=TrelloConfiguration(
                configured=bool(board_id and has_trello_credentials), board_id=board_id,
                credential_status=trello_status,
            ),
            providers=providers,
            policies=PoliciesConfiguration(configured=policies_configured, version=policy_version),
            schedule=configured_schedule, schedule_status=schedule_status,
            capabilities=capabilities or [], editable_fields=[],
        )

    def schedule_settings_view(self, project_id: str):
        return self._weekly_schedule_view(
            self.settings.weekly_schedule
            if self.settings.weekly_schedule is not None
            and self.settings.weekly_schedule.project_id == project_id else None
        )


def _safe_proposal_value(value: object, sanitizer):
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, (list, tuple)):
        return [sanitizer(item, 256) or "" for item in value]
    if isinstance(value, str):
        return sanitizer(value, 1000)
    if hasattr(value, "value") and isinstance(value.value, str):
        return value
    return None


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _float_or_none(value: object) -> float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

