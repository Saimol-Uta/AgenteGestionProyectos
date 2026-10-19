"""Query service and structured views for project task planning and monitoring.

Adheres strictly to domain models and acceptance criteria:
- Reuses TaskCard, WeeklySnapshot, and calculate_task_metrics.
- Normalizes states and dates according to America/Guayaquil timezone.
- Never infers priority from labels (unknown remains None).
- Detects explicit dependencies and impacts only when grounded by explicit evidence.
- Supports structured JSON export and human-readable terminal rendering.
"""

from datetime import datetime
import re
from typing import Optional
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field

from agente_gestion.domain.risks import RiskKind, TaskRisk, risks_from_metrics
from agente_gestion.domain.tasks import (
    DEFAULT_TIMEZONE,
    TaskCard,
    TaskState,
    calculate_task_metrics,
    normalize_datetime_to_tz,
    validate_due_soon_days,
)
from agente_gestion.domain.snapshot import WeeklySnapshot

__all__ = [
    "RiskKind",
    "TaskFilter",
    "TaskDetailView",
    "TaskPlanSummary",
    "TaskRisk",
    "build_task_plan_summary",
    "extract_explicit_dependencies",
    "format_plan_summary",
    "format_task_detail",
    "format_tasks_table",
    "query_tasks_view",
]


class TaskFilter(BaseModel):
    """Filters applicable when querying snapshot tasks."""
    state: Optional[TaskState] = None
    critical_only: bool = False
    assignee_id: Optional[str] = None
    list_name: Optional[str] = None
    due_soon_days: int = 7


class TaskDetailView(BaseModel):
    """Detailed projection of a task card for planning and status inspection."""
    id: str
    title: str
    state: TaskState
    list_id: Optional[str] = None
    list_name: Optional[str] = None
    assignee_ids: list[str] = Field(default_factory=list)
    start_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    due_complete: Optional[bool] = None
    priority: Optional[str] = None
    labels: list[str] = Field(default_factory=list)
    is_included: bool = True
    is_archived: Optional[bool] = None
    has_contradiction: bool = False
    detail: Optional[str] = None
    last_activity_at: Optional[datetime] = None
    observed_fields: Optional[list[str]] = None
    is_overdue: bool = False
    is_due_soon: bool = False
    is_critical: bool = False
    explicit_dependencies: list[str] = Field(default_factory=list)
    dependency_impacts: list[str] = Field(default_factory=list)


class TaskPlanSummary(BaseModel):
    """Aggregated project planning summary computed deterministically from snapshot."""
    project_id: str
    capture_id: str
    captured_at: datetime
    cutoff_datetime: datetime
    total_tasks: int
    included_tasks: int
    completed_tasks: int
    progress_percentage: Optional[float] = None
    state_counts: dict[str, int] = Field(default_factory=dict)
    overdue_count: int = 0
    due_soon_count: int = 0
    blocked_count: int = 0
    unknown_count: int = 0
    contradictory_count: int = 0
    overdue_task_ids: list[str] = Field(default_factory=list)
    due_soon_task_ids: list[str] = Field(default_factory=list)
    blocked_task_ids: list[str] = Field(default_factory=list)
    unknown_task_ids: list[str] = Field(default_factory=list)
    contradictory_task_ids: list[str] = Field(default_factory=list)
    identified_risks: list[str] = Field(default_factory=list)
    risks: list[TaskRisk] = Field(default_factory=list)


# Pattern matching explicit dependency declarations in card detail or labels
_DEP_PATTERN = re.compile(
    r"(?:dep(?:ends?[ _-]?on)?|depende(?:[ _-]?de)?|bloquead[oa][ _-]?por|blocked[ _-]?by|requiere)"
    r"[:\s]+([a-zA-Z0-9_-]{8,32})",
    re.IGNORECASE,
)


def extract_explicit_dependencies(
    card: TaskCard,
    all_cards: dict[str, TaskCard],
) -> tuple[list[str], list[str]]:
    """
    Extracts explicit task dependencies from card details or labels only when
    there is direct evidence. Never infers unrecorded dependencies.

    Returns:
        (explicit_dependencies, dependency_impacts)
    """
    candidates: set[str] = set()

    sources: list[str] = []
    if card.detail:
        sources.append(card.detail)
    for label in card.labels:
        sources.append(label)

    for text in sources:
        for match in _DEP_PATTERN.finditer(text):
            ref_id = match.group(1).strip()
            if ref_id in all_cards and ref_id != card.id:
                candidates.add(ref_id)

    deps = sorted(candidates)
    impacts: list[str] = []

    for dep_id in deps:
        dep_card = all_cards[dep_id]
        if dep_card.state == TaskState.blocked:
            impacts.append(
                f"Dependencia '{dep_card.title}' ({dep_id}) está bloqueada; detiene el inicio/avance."
            )
        elif dep_card.state == TaskState.unknown:
            impacts.append(
                f"Dependencia '{dep_card.title}' ({dep_id}) tiene estado desconocido; riesgo de planificación."
            )
        elif dep_card.state != TaskState.completed:
            impacts.append(
                f"Dependencia '{dep_card.title}' ({dep_id}) no completada ({dep_card.state.value}); requiere seguimiento."
            )

    return deps, impacts


