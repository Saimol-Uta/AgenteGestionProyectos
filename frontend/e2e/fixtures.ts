import { expect, type Page, type Route } from "@playwright/test";
import type { ProposalDetail, ProposalSummary } from "../src/api/types";

export const managerCredentials = {
  email: "gestor@hormigestion.ec",
  password: "HormiGestion2026!",
} as const;

export const summary = {
  project_id: "hormigestio",
  capture_id: "capture-current",
  captured_at: "2026-01-12T10:00:00-05:00",
  cutoff_datetime: "2026-01-11T23:59:59-05:00",
  total_tasks: 26,
  included_tasks: 26,
  completed_tasks: 0,
  progress_percentage: 0,
  state_counts: { pending: 0, en_curso: 1, completada: 0, bloqueada: 0, desconocido: 25 },
  overdue_count: 1,
  due_soon_count: 1,
  blocked_count: 0,
  unknown_count: 25,
  contradictory_count: 0,
  overdue_task_ids: ["task-1"],
  due_soon_task_ids: ["task-3"],
  blocked_task_ids: [],
  unknown_task_ids: Array.from({ length: 25 }, (_, index) => `task-${index + 2}`),
  contradictory_task_ids: [],
  identified_risks: [],
};

export const project = {
  id: "hormigestio",
  name: "HORMIGESTION",
  source_mode: "trello",
  latest_capture_at: summary.captured_at,
  latest_run_id: null,
  latest_run_status: null,
  summary,
};

export const secondProject = {
  ...project,
  id: "proyecto-secundario",
  name: "Proyecto secundario",
  summary: null,
};

export const tasks = [
  {
    id: "task-1",
    title: "Implementar API de pagos",
    state: "en_curso",
    list_id: "list-build",
    list_name: "Construcción",
    assignee_ids: ["operator-1"],
    start_date: null,
    due_date: "2026-01-10T12:00:00-05:00",
    due_complete: false,
    priority: null,
    labels: [],
    is_included: true,
    is_archived: false,
    has_contradiction: false,
    detail: null,
    last_activity_at: "2026-01-12T09:00:00-05:00",
    observed_fields: ["title", "state", "list_name", "assignee_ids", "due_date"],
    is_overdue: true,
    is_due_soon: false,
    is_critical: true,
    explicit_dependencies: [],
    dependency_impacts: [],
  },
  {
    id: "task-2",
    title: "Validar sincronización",
    state: "desconocido",
    list_name: null,
    assignee_ids: [],
    due_date: null,
    priority: null,
    labels: [],
    is_included: true,
    has_contradiction: false,
    observed_fields: null,
    is_overdue: false,
    is_due_soon: false,
    is_critical: false,
    explicit_dependencies: [],
    dependency_impacts: [],
  },
];

export const allTasks = [
  ...tasks,
  ...Array.from({ length: 24 }, (_, index) => ({
    ...tasks[1],
    id: `task-${index + 3}`,
    title: `Validar sincronización ${index + 1}`,
    ...(index === 0 ? {
      due_date: "2026-01-14T10:00:00-05:00",
      is_due_soon: true,
      observed_fields: ["title", "state", "due_date"],
    } : {}),
  })),
];

export const history = {
  items: [
    {
      capture_id: "capture-current",
      captured_at: summary.captured_at,
      summary,
    },
    {
      capture_id: "capture-previous",
      captured_at: "2026-01-05T10:00:00-05:00",
      summary: {
        ...summary,
        capture_id: "capture-previous",
        captured_at: "2026-01-05T10:00:00-05:00",
        total_tasks: 1,
        unknown_count: 0,
      },
    },
  ],
  total: 2,
  page: 1,
  page_size: 25,
};

export const runCurrent = {
  run_id: "run-current",
  project_id: "hormigestio",
  status: "completed",
  created_at: "2026-01-12T10:05:00-05:00",
  updated_at: "2026-01-12T10:07:00-05:00",
  snapshot_id: "capture-current",
  duration_seconds: 120.5,
  is_simulated: false,
  report_id: "report-current",
};

export const runPrevious = {
  ...runCurrent,
  run_id: "run-previous",
  status: "needs_attention",
  created_at: "2026-01-05T10:05:00-05:00",
  updated_at: "2026-01-05T10:06:00-05:00",
  snapshot_id: "capture-previous",
  duration_seconds: 77.25,
  report_id: null,
};

export const runHistory = {
  items: [runCurrent, runPrevious],
  total: 2,
  page: 1,
  page_size: 25,
};

export const stageRecords = {
  run_id: "run-current",
  items: [
    { stage_name: "source_collection", status: "completed", recorded: true, duration_seconds: 4, completed_at: "2026-01-12T10:05:04-05:00", providers: null, result_summary: "Fuentes almacenadas consultadas", error_code: null },
    { stage_name: "monitoring", status: "completed", recorded: true, duration_seconds: 30, completed_at: "2026-01-12T10:05:34-05:00", providers: [{ provider: "local", model: "fixture-monitor", status: "succeeded", attempt_count: 1, duration_seconds: 30 }], result_summary: "Monitoreo persistido", error_code: null },
    { stage_name: "quality", status: "completed", recorded: true, duration_seconds: 42, completed_at: "2026-01-12T10:06:16-05:00", providers: [{ provider: "local", model: "fixture-quality", status: "succeeded", attempt_count: 1, duration_seconds: 42 }], result_summary: "Dos hallazgos candidatos", error_code: null },
    { stage_name: "verifier", status: "unknown", recorded: false, duration_seconds: null, completed_at: null, providers: null, result_summary: null, error_code: null },
    { stage_name: "report", status: "skipped", recorded: true, duration_seconds: null, completed_at: null, providers: null, result_summary: null, error_code: null },
  ],
};

