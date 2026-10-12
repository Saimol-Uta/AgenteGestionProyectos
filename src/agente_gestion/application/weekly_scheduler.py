"""Application service orchestrating periodic weekly report scheduling and outbox delivery (H3)."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field

from agente_gestion.adapters.outbox.hybrid_outbox import HybridNotificationOutboxAdapter
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.application.tasks_query import build_task_plan_summary
from agente_gestion.domain.notification_outbox import OutboxMessage, OutboxReceipt
from agente_gestion.domain.weekly_schedule import WeeklySchedule, WeeklyWindow, plan_weekly_window
from agente_gestion.ports.outbox import NotificationOutboxPort

DEFAULT_TZ = ZoneInfo("America/Guayaquil")


def get_default_schedule(
    project_id: str,
    weekday: int = 4,  # Friday by default
    local_time: str = "17:00",
    timezone_name: str = "America/Guayaquil",
) -> WeeklySchedule:
    """Owner-specified weekly schedule definition. Default: Friday 17:00 America/Guayaquil."""
    return WeeklySchedule(
        project_id=project_id,
        weekday=weekday,
        local_time=local_time,
        timezone=timezone_name,
        window_rule="previous_scheduled_week",
    )


class ScheduleStatus(BaseModel):
    """Evaluation status of weekly schedule against evaluation moment."""
    project_id: str
    window: WeeklyWindow
    is_due: bool
    is_dispatched: bool
    can_run_scheduled: bool
    last_receipt: Optional[OutboxReceipt] = None
    evaluated_at: datetime


class ScheduledCycleResult(BaseModel):
    """Result of attempting or executing a scheduled reporting cycle."""
    project_id: str
    status: Literal["EXECUTED", "SKIPPED_NOT_DUE", "SKIPPED_ALREADY_DISPATCHED", "FAILED"]
    window_id: str
    receipt: Optional[OutboxReceipt] = None
    message: Optional[OutboxMessage] = None
    reason: Optional[str] = None


class WeeklySchedulerService:
    """
    Coordinates evaluation of weekly cutoff windows, aggregation of comprehensive reports,
    and idempotent notification delivery.
    """

    def __init__(
        self,
        storage: SQLiteStorageAdapter,
        outbox: Optional[NotificationOutboxPort] = None,
    ) -> None:
        self.storage = storage
        self.outbox = outbox or HybridNotificationOutboxAdapter(storage)

    def check_schedule(
        self,
        project_id: str,
        schedule: WeeklySchedule,
        eval_time: Optional[datetime] = None,
        trigger_window_hours: float = 24.0,
    ) -> ScheduleStatus:
        """Evaluate schedule against current or supplied evaluation datetime."""
        now = eval_time or datetime.now(DEFAULT_TZ)
        if now.tzinfo is None:
            now = now.replace(tzinfo=DEFAULT_TZ)

        window = plan_weekly_window(schedule, now)
        last_receipt = self.outbox.get_receipt_for_window(project_id, window.window_id)
        is_dispatched = last_receipt is not None

        # Check if current local time is within trigger window of elapsed boundary
        local_now = now.astimezone(ZoneInfo(schedule.timezone))
        delta_seconds = (local_now - window.elapsed_boundary_local).total_seconds()
        is_due = (0 <= delta_seconds <= trigger_window_hours * 3600)
        can_run = is_due and not is_dispatched

        return ScheduleStatus(
            project_id=project_id,
            window=window,
            is_due=is_due,
            is_dispatched=is_dispatched,
            can_run_scheduled=can_run,
            last_receipt=last_receipt,
            evaluated_at=now,
        )

    def run_scheduled_cycle(
        self,
        project_id: str,
        schedule: WeeklySchedule,
        eval_time: Optional[datetime] = None,
        force: bool = False,
        channel: Literal["file", "sqlite", "console", "all"] = "all",
        due_soon_days: int = 7,
    ) -> ScheduledCycleResult:
        """
        Execute scheduled reporting cycle:
        - If not force and not due: returns SKIPPED_NOT_DUE.
        - If not force and already dispatched: returns SKIPPED_ALREADY_DISPATCHED.
        - With force=True: executes immediately regardless of boundary or prior dispatches.
        """
        status = self.check_schedule(project_id, schedule, eval_time)
        window = status.window

        if not force:
            if not status.is_due:
                return ScheduledCycleResult(
                    project_id=project_id,
                    status="SKIPPED_NOT_DUE",
                    window_id=window.window_id,
                    reason=(
                        f"La fecha/hora actual ({status.evaluated_at.isoformat()}) aún no alcanza el corte semanal programado "
                        f"({window.elapsed_boundary_local.isoformat()}). Usa --force para ejecutar la prueba inmediatamente."
                    ),
                )
            if status.is_dispatched:
                return ScheduledCycleResult(
                    project_id=project_id,
                    status="SKIPPED_ALREADY_DISPATCHED",
                    window_id=window.window_id,
                    receipt=status.last_receipt,
                    reason=(
                        f"El reporte para la ventana semanal {window.window_id[:16]} ya fue despachado previamente "
                        f"({status.last_receipt.delivery_id if status.last_receipt else 'N/D'}). Usa --force para re-despachar."
                    ),
                )

        # 1. Gather Task Planning Metrics from latest snapshot
        captures = self.storage.list_snapshot_captures(project_id)
        if not captures:
            return ScheduledCycleResult(
                project_id=project_id,
                status="FAILED",
                window_id=window.window_id,
                reason=f"No existen capturas de snapshot registradas para el proyecto '{project_id}'.",
            )
        latest_capture = captures[-1]
        task_summary = build_task_plan_summary(
            snapshot=latest_capture.snapshot,
            capture_id=latest_capture.capture_id,
            due_soon_days=due_soon_days,
        )

        # 2. Gather Action Execution Ledger Summary
        execution_records = self.storage.list_proposal_executions(project_id)
        ledger_stats = {
            "total_attempts": len(execution_records),
            "executed_count": sum(1 for r in execution_records if r.status == "EXECUTED"),
            "already_applied_count": sum(1 for r in execution_records if r.status == "ALREADY_APPLIED"),
            "conflict_count": sum(1 for r in execution_records if r.status == "CONFLICT"),
            "denied_count": sum(1 for r in execution_records if r.status == "DENIED"),
            "failed_count": sum(1 for r in execution_records if r.status == "FAILED"),
        }

        # 3. Retrieve Latest Final Report if available
        latest_report = self.storage.get_latest_report(project_id)
        report_id = latest_report.report_id if latest_report and latest_report.report_id else f"rep-{window.window_id[:12]}"
        advisory_recommendation = latest_report.recommendation if latest_report else "Sin recomendación de agente previa."
        report_recommendations = latest_report.recommendations if latest_report else []

        # 4. Synthesize Formatted Markdown Report for Outbox
        lines = [
            f"# REPORTE SEMANAL DE SEGUIMIENTO Y CALIDAD — {project_id.upper()}",
            "",
            "## 1. Información del Ciclo y Ventana Semanal",
            f"- **Proyecto:** {project_id}",
            f"- **ID de Ventana Semanal:** `{window.window_id}`",
            f"- **Corte local planificado:** {window.elapsed_boundary_local.strftime('%Y-%m-%d %H:%M:%S %Z')}",
            f"- **Próximo corte programado:** {window.next_boundary_local.strftime('%Y-%m-%d %H:%M:%S %Z')}",
            f"- **Fecha y hora de generación:** {datetime.now(DEFAULT_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
            f"- **Captura utilizada:** `{latest_capture.capture_id[:16]}...`",
            "",
            "## 2. Métricas de Avance y Estado de Tareas (REQ-01, REQ-02)",
            f"- **Progreso global:** {task_summary.progress_percentage:.1f}% ({task_summary.completed_tasks} de {task_summary.included_tasks} computables completadas)",
            f"- **Total tarjetas en tablero:** {task_summary.total_tasks}",
            "- **Distribución por estado:**",
        ]
        for st_name, st_count in task_summary.state_counts.items():
            lines.append(f"  * `{st_name}`: {st_count}")

        lines.extend([
            "",
            f"## 3. Riesgos y Alertas Críticas (REQ-04, ventana {due_soon_days} días)",
            f"- **Tareas vencidas:** {task_summary.overdue_count}",
            f"- **Próximas a vencer ({due_soon_days} días):** {task_summary.due_soon_count} ({', '.join(task_summary.due_soon_task_ids) if task_summary.due_soon_task_ids else 'Ninguna'})",
            f"- **Tareas bloqueadas:** {task_summary.blocked_count}",
            f"- **Señales contradictorias:** {task_summary.contradictory_count} ({', '.join(task_summary.contradictory_task_ids) if task_summary.contradictory_task_ids else 'Ninguna'})",
            f"- **Estado desconocido:** {task_summary.unknown_count}",
        ])
        if task_summary.identified_risks:
            lines.append("- **Riesgos destacados:**")
            for r in task_summary.identified_risks:
                lines.append(f"  * [!] {r}")

        lines.extend([
            "",
            "## 4. Auditoría de Propuestas y Ejecución de Acciones (REQ-05, REQ-08, REQ-10)",
            f"- **Intentos totales de ejecución registrados:** {ledger_stats['total_attempts']}",
            f"- **Mutaciones ejecutadas y verificadas en vivo:** {ledger_stats['executed_count']}",
            f"- **Operaciones idempotentes (ya aplicadas):** {ledger_stats['already_applied_count']}",
            f"- **Conflictos de concurrencia / drift externo:** {ledger_stats['conflict_count']}",
            f"- **Solicitudes denegadas por autorización/política:** {ledger_stats['denied_count']}",
            "",
            "## 5. Recomendación y Próximos Pasos (REQ-06)",
            f"- **Dictamen de políticas:** `{advisory_recommendation}`",
        ])
        if report_recommendations:
            lines.append("- **Acciones sugeridas:**")
            for rec in report_recommendations:
                lines.append(f"  * {rec}")

        body_md = "\n".join(lines)
        summary_text = (
            f"Reporte semanal {project_id} (corte {window.elapsed_boundary_local.strftime('%Y-%m-%d %H:%M')}). "
            f"Avance: {task_summary.progress_percentage:.1f}%. Riesgos: {len(task_summary.identified_risks)}. "
            f"Acciones ejecutadas: {ledger_stats['executed_count']}."
        )

        # 5. Build Outbox Message and Deliver
        msg_id = f"msg-{window.window_id[:12]}-{int(datetime.now(timezone.utc).timestamp())}"
        message = OutboxMessage(
            message_id=msg_id,
            project_id=project_id,
            window_id=window.window_id,
            report_id=report_id,
            channel=channel,
            subject=f"Reporte Semanal de Proyecto: {project_id} ({window.elapsed_boundary_local.strftime('%Y-%m-%d')})",
            summary=summary_text,
            body_markdown=body_md,
            metrics=task_summary.model_dump(),
            risks=list(task_summary.identified_risks),
            action_ledger_summary=ledger_stats,
            recipient="gestor_proyecto",
        )

        receipt = self.outbox.deliver(message, force=force)
        return ScheduledCycleResult(
            project_id=project_id,
            status="EXECUTED" if receipt.status == "DELIVERED" else "SKIPPED_ALREADY_DISPATCHED",
            window_id=window.window_id,
            receipt=receipt,
            message=message,
        )
