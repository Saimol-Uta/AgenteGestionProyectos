import type {
  AnalysisExecutionMode,
  AnalysisPreparation,
  AttentionKind,
  AttentionNotice,
  DomainTaskState,
  ProposalAuthorizationStatus,
  ProposalExecutionStatus,
  ProposalFreshness,
  ProposalReviewStatus,
  ProposalStageDiagnostic,
  ProposalStageOutcome,
  RiskKind,
  RiskProposalTechnicalStatus,
} from "./types";

const sourceProviderLabels: Record<string, string> = {
  trello: "Trello",
  github: "GitHub",
  github_actions: "GitHub Actions",
  local_policy: "Políticas del proyecto",
};

const providerLabels: Record<string, string> = {
  ...sourceProviderLabels,
  gemini: "Gemini",
  groq: "Groq",
  local: "local",
};

export function analysisPreparationIsReady(preparation: Pick<AnalysisPreparation, "eligible" | "ready">): boolean {
  return preparation.eligible && (preparation.ready ?? preparation.eligible);
}

export function analysisExecutionModeLabel(mode: AnalysisExecutionMode | undefined): string | null {
  if (mode === "live") return "Nube";
  if (mode === "sandbox") return "Local";
  if (mode === "mixed") return "Mixto";
  return null;
}

export function providerNameLabel(provider: string): string {
  const normalizedProvider = provider.toLocaleLowerCase("es-EC");
  return Object.prototype.hasOwnProperty.call(providerLabels, normalizedProvider)
    ? providerLabels[normalizedProvider]
    : provider;
}

export function providerModelLabel(provider: string, model: string | null | undefined): string | null {
  const normalizedProvider = provider.toLocaleLowerCase("es-EC");
  const normalizedModel = model?.trim();
  if (Object.prototype.hasOwnProperty.call(sourceProviderLabels, normalizedProvider) || !normalizedModel || normalizedModel.toLocaleLowerCase("es-EC") === "not_applicable") return null;
  return normalizedModel;
}

export function providerAttemptLabel(provider: string, model: string | null | undefined): string {
  const name = providerNameLabel(provider);
  const modelName = providerModelLabel(provider, model);
  return modelName ? `${name} · ${modelName}` : name;
}

const stateLabels: Record<DomainTaskState, string> = {
  pending: "Pendiente",
  en_curso: "En curso",
  completada: "Completada",
  bloqueada: "Bloqueada",
  desconocido: "Desconocido",
};

export function taskStateLabel(state: DomainTaskState | string): string {
  return stateLabels[state as DomainTaskState] ?? `Estado del dominio: ${state}`;
}

export function taskStateTone(state: DomainTaskState | string): "neutral" | "positive" | "warning" | "danger" | "unknown" | "accent" {
  if (state === "completada") return "positive";
  if (state === "bloqueada") return "danger";
  if (state === "desconocido") return "unknown";
  if (state === "en_curso") return "accent";
  return "neutral";
}

export function formatDate(value: string | null | undefined, includeTime = false): string {
  if (!value) return "Sin dato";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Fecha no válida";
  return new Intl.DateTimeFormat("es-EC", {
    timeZone: "America/Guayaquil",
    dateStyle: "medium",
    ...(includeTime ? { timeStyle: "short" as const } : {}),
  }).format(date);
}

export function formatPercentage(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "Sin dato";
  return `${new Intl.NumberFormat("es-EC", { maximumFractionDigits: 1 }).format(value)} %`;
}

export function formatDurationSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "Sin dato";
  return `${new Intl.NumberFormat("es-EC", { maximumFractionDigits: 1 }).format(value)} s`;
}

export function runStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    queued: "En cola",
    collecting: "Recopilando fuentes",
    analyzing: "En análisis",
    awaiting_human: "Pendiente de revisión humana",
    completed: "Completada",
    needs_attention: "Requiere atención",
    failed: "Fallida",
    cancelled: "Cancelada",
    unknown: "Desconocida",
  };
  return labels[status] ?? `Estado del dominio: ${status}`;
}