export const candidateFinding = {
  finding_id: "finding-candidate",
  run_id: "run-current",
  category: "seguridad",
  severity: "high",
  rule_id: "Q2",
  file_path: "src/token.ts",
  line: 17,
  commit_sha: "0123456789abcdef0123456789abcdef01234567",
  confidence: 0.72,
  candidate: true,
  verification_status: "insufficient",
};

export const findingDetail = {
  ...candidateFinding,
  claim: "El fragmento candidato expone una cadena con tratamiento inseguro.",
  suggested_fix: "Validar el dato antes de usarlo.",
  evidence_ids: ["evidence-code-1"],
  verification: { verdict: "insufficient", reason: "El fragmento no muestra el flujo completo.", verified_by: "verifier_agent", evidence_ids: ["evidence-code-1"] },
  evidence: {
    availability: "available",
    items: [{ id: "evidence-code-1", source: "github", timestamp: "2026-01-12T10:06:00-05:00", repository: "owner/project", commit_sha: "0123456789abcdef0123456789abcdef01234567", file_path: "src/token.ts", lines: [16, 17], category: "code", summary: "Fragmento persistido para inspección" }],
  },
  code_excerpt: {
    evidence_id: "evidence-code-1",
    file_path: "src/token.ts",
    lines: [16, 17],
    content: "16  const token = readToken();\\n17  <script>window.__findingXss = true</script>",
    evidence_scope: "snippet",
    is_partial: true,
    redacted: false,
  },
};

export const quality = {
  project_id: "hormigestio",
  run_id: "run-current",
  stage_status: "completed",
  availability: "available",
  finding_counts: { candidate: 2, confirmed: 1, rejected: 0, insufficient: 1, pending: 0, unknown: 0 },
  ci: null,
};

export const policies = {
  project_id: "hormigestio",
  run_id: "run-current",
  availability: "available",
  items: [
    { rule_id: "Q1", description: "Cobertura mínima documentada", status: "pass", mandatory: true, observed_value: 85, reason: null },
    { rule_id: "Q2", description: "Verificación independiente", status: "unknown", mandatory: true, observed_value: null, reason: "No hay evidencia suficiente para decidir." },
  ],
};

export const executiveSummary = {
  project_id: "hormigestio",
  capture_id: "capture-current",
  task_summary: summary,
  capture_series: [
    { capture_id: "capture-previous", captured_at: "2026-01-05T10:00:00-05:00", progress_percentage: 0, total_tasks: 1, completed_tasks: 0, state_counts: { pending: 0, en_curso: 0, completada: 0, bloqueada: 0, desconocido: 1 } },
    { capture_id: "capture-current", captured_at: summary.captured_at, progress_percentage: 0, total_tasks: 26, completed_tasks: 0, state_counts: summary.state_counts },
  ],
  quality,
  policies,
  latest_report: { report_id: "report-current", run_id: "run-current", cutoff_datetime: "2026-01-11T23:59:59-05:00", recommendation: "Revisar los hallazgos con evidencia insuficiente.", human_decision: "pending", summary: "Informe de revisión persistido." },
  runs: { total: 2, by_status: { completed: 1, needs_attention: 1 }, series: [
    { run_id: "run-previous", created_at: runPrevious.created_at, status: runPrevious.status, duration_seconds: runPrevious.duration_seconds },
    { run_id: "run-current", created_at: runCurrent.created_at, status: runCurrent.status, duration_seconds: runCurrent.duration_seconds },
  ] },
};

export const analysisPreparation = {
  preparation_id: "preparation-fixture-12345678",
  project_id: "hormigestio",
  expires_at: "2026-01-12T11:00:00-05:00",
  configuration_fingerprint: "a".repeat(64),
  eligible: true,
  capabilities: ["analysis:prepare", "analysis:confirm"],
  preflight: { source_mode: "trello", configuration_status: "ready", checks: [{ code: "source_configured", status: "ready" }, { code: "local_state", status: "unknown" }] },
  remote: { required: true, server_allowed: true, availability: "not_checked", providers: [{ stage: "quality", provider: "gemini", model: "gemini-fixture", transport: "remote", credential_status: "configured" }] },
};

export const analysisJob = { job_id: "job-current", project_id: "hormigestio", status: "queued", created_at: "2026-01-12T10:10:00-05:00", updated_at: "2026-01-12T10:10:00-05:00", run_id: null, error_code: null, remote_consent: true };

export const comparison = {
  project_id: "hormigestio",
  previous_snapshot_id: "capture-previous",
  current_snapshot_id: "capture-current",
  previous_captured_at: "2026-01-05T10:00:00-05:00",
  current_captured_at: summary.captured_at,
  changes: [
    {
      id: "change-1",
      task_id: "task-1",
      kind: "field_changed",
      field: "state",
      before: "pending",
      after: "en_curso",
      previous_evidence_ids: [],
      current_evidence_ids: ["evidence-1"],
      actor: null,
    },
  ],
  warnings: [],
};