def build_task_plan_summary(
    snapshot: WeeklySnapshot,
    capture_id: str,
    due_soon_days: int = 7,
    timezone_name: str = "America/Guayaquil",
) -> TaskPlanSummary:
    """Computes high-level planning metrics and risks for a snapshot."""
    validate_due_soon_days(due_soon_days)
    metrics = calculate_task_metrics(
        cards=snapshot.tasks,
        cutoff=snapshot.cutoff_datetime,
        timezone_name=timezone_name,
        due_soon_days=due_soon_days,
    )

    state_counts: dict[str, int] = {st.value: 0 for st in TaskState}
    for card in snapshot.tasks:
        state_counts[card.state.value] = state_counts.get(card.state.value, 0) + 1

    risks = risks_from_metrics(metrics, snapshot.project_id, capture_id, due_soon_days)
    identified_risks = [risk.summary for risk in risks]

    return TaskPlanSummary(
        project_id=snapshot.project_id,
        capture_id=capture_id,
        captured_at=snapshot.captured_at,
        cutoff_datetime=snapshot.cutoff_datetime,
        total_tasks=metrics.total_cards,
        included_tasks=metrics.included_cards,
        completed_tasks=metrics.completed_cards,
        progress_percentage=metrics.progress_percentage,
        state_counts=state_counts,
        overdue_count=len(metrics.overdue_card_ids),
        due_soon_count=len(metrics.due_soon_card_ids),
        blocked_count=len(metrics.blocked_card_ids),
        unknown_count=len(metrics.unknown_card_ids),
        contradictory_count=len(metrics.contradictory_card_ids),
        overdue_task_ids=metrics.overdue_card_ids,
        due_soon_task_ids=metrics.due_soon_card_ids,
        blocked_task_ids=metrics.blocked_card_ids,
        unknown_task_ids=metrics.unknown_card_ids,
        contradictory_task_ids=metrics.contradictory_card_ids,
        identified_risks=identified_risks,
        risks=risks,
    )


def query_tasks_view(
    snapshot: WeeklySnapshot,
    capture_id: str,
    filters: Optional[TaskFilter] = None,
    timezone_name: str = "America/Guayaquil",
) -> tuple[TaskPlanSummary, list[TaskDetailView]]:
    """
    Evaluates all cards in snapshot against deadline metrics, explicit dependencies,
    and returns planning summary alongside filtered task views.
    """
    f = filters or TaskFilter()
    summary = build_task_plan_summary(
        snapshot=snapshot,
        capture_id=capture_id,
        due_soon_days=f.due_soon_days,
        timezone_name=timezone_name,
    )

    overdue_set = set(summary.overdue_task_ids)
    due_soon_set = set(summary.due_soon_task_ids)
    all_cards = {card.id: card for card in snapshot.tasks}

    views: list[TaskDetailView] = []
    for card in snapshot.tasks:
        is_overdue = card.id in overdue_set
        is_due_soon = card.id in due_soon_set
        is_critical = (
            is_overdue
            or is_due_soon
            or card.state == TaskState.blocked
            or card.has_contradiction
        )

        deps, impacts = extract_explicit_dependencies(card, all_cards)

        view = TaskDetailView(
            id=card.id,
            title=card.title,
            state=card.state,
            list_id=card.list_id,
            list_name=card.list_name,
            assignee_ids=card.assignee_ids or [],
            start_date=card.start_date,
            due_date=card.due_date,
            due_complete=card.due_complete,
            priority=card.priority,
            labels=card.labels,
            is_included=card.is_included,
            is_archived=card.is_archived,
            has_contradiction=card.has_contradiction,
            detail=card.detail,
            last_activity_at=card.last_activity_at,
            observed_fields=card.observed_fields,
            is_overdue=is_overdue,
            is_due_soon=is_due_soon,
            is_critical=is_critical,
            explicit_dependencies=deps,
            dependency_impacts=impacts,
        )

        # Apply filtering
        if f.state is not None and view.state != f.state:
            continue
        if f.critical_only and not view.is_critical:
            continue
        if f.assignee_id is not None and f.assignee_id not in view.assignee_ids:
            continue
        if f.list_name is not None and (view.list_name or "").lower() != f.list_name.lower():
            continue

        views.append(view)

    return summary, views


