import type {
  ApiProblem,
  AnalysisJob,
  AnalysisPreparation,
  AuditCollection,
  AuditEventDetail,
  CaptureSummary,
  ConfirmAnalysisResponse,
  DownloadedFile,
  ExecutiveSummary,
  EvidenceSummary,
  FindingDetail,
  FindingSummary,
  OperatorSession,
  OutboxCollection,
  OutboxDetail,
  PoliciesSummary,
  QualitySummary,
  RunDetail,
  RunStages,
  RunSummary,
  Paginated,
  Project,
  ProjectConfiguration,
  ProposalCollection,
  ProposalDetail,
  ProposalMutationResponse,
  ProposalDryRunResponse,
  ProposalReviewFilter,
  ReportCollection,
  ReportHumanDecision,
  ReportDecisionResponse,
  ReportDetail,
  ScheduleCyclePreparation,
  ScheduleView,
  SystemHealth,
  TaskDetailResponse,
  TaskDetailView,
  TaskPlanSummary,
  TaskQuery,
  TaskSnapshotComparison,
  WeeklyCycleConfirmResponse,
  WeeklyCycleJob,
  WeeklyCycleJobCollection,
} from "./types";

const API_BASE = "/api/v1";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(message: string, status: number, code: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

interface RequestOptions extends RequestInit {
  csrfToken?: string | null;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  if (options.csrfToken) headers.set("X-CSRF-Token", options.csrfToken);
  const { csrfToken: _csrfToken, ...fetchOptions } = options;

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...fetchOptions,
      headers,
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError("No fue posible conectar con la API. Verifica que el servicio esté disponible.", 0);
  }

  if (response.status === 204) return undefined as T;
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const problem = readProblem(body);
    throw new ApiError(
      problem?.message ?? `La solicitud no pudo completarse (HTTP ${response.status}).`,
      response.status,
      problem?.code ?? null,
    );
  }
  return body as T;
}

function readProblem(body: unknown): ApiProblem | null {
  if (!body || typeof body !== "object") return null;
  const root = body as Record<string, unknown>;
  const nested = root.error;
  if (nested && typeof nested === "object") {
    const error = nested as Record<string, unknown>;
    if (typeof error.message === "string") {
      return {
        code: typeof error.code === "string" ? error.code : "api_error",
        message: error.message,
      };
    }
  }
  if (typeof root.detail === "string") return { code: "api_error", message: root.detail };
  if (Array.isArray(root.detail)) {
    const messages = root.detail.flatMap((issue) => {
      if (!issue || typeof issue !== "object") return [];
      const message = (issue as Record<string, unknown>).msg;
      return typeof message === "string" ? [message] : [];
    });
    if (messages.length) return { code: "validation_error", message: messages.join("; ") };
  }
  return null;
}

function query(values: Record<string, string | number | boolean | undefined | null>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const serialized = params.toString();
  return serialized ? `?${serialized}` : "";
}

function safeFilename(disposition: string | null, fallback: string): string {
  const encoded = disposition?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const quoted = disposition?.match(/filename="?([^";]+)"?/i)?.[1];
  let candidate = encoded ?? quoted ?? fallback;
  if (encoded) {
    try { candidate = decodeURIComponent(encoded); } catch { candidate = fallback; }
  }
  const safe = candidate.replace(/[\/\\\0-\x1f]/g, "_").replace(/\.{2,}/g, ".").replace(/[^A-Za-z0-9._-]/g, "_");
  return safe && safe !== "." ? safe : fallback;
}

async function download(path: string, fallbackFilename: string): Promise<DownloadedFile> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: { Accept: "application/json, text/markdown" },
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError("No fue posible conectar con la API. Verifica que el servicio esté disponible.", 0);
  }
  if (!response.ok) {
    const problem = readProblem(await response.json().catch(() => null));
    throw new ApiError(problem?.message ?? `La descarga no pudo completarse (HTTP ${response.status}).`, response.status, problem?.code ?? null);
  }
  return { blob: await response.blob(), filename: safeFilename(response.headers.get("Content-Disposition"), fallbackFilename) };
}