export const proposalSummary: ProposalSummary = {
  proposal_id: "proposal-date-001",
  project_id: "hormigestio",
  task_id: "task-1",
  capture_id: "capture-current",
  field: "due_date",
  current_value: "2026-01-20T17:00:00-05:00",
  proposed_value: "2026-01-22T17:00:00-05:00",
  rationale: "La evidencia observada muestra un bloqueo de proveedor.",
  expected_impact: "Disponer de dos días adicionales para completar la tarea.",
  evidence: [{ evidence_id: "evidence-proposal-1", category: "task", summary: "Bloqueo anotado en la captura." }],
  proposal_body_sha256: "b".repeat(64),
  version: 2,
  technical_eligibility: "eligible",
  technical_errors: [],
  technical_review: {
    review_id: "technical-review-fixture-001",
    technical_status: "ready",
    technical_errors: [],
    dry_run: { status: "SIMULATED", reasons: [], field: "due_date", before: "2026-01-20T17:00:00-05:00", after: "2026-01-22T17:00:00-05:00", live_state_changed: false },
    human_approval_status: "required",
    execution_authorized: false,
    recorded_at: "2026-01-12T10:20:00-05:00",
  },
  freshness: "current",
  execution_status: "not_executed",
};

export const proposalDetail: ProposalDetail = { ...proposalSummary };

export const priorityProposalDetail: ProposalDetail = {
  ...proposalDetail,
  field: "priority",
  current_value: "medium",
  proposed_value: "high",
  proposal_body_sha256: "c".repeat(64),
};

export const assigneeProposalDetail: ProposalDetail = {
  ...proposalDetail,
  field: "assignee_ids",
  current_value: ["operator-1"],
  proposed_value: ["operator-2"],
  proposal_body_sha256: "d".repeat(64),
};

export const reportSummary = {
  report_id: "report-001",
  project_id: "hormigestio",
  run_id: "run-current",
  report_version: 1,
  created_at: "2026-01-12T10:08:00-05:00",
  cutoff_datetime: "2026-01-12T10:00:00-05:00",
  recommendation: "Revisar el riesgo de la tarea crítica.",
  human_decision: "pending",
  decision_reason: null,
  decision_timestamp: null,
  summary: "Resumen almacenado del informe.",
  metrics: {
    total_cards: 26,
    included_cards: 26,
    completed_cards: 12,
    progress_percentage: 46.2,
    overdue_card_ids: ["task-1"],
    due_soon_card_ids: ["task-3"],
    blocked_card_ids: [],
    unknown_card_ids: ["task-2"],
    contradictory_card_ids: [],
  },
  risk_task_ids: ["task-1", "task-3"],
  recommendations: ["Confirmar una nueva fecha con el responsable."],
  policy_results: [{ rule_id: "Q1", description: "Cobertura documentada", status: "unknown", mandatory: true, observed_value: null, reason: "No se registró CI." }],
};

export const reportDetail = {
  ...reportSummary,
  decision_history: [],
  markdown_text: "# Informe almacenado\\n\\nResumen del resultado.\\n\\n<img src=\\\"https://beacon.invalid/pixel\\\" onerror=\\\"alert(1)\\\">\\n\\n[Enlace peligroso](javascript:alert(1))\\n\\n![Baliza](https://beacon.invalid/pixel.png)\\n\\n[Documentación](https://example.invalid/docs)",
};

export const auditEvent = {
  event_id: "audit-local-001",
  project_id: "hormigestio",
  event_type: "report_decision",
  occurred_at: "2026-01-12T10:12:00-05:00",
  resource_type: "report",
  resource_id: "report-001",
  outcome: "recorded",
  summary: "Se registró una decisión humana sobre el informe.",
  actor_id: "operador-local",
  actor_verification: "verified_local_session",
  source: "web",
};

export const unknownAuditEvent = {
  ...auditEvent,
  event_id: "audit-legacy-002",
  event_type: "proposal_review",
  actor_id: null,
  actor_verification: "unknown",
  source: "core",
  summary: "Evento heredado sin atribución verificable.",
};

export const outboxReceipt = {
  delivery_id: "delivery-001",
  message_id: "message-001",
  project_id: "hormigestio",
  window_id: "window-001",
  channel: "sqlite",
  status: "DELIVERED",
  dispatched_at: "2026-01-12T10:09:00-05:00",
  artifact_available: true,
  report_id: "report-001",
};

export const weeklySchedule = {
  project_id: "hormigestio",
  weekday: 4,
  local_time: "17:00",
  timezone: "America/Guayaquil",
  window_rule: "previous_scheduled_week",
};

export const weeklyWindow = {
  window_id: "window-001",
  window_start_local: "2026-01-05T00:00:00-05:00",
  window_start_utc: "2026-01-05T05:00:00Z",
  window_end_local: "2026-01-12T00:00:00-05:00",
  window_end_utc: "2026-01-12T05:00:00Z",
  cutoff_local: "2026-01-09T17:00:00-05:00",
  cutoff_utc: "2026-01-09T22:00:00Z",
  next_boundary_local: "2026-01-16T17:00:00-05:00",
  next_boundary_utc: "2026-01-16T22:00:00Z",
};

export const scheduleView = {
  project_id: "hormigestio",
  availability: "configured",
  enabled: true,
  schedule: weeklySchedule,
  planned_window: weeklyWindow,
  is_due: true,
  is_dispatched: false,
  outbox_history: "available",
  can_run_manually: true,
  last_receipt: outboxReceipt,
  automatic_trigger_status: "not_verified",
  remote: { required: false, server_allowed: false, availability: "not_checked", providers: [] },
};

