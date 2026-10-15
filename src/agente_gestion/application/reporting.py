"""Report generation, markdown rendering in Spanish, and JSON export."""

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from agente_gestion.domain.report import FinalReport, HumanDecisionState
from agente_gestion.domain.findings import CandidateFinding, FindingVerification


def generate_report_markdown(
    report: FinalReport,
    execution_metrics: Optional[dict[str, Any]] = None,
    all_candidate_findings: Optional[list[CandidateFinding]] = None,
    all_evidence_ids: Optional[list[str]] = None,
) -> str:
    """
    Renders an authoritative, clear Spanish Markdown report matching repository conventions.
    Includes all mandatory sections:
    period, status, summary, progress, blocks, overdue, risks, quality,
    confirmed, rejected, insufficient, pending, recommendations, evidence,
    providers, and pendiente de decisión del gestor.
    """
    m = report.metrics
    cutoff_str = report.cutoff_datetime.strftime("%Y-%m-%d %H:%M:%S %Z")

    # Progress formatting
    if m.progress_percentage is not None:
        progress_str = f"{m.progress_percentage:.1f}% ({m.completed_cards} de {m.included_cards} tareas incluidas completadas)"
    else:
        progress_str = "Sin datos (0 tareas computables)"

    # Status formatting
    status_label = {
        "recomendar_aprobacion": "Favorable (Recomendar aprobación)",
        "solicitar_cambios": "Requiere cambios (Criterios no satisfechos)",
        "evidencia_insuficiente": "Evidencia insuficiente (Falta información)",
    }.get(report.recommendation, report.recommendation)

    decision_label = {
        HumanDecisionState.pending: "Pendiente de revisión del gestor",
        HumanDecisionState.approved: "Aprobado por el gestor",
        HumanDecisionState.rejected: "Rechazado por el gestor",
        HumanDecisionState.changes_requested: "Cambios solicitados por el gestor",
        HumanDecisionState.postponed: "Pospuesto por el gestor",
    }.get(report.human_decision, str(report.human_decision))

    lines = []
    lines.append(f"# Reporte Semanal de Gestión de Proyecto: {report.project_id}\n")

    # 1. Período
    lines.append("## Período")
    lines.append(f"- **Fecha de corte:** {cutoff_str}")
    lines.append(f"- **Zona horaria:** America/Guayaquil")
    lines.append(f"- **Generado el:** {datetime.now(ZoneInfo('America/Guayaquil')).strftime('%Y-%m-%d %H:%M:%S %Z')}\n")

    # 2. Estado general
    lines.append("## Estado general")
    lines.append(f"- **Evaluación objetiva (Backend):** {status_label}")
    lines.append(f"- **Decisión del gestor:** {decision_label}")
    if report.decision_reason:
        lines.append(f"- **Motivo de la decisión:** {report.decision_reason}")
    lines.append("")

    # 3. Resumen ejecutivo
    lines.append("## Resumen ejecutivo")
    lines.append(report.summary.strip() or "No se dispone de resumen ejecutivo adicional.")
    lines.append("")

    # 4. Avance de tareas
    lines.append("## Avance de tareas")
    lines.append(f"- **Progreso global:** {progress_str}")
    lines.append(f"- **Tareas completadas:** {m.completed_cards}")
    lines.append(f"- **Tareas incluidas en el corte:** {m.included_cards}")
    lines.append(f"- **Total de tarjetas en tablero:** {m.total_cards}")
    if m.unknown_card_ids:
        lines.append(f"- **Tarjetas con estado desconocido:** {', '.join(m.unknown_card_ids)}")
    if m.contradictory_card_ids:
        lines.append(f"- **Tarjetas con señales contradictorias:** {', '.join(m.contradictory_card_ids)}")
    lines.append("")

    # 5. Bloqueos detectados
    lines.append("## Bloqueos detectados")
    if m.blocked_card_ids:
        lines.append(f"Se identificaron {len(m.blocked_card_ids)} tarea(s) en estado bloqueado:")
        for bid in m.blocked_card_ids:
            lines.append(f"- **{bid}** (ver detalle de tarea en evidencias)")
    else:
        lines.append("No se registran tareas bloqueadas activas al corte.")
    lines.append("")

    # 6. Vencimientos
    lines.append("## Vencimientos")
    if m.overdue_card_ids:
        lines.append(f"Se identificaron {len(m.overdue_card_ids)} tarea(s) vencidas a la fecha de corte:")
        for oid in m.overdue_card_ids:
            lines.append(f"- **{oid}** (plazo vencido anterior al corte)")
    else:
        lines.append("No se registran tareas vencidas pendientes a la fecha de corte.")
    lines.append("")

    # Near-due facts come exclusively from backend metrics, not report prose.
    lines.append("## Próximos vencimientos")
    if m.due_soon_card_ids:
        lines.append(f"Se identificaron {len(m.due_soon_card_ids)} tarea(s) próximas a vencer en la ventana configurada:")
        for sid in m.due_soon_card_ids:
            lines.append(f"- **{sid}** (plazo dentro de la ventana inclusiva desde el corte)")
    else:
        lines.append("No se registran tareas próximas a vencer en la ventana configurada.")
    lines.append("")

    # 7. Riesgos
    lines.append("## Riesgos")
    # Collect risks from policy failures, blocks, overdues, and findings
    risks_found = []
    if m.blocked_card_ids:
        risks_found.append(f"Entrega comprometida por {len(m.blocked_card_ids)} tarea(s) bloqueada(s).")
    if m.overdue_card_ids:
        risks_found.append(f"Desviación de cronograma por {len(m.overdue_card_ids)} tarea(s) vencida(s).")
    if report.policy_evaluations.mandatory_failed_ids:
        risks_found.append(f"Incumplimiento de criterios de calidad obligatorios: {', '.join(report.policy_evaluations.mandatory_failed_ids)}.")
    if report.policy_evaluations.mandatory_unknown_ids:
        risks_found.append(f"Falta de telemetría en criterios obligatorios: {', '.join(report.policy_evaluations.mandatory_unknown_ids)}.")
    if report.confirmed_findings:
        risks_found.append(f"Presencia de {len(report.confirmed_findings)} defecto(s) crítico(s) confirmados en código.")

    if risks_found:
        for r in risks_found:
            lines.append(f"- {r}")
    else:
        lines.append("No se detectaron riesgos críticos en las fuentes analizadas.")
    lines.append("")

    # 8. Criterios de calidad y CI
    lines.append("## Criterios de calidad y CI")
    if report.ci_result:
        lines.append(f"- **Workflow de CI:** {report.ci_result.workflow_name} (`{report.ci_result.commit_sha}`)")
        lines.append(f"- **Estado de CI:** `{report.ci_result.status}`")
        if report.ci_result.total_tests is not None:
            lines.append(f"- **Pruebas:** {report.ci_result.total_tests} totales, {report.ci_result.failed_tests or 0} fallidas")
        if report.ci_result.coverage_percentage is not None:
            lines.append(f"- **Cobertura:** {report.ci_result.coverage_percentage:.1f}%")
    else:
        lines.append("- **Estado de CI:** No hay ejecución de CI vinculada al commit evaluado.")

    lines.append("\n### Evaluación de políticas:")
    lines.append("| Regla | Descripción | Obligatoria | Estado | Valor Observado | Motivo |")
    lines.append("|---|---|:---:|:---:|:---:|---|")
    for ev in report.policy_evaluations.evaluations:
        obs = ev.observed_value if ev.observed_value is not None else "N/D"
        mand = "Sí" if ev.mandatory else "No"
        lines.append(f"| `{ev.rule_id}` | {ev.description} | {mand} | **{ev.status.value}** | {obs} | {ev.reason or ''} |")
    lines.append("")

    # 9. Hallazgos confirmados
    lines.append("## Hallazgos confirmados")
    if report.confirmed_findings:
        for f in report.confirmed_findings:
            lines.append(f"- **[{f.id}] {f.category}** en `{f.file_path}:{f.line}` (commit `{f.commit_sha}`)")
            lines.append(f"  - *Evidencia:* `{f.evidence}`")
            lines.append(f"  - *Solución sugerida:* {f.suggested_fix}")
    else:
        lines.append("No se confirmaron defectos de código en este ciclo.")
    lines.append("")

    # 10. Hallazgos rechazados
    lines.append("## Hallazgos rechazados")
    if report.rejected_findings:
        for f in report.rejected_findings:
            lines.append(f"- **[{f.id}]** en `{f.file_path}:{f.line}`: Desestimado tras verificación técnica independiente.")
    else:
        lines.append("No hay hallazgos descartados.")
    lines.append("")

    # 11. Hallazgos con evidencia insuficiente
    lines.append("## Hallazgos con evidencia insuficiente")
    if report.insufficient_findings:
        for f in report.insufficient_findings:
            lines.append(f"- **[{f.id}]** en `{f.file_path}:{f.line}`: La evidencia aportada no permite corroborar el defecto.")
    else:
        lines.append("Ninguno.")
    lines.append("")

    # 12. Hallazgos pendientes de verificación
    lines.append("## Hallazgos pendientes de verificación")
    if report.pending_findings:
        for f in report.pending_findings:
            lines.append(f"- **[{f.id}] {f.category}** en `{f.file_path}:{f.line}`: Pendiente de inspección profunda.")
    else:
        lines.append("No hay hallazgos pendientes de verificación.")
    lines.append("")

    # 13. Recomendaciones
    lines.append("## Recomendaciones")
    if report.recommendations:
        for rec in report.recommendations:
            lines.append(f"- {rec}")
    else:
        lines.append("- Revisar métricas y proceder según criterio del gestor.")
    lines.append("")

    # 14. Evidencias consultadas
    lines.append("## Evidencias consultadas")
    all_evs = report.all_evidence_ids or all_evidence_ids or []
    if all_evs:
        lines.append(f"Se correlacionaron {len(all_evs)} evidencia(s) registradas: `{', '.join(all_evs)}`.")
    else:
        lines.append("Evidencias normalizadas desde repositorios Git, tableros Trello y artefactos CI.")
    lines.append("")

    # 15. Proveedores y modelos utilizados
    lines.append("## Proveedores y modelos utilizados")
    prov_dict = report.providers_used or (execution_metrics.get("models_used") if execution_metrics else {}) or {}
    if prov_dict:
        for role, mod in prov_dict.items():
            lines.append(f"- **Rol `{role}`:** `{mod}`")
    else:
        lines.append("- Proveedor y modelo no registrados para esta ejecución.")
    if execution_metrics and "duration_seconds" in execution_metrics:
        lines.append(f"- **Duración total:** {execution_metrics['duration_seconds']:.2f} s")
    lines.append("")

    # 16. Pendiente de decisión del gestor
    lines.append("## Pendiente de decisión del gestor")
    lines.append(
        "El presente informe se encuentra **pendiente de decisión del gestor**. "
        "La inteligencia artificial cumple una función exclusivamente asesora y explicativa; "
        "las métricas, políticas objetivas y bloqueos son calculados directamente por el motor del backend. "
        "El gestor humano debe revisar las evidencias y registrar su decisión explícita (aprobar, rechazar o solicitar cambios)."
    )

    return "\n".join(lines)


def export_report_json(report: FinalReport, execution_metrics: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Serializes the report and associated candidate status as structured JSON."""
    data = report.model_dump()
    if execution_metrics:
        data["execution_metrics"] = execution_metrics
    return data