/**
 * Plain-language reason of a verified attention notice.
 *
 * The notice carries a stable code and the recorded text; this label only
 * names the kind of fact, so the interface never invents a cause.
 */
export function attentionReasonLabel(notice: AttentionNotice): string {
  const stage = notice.stage_name ? stageNameLabel(notice.stage_name) : null;
  const repository = notice.repository_id ?? null;
  switch (notice.reason_code) {
    case "run_failed":
      return "El análisis falló";
    case "stage_failed":
      return stage ? `Falló la etapa «${stage}»` : "Falló una etapa del análisis";
    case "stage_needs_attention":
      return stage ? `La etapa «${stage}» quedó incompleta` : "Una etapa quedó incompleta";
    case "repository_needs_attention":
      return repository
        ? `El repositorio ${repository} quedó incompleto${stage ? ` en «${stage}»` : ""}`
        : "Un repositorio quedó incompleto";
    case "repository_stage_failed":
      return repository
        ? `Falló «${stage ?? "una etapa"}» en el repositorio ${repository}`
        : "Falló una etapa en un repositorio";
    case "proposal_rules_not_configured":
      return "Las reglas de propuestas no están configuradas";
    case "proposal_rules_invalid":
      return "Las reglas de propuestas no superaron la validación";
    case "stage_not_recorded":
      return stage ? `La etapa «${stage}» no dejó registro` : "Una etapa no dejó registro";
    default:
      return "El análisis registró un motivo que requiere revisión";
  }
}

export function attentionKindLabel(kind: AttentionKind): string {
  const labels: Record<AttentionKind, string> = {
    failure: "Fallo",
    attention: "Requiere atención",
    limitation: "Limitación",
    unknown: "Información desconocida",
  };
  return labels[kind] ?? "Motivo registrado";
}

/**
 * Description of one preflight check.
 *
 * Every code the backend can emit has its own description; an unknown code is
 * shown with the code itself instead of a generic label.
 */
export function preflightCheckLabel(code: string): string {
  const labels: Record<string, string> = {
    configuration_unavailable: "Configuración efectiva del análisis",
    repository_collection_configuration: "Recopilación de los repositorios del proyecto",
    policy_configuration: "Reglas de calidad del proyecto",
    source_mode: "Modo de fuentes del proyecto",
    sandbox_source: "Fuentes locales de prueba",
    fixture_source: "Fuentes de prueba del proyecto",
    effective_configuration: "Configuración efectiva del análisis",
    source_configured: "Fuentes del proyecto",
    local_state: "Estado local del análisis",
  };
  return labels[code] ?? `Comprobación de disponibilidad (${code})`;
}

export function stageStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: "Pendiente",
    running: "En curso",
    completed: "Completada",
    skipped: "Omitida explícitamente",
    failed: "Fallida",
    needs_attention: "Requiere atención",
    unknown: "Desconocida",
  };
  return labels[status] ?? `Estado del dominio: ${status}`;
}

export function stageNameLabel(stage: string): string {
  const labels: Record<string, string> = {
    source_collection: "Recopilación de fuentes",
    monitoring: "Seguimiento del proyecto",
    proposals: "Propuestas generadas",
    quality: "Revisión de calidad",
    verifier: "Verificación de hallazgos",
    report: "Elaboración del informe",
  };
  return labels[stage] ?? `Etapa del dominio: ${stage}`;
}

export function findingVerificationLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: "Pendiente de verificación",
    confirmed: "Confirmado por Verifier",
    rejected: "Rechazado por Verifier",
    insufficient: "Evidencia insuficiente",
    unknown: "Desconocido",
  };
  return labels[status] ?? `Verificación del dominio: ${status}`;
}

export function policyStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    pass: "Cumple",
    fail: "No cumple",
    unknown: "Desconocido",
  };
  return labels[status] ?? `Estado de política: ${status}`;
}

export function analysisJobStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    queued: "En cola",
    running: "En curso",
    completed: "Completado",
    failed: "Fallido",
    interrupted: "Interrumpido",
  };
  return labels[status] ?? `Estado del trabajo: ${status}`;
}