export const outboxMessage = {
  message_id: outboxReceipt.message_id,
  project_id: "hormigestio",
  window_id: "window-001",
  report_id: "report-001",
  channel: "sqlite",
  subject: "Informe semanal almacenado",
  summary: "Resumen del outbox guardado por el backend.",
  body_markdown: "# Entrega almacenada\\n\\nCuerpo ligado al recibo persistido.",
  metrics: { total_tasks: 26, included_tasks: 26, completed_tasks: 12, progress_percentage: 46.2, overdue_count: 1, due_soon_count: 1, blocked_count: 0, unknown_count: 1, contradictory_count: 0 },
  risks: ["task-1"],
  recipient: null,
  created_at: "2026-01-12T10:08:00-05:00",
};

export const outboxDetail = { receipt: outboxReceipt, message: outboxMessage };

export const schedulePreparation = {
  preparation_id: "cycle-preparation-001",
  project_id: "hormigestio",
  expires_at: "2026-01-12T11:00:00-05:00",
  configuration_fingerprint: "c".repeat(64),
  eligible: true,
  capabilities: ["weekly_cycle:prepare", "weekly_cycle:confirm"],
  preflight: { schedule_status: "configured", window_id: "window-001", has_captures: true, outbox_history: "available", report_binding: "available", can_run: true },
  remote: { required: false, server_allowed: false, availability: "not_checked", providers: [] },
};

export const weeklyCycleJob = {
  job_id: "cycle-job-001",
  project_id: "hormigestio",
  status: "queued",
  created_at: "2026-01-12T10:13:00-05:00",
  updated_at: "2026-01-12T10:13:00-05:00",
  outcome: "unknown",
  window_id: "window-001",
  delivery_id: null,
  error_code: null,
  remote_consent: false,
};

export const projectConfiguration = {
  project_id: "hormigestio",
  availability: "available",
  source_mode: "real",
  timezone: "America/Guayaquil",
  due_soon_days: 7,
  repository: { configured: true, owner: "org-fixture", name: "gestion-fixture", branch: "main", commit_sha: "a".repeat(40) },
  trello: { configured: true, board_id: "board-fixture", credential_status: "configured" },
  providers: [{ stage: "quality", provider: "gemini", model: "gemini-fixture", transport: "remote", credential_status: "configured" }],
  policies: { configured: true, version: "policy-v2" },
  schedule: weeklySchedule,
  schedule_status: "configured",
  capabilities: ["configuration:read"],
  editable_fields: [],
};

export const auditCollection = {
  availability: "available",
  items: [auditEvent, unknownAuditEvent],
  total: 2,
  page: 1,
  page_size: 25,
};

async function json(route: Route, value: unknown, status = 200) {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
}

