"""Deterministic task risks shared by the read model, the orchestrator and validation.

One projection, one source of truth: a risk is computed from
``DeterministicTaskMetrics`` and never from narrative text, so the API, the
proposal builder and the proposal validation always agree on what a risk is.
"""

from enum import Enum
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.domain.tasks import (
    DEFAULT_TIMEZONE,
    DeterministicTaskMetrics,
    calculate_task_metrics,
    validate_due_soon_days,
)


class RiskKind(str, Enum):
    """Deterministic risk families derived from task metrics only."""
    blocked = "blocked"
    overdue = "overdue"
    due_soon = "due_soon"
    contradictory_state = "contradictory_state"
    unknown_state = "unknown_state"


class TaskRisk(BaseModel):
    """One deterministic risk bound to a capture and its affected tasks."""
    risk_id: str
    project_id: str
    capture_id: str
    kind: RiskKind
    task_ids: list[str] = Field(default_factory=list)
    task_count: int = 0
    summary: str
    basis: str
    source: Literal["deterministic"] = "deterministic"


class ProposalRiskRef(BaseModel):
    """Reference to one concrete risk resolved against a capture and a task."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    risk_id: StrictStr
    kind: RiskKind
    task_id: StrictStr
    capture_id: StrictStr


def risk_identifier(project_id: str, capture_id: str, kind: RiskKind, task_ids: list[str]) -> str:
    """Stable identifier for the same capture, kind and affected task set."""
    canonical = json.dumps(
        [project_id, capture_id, kind.value, sorted(task_ids)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "risk-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def risks_from_metrics(
    metrics: DeterministicTaskMetrics,
    project_id: str,
    capture_id: str,
    due_soon_days: int = 7,
) -> list[TaskRisk]:
    """Project deterministic metrics into the stable risk representation."""
    validate_due_soon_days(due_soon_days)
    specs: list[tuple[RiskKind, list[str], str, str]] = []
    if metrics.blocked_card_ids:
        specs.append((
            RiskKind.blocked, metrics.blocked_card_ids,
            f"{len(metrics.blocked_card_ids)} tarea(s) bloqueada(s) activa(s).",
            "Tareas en estado bloqueado observado en la captura.",
        ))
    if metrics.overdue_card_ids:
        specs.append((
            RiskKind.overdue, metrics.overdue_card_ids,
            f"{len(metrics.overdue_card_ids)} tarea(s) vencida(s) no completada(s).",
            "Tareas no completadas cuyo vencimiento es anterior al corte de la captura.",
        ))
    if metrics.due_soon_card_ids:
        specs.append((
            RiskKind.due_soon, metrics.due_soon_card_ids,
            f"{len(metrics.due_soon_card_ids)} tarea(s) próxima(s) a vencer en la ventana de {due_soon_days} días.",
            f"Tareas no completadas cuyo vencimiento cae dentro de los {due_soon_days} días posteriores al corte.",
        ))
    if metrics.contradictory_card_ids:
        specs.append((
            RiskKind.contradictory_state, metrics.contradictory_card_ids,
            f"{len(metrics.contradictory_card_ids)} tarea(s) con señales contradictorias de estado.",
            "Tareas con señales contradictorias de estado en la captura.",
        ))
    if metrics.unknown_card_ids:
        specs.append((
            RiskKind.unknown_state, metrics.unknown_card_ids,
            f"{len(metrics.unknown_card_ids)} tarea(s) con estado desconocido (falta mapeo o evidencia).",
            "Tareas cuyo estado no pudo mapearse desde la fuente; no se infiere un estado.",
        ))
    return [
        TaskRisk(
            risk_id=risk_identifier(project_id, capture_id, kind, task_ids),
            project_id=project_id,
            capture_id=capture_id,
            kind=kind,
            task_ids=list(task_ids),
            task_count=len(task_ids),
            summary=summary,
            basis=basis,
        )
        for kind, task_ids, summary, basis in specs
    ]


def build_task_risks(
    snapshot: WeeklySnapshot,
    capture_id: str,
    *,
    due_soon_days: int = 7,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> list[TaskRisk]:
    """Compute the metrics of one snapshot and project them into risks."""
    metrics = calculate_task_metrics(
        cards=snapshot.tasks,
        cutoff=snapshot.cutoff_datetime,
        timezone_name=timezone_name,
        due_soon_days=due_soon_days,
    )
    return risks_from_metrics(metrics, snapshot.project_id, capture_id, due_soon_days)
