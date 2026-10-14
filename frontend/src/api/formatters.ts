import type { DomainTaskState } from "./types";

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
    monitoring: "Monitoring",
    quality: "Quality",
    verifier: "Verifier",
    report: "Report",
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
  };
  return labels[field] ?? field;
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