export async function installApi(page: Page, options: {
  loginStatus?: number;
  projectStatus?: number;
  projectPagination?: boolean;
  summaryPayload?: unknown;
  historyPayload?: unknown;
  criticalStatus?: number;
  authenticated?: boolean;
  capabilities?: string[];
  sessionEmail?: string | null;
  notificationEmail?: string | null;
  prepareStatus?: number;
  preparePayload?: Partial<typeof analysisPreparation> & {
    ready?: boolean;
    execution_mode?: "live" | "sandbox" | "mixed";
    fallback_reasons?: string[];
  };
  analysisJobPayload?: Partial<typeof analysisJob> & {
    execution_mode?: "live" | "sandbox" | "mixed";
    fallback_reasons?: string[];
  };
  confirmStatuses?: number[];
  jobPollStatuses?: Array<"queued" | "running" | "completed" | "failed" | "interrupted">;
  proposalExecutionStatus?: number;
  proposalExecutionBody?: unknown;
  proposalFixture?: ProposalDetail;
  reportDecisionStatus?: number;
  cyclePrepareEligible?: boolean;
  cycleConfirmStatus?: number;
  weeklyCycleStatuses?: Array<"queued" | "running" | "completed" | "failed" | "interrupted">;
  proposalAvailability?: "available" | "unknown";
  reportAvailability?: "available" | "unknown";
  outboxAvailability?: "available" | "unknown";
  scheduleAvailability?: "configured" | "unknown";
  auditStatus?: number;
  configurationStatus?: number;
  requireLogin?: boolean;
} = {}) {
  let authenticated = options.authenticated ?? false;

  if (!options.requireLogin) {
    await page.addInitScript(() => {
      try {
        sessionStorage.setItem("gestionpro_guest_mode", "true");
      } catch {}
    });
  }
  let confirmedJob = false;
  let confirmCount = 0;
  let jobPollCount = 0;
  let jobPollRequests = 0;
  let analysisJobListReads = 0;
  let currentProposal = { ...(options.proposalFixture ?? proposalDetail) };
  const currentAuditItems = [...auditCollection.items];
  const executedProposalKeys = new Set<string>();
  type ReportDecisionItem = { decision_id: string; report_id: string; run_id: string | null; decision: string; reason: string | null; recorded_at: string; actor_id: null; actor_verification: "unknown" };
  let currentReport = { ...reportDetail, decision_history: [] as ReportDecisionItem[] };
  let cyclePrepared = false;
  let cycleConfirmed = false;
  let cyclePollCount = 0;
  const analysisWrites: Array<{ path: string; body: string | null; csrfToken: string | null; idempotencyKey: string | null }> = [];
  const governanceRequests: Array<{ path: string; body: string | null; csrfToken: string | null; idempotencyKey: string | null; origin: string | null }> = [];
  const reportDecisionRequests: Array<{ path: string; body: string | null; csrfToken: string | null; idempotencyKey: string | null }> = [];
  const weeklyCycleRequests: Array<{ path: string; body: string | null; csrfToken: string | null; idempotencyKey: string | null }> = [];
  const moduleReads: string[] = [];
  const downloadRequests: string[] = [];
  const loginStatus = options.loginStatus ?? 200;
  const sessionCapabilities = options.capabilities ?? [];

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (request.method() === "GET" && path.endsWith("/analysis/jobs")) analysisJobListReads += 1;
    if (request.method() === "GET" && /\/(proposals|reports|audit|configuration|schedule)(\/|$)/.test(path)) moduleReads.push(`${request.method()} ${path}${url.search}`);
    if (request.method() === "POST" && /\/projects\/hormigestio\/(proposals|reports|schedule)\//.test(path)) {
      const entry = {
        path,
        body: request.postData(),
        csrfToken: request.headers()["x-csrf-token"] ?? null,
        idempotencyKey: request.headers()["idempotency-key"] ?? null,
        origin: request.headers().origin ?? null,
      };
      if (path.includes("/proposals/")) governanceRequests.push(entry);
      else if (path.includes("/reports/")) reportDecisionRequests.push(entry);
      else weeklyCycleRequests.push(entry);
    }
    if (request.method() === "POST" && path.includes("/analysis/")) {
      analysisWrites.push({
        path,
        body: request.postData(),
        csrfToken: request.headers()["x-csrf-token"] ?? null,
        idempotencyKey: request.headers()["idempotency-key"] ?? null,
      });
    }

    if (path.endsWith("/auth/session")) {
      await json(route, {
        authenticated,
        actor: authenticated ? "Gestor del Proyecto" : null,
        csrf_token: authenticated ? "csrf-fixture" : null,
        capabilities: authenticated ? sessionCapabilities : [],
        login_available: true,
        email: authenticated ? options.sessionEmail === undefined ? managerCredentials.email : options.sessionEmail : null,
        notification_email: authenticated ? options.notificationEmail ?? null : null,
        role: authenticated ? "PROJECT_MANAGER" : null,
      });
      return;
    }

    if (path.endsWith("/auth/login")) {
      let body: unknown = null;
      try { body = request.postDataJSON(); } catch { /* Missing or malformed JSON is rejected below. */ }
      const credentialsMatch = Boolean(body && typeof body === "object"
        && Object.keys(body).length === 2
        && (body as Record<string, unknown>).email === managerCredentials.email
        && (body as Record<string, unknown>).password === managerCredentials.password);
      const status = credentialsMatch ? loginStatus : 401;
      authenticated = status === 200;
      await json(route, status === 200
        ? {
          authenticated: true,
          actor: "Gestor del Proyecto",
          csrf_token: "csrf-fixture",
          capabilities: sessionCapabilities,
          login_available: true,
          email: managerCredentials.email,
          notification_email: null,
          role: "PROJECT_MANAGER",
        }
        : { error: { code: "invalid_credentials", message: "Correo electrónico o contraseña incorrectos." } }, status);
      return;
    }

    if (path.endsWith("/auth/logout")) {
      expect(request.headers()["x-csrf-token"]).toBe("csrf-fixture");
      authenticated = false;
      await json(route, { ok: true });
      return;
    }

    if (path.endsWith("/system/health")) {
      await json(route, { status: "ok", version: "fixture", external_writes_enabled: false });
      return;
    }

    if (path.endsWith("/projects") && request.method() === "GET") {
      const pageNumber = Number(url.searchParams.get("page") ?? 1);
      await json(route, options.projectPagination
        ? { items: pageNumber === 1 ? [project] : [secondProject], total: 101, page: pageNumber, page_size: 100 }
        : { items: [project], total: 1, page: 1, page_size: 100 }, options.projectStatus ?? 200);
      return;
    }

    if (path.endsWith("/projects/hormigestio/proposals") && request.method() === "GET") {
      const available = options.proposalAvailability ?? "available";
      await json(route, { availability: available, items: available === "available" ? [currentProposal] : [], total: available === "available" ? 1 : 0, page: 1, page_size: 25 });
      return;
    }

    if (path.endsWith("/projects/hormigestio/proposals/proposal-date-001") && request.method() === "GET") {
      await json(route, currentProposal);
      return;
    }

    if (path.endsWith("/projects/hormigestio/proposals/proposal-date-001/execute") && request.method() === "POST") {
      expect(request.headers()["x-csrf-token"]).toBe("csrf-fixture");
      expect(request.headers()["idempotency-key"]).toBeTruthy();
      const payload = JSON.parse(request.postData() ?? "{}");
      if (options.proposalExecutionStatus && options.proposalExecutionStatus !== 200) {
        await json(route, { error: { code: "proposal_execution_unavailable", message: "La ejecución no está disponible por el servidor." } }, options.proposalExecutionStatus);
        return;
      }
      const status = payload.decision === "reject" ? "DENIED" : "SIMULATED";
      const result = options.proposalExecutionBody ?? {
        proposal_id: "proposal-date-001",
        decision: payload.decision,
        execution: { status, verification_status: payload.decision === "reject" ? "not_required" : "verified" },
        execution_mode: "sandbox",
      };
      const idempotencyKey = request.headers()["idempotency-key"] ?? "";
      if (!executedProposalKeys.has(idempotencyKey)) {
        executedProposalKeys.add(idempotencyKey);
        const reportedStatus = (result as { execution?: { status?: string } }).execution?.status ?? status;
        const executionStatus: ProposalDetail["execution_status"] = reportedStatus === "SIMULATED" || reportedStatus === "EXECUTED"
          ? "EXECUTED"
          : ["ALREADY_APPLIED", "DENIED", "CONFLICT", "FAILED", "unknown"].includes(reportedStatus)
            ? reportedStatus as ProposalDetail["execution_status"] : "unknown";
        currentProposal = { ...currentProposal, execution_status: executionStatus };
        currentAuditItems.push({
          ...auditEvent,
          event_id: "proposal-execution-proposal-date-001",
          event_type: "proposal_execution",
          resource_type: "proposal",
          resource_id: "proposal-date-001",
          summary: `Decisión ${payload.decision} recibida por el backend.`,
        });
      }
      await json(route, result);
      return;
    }

    if (path.endsWith("/projects/hormigestio/reports") && request.method() === "GET") {
      const available = options.reportAvailability ?? "available";
      await json(route, { availability: available, items: available === "available" ? [currentReport] : [], total: available === "available" ? 1 : 0, page: Number(url.searchParams.get("page") ?? 1), page_size: 25 });
      return;
    }

    if (path.endsWith("/projects/hormigestio/reports/report-001") && request.method() === "GET") {
      await json(route, currentReport);
      return;
    }

    if (path.endsWith("/projects/hormigestio/reports/report-001/decision") && request.method() === "POST") {
      expect(request.headers()["x-csrf-token"]).toBe("csrf-fixture");
      expect(request.headers()["idempotency-key"]).toBeTruthy();
      const payload = JSON.parse(request.postData() ?? "{}");
      if (options.reportDecisionStatus && options.reportDecisionStatus !== 200) {
        await json(route, { error: { code: "report_decisions_no_habilitadas", message: "La decisión humana está deshabilitada por el servidor." } }, options.reportDecisionStatus);
        return;
      }
      const event = { decision_id: "report-decision-001", report_id: "report-001", run_id: "run-current", decision: payload.decision, reason: payload.reason, recorded_at: "2026-01-12T10:17:00-05:00", actor_id: null, actor_verification: "unknown" };
      currentReport = { ...currentReport, human_decision: payload.decision, decision_reason: payload.reason, decision_timestamp: event.recorded_at, decision_history: [...currentReport.decision_history, event] };
      await json(route, { report: currentReport, decision_event: event, reused: false, audit_event_id: "audit-report-decision-001" });
      return;
    }

    if (path.endsWith("/projects/hormigestio/reports/report-001/download")) {
      const format = url.searchParams.get("format");
      downloadRequests.push(`report:${format}`);
      const isJson = format === "json";
      await route.fulfill({
        status: 200,
        contentType: isJson ? "application/json" : "text/markdown; charset=utf-8",
        headers: { "content-disposition": `attachment; filename=\"report-report-001.${isJson ? "json" : "md"}\"` },
        body: isJson ? JSON.stringify(currentReport) : currentReport.markdown_text ?? "",
      });
      return;
    }

    if (path.endsWith("/projects/hormigestio/audit") && request.method() === "GET") {
      if (!authenticated || !sessionCapabilities.includes("audit:read")) {
        await json(route, { error: { code: "audit_access_required", message: "Se requiere la capacidad de auditoría." } }, 403);
        return;
      }
      const eventType = url.searchParams.get("event_type");
      if (options.auditStatus && options.auditStatus !== 200) {
        await json(route, { error: { code: "audit_unavailable", message: "El historial no está disponible." } }, options.auditStatus);
        return;
      }
      const items = eventType ? currentAuditItems.filter((item) => item.event_type === eventType) : currentAuditItems;
      await json(route, { ...auditCollection, items, total: items.length, page: Number(url.searchParams.get("page") ?? 1) });
      return;
    }

    const auditDetailMatch = path.match(/\/projects\/hormigestio\/audit\/([^/]+)$/);
    if (auditDetailMatch && request.method() === "GET") {
      if (!authenticated || !sessionCapabilities.includes("audit:read")) {
        await json(route, { error: { code: "audit_access_required", message: "Se requiere la capacidad de auditoría." } }, 403);
        return;
      }
      const event = currentAuditItems.find((item) => item.event_id === auditDetailMatch[1]);
      await json(route, event ? { ...event, details: { source: event.source, captured: true } } : { error: { code: "event_not_found", message: "Evento no encontrado." } }, event ? 200 : 404);
      return;
    }

    if (path.endsWith("/projects/hormigestio/configuration") && request.method() === "GET") {
      if (!authenticated || !sessionCapabilities.includes("configuration:read")) {
        await json(route, { error: { code: "configuration_access_required", message: "Se requiere la capacidad de configuración." } }, 403);
        return;
      }
      if (options.configurationStatus && options.configurationStatus !== 200) {
        await json(route, { error: { code: "configuration_unavailable", message: "La configuración no está disponible." } }, options.configurationStatus);
        return;
      }
      await json(route, projectConfiguration);
      return;
    }

    if (path.endsWith("/projects/hormigestio/schedule") && request.method() === "GET") {
      await json(route, { ...scheduleView, availability: options.scheduleAvailability ?? "configured" });
      return;
    }

    if (path.endsWith("/projects/hormigestio/schedule/outbox") && request.method() === "GET") {
      const available = options.outboxAvailability ?? "available";
      await json(route, { availability: available, items: available === "available" ? [outboxReceipt] : [], total: available === "available" ? 1 : 0, page: Number(url.searchParams.get("page") ?? 1), page_size: 25 });
      return;
    }

    if (path.endsWith("/projects/hormigestio/schedule/outbox/delivery-001") && request.method() === "GET") {
      await json(route, outboxDetail);
      return;
    }

    if (path.endsWith("/projects/hormigestio/schedule/outbox/delivery-001/download")) {
      const format = url.searchParams.get("format");
      downloadRequests.push(`outbox:${format}`);
      const isJson = format === "json";
      await route.fulfill({
        status: 200,
        contentType: isJson ? "application/json" : "text/markdown; charset=utf-8",
        headers: { "content-disposition": `attachment; filename=\"outbox-delivery-001.${isJson ? "json" : "md"}\"` },
        body: isJson ? JSON.stringify(outboxDetail) : outboxDetail.message.body_markdown,
      });
      return;
    }

    if (path.endsWith("/projects/hormigestio/schedule/manual-cycle/prepare") && request.method() === "POST") {
      expect(request.headers()["x-csrf-token"]).toBe("csrf-fixture");
      const eligible = options.cyclePrepareEligible ?? true;
      cyclePrepared = true;
      await json(route, { ...schedulePreparation, eligible, preflight: { ...schedulePreparation.preflight, can_run: eligible } });
      return;
    }

    if (path.endsWith("/projects/hormigestio/schedule/manual-cycle/confirm") && request.method() === "POST") {
      expect(request.headers()["x-csrf-token"]).toBe("csrf-fixture");
      expect(request.headers()["idempotency-key"]).toBeTruthy();
      const payload = JSON.parse(request.postData() ?? "{}");
      if (!cyclePrepared || payload.remote_consent !== false || payload.confirm_cycle !== true) {
        await json(route, { error: { code: "invalid_cycle_confirmation", message: "La confirmación no coincide con la preparación." } }, 409);
        return;
      }
      if (options.cycleConfirmStatus && options.cycleConfirmStatus !== 202) {
        await json(route, { error: { code: "weekly_cycle_no_habilitado", message: "El ciclo semanal no está habilitado." } }, options.cycleConfirmStatus);
        return;
      }
      cycleConfirmed = true;
      await json(route, { job: weeklyCycleJob, reused: false, poll_url: "/api/v1/projects/hormigestio/schedule/manual-cycle/jobs/cycle-job-001", poll_after_ms: 250 }, 202);
      return;
    }

    if (path.endsWith("/projects/hormigestio/schedule/manual-cycle/jobs") && request.method() === "GET") {
      if (!authenticated || !sessionCapabilities.includes("weekly_cycle:confirm")) {
        await json(route, { error: { code: "weekly_cycle_access_required", message: "Se requiere la capacidad del ciclo semanal." } }, 403);
        return;
      }
      await json(route, { availability: "available", items: cycleConfirmed ? [weeklyCycleJob] : [], total: cycleConfirmed ? 1 : 0, page: 1, page_size: 25 });
      return;
    }

    if (path.endsWith("/projects/hormigestio/schedule/manual-cycle/jobs/cycle-job-001") && request.method() === "GET") {
      if (!authenticated || !sessionCapabilities.includes("weekly_cycle:confirm")) {
        await json(route, { error: { code: "weekly_cycle_access_required", message: "Se requiere la capacidad del ciclo semanal." } }, 403);
        return;
      }
      const statuses = options.weeklyCycleStatuses ?? ["completed"];
      const status = statuses[Math.min(cyclePollCount, statuses.length - 1)];
      cyclePollCount += 1;
      await json(route, { ...weeklyCycleJob, status, outcome: status === "completed" ? "SKIPPED_NOT_DUE" : "unknown", updated_at: "2026-01-12T10:14:00-05:00" });
      return;
    }

    if (path.endsWith("/projects/hormigestio/summary")) {
      await json(route, options.summaryPayload ?? summary);
      return;
    }

    if (path.endsWith("/projects/hormigestio/history")) {
      await json(route, options.historyPayload ?? history);
      return;
    }

    if (path.endsWith("/projects/hormigestio/executive-summary")) {
      await json(route, executiveSummary);
      return;
    }

    if (path.endsWith("/projects/hormigestio/quality")) {
      await json(route, quality);
      return;
    }

    if (path.endsWith("/projects/hormigestio/policies")) {
      await json(route, policies);
      return;
    }

    if (path.endsWith("/runs") && request.method() === "GET") {
      await json(route, runHistory);
      return;
    }

    if (path.endsWith("/runs/run-current/stages")) {
      await json(route, stageRecords);
      return;
    }

    if (path.endsWith("/runs/run-current/findings/finding-candidate")) {
      await json(route, findingDetail);
      return;
    }

    if (path.endsWith("/runs/run-current/findings")) {
      await json(route, { items: [candidateFinding], total: 1, page: 1, page_size: 25 });
      return;
    }

    if (path.endsWith("/runs/run-current")) {
      await json(route, runCurrent);
      return;
    }

    if (path.endsWith("/analysis/prepare") && request.method() === "POST") {
      if (options.prepareStatus && options.prepareStatus !== 200) {
        await json(route, { error: { code: "preparation_forbidden", message: "La preparación no está autorizada por el servidor." } }, options.prepareStatus);
      } else {
        await json(route, { ...analysisPreparation, ...options.preparePayload });
      }
      return;
    }

    if (path.endsWith("/analysis/confirm") && request.method() === "POST") {
      const status = options.confirmStatuses?.[confirmCount] ?? 202;
      confirmCount += 1;
      if (status !== 202) {
        await json(route, { error: { code: "confirmation_conflict", message: "El servidor rechazó la confirmación." } }, status);
        return;
      }
      confirmedJob = true;
      await json(route, { job: { ...analysisJob, ...options.analysisJobPayload }, reused: false, poll_url: "/api/v1/analysis/jobs/job-current", poll_after_ms: 250 }, 202);
      return;
    }

    if (path.endsWith("/analysis/jobs") && request.method() === "GET") {
      const currentAnalysisJob = { ...analysisJob, ...options.analysisJobPayload };
      await json(route, confirmedJob
        ? { items: [currentAnalysisJob], total: 1, page: 1, page_size: 25 }
        : { items: [], total: 0, page: 1, page_size: 25 });
      return;
    }

    if (path.endsWith("/analysis/jobs/job-current") && request.method() === "GET") {
      jobPollRequests += 1;
      const statuses = options.jobPollStatuses ?? ["queued"];
      const status = statuses[Math.min(jobPollCount, statuses.length - 1)];
      jobPollCount += 1;
      await json(route, {
        ...analysisJob,
        ...options.analysisJobPayload,
        status,
        updated_at: status === "queued" ? analysisJob.updated_at : "2026-01-12T10:11:00-05:00",
        run_id: status === "completed" ? "run-current" : null,
        error_code: status === "failed" ? "analysis_failed" : status === "interrupted" ? "worker_interrupted" : null,
      });
      return;
    }

    if (path.endsWith("/projects/hormigestio/compare")) {
      expect(url.searchParams.get("before")).toBe("capture-previous");
      expect(url.searchParams.get("after")).toBe("capture-current");
      await json(route, comparison);
      return;
    }

    if (path.endsWith("/projects/hormigestio/tasks/task-1")) {
      await json(route, {
        ...tasks[0],
        evidence: [
          {
            provenance: { id: "evidence-1", source: "trello", project_id: "hormigestio", timestamp: summary.captured_at },
            category: "task",
            summary: "Prueba observada en el tablero",
          },
        ],
        history: [
          {
            id: "change-1",
            task_id: "task-1",
            kind: "field_changed",
            field: "state",
            before: "pending",
            after: "en_curso",
            previous_evidence_ids: [],
            current_evidence_ids: ["evidence-1"],
            actor: null,
          },
        ],
      });
      return;
    }

    const taskDetailMatch = path.match(/\/projects\/hormigestio\/tasks\/([^/]+)$/);
    if (taskDetailMatch) {
      const task = allTasks.find((item) => item.id === taskDetailMatch[1]);
      if (task) {
        await json(route, task);
        return;
      }
    }

    if (path.endsWith("/projects/hormigestio/tasks")) {
      if (url.searchParams.get("critical_only") === "true" && options.criticalStatus) {
        await json(route, { error: { code: "temporary_unavailable", message: "El filtro no está disponible." } }, options.criticalStatus);
        return;
      }
      const pageNumber = Number(url.searchParams.get("page") ?? 1);
      const requestedPageSize = Number(url.searchParams.get("page_size") ?? 25);
      let filtered = [...allTasks];
      const q = url.searchParams.get("q")?.toLocaleLowerCase("es-EC");
      if (q) filtered = filtered.filter((task) => task.title.toLocaleLowerCase("es-EC").includes(q));
      const state = url.searchParams.get("state");
      if (state) filtered = filtered.filter((task) => task.state === state);
      const assigneeId = url.searchParams.get("assignee_id");
      if (assigneeId) filtered = filtered.filter((task) => task.assignee_ids.includes(assigneeId));
      const listName = url.searchParams.get("list_name");
      if (listName) filtered = filtered.filter((task) => task.list_name === listName);
      if (url.searchParams.get("critical_only") === "true") filtered = filtered.filter((task) => task.is_critical);
      if (url.searchParams.get("overdue_only") === "true") filtered = filtered.filter((task) => task.is_overdue);
      if (url.searchParams.get("due_soon_only") === "true") filtered = filtered.filter((task) => task.is_due_soon);
      const sort = url.searchParams.get("sort");
      const direction = url.searchParams.get("direction") === "desc" ? -1 : 1;
      if (sort === "title") filtered.sort((left, right) => left.title.localeCompare(right.title) * direction);
      if (sort === "due_date") filtered.sort((left, right) => (left.due_date ?? "").localeCompare(right.due_date ?? "") * direction);
      const offset = (pageNumber - 1) * requestedPageSize;
      await json(route, {
        items: filtered.slice(offset, offset + requestedPageSize),
        total: filtered.length,
        page: pageNumber,
        page_size: requestedPageSize,
      });
      return;
    }

    await json(route, { error: { code: "not_found", message: `Sin fixture para ${path}` } }, 404);
  });
  return {
    analysisWrites,
    governanceRequests,
    reportDecisionRequests,
    weeklyCycleRequests,
    moduleReads,
    downloadRequests,
    get jobPollRequests() { return jobPollRequests; },
    get analysisJobListReads() { return analysisJobListReads; },
  };
}