export function fieldLabel(field: string | null | undefined): string {
  if (!field) return "Observación";
  const labels: Record<string, string> = {
    title: "Título",
    state: "Estado",
    list_id: "Identificador de fase",
    list_name: "Fase",
    assignee_ids: "Responsables",
    start_date: "Fecha de inicio",
    due_date: "Fecha de vencimiento",
    due_complete: "Vencimiento completado",
    priority: "Prioridad explícita",
    labels: "Etiquetas",
    is_included: "Incluida en el avance",
    has_contradiction: "Señales contradictorias",
    detail: "Descripción de la tarjeta",
    is_archived: "Tarjeta archivada",
    last_activity_at: "Última actividad en Trello",
    id: "Identificador de la tarea",
  };
  return labels[field] ?? field;
}

/** Shape of the task fields the manager-facing helpers read. */
export interface ObservableTask {
  assignee_ids?: string[] | null;
  assignee_names?: Record<string, string> | null;
  priority?: string | null;
  observed_fields?: string[] | null;
}

/** Whether the source reported the given field for this task. */
export function wasObserved(task: ObservableTask, field: string): boolean {
  return task.observed_fields?.includes(field) ?? false;
}

/**
 * Responsible person of a task. The origin only exposes member identifiers, never member
 * names, so the three honest states are kept apart: unassigned, not observed, and
 * assigned-but-name-unavailable. No name is ever invented.
 */
export function assigneeDisplay(task: ObservableTask): string {
  const assignees = task.assignee_ids ?? [];
  if (assignees.length === 0) {
    return wasObserved(task, "assignee_ids") ? "Sin asignar" : "No observado";
  }
  const names = task.assignee_names ?? {};
  const labels = assignees.map((id) => names[id] ?? "Nombre no disponible");
  return [...new Set(labels)].join(", ");
}

/** Whether the task has at least one responsible person reported by the source. */
export function hasAssignees(task: ObservableTask): boolean {
  return (task.assignee_ids ?? []).length > 0;
}

/**
 * Priority of a task. Trello only reports a priority when the card carries one or when a
 * priority label map is configured, so an absent priority is declared instead of guessed.
 */
export function priorityDisplay(task: ObservableTask): string {
  if (task.priority) return task.priority;
  return wasObserved(task, "priority") ? "Sin prioridad especificada" : "No disponible en la fuente";
}

/** Explanation that accompanies an absent priority, so the manager knows why it is absent. */
export function priorityUnavailableReason(task: ObservableTask): string | null {
  if (task.priority) return null;
  if (wasObserved(task, "priority")) return null;
  return "La fuente no informa prioridad para esta tarea y el tablero no tiene un mapa de etiquetas de prioridad configurado.";
}