export const api = {
  getHealth: () => request<SystemHealth>("/system/health"),
  getSession: () => request<OperatorSession>("/auth/session"),
  login: async (token: string): Promise<void> => {
    await request<{ token: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ token }),
    });
  },
  logout: (csrfToken: string) =>
    request<void>("/auth/logout", { method: "POST", csrfToken }),
  getProjects: (page = 1, pageSize = 100) =>
    request<Paginated<Project>>(`/projects${query({ page, page_size: pageSize })}`),
  getProject: (projectId: string) =>
    request<Project>(`/projects/${encodeURIComponent(projectId)}`),
  getSummary: (projectId: string, captureId?: string) =>
    request<TaskPlanSummary>(
      `/projects/${encodeURIComponent(projectId)}/summary${query({ capture_id: captureId })}`,
    ),
  getTasks: (projectId: string, filters: TaskQuery) =>
    request<Paginated<TaskDetailView>>(
      `/projects/${encodeURIComponent(projectId)}/tasks${query({
        capture_id: filters.capture_id,
        q: filters.q,
        state: filters.state,
        assignee_id: filters.assignee_id,
        list_name: filters.list_name,
        critical_only: filters.critical_only,
        overdue_only: filters.overdue_only,
        due_soon_only: filters.due_soon_only,
        sort: filters.sort,
        direction: filters.direction,
        page: filters.page,
        page_size: filters.page_size,
      })}`,
    ),
  getTask: (projectId: string, taskId: string, captureId?: string) =>
    request<TaskDetailResponse>(
      `/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}${query({ capture_id: captureId })}`,
    ),
  getHistory: (projectId: string, page = 1, pageSize = 100) =>
    request<Paginated<CaptureSummary>>(
      `/projects/${encodeURIComponent(projectId)}/history${query({ page, page_size: pageSize })}`,
    ),
  getExecutiveSummary: (projectId: string) =>
    request<ExecutiveSummary>(`/projects/${encodeURIComponent(projectId)}/executive-summary`),
  getQuality: (projectId: string) =>
    request<QualitySummary>(`/projects/${encodeURIComponent(projectId)}/quality`),
  getPolicies: (projectId: string) =>
    request<PoliciesSummary>(`/projects/${encodeURIComponent(projectId)}/policies`),
  getRuns: (projectId: string, page = 1, pageSize = 25) =>
    request<Paginated<RunSummary>>(`/runs${query({ project_id: projectId, page, page_size: pageSize })}`),
  getRun: (runId: string) => request<RunDetail>(`/runs/${encodeURIComponent(runId)}`),
  getRunStages: (runId: string) => request<RunStages>(`/runs/${encodeURIComponent(runId)}/stages`),
  getRunFindings: (runId: string, page = 1, pageSize = 25) =>
    request<Paginated<FindingSummary>>(`/runs/${encodeURIComponent(runId)}/findings${query({ page, page_size: pageSize })}`),
  getFinding: (runId: string, findingId: string) =>
    request<FindingDetail>(`/runs/${encodeURIComponent(runId)}/findings/${encodeURIComponent(findingId)}`),
  getRunEvidence: (runId: string, page = 1, pageSize = 25) =>
    request<Paginated<EvidenceSummary>>(`/runs/${encodeURIComponent(runId)}/evidence${query({ page, page_size: pageSize })}`),
  prepareAnalysis: (projectId: string, csrfToken: string) =>
    request<AnalysisPreparation>(`/projects/${encodeURIComponent(projectId)}/analysis/prepare`, {
      method: "POST",
      csrfToken,
      body: JSON.stringify({ request_analysis: true }),
    }),
  confirmAnalysis: (
    projectId: string,
    payload: {
      preparation_id: string;
      configuration_fingerprint: string;
      confirm_analysis: true;
      remote_consent: boolean;
    },
    csrfToken: string,
    idempotencyKey: string,
  ) => request<ConfirmAnalysisResponse>(`/projects/${encodeURIComponent(projectId)}/analysis/confirm`, {
    method: "POST",
    csrfToken,
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(payload),
  }),
  getAnalysisJobs: (projectId: string, page = 1, pageSize = 25) =>
    request<Paginated<AnalysisJob>>(`/analysis/jobs${query({ project_id: projectId, page, page_size: pageSize })}`),
  getAnalysisJob: (jobId: string) => request<AnalysisJob>(`/analysis/jobs/${encodeURIComponent(jobId)}`),
  getProposals: (projectId: string, page = 1, pageSize = 25, reviewStatus?: ProposalReviewFilter) =>
    request<ProposalCollection>(`/projects/${encodeURIComponent(projectId)}/proposals${query({ page, page_size: pageSize, review_status: reviewStatus })}`),
  getProposal: (projectId: string, proposalId: string) =>
    request<ProposalDetail>(`/projects/${encodeURIComponent(projectId)}/proposals/${encodeURIComponent(proposalId)}`),
  reviewProposal: (projectId: string, proposalId: string, payload: { decision: "approve" | "reject"; expected_version: number; proposal_body_sha256: string; capture_id: string }, csrfToken: string, idempotencyKey: string) =>
    request<ProposalMutationResponse>(`/projects/${encodeURIComponent(projectId)}/proposals/${encodeURIComponent(proposalId)}/review`, {
      method: "POST", csrfToken, headers: { "Idempotency-Key": idempotencyKey }, body: JSON.stringify(payload),
    }),
  authorizeProposalDryRun: (projectId: string, proposalId: string, payload: { authorize: true; expected_version: number; proposal_body_sha256: string; capture_id: string }, csrfToken: string, idempotencyKey: string) =>
    request<ProposalMutationResponse>(`/projects/${encodeURIComponent(projectId)}/proposals/${encodeURIComponent(proposalId)}/dry-run-authorization`, {
      method: "POST", csrfToken, headers: { "Idempotency-Key": idempotencyKey }, body: JSON.stringify(payload),
    }),
  runProposalDryRun: (projectId: string, proposalId: string, payload: { expected_version: number; proposal_body_sha256: string; capture_id: string; permission_event_id: string }, csrfToken: string, idempotencyKey: string) =>
    request<ProposalDryRunResponse>(`/projects/${encodeURIComponent(projectId)}/proposals/${encodeURIComponent(proposalId)}/dry-runs`, {
      method: "POST", csrfToken, headers: { "Idempotency-Key": idempotencyKey }, body: JSON.stringify(payload),
    }),
  getReports: (projectId: string, page = 1, pageSize = 25, decision?: ReportHumanDecision) =>
    request<ReportCollection>(`/projects/${encodeURIComponent(projectId)}/reports${query({ page, page_size: pageSize, decision })}`),
  getReport: (projectId: string, reportId: string) =>
    request<ReportDetail>(`/projects/${encodeURIComponent(projectId)}/reports/${encodeURIComponent(reportId)}`),
  decideReport: (projectId: string, reportId: string, payload: { expected_report_version: number; expected_human_decision: "pending"; decision: "approved" | "rejected" | "changes_requested" | "postponed"; reason: string | null }, csrfToken: string, idempotencyKey: string) =>
    request<ReportDecisionResponse>(`/projects/${encodeURIComponent(projectId)}/reports/${encodeURIComponent(reportId)}/decision`, {
      method: "POST", csrfToken, headers: { "Idempotency-Key": idempotencyKey }, body: JSON.stringify(payload),
    }),
  downloadReport: (projectId: string, reportId: string, format: "markdown" | "json") =>
    download(`/projects/${encodeURIComponent(projectId)}/reports/${encodeURIComponent(reportId)}/download${query({ format })}`, `report-${reportId}.${format === "markdown" ? "md" : "json"}`),
  getAudit: (projectId: string, page = 1, pageSize = 25, filters: { event_type?: string; since?: string; until?: string } = {}) =>
    request<AuditCollection>(`/projects/${encodeURIComponent(projectId)}/audit${query({ page, page_size: pageSize, ...filters })}`),
  getAuditEvent: (projectId: string, eventId: string) =>
    request<AuditEventDetail>(`/projects/${encodeURIComponent(projectId)}/audit/${encodeURIComponent(eventId)}`),
  getSchedule: (projectId: string) =>
    request<ScheduleView>(`/projects/${encodeURIComponent(projectId)}/schedule`),
  getOutbox: (projectId: string, page = 1, pageSize = 25) =>
    request<OutboxCollection>(`/projects/${encodeURIComponent(projectId)}/schedule/outbox${query({ page, page_size: pageSize })}`),
  getOutboxDetail: (projectId: string, deliveryId: string) =>
    request<OutboxDetail>(`/projects/${encodeURIComponent(projectId)}/schedule/outbox/${encodeURIComponent(deliveryId)}`),
  downloadOutbox: (projectId: string, deliveryId: string, format: "markdown" | "json") =>
    download(`/projects/${encodeURIComponent(projectId)}/schedule/outbox/${encodeURIComponent(deliveryId)}/download${query({ format })}`, `outbox-${deliveryId}.${format === "markdown" ? "md" : "json"}`),
  prepareWeeklyCycle: (projectId: string, csrfToken: string) =>
    request<ScheduleCyclePreparation>(`/projects/${encodeURIComponent(projectId)}/schedule/manual-cycle/prepare`, {
      method: "POST", csrfToken, body: JSON.stringify({ request_cycle: true }),
    }),
  confirmWeeklyCycle: (projectId: string, payload: { preparation_id: string; configuration_fingerprint: string; confirm_cycle: true; remote_consent: false }, csrfToken: string, idempotencyKey: string) =>
    request<WeeklyCycleConfirmResponse>(`/projects/${encodeURIComponent(projectId)}/schedule/manual-cycle/confirm`, {
      method: "POST", csrfToken, headers: { "Idempotency-Key": idempotencyKey }, body: JSON.stringify(payload),
    }),
  getWeeklyCycleJobs: (projectId: string, page = 1, pageSize = 25) =>
    request<WeeklyCycleJobCollection>(`/projects/${encodeURIComponent(projectId)}/schedule/manual-cycle/jobs${query({ page, page_size: pageSize })}`),
  getWeeklyCycleJob: (projectId: string, jobId: string) =>
    request<WeeklyCycleJob>(`/projects/${encodeURIComponent(projectId)}/schedule/manual-cycle/jobs/${encodeURIComponent(jobId)}`),
  getProjectConfiguration: (projectId: string) =>
    request<ProjectConfiguration>(`/projects/${encodeURIComponent(projectId)}/configuration`),
  compareCaptures: (projectId: string, before: string, after: string) =>
    request<TaskSnapshotComparison>(
      `/projects/${encodeURIComponent(projectId)}/compare${query({ before, after })}`,
    ),
};