def format_plan_summary(summary: TaskPlanSummary) -> str:
    """Formats TaskPlanSummary as a structured readable terminal block."""
    cutoff_str = summary.cutoff_datetime.strftime("%Y-%m-%d %H:%M:%S %Z")
    pct_str = f"{summary.progress_percentage:.1f}%" if summary.progress_percentage is not None else "N/D"

    lines = [
        "=" * 70,
        f"PLANIFICACIÓN Y ESTADO DE TAREAS — PROYECTO: {summary.project_id}",
        "=" * 70,
        f"ID de captura:     {summary.capture_id}",
        f"Fecha de corte:    {cutoff_str}",
        f"Progreso global:   {pct_str} ({summary.completed_tasks} de {summary.included_tasks} computables completadas)",
        f"Total tarjetas:    {summary.total_tasks}",
        "-" * 70,
        "Distribución por estado normalizado:",
    ]
    for st_name, count in summary.state_counts.items():
        lines.append(f"  - {st_name:<16}: {count}")

    lines.extend([
        "-" * 70,
        "Actividades críticas identificadas:",
        f"  - Vencidas (overdue):       {summary.overdue_count}",
        f"  - Próximas a vencer:        {summary.due_soon_count}",
        f"  - Bloqueadas (blocked):     {summary.blocked_count}",
        f"  - Señales contradictorias:  {summary.contradictory_count}",
        f"  - Estado desconocido:       {summary.unknown_count}",
    ])

    if summary.identified_risks:
        lines.append("-" * 70)
        lines.append("Riesgos detectados:")
        for r in summary.identified_risks:
            lines.append(f"  [!] {r}")

    lines.append("=" * 70)
    return "\n".join(lines)


def format_tasks_table(tasks: list[TaskDetailView]) -> str:
    """Renders a formatted text table of task views."""
    if not tasks:
        return "No se encontraron tareas que coincidan con los criterios de búsqueda.\n"

    header = (
        f"{'ID':<12} {'ESTADO':<12} {'FASE / LISTA':<24} "
        f"{'VENCIMIENTO':<16} {'PRIORIDAD':<10} {'ALERTAS':<16} {'TÍTULO'}"
    )
    separator = "-" * len(header)
    lines = [header, separator]

    for t in tasks:
        alerts = []
        if t.is_overdue:
            alerts.append("VENCIDA")
        if t.is_due_soon:
            alerts.append("PRÓXIMA")
        if t.state == TaskState.blocked:
            alerts.append("BLOQUEADA")
        if t.has_contradiction:
            alerts.append("CONTRADICCIÓN")
        if t.state == TaskState.unknown:
            alerts.append("DESCONOCIDO")

        alerts_str = ",".join(alerts) if alerts else "-"
        due_str = t.due_date.strftime("%Y-%m-%d") if t.due_date else "-"
        prio_str = t.priority or "-"
        list_str = (t.list_name or "-")[:22]
        title_str = t.title[:45] + ("..." if len(t.title) > 45 else "")
        short_id = t.id[:10]

        lines.append(
            f"{short_id:<12} {t.state.value:<12} {list_str:<24} "
            f"{due_str:<16} {prio_str:<10} {alerts_str:<16} {title_str}"
        )

    return "\n".join(lines) + f"\n\nTotal mostradas: {len(tasks)}\n"


def format_task_detail(task: TaskDetailView) -> str:
    """Renders complete detailed view of a single task card."""
    lines = [
        "=" * 70,
        f"DETALLE DE TAREA: {task.id}",
        "=" * 70,
        f"Título:             {task.title}",
        f"Estado:             {task.state.value}",
        f"Fase / Lista:       {task.list_name or '-'} ({task.list_id or 'N/D'})",
        f"Responsables:       {', '.join(task.assignee_ids) if task.assignee_ids else 'Sin asignar'}",
        f"Inicio:             {task.start_date.isoformat() if task.start_date else 'No registrado'}",
        f"Vencimiento:        {task.due_date.isoformat() if task.due_date else 'Sin fecha'}",
        f"Due Complete:       {task.due_complete if task.due_complete is not None else 'N/D'}",
        f"Prioridad:          {task.priority or 'No especificada (null)'}",
        f"Etiquetas:          {', '.join(task.labels) if task.labels else 'Ninguna'}",
        f"Computable avance:  {'Sí' if task.is_included else 'No (excluida de denominador)'}",
        f"Archivada:          {'Sí' if task.is_archived else 'No'}",
        f"Contradicción:      {'Sí (señales incompatibles)' if task.has_contradiction else 'No'}",
        f"Última actividad:   {task.last_activity_at.isoformat() if task.last_activity_at else 'N/D'}",
    ]

    lines.append("-" * 70)
    lines.append("Alertas temporales y de estado:")
    lines.append(f"  - Vencida al corte:  {'SÍ' if task.is_overdue else 'No'}")
    lines.append(f"  - Próxima a vencer:  {'SÍ' if task.is_due_soon else 'No'}")
    lines.append(f"  - Crítica:           {'SÍ' if task.is_critical else 'No'}")

    lines.append("-" * 70)
    lines.append("Dependencias e impacto (basadas en evidencia explícita):")
    if task.explicit_dependencies:
        lines.append(f"  - Tareas requeridas: {', '.join(task.explicit_dependencies)}")
        if task.dependency_impacts:
            for imp in task.dependency_impacts:
                lines.append(f"    [Impacto] {imp}")
    else:
        lines.append("  - Sin dependencias explícitas declaradas.")

    if task.detail:
        lines.append("-" * 70)
        lines.append(f"Descripción / Detalle:\n{task.detail}")

    if task.observed_fields is not None:
        lines.append("-" * 70)
        lines.append(f"Campos observados explícitamente: {', '.join(task.observed_fields)}")

    lines.append("=" * 70)
    return "\n".join(lines)

