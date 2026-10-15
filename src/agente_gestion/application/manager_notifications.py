"""Compose and dispatch evidence-grounded executive notifications for the manager."""

from datetime import datetime, timezone
import hashlib
import html
import json
from typing import Iterable
from urllib.parse import urlsplit

from agente_gestion.adapters.outbox.hybrid_outbox import HybridNotificationOutboxAdapter
from agente_gestion.adapters.outbox.smtp import SMTPConfig, validate_email_address
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.application.tasks_query import TaskPlanSummary, build_task_plan_summary
from agente_gestion.domain.notification_outbox import OutboxMessage, OutboxReceipt


DEFAULT_MANAGER_WEB_URL = "http://127.0.0.1:5173/"


class ManagerNotificationService:
    """Build manager reports exclusively from persisted snapshots, proposals and reports.

    ``send_project_email`` is the public application helper for a finished orchestrator
    run. Callers supply a stable run-derived idempotency key; API callers should instead
    bind their key to the authenticated session before creating the message.
    """

    def __init__(
        self,
        storage: SQLiteStorageAdapter,
        outbox: HybridNotificationOutboxAdapter | None = None,
        *,
        settings: object | None = None,
        smtp_config: SMTPConfig | None = None,
        web_url: str = DEFAULT_MANAGER_WEB_URL,
    ) -> None:
        self.storage = storage
        self.settings = settings
        self.smtp_config = smtp_config or (
            SMTPConfig.from_settings(settings) if settings is not None else SMTPConfig()
        )
        self.web_url = self._safe_web_url(web_url)
        outbox_dir = getattr(settings, "outbox_dir", ".runtime/outbox")
        self.outbox = outbox or HybridNotificationOutboxAdapter(
            storage, outbox_dir, smtp_config=self.smtp_config,
        )
        self.due_soon_days = getattr(settings, "due_soon_days", 7)
        self.timezone_name = getattr(settings, "timezone", "America/Guayaquil")

    def compose_message(
        self,
        project_id: str,
        *,
        recipient_email: str,
        window_id: str,
        delivery_key: str,
        delivery_fingerprint: str,
        message_id: str | None = None,
    ) -> OutboxMessage:
        """Compose plain and HTML bodies from the latest stored project evidence."""
        recipient = validate_email_address(recipient_email)
        if not window_id.strip():
            raise ValueError("El identificador de ventana no puede estar vacío.")
        capture = self._latest_capture(project_id)
        summary = build_task_plan_summary(
            capture.snapshot,
            capture.capture_id,
            due_soon_days=self.due_soon_days,
            timezone_name=self.timezone_name,
        )
        pending_proposals = self._pending_proposals(project_id)
        quality_suggestions = self._quality_suggestions(project_id)
        latest_report = self.storage.get_latest_report(project_id)
        report_id = (
            latest_report.report_id if latest_report and latest_report.report_id
            else "rep-manager-" + hashlib.sha256(f"{project_id}:{window_id}".encode()).hexdigest()[:16]
        )
        subject = f"Reporte ejecutivo de proyecto: {project_id}"
        plain = self._plain_body(summary, pending_proposals, quality_suggestions, self.web_url)
        summary_text = self._summary_text(summary)
        html_body = self._html_body(
            project_id, summary, pending_proposals, quality_suggestions, summary_text, self.web_url,
        )
        return OutboxMessage(
            message_id=message_id or f"msg-email-{delivery_key[:24]}",
            project_id=project_id,
            window_id=window_id,
            report_id=report_id,
            channel="all",
            subject=subject,
            summary=summary_text,
            body_markdown=plain,
            body_html=html_body,
            metrics=summary.model_dump(mode="json"),
            risks=list(summary.identified_risks),
            recipient=recipient,
            delivery_key=delivery_key,
            delivery_fingerprint=delivery_fingerprint,
        )

    def send_project_email(
        self,
        project_id: str,
        *,
        recipient_email: str,
        idempotency_key: str,
    ) -> OutboxReceipt:
        """Send a composed report; intended for a finished orchestrator run."""
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("Se requiere una clave de idempotencia para el envío.")
        delivery_key = hashlib.sha256(
            f"manager-email\0{project_id}\0{idempotency_key}".encode("utf-8")
        ).hexdigest()
        fingerprint = self.request_fingerprint(project_id, recipient_email, None)
        window_id = "manual-" + delivery_key[:32]
        message = self.compose_message(
            project_id,
            recipient_email=recipient_email,
            window_id=window_id,
            delivery_key=delivery_key,
            delivery_fingerprint=fingerprint,
        )
        return self.outbox.deliver(message)

    @staticmethod
    def request_fingerprint(
        project_id: str, recipient_email: str | None, outbox_delivery_id: str | None,
    ) -> str:
        canonical = json.dumps({
            "project_id": project_id,
            "recipient_email": recipient_email,
            "outbox_delivery_id": outbox_delivery_id,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def message_from_receipt(
        self,
        source: OutboxMessage,
        *,
        recipient_email: str,
        window_id: str,
        delivery_key: str,
        delivery_fingerprint: str,
    ) -> OutboxMessage:
        """Create a new email attempt from an already project-scoped outbox message."""
        recipient = validate_email_address(recipient_email)
        subject = source.subject if source.subject else f"Reporte ejecutivo: {source.project_id}"
        plain = source.body_markdown
        if source.summary and source.summary not in plain:
            plain = f"{source.summary}\n\n{plain}"
        html_body = self._html_from_existing(source, subject, self.web_url)
        return source.model_copy(update={
            "message_id": f"msg-email-{delivery_key[:24]}",
            "window_id": window_id,
            "subject": subject,
            "body_markdown": plain,
            "body_html": html_body,
            "recipient": recipient,
            "delivery_key": delivery_key,
            "delivery_fingerprint": delivery_fingerprint,
        })

    def with_email_delivery(
        self,
        message: OutboxMessage,
        *,
        recipient_email: str,
        delivery_key: str,
        delivery_fingerprint: str,
    ) -> OutboxMessage:
        """Add SMTP/local-email delivery to a legacy scheduled report message."""
        recipient = validate_email_address(recipient_email)
        proposals = self._pending_proposals(message.project_id)
        suggestions = self._quality_suggestions(message.project_id)
        supplemental = self._supplemental_body(proposals, suggestions, self.web_url)
        plain = f"{message.body_markdown}\n\n{supplemental}"
        html_body = self._html_from_scheduled(message, proposals, suggestions, self.web_url)
        return message.model_copy(update={
            "recipient": recipient,
            "body_markdown": plain,
            "body_html": html_body,
            "delivery_key": delivery_key,
            "delivery_fingerprint": delivery_fingerprint,
        })

    def _latest_capture(self, project_id: str):
        captures = self.storage.list_snapshot_captures(project_id)
        if not captures:
            raise LookupError("capture_not_found")
        return captures[-1]

    def _pending_proposals(self, project_id: str) -> list[str]:
        try:
            proposals = self.storage.list_action_proposals(project_id)
        except (NotImplementedError, LookupError):
            return []
        pending: list[str] = []
        for proposal, _evidence in proposals:
            try:
                review = self.storage.get_proposal_review_state(project_id, proposal.proposal_id)
            except NotImplementedError:
                review = None
            if review is not None and review[2].review_status != "pending":
                continue
            value = self._display_value(proposal.proposed_value)
            pending.append(
                f"{proposal.proposal_id} — tarea {proposal.task_id}: "
                f"{proposal.field} → {value}; {proposal.rationale}"
            )
        return pending

    def _quality_suggestions(self, project_id: str) -> list[str]:
        report = self.storage.get_latest_report(project_id)
        if report is None:
            return []
        suggestions: list[str] = []
        for finding in getattr(report, "candidate_findings", ()) or ():
            proposal = (
                getattr(finding, "suggested_fix", None)
                or getattr(finding, "suggested_code", None)
            )
            if proposal:
                claim = getattr(finding, "claim", None) or getattr(finding, "id", "Hallazgo")
                path = getattr(finding, "file_path", None) or "Archivo no disponible"
                ready = "Código ejemplo listo para revisar" if getattr(finding, "suggested_code", None) else "Código ejemplo no disponible (corrida antigua)"
                suggestions.append(f"{path} — {claim}: {ready}. {proposal}")
        for recommendation in getattr(report, "recommendations", ()) or ():
            if recommendation:
                text = str(recommendation)
                if text not in suggestions:
                    suggestions.append(text)
        return suggestions

    @staticmethod
    def _display_value(value: object) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return str(value)

    @staticmethod
    def _summary_text(summary: TaskPlanSummary) -> str:
        progress = (
            f"{summary.progress_percentage:.1f}%" if summary.progress_percentage is not None
            else "No disponible"
        )
        return (
            f"Avance global: {progress}; completadas: {summary.completed_tasks}; "
            f"en ejecución: {summary.state_counts.get('in_progress', 0)}; "
            f"retrasadas: {summary.overdue_count}; bloqueadas: {summary.blocked_count}."
        )

    @classmethod
    def _plain_body(
        cls, summary: TaskPlanSummary, proposals: list[str], suggestions: list[str], web_url: str,
    ) -> str:
        progress = (
            f"{summary.progress_percentage:.1f}%" if summary.progress_percentage is not None
            else "No disponible"
        )
        lines = [
            "Reporte ejecutivo de seguimiento del proyecto",
            f"Proyecto: {summary.project_id}",
            f"Progreso global: {progress}",
            f"Completadas: {summary.completed_tasks}",
            f"En ejecución: {summary.state_counts.get('in_progress', 0)}",
            f"Retrasadas: {summary.overdue_count}",
            f"Bloqueadas: {summary.blocked_count}",
            "",
            "Alertas críticas y retrasos:",
        ]
        lines.extend(f"- {risk}" for risk in summary.identified_risks)
        lines.extend(cls._task_alert_lines(summary))
        lines.extend(["", "Propuestas de Trello pendientes:"])
        lines.extend(f"- {item}" for item in proposals)
        lines.extend(["", "Sugerencias de calidad de código:"])
        lines.extend(f"- {item}" for item in suggestions)
        lines.extend(["", f"Portal del proyecto: {web_url}"])
        return "\n".join(lines)

    @staticmethod
    def _task_alert_lines(summary: TaskPlanSummary) -> list[str]:
        result = []
        for label, identifiers in (
            ("Tareas retrasadas", summary.overdue_task_ids),
            ("Tareas bloqueadas", summary.blocked_task_ids),
            ("Próximas a vencer", summary.due_soon_task_ids),
        ):
            if identifiers:
                result.append(f"- {label}: {', '.join(identifiers)}")
        return result

    @classmethod
    def _supplemental_body(cls, proposals: list[str], suggestions: list[str], web_url: str) -> str:
        lines = ["## Propuestas de Trello pendientes"]
        lines.extend(f"- {item}" for item in proposals)
        if not proposals:
            lines.append("- No hay propuestas pendientes registradas.")
        lines.extend(["", "## Sugerencias de calidad de código"])
        lines.extend(f"- {item}" for item in suggestions)
        if not suggestions:
            lines.append("- No hay sugerencias de calidad registradas.")
        lines.extend(["", f"Portal del proyecto: {web_url}"])
        return "\n".join(lines)

    @classmethod
    def _html_body(
        cls,
        project_id: str,
        summary: TaskPlanSummary,
        proposals: list[str],
        suggestions: list[str],
        summary_text: str,
        web_url: str,
    ) -> str:
        progress = (
            f"{summary.progress_percentage:.1f}%" if summary.progress_percentage is not None
            else "No disponible"
        )
        rows = (
            ("Progreso global", progress),
            ("Completadas", str(summary.completed_tasks)),
            ("En ejecución", str(summary.state_counts.get("in_progress", 0))),
            ("Retrasadas", str(summary.overdue_count)),
            ("Bloqueadas", str(summary.blocked_count)),
        )
        table_rows = "".join(
            f"<tr><th>{html.escape(label)}</th><td>{html.escape(value)}</td></tr>"
            for label, value in rows
        )
        alerts = list(summary.identified_risks) + cls._task_alert_lines(summary)
        return cls._html_document(
            f"Reporte ejecutivo — {project_id}",
            f"<p>{html.escape(summary_text)}</p><table>{table_rows}</table>"
            f"{cls._html_list('Alertas críticas y retrasos', alerts)}"
            f"{cls._html_list('Propuestas de Trello pendientes', proposals)}"
            f"{cls._html_list('Sugerencias de calidad de código', suggestions)}"
            f"<p><a href=\"{html.escape(web_url, quote=True)}\">"
            "Abrir portal del proyecto</a></p>",
        )

    @classmethod
    def _html_from_existing(cls, message: OutboxMessage, subject: str, web_url: str) -> str:
        content = (
            f"<p>{html.escape(message.summary)}</p>"
            f"<pre>{html.escape(message.body_markdown)}</pre>"
            f"<p><a href=\"{html.escape(web_url, quote=True)}\">"
            "Abrir portal del proyecto</a></p>"
        )
        return cls._html_document(subject, content)

    @classmethod
    def _html_from_scheduled(
        cls, message: OutboxMessage, proposals: list[str], suggestions: list[str], web_url: str,
    ) -> str:
        content = (
            f"<p>{html.escape(message.summary)}</p>"
            f"<pre>{html.escape(message.body_markdown)}</pre>"
            f"{cls._html_list('Propuestas de Trello pendientes', proposals)}"
            f"{cls._html_list('Sugerencias de calidad de código', suggestions)}"
            f"<p><a href=\"{html.escape(web_url, quote=True)}\">"
            "Abrir portal del proyecto</a></p>"
        )
        return cls._html_document(message.subject, content)

    @staticmethod
    def _html_list(title: str, values: Iterable[str]) -> str:
        items = list(values)
        if not items:
            return f"<section><h2>{html.escape(title)}</h2><p>No hay registros disponibles.</p></section>"
        entries = "".join(f"<li>{html.escape(value)}</li>" for value in items)
        return f"<section><h2>{html.escape(title)}</h2><ul>{entries}</ul></section>"

    @staticmethod
    def _html_document(title: str, content: str) -> str:
        return (
            "<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\">"
            f"<title>{html.escape(title)}</title>"
            "<style>body{font:16px sans-serif;line-height:1.5;color:#17202a;max-width:850px;"
            "margin:2rem auto;padding:0 1rem}table{border-collapse:collapse}th,td{padding:.4rem .8rem;"
            "border:1px solid #ccd1d1;text-align:left}h1,h2{color:#154360}</style></head>"
            f"<body><main><h1>{html.escape(title)}</h1>{content}</main></body></html>"
        )

    @staticmethod
    def _safe_web_url(value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("La URL web del proyecto no es válida.")
        return value