export function comparisonValue(value: unknown, field?: string | null): string {
  if (value === null || value === undefined) return "No observado";
  if (field === "state" && typeof value === "string") return taskStateLabel(value);
  if (typeof value === "boolean") return value ? "Sí" : "No";
  if (Array.isArray(value)) return value.length ? value.map(String).join(", ") : "Lista vacía observada";
  if (typeof value === "string" && field?.endsWith("date")) return formatDate(value, true);
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

export function riskKindLabel(kind: RiskKind): string {
  const labels: Record<RiskKind, string> = {
    blocked: "Bloqueada",
    overdue: "Vencida",
    due_soon: "Próxima a vencer",
    contradictory_state: "Señales contradictorias",
    unknown_state: "Estado desconocido",
  };
  return labels[kind] ?? kind;
}

export function riskKindTone(kind: RiskKind): "neutral" | "positive" | "warning" | "danger" | "unknown" {
  if (kind === "overdue") return "danger";
  if (kind === "blocked" || kind === "contradictory_state") return "warning";
  if (kind === "unknown_state") return "unknown";
  return "neutral";
}

export function proposalOutcomeLabel(outcome: ProposalStageOutcome): string {
  const labels: Record<ProposalStageOutcome, string> = {
    saved: "Propuestas generadas",
    not_configured: "Reglas de propuestas no configuradas",
    rules_invalid: "Reglas de propuestas no válidas",
    no_candidates: "No se encontraron candidatos válidos",
    candidates_discarded: "Los candidatos fueron descartados",
    not_selected: "El agente no seleccionó candidatos",
    unknown: "Motivo no determinable",
  };
  return labels[outcome] ?? "Motivo no determinable";
}

export function proposalOutcomeTone(outcome: ProposalStageOutcome): "neutral" | "positive" | "warning" | "danger" | "unknown" {
  if (outcome === "saved") return "positive";
  if (outcome === "candidates_discarded" || outcome === "rules_invalid") return "warning";
  if (outcome === "unknown") return "unknown";
  return "neutral";
}

export function proposalOutcomeExplanation(diagnostic: ProposalStageDiagnostic): string {
  switch (diagnostic.outcome) {
    case "saved":
      return `El backend guardó ${diagnostic.saved ?? 0} propuesta(s) de ${diagnostic.selected ?? 0} selección(es) sobre ${diagnostic.offered ?? 0} candidato(s) disponible(s).`;
    case "not_configured":
      return "El proyecto no tiene reglas de propuestas configuradas, por lo que el backend no pudo generar candidatos.";
    case "rules_invalid":
      return "El proyecto tiene reglas configuradas, pero el backend las rechazó al validarlas; no se generaron candidatos.";
    case "no_candidates":
      return "Las reglas se aplicaron a la captura y no produjeron ningún candidato válido.";
    case "candidates_discarded":
      return `El agente seleccionó ${diagnostic.selected ?? 0} candidato(s) y el backend descartó ${diagnostic.discarded} al validarlos.`;
    case "not_selected":
      return `El backend ofreció ${diagnostic.offered ?? 0} candidato(s) y el agente no seleccionó ninguno.`;
    default:
      return "El resumen persistido no alcanza para probar el motivo; no se atribuye una causa que los datos no sostienen.";
  }
}

export function proposalStatusTone(value: string | null | undefined): "neutral" | "positive" | "warning" | "danger" | "unknown" {
  if (value == null || value === "unknown") return "unknown";
  if (["ready", "eligible", "current"].includes(value)) return "positive";
  if (["pending", "not_executed", "not_authorized"].includes(value)) return "warning";
  if (["blocked", "rejected", "denied", "revoked", "DENIED", "CONFLICT", "FAILED", "ineligible", "stale"].includes(value)) return "danger";
  return "neutral";
}

export function proposalReviewStatusLabel(status: ProposalReviewStatus | string): string {
  const labels: Record<string, string> = {
    pending: "Decisión pendiente",
    approved_offline: "Aprobación offline registrada",
    rejected: "Rechazada",
    unknown: "Desconocida",
  };
  return labels[status] ?? "Desconocida";
}

export function proposalAuthorizationStatusLabel(status: ProposalAuthorizationStatus | string): string {
  const labels: Record<string, string> = {
    not_authorized: "No autorizada",
    authorized_dry_run: "Autorizada para prueba local",
    denied: "Denegada",
    revoked: "Revocada",
    unknown: "Desconocida",
  };
  return labels[status] ?? "Desconocida";
}

export function proposalExecutionStatusLabel(status: ProposalExecutionStatus | string): string {
  const labels: Record<string, string> = {
    not_executed: "Sin ejecución registrada",
    EXECUTED: "Ejecución registrada",
    ALREADY_APPLIED: "El backend informa que ya estaba aplicado",
    DENIED: "Denegada por el backend",
    CONFLICT: "Conflicto informado por el backend",
    FAILED: "Fallida según el backend",
    unknown: "Estado de ejecución desconocido",
  };
  return labels[status] ?? "Estado de ejecución desconocido";
}

export function proposalTechnicalStatusLabel(status: RiskProposalTechnicalStatus | string | null | undefined): string {
  if (status === "ready") return "Revisión técnica lista";
  if (status === "blocked") return "Revisión técnica bloqueada";
  return "Revisión técnica desconocida";
}

export function proposalFreshnessLabel(freshness: ProposalFreshness | string): string {
  if (freshness === "current") return "Actual";
  if (freshness === "stale") return "Obsoleta";
  return "Desconocida";
}
