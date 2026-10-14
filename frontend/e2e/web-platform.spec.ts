import { expect, test, type Page, type Route } from "@playwright/test";

const summary = {
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

const project = {
  id: "hormigestio",
  name: "HORMIGESTION",
  source_mode: "trello",
  latest_capture_at: summary.captured_at,
  latest_run_id: null,
  latest_run_status: null,
  summary,
};

const secondProject = {
  ...project,
  id: "proyecto-secundario",
  name: "Proyecto secundario",
  summary: null,
};

const tasks = [
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

const allTasks = [
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

const history = {
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

const runCurrent = {
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

const runPrevious = {
  ...runCurrent,
  run_id: "run-previous",
  status: "needs_attention",
  created_at: "2026-01-05T10:05:00-05:00",
  updated_at: "2026-01-05T10:06:00-05:00",
  snapshot_id: "capture-previous",
  duration_seconds: 77.25,
  report_id: null,
};

const runHistory = {
  items: [runCurrent, runPrevious],
  total: 2,
  page: 1,
  page_size: 25,
};

const stageRecords = {
  run_id: "run-current",
  items: [
    { stage_name: "source_collection", status: "completed", recorded: true, duration_seconds: 4, completed_at: "2026-01-12T10:05:04-05:00", providers: null, result_summary: "Fuentes almacenadas consultadas", error_code: null },
    { stage_name: "monitoring", status: "completed", recorded: true, duration_seconds: 30, completed_at: "2026-01-12T10:05:34-05:00", providers: [{ provider: "local", model: "fixture-monitor", status: "succeeded", attempt_count: 1, duration_seconds: 30 }], result_summary: "Monitoreo persistido", error_code: null },
    { stage_name: "quality", status: "completed", recorded: true, duration_seconds: 42, completed_at: "2026-01-12T10:06:16-05:00", providers: [{ provider: "local", model: "fixture-quality", status: "succeeded", attempt_count: 1, duration_seconds: 42 }], result_summary: "Dos hallazgos candidatos", error_code: null },
    { stage_name: "verifier", status: "unknown", recorded: false, duration_seconds: null, completed_at: null, providers: null, result_summary: null, error_code: null },
    { stage_name: "report", status: "skipped", recorded: true, duration_seconds: null, completed_at: null, providers: null, result_summary: null, error_code: null },
  ],
};

const candidateFinding = {
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

const findingDetail = {
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

const quality = {
  project_id: "hormigestio",
  run_id: "run-current",
  stage_status: "completed",
  availability: "available",
  finding_counts: { candidate: 2, confirmed: 1, rejected: 0, insufficient: 1, pending: 0, unknown: 0 },
  ci: null,
};

const policies = {
  project_id: "hormigestio",
  run_id: "run-current",
  availability: "available",
  items: [
    { rule_id: "Q1", description: "Cobertura mínima documentada", status: "pass", mandatory: true, observed_value: 85, reason: null },
    { rule_id: "Q2", description: "Verificación independiente", status: "unknown", mandatory: true, observed_value: null, reason: "No hay evidencia suficiente para decidir." },
  ],
};

const executiveSummary = {
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

const analysisPreparation = {
  preparation_id: "preparation-fixture-12345678",
  project_id: "hormigestio",
  expires_at: "2026-01-12T11:00:00-05:00",
  configuration_fingerprint: "a".repeat(64),
  eligible: true,
  capabilities: ["analysis:prepare", "analysis:confirm"],
  preflight: { source_mode: "trello", configuration_status: "ready", checks: [{ code: "source_configured", status: "ready" }, { code: "local_state", status: "unknown" }] },
  remote: { required: true, server_allowed: true, availability: "not_checked", providers: [{ stage: "quality", provider: "gemini", model: "gemini-fixture", transport: "remote", credential_status: "configured" }] },
};

const analysisJob = { job_id: "job-current", project_id: "hormigestio", status: "queued", created_at: "2026-01-12T10:10:00-05:00", updated_at: "2026-01-12T10:10:00-05:00", run_id: null, error_code: null, remote_consent: true };

const comparison = {
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

const proposalSummary = {
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
  freshness: "current",
  review_status: "pending",
  authorization_status: "not_authorized",
  execution_status: "not_executed",
};

const proposalDetail = {
  ...proposalSummary,
  decision_history_availability: "available",
  dry_run_availability: "available",
  decision_history: [],
  dry_runs: [],
};

const reportSummary = {
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

const reportDetail = {
  ...reportSummary,
  decision_history: [],
  markdown_text: "# Informe almacenado\\n\\nResumen del resultado.\\n\\n<img src=\\\"https://beacon.invalid/pixel\\\" onerror=\\\"alert(1)\\\">\\n\\n[Enlace peligroso](javascript:alert(1))\\n\\n![Baliza](https://beacon.invalid/pixel.png)\\n\\n[Documentación](https://example.invalid/docs)",
};

const auditEvent = {
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

const unknownAuditEvent = {
  ...auditEvent,
  event_id: "audit-legacy-002",
  event_type: "proposal_review",
  actor_id: null,
  actor_verification: "unknown",
  source: "core",
  summary: "Evento heredado sin atribución verificable.",
};

const outboxReceipt = {
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

const weeklySchedule = {
  project_id: "hormigestio",
  weekday: 4,
  local_time: "17:00",
  timezone: "America/Guayaquil",
  window_rule: "previous_scheduled_week",
};

const weeklyWindow = {
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

const scheduleView = {
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

const outboxMessage = {
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

const outboxDetail = { receipt: outboxReceipt, message: outboxMessage };

const schedulePreparation = {
  preparation_id: "cycle-preparation-001",
  project_id: "hormigestio",
  expires_at: "2026-01-12T11:00:00-05:00",
  configuration_fingerprint: "c".repeat(64),
  eligible: true,
  capabilities: ["weekly_cycle:prepare", "weekly_cycle:confirm"],
  preflight: { schedule_status: "configured", window_id: "window-001", has_captures: true, outbox_history: "available", report_binding: "available", can_run: true },
  remote: { required: false, server_allowed: false, availability: "not_checked", providers: [] },
};

const weeklyCycleJob = {
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

const projectConfiguration = {
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

const auditCollection = {
  availability: "available",
  items: [auditEvent, unknownAuditEvent],
  total: 2,
  page: 1,
  page_size: 25,
};

async function json(route: Route, value: unknown, status = 200) {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
}

async function installApi(page: Page, options: {
  loginStatus?: number;
  projectStatus?: number;
  projectPagination?: boolean;
  summaryPayload?: unknown;
  historyPayload?: unknown;
  criticalStatus?: number;
  authenticated?: boolean;
  capabilities?: string[];
  prepareStatus?: number;
  confirmStatuses?: number[];
  jobPollStatuses?: Array<"queued" | "running" | "completed" | "failed" | "interrupted">;
  proposalWriteStatus?: number;
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
} = {}) {
  let authenticated = options.authenticated ?? false;
  let confirmedJob = false;
  let confirmCount = 0;
  let jobPollCount = 0;
  let jobPollRequests = 0;
  let analysisJobListReads = 0;
  type ProposalHistoryItem = { event_id: string; decision: string; version: number; recorded_at: string | null; actor_id: string | null; actor_provenance: string };
  type ProposalDryRunItem = { request_id: string; status: string; reasons: string[]; field: string | null; before: unknown; after: unknown; recorded_at: string; execution_status: string; live_state_changed: false; real_execution_allowed: false };
  let currentProposal = { ...proposalDetail, decision_history: [] as ProposalHistoryItem[], dry_runs: [] as ProposalDryRunItem[] };
  type ReportDecisionItem = { decision_id: string; report_id: string; run_id: string | null; decision: string; reason: string | null; recorded_at: string; actor_id: null; actor_verification: "unknown" };
  let currentReport = { ...reportDetail, decision_history: [] as ReportDecisionItem[] };
  let cyclePrepared = false;
  let cycleConfirmed = false;
  let cyclePollCount = 0;
  const analysisWrites: Array<{ path: string; body: string | null; csrfToken: string | null; idempotencyKey: string | null }> = [];
  const governanceRequests: Array<{ path: string; body: string | null; csrfToken: string | null; idempotencyKey: string | null }> = [];
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
        actor: authenticated ? "operadora" : null,
        csrf_token: authenticated ? "csrf-fixture" : null,
        capabilities: authenticated ? sessionCapabilities : [],
        login_available: true,
      });
      return;
    }

    if (path.endsWith("/auth/login")) {
      if (loginStatus === 200) authenticated = true;
      await json(route, loginStatus === 200
        ? { token: "opaque-session-value" }
        : { error: { code: "invalid_token", message: "El token de operador no es válido." } }, loginStatus);
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

    if (path.endsWith("/projects/hormigestio/proposals/proposal-date-001/review") && request.method() === "POST") {
      expect(request.headers()["x-csrf-token"]).toBe("csrf-fixture");
      expect(request.headers()["idempotency-key"]).toBeTruthy();
      const payload = JSON.parse(request.postData() ?? "{}");
      if (options.proposalWriteStatus && options.proposalWriteStatus !== 200) {
        await json(route, { error: { code: "gobernanza_no_habilitada", message: "La revisión local no está habilitada por el servidor." } }, options.proposalWriteStatus);
        return;
      }
      const version = payload.expected_version + 1;
      const event = { event_id: "proposal-review-001", decision: payload.decision === "approve" ? "approve_offline" : "reject", version, recorded_at: "2026-01-12T10:14:00-05:00", actor_id: null, actor_provenance: "unknown" };
      currentProposal = { ...currentProposal, version, review_status: payload.decision === "approve" ? "approved_offline" : "rejected", decision_history: [...currentProposal.decision_history, event] };
      await json(route, { proposal: currentProposal, reused: false, audit_event_id: "audit-proposal-001" });
      return;
    }

    if (path.endsWith("/projects/hormigestio/proposals/proposal-date-001/dry-run-authorization") && request.method() === "POST") {
      expect(request.headers()["x-csrf-token"]).toBe("csrf-fixture");
      expect(request.headers()["idempotency-key"]).toBeTruthy();
      const payload = JSON.parse(request.postData() ?? "{}");
      if (options.proposalWriteStatus && options.proposalWriteStatus !== 200) {
        await json(route, { error: { code: "gobernanza_no_habilitada", message: "La autorización de simulación no está habilitada." } }, options.proposalWriteStatus);
        return;
      }
      const version = payload.expected_version + 1;
      const event = { event_id: "proposal-authorization-001", decision: "authorize_dry_run", version, recorded_at: "2026-01-12T10:15:00-05:00", actor_id: null, actor_provenance: "unknown" };
      currentProposal = { ...currentProposal, version, authorization_status: "authorized_dry_run", decision_history: [...currentProposal.decision_history, event] };
      await json(route, { proposal: currentProposal, reused: false, audit_event_id: "audit-authorization-001" });
      return;
    }

    if (path.endsWith("/projects/hormigestio/proposals/proposal-date-001/dry-runs") && request.method() === "POST") {
      expect(request.headers()["x-csrf-token"]).toBe("csrf-fixture");
      expect(request.headers()["idempotency-key"]).toBeTruthy();
      const payload = JSON.parse(request.postData() ?? "{}");
      if (options.proposalWriteStatus && options.proposalWriteStatus !== 200) {
        await json(route, { error: { code: "permiso_de_simulacion_requerido", message: "La simulación no está autorizada." } }, options.proposalWriteStatus);
        return;
      }
      const dryRun = { request_id: request.headers()["idempotency-key"], status: "SIMULATED", reasons: [], field: currentProposal.field, before: currentProposal.current_value, after: currentProposal.proposed_value, recorded_at: "2026-01-12T10:16:00-05:00", execution_status: "not_executed", live_state_changed: false, real_execution_allowed: false };
      currentProposal = { ...currentProposal, dry_runs: [...currentProposal.dry_runs, dryRun] };
      expect(payload.permission_event_id).toBe("proposal-authorization-001");
      await json(route, { dry_run: dryRun, reused: false, audit_event_id: "audit-dry-run-001" });
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
      const items = eventType ? auditCollection.items.filter((item) => item.event_type === eventType) : auditCollection.items;
      await json(route, { ...auditCollection, items, total: items.length, page: Number(url.searchParams.get("page") ?? 1) });
      return;
    }

    const auditDetailMatch = path.match(/\/projects\/hormigestio\/audit\/([^/]+)$/);
    if (auditDetailMatch && request.method() === "GET") {
      if (!authenticated || !sessionCapabilities.includes("audit:read")) {
        await json(route, { error: { code: "audit_access_required", message: "Se requiere la capacidad de auditoría." } }, 403);
        return;
      }
      const event = auditCollection.items.find((item) => item.event_id === auditDetailMatch[1]);
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
        await json(route, analysisPreparation);
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
      await json(route, { job: analysisJob, reused: false, poll_url: "/api/v1/analysis/jobs/job-current", poll_after_ms: 250 }, 202);
      return;
    }

    if (path.endsWith("/analysis/jobs") && request.method() === "GET") {
      await json(route, confirmedJob
        ? { items: [analysisJob], total: 1, page: 1, page_size: 25 }
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

test("el dashboard enlaza estados y señales desde conteos del backend sin sumar categorías solapadas", async ({ page }) => {
  await installApi(page);
  await page.goto("/");

  await expect(page.getByRole("link", { name: /Pendientes/ }).getByText("0", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /En curso/ }).getByText("1", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /Próximas a vencer/ }).getByText("1", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /Actividades críticas/ }).getByText("1", { exact: true })).toBeVisible();
  await expect(page.getByRole("img", { name: "Distribución de tareas por estado" })).toBeVisible();
  await expect(page.getByRole("img", { name: "Señales de actividad crítica por categoría" })).toBeVisible();
  await expect(page.getByText("Las categorías de alerta se solapan; no deben sumarse como un total.")).toBeVisible();
  await expect(page.getByRole("link", { name: /Actividades críticas/ })).toHaveAttribute("href", "/tasks?critical_only=true");

  await page.getByRole("link", { name: /Pendientes/ }).click();
  await expect(page).toHaveURL(/state=pending/);
  const selectedState = await page.locator('select[aria-label="Estado"]').evaluate((element) => (element as HTMLSelectElement).value);
  expect(selectedState).toBe("pending");
});

test("mantiene visibles los KPI y gráficos del dashboard en una pantalla móvil", async ({ page }) => {
  await installApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");

  await expect(page.getByRole("link", { name: /Actividades críticas/ })).toBeVisible();
  await expect(page.getByRole("img", { name: "Distribución de tareas por estado" })).toBeVisible();
  await expect(page.getByRole("img", { name: "Señales de actividad crítica por categoría" })).toBeVisible();
  const hasHorizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(hasHorizontalOverflow).toBe(false);
});

test("los centros de análisis y calidad conservan el layout móvil sin desbordamiento", async ({ page }) => {
  await installApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/analysis?run_id=run-current");
  await expect(page.getByRole("heading", { name: "Ejecución run-current" })).toBeVisible();
  let overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);

  await page.goto("/quality");
  await expect(page.getByRole("heading", { name: "Centro de calidad de código" })).toBeVisible();
  await page.getByRole("button", { name: "Revisar hallazgo finding-candidate" }).click();
  await expect(page.getByRole("dialog", { name: /Hallazgo candidato/ })).toBeVisible();
  overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});

test("no convierte conteos de estado ausentes en ceros locales", async ({ page }) => {
  await installApi(page, { summaryPayload: { ...summary, state_counts: { en_curso: 1 } } });
  await page.goto("/");

  await expect(page.getByRole("link", { name: /Pendientes/ }).getByText("Sin dato")).toBeVisible();
  await expect(page.getByRole("link", { name: /En curso/ }).getByText("1", { exact: true })).toBeVisible();
  await expect(page.getByRole("img", { name: "Distribución de tareas por estado" })).toBeVisible();
});

test("no dibuja tendencias temporales a partir de una sola captura", async ({ page }) => {
  await installApi(page, { historyPayload: { ...history, items: [history.items[0]], total: 1 } });
  await page.goto("/");

  const historySection = page.locator(".history-section");
  await expect(historySection.getByRole("heading", { name: "Una sola captura disponible" })).toBeVisible();
  await expect(historySection.getByRole("img")).toHaveCount(0);
});

test("un error en la consulta crítica permanece desconocido y no se presenta como cero", async ({ page }) => {
  await installApi(page, { criticalStatus: 503 });
  await page.goto("/");

  const criticalMetric = page.getByRole("link", { name: /Actividades críticas/ });
  await expect(criticalMetric.getByText("Sin dato")).toBeVisible();
  await expect(criticalMetric.getByText("0", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "No se pudieron cargar las tareas críticas" })).toBeVisible();
});

test("el dashboard presenta políticas e historial de ejecuciones solo con registros reales", async ({ page }) => {
  await installApi(page);
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Informe y políticas de calidad" })).toBeVisible();
  await expect(page.getByText("Revisar los hallazgos con evidencia insuficiente.", { exact: true })).toBeVisible();
  const policyPanel = page.locator(".executive-policies-card");
  await expect(policyPanel.getByText("Q1", { exact: true })).toBeVisible();
  await expect(policyPanel.getByText("Q2", { exact: true })).toBeVisible();
  await expect(policyPanel.getByText("Q3", { exact: true })).toHaveCount(0);
  await expect(policyPanel.getByText("Desconocido", { exact: true })).toBeVisible();
  await expect(policyPanel.getByText("Observado: Sin dato", { exact: true })).toBeVisible();
  await expect(page.locator(".executive-report-card").getByText(/Fecha de corte:/)).toBeVisible();
  await expect(page.getByRole("img", { name: "Historial de análisis por estado" })).toBeVisible();
});

test("el centro de IA consulta el historial sin iniciar trabajos al abrirlo", async ({ page }) => {
  const apiObserver = await installApi(page);
  const { analysisWrites } = apiObserver;
  await page.goto("/analysis");

  await expect(page.getByRole("heading", { name: "Centro de análisis de IA" })).toBeVisible();
  await expect(page.getByText("Historial de ejecuciones", { exact: true })).toBeVisible();
  await expect(page.getByText("run-current", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Preparar análisis" })).toBeDisabled();
  await expect(page.getByRole("heading", { name: "Historial de trabajos protegido" })).toBeVisible();
  expect(analysisWrites).toHaveLength(0);
  expect(apiObserver.analysisJobListReads).toBe(0);
});

test("una etapa desconocida no se presenta como omitida y una omisión requiere registro explícito", async ({ page }) => {
  await installApi(page);
  await page.goto("/analysis?run_id=run-current");

  await expect(page.getByText("No registrado; estado desconocido", { exact: true })).toBeVisible();
  await expect(page.getByText("Omitida explícitamente", { exact: true })).toBeVisible();
  await expect(page.getByText("Fuentes almacenadas consultadas", { exact: true })).toBeVisible();
  await expect(page.getByText("local · fixture-monitor", { exact: true })).toBeVisible();
});

test("la calidad conserva hallazgos candidatos, verificaciones y fragmentos como texto", async ({ page }) => {
  await installApi(page);
  await page.goto("/quality");

  await expect(page.getByRole("heading", { name: "Centro de calidad de código" })).toBeVisible();
  await expect(page.getByText("Hallazgo candidato", { exact: true })).toBeVisible();
  await expect(page.locator(".finding-list").getByText("Evidencia insuficiente", { exact: true })).toBeVisible();
  await expect(page.getByText(/CI.*no disponible/i)).toBeVisible();
  await page.getByRole("button", { name: "Revisar hallazgo finding-candidate" }).click();
  await expect(page.getByRole("dialog", { name: /Hallazgo candidato/ })).toBeVisible();
  await expect(page.getByText("El fragmento mostrado es parcial.", { exact: true })).toBeVisible();
  await expect(page.locator("pre")).toContainText("<script>window.__findingXss = true</script>");
  expect(await page.evaluate(() => (window as Window & { __findingXss?: boolean }).__findingXss)).toBeUndefined();
});

test("la preparación y la confirmación son pasos protegidos independientes con consentimiento remoto", async ({ page }) => {
  const apiObserver = await installApi(page, {
    authenticated: true,
    capabilities: ["analysis:prepare", "analysis:confirm"],
    jobPollStatuses: ["running", "completed"],
  });
  const { analysisWrites } = apiObserver;
  await page.goto("/analysis");
  await expect(page.getByRole("button", { name: "Preparar análisis" })).toBeEnabled();
  expect(analysisWrites).toHaveLength(0);

  await page.getByRole("button", { name: "Preparar análisis" }).click();
  await expect(page.getByText("La disponibilidad remota no se comprueba durante la preparación.", { exact: true })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /consentimiento explícito para usar proveedores remotos/i })).not.toBeChecked();
  const confirmButton = page.getByRole("button", { name: "Confirmar análisis" });
  await expect(confirmButton).toBeDisabled();
  expect(analysisWrites).toHaveLength(1);
  expect(analysisWrites[0].path).toBe("/api/v1/projects/hormigestio/analysis/prepare");
  expect(JSON.parse(analysisWrites[0].body ?? "{}")).toEqual({ request_analysis: true });
  expect(analysisWrites[0].csrfToken).toBe("csrf-fixture");

  await page.getByRole("checkbox", { name: /consentimiento explícito para usar proveedores remotos/i }).check();
  await expect(confirmButton).toBeEnabled();
  await confirmButton.click();
  await expect(page.getByText("Trabajo de análisis: completado", { exact: true })).toBeVisible();
  expect(analysisWrites).toHaveLength(2);
  expect(analysisWrites[1].path).toBe("/api/v1/projects/hormigestio/analysis/confirm");
  expect(JSON.parse(analysisWrites[1].body ?? "{}")).toEqual({
    preparation_id: "preparation-fixture-12345678",
    configuration_fingerprint: "a".repeat(64),
    confirm_analysis: true,
    remote_consent: true,
  });
  expect(analysisWrites[1].csrfToken).toBe("csrf-fixture");
  expect(analysisWrites[1].idempotencyKey).toBeTruthy();
  expect(apiObserver.jobPollRequests).toBe(2);
  await page.waitForTimeout(350);
  expect(apiObserver.jobPollRequests).toBe(2);
});

test("la interfaz no inicia trabajo si la sesión carece de la capacidad de preparación", async ({ page }) => {
  const { analysisWrites } = await installApi(page, { authenticated: true, capabilities: ["operator_session"] });
  await page.goto("/analysis");

  await expect(page.getByRole("button", { name: "Preparar análisis" })).toBeDisabled();
  await expect(page.getByText(/analysis:prepare/)).toBeVisible();
  expect(analysisWrites).toHaveLength(0);
});

test("la capacidad de preparación no sustituye la capacidad de confirmación", async ({ page }) => {
  const { analysisWrites } = await installApi(page, { authenticated: true, capabilities: ["analysis:prepare"] });
  await page.goto("/analysis");
  await page.getByRole("button", { name: "Preparar análisis" }).click();
  await page.getByRole("checkbox", { name: /consentimiento explícito para usar proveedores remotos/i }).check();

  await expect(page.getByRole("button", { name: "Confirmar análisis" })).toBeDisabled();
  await expect(page.locator(".preparation-result").getByText(/La sesión no informa la capacidad analysis:confirm/)).toBeVisible();
  expect(analysisWrites.map((write) => write.path)).toEqual(["/api/v1/projects/hormigestio/analysis/prepare"]);
});

test("un rechazo de autorización del servidor se muestra y no crea un trabajo", async ({ page }) => {
  const { analysisWrites } = await installApi(page, {
    authenticated: true,
    capabilities: ["analysis:prepare", "analysis:confirm"],
    prepareStatus: 403,
  });
  await page.goto("/analysis");
  await page.getByRole("button", { name: "Preparar análisis" }).click();

  await expect(page.getByRole("heading", { name: "No se pudo preparar el análisis" })).toBeVisible();
  await expect(page.getByText("La preparación no está autorizada por el servidor.", { exact: true })).toBeVisible();
  expect(analysisWrites).toHaveLength(1);
  expect(analysisWrites[0].path).toBe("/api/v1/projects/hormigestio/analysis/prepare");
});

test("un reintento de confirmación conserva la misma clave de idempotencia", async ({ page }) => {
  const { analysisWrites } = await installApi(page, {
    authenticated: true,
    capabilities: ["analysis:prepare", "analysis:confirm"],
    confirmStatuses: [503, 202],
  });
  await page.goto("/analysis");
  await page.getByRole("button", { name: "Preparar análisis" }).click();
  await page.getByRole("checkbox", { name: /consentimiento explícito para usar proveedores remotos/i }).check();
  await page.getByRole("button", { name: "Confirmar análisis" }).click();
  await expect(page.getByRole("heading", { name: "No se pudo confirmar el análisis" })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /consentimiento explícito para usar proveedores remotos/i })).toBeDisabled();
  await page.getByRole("button", { name: "Intentar de nuevo" }).click();

  await expect(page.getByText("Trabajo de análisis: en cola", { exact: true })).toBeVisible();
  const confirmations = analysisWrites.filter((write) => write.path.endsWith("/analysis/confirm"));
  expect(confirmations).toHaveLength(2);
  expect(confirmations[0].idempotencyKey).toBeTruthy();
  expect(confirmations[1].idempotencyKey).toBe(confirmations[0].idempotencyKey);
  expect(JSON.parse(confirmations[1].body ?? "{}")).toEqual(JSON.parse(confirmations[0].body ?? "{}"));
});

test("el polling de trabajos se detiene en el estado terminal fallido", async ({ page }) => {
  const apiObserver = await installApi(page, {
    authenticated: true,
    capabilities: ["analysis:prepare", "analysis:confirm"],
    jobPollStatuses: ["failed"],
  });
  await page.goto("/analysis");
  await page.getByRole("button", { name: "Preparar análisis" }).click();
  await page.getByRole("checkbox", { name: /consentimiento explícito para usar proveedores remotos/i }).check();
  await page.getByRole("button", { name: "Confirmar análisis" }).click();
  await expect(page.getByText("Trabajo de análisis: fallido", { exact: true })).toBeVisible();
  await expect(page.getByText("analysis_failed", { exact: true })).toBeVisible();
  await page.waitForTimeout(350);
  expect(apiObserver.jobPollRequests).toBe(1);
});

test("el polling de trabajos se detiene en el estado terminal interrumpido", async ({ page }) => {
  const apiObserver = await installApi(page, {
    authenticated: true,
    capabilities: ["analysis:prepare", "analysis:confirm"],
    jobPollStatuses: ["interrupted"],
  });
  await page.goto("/analysis");
  await page.getByRole("button", { name: "Preparar análisis" }).click();
  await page.getByRole("checkbox", { name: /consentimiento explícito para usar proveedores remotos/i }).check();
  await page.getByRole("button", { name: "Confirmar análisis" }).click();
  await expect(page.getByText("Trabajo de análisis: interrumpido", { exact: true })).toBeVisible();
  await expect(page.getByText("worker_interrupted", { exact: true })).toBeVisible();
  await page.waitForTimeout(350);
  expect(apiObserver.jobPollRequests).toBe(1);
});

test("conecta dashboard, filtros URL y comparación basada en capturas", async ({ page }) => {
  await installApi(page);
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Resumen del proyecto" })).toBeVisible();
  await expect(page.getByText("VISTA GENERAL · HORMIGESTION")).toBeVisible();
  const totalMetric = page.getByRole("link", { name: /Tareas registradas/ });
  await expect(totalMetric.getByText("26", { exact: true })).toBeVisible();

  await page.getByRole("link", { name: "Tareas", exact: true }).click();
  await expect(page).toHaveURL(/\/tasks/);
  await page.getByLabel("Buscar tareas", { exact: true }).fill("Implementar API");
  await page.getByLabel("Estado").selectOption("en_curso");
  await page.getByLabel("Responsable (ID)").fill("operator-1");
  await page.getByLabel("Fase o lista").fill("Construcción");
  await page.getByLabel("Solo críticas").click();
  await expect(page.getByLabel("Solo críticas")).toBeChecked();
  await expect(page).toHaveURL(/q=Implementar/);
  await expect(page).toHaveURL(/state=en_curso/);
  await expect(page).toHaveURL(/assignee_id=operator-1/);
  await expect(page).toHaveURL(/list_name=Construcci%C3%B3n/);
  await expect(page).toHaveURL(/critical_only=true/);

  await page.getByRole("button", { name: /Implementar API de pagos/ }).click();
  await expect(page.getByRole("dialog", { name: /Implementar API de pagos/ })).toBeVisible();
  await expect(page.getByText("Prueba observada en el tablero")).toBeVisible();
  await page.getByRole("button", { name: "Comparar capturas" }).click();
  await expect(page.getByRole("deletion")).toHaveText("Pendiente");
  await expect(page.locator(".diff-after")).toHaveText("En curso");
});

test("el inicio de sesión informa errores y no persiste el token del operador", async ({ page }) => {
  await installApi(page, { loginStatus: 401 });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");

  await page.getByRole("button", { name: "Abrir navegación" }).click();
  await expect(page.getByRole("link", { name: "Propuestas" })).toBeVisible();
  await page.getByRole("button", { name: "Cerrar navegación" }).click();
  await page.getByRole("button", { name: "Iniciar sesión" }).click();
  await page.getByLabel("Token de operador").fill("token-de-prueba");
  await page.getByRole("button", { name: "Verificar y continuar" }).click();

  await expect(page.getByText("El token de operador no es válido.")).toBeVisible();
  const stored = await page.evaluate(() => Object.values(localStorage));
  expect(stored).not.toContain("token-de-prueba");
});

test("explica una API no disponible en vez de inventar datos", async ({ page }) => {
  await installApi(page, { projectStatus: 503 });
  await page.goto("/");

  await expect(page.getByRole("heading", { name: /No se pudo cargar|Error al cargar/ })).toBeVisible();
  await expect(page.getByText(/No hay datos|0 tareas/)).toHaveCount(0);
});

test("la sesión usa la identidad del servidor y cierra con protección CSRF", async ({ page }) => {
  await installApi(page);
  await page.goto("/");

  await page.getByRole("button", { name: "Iniciar sesión" }).click();
  await page.getByLabel("Token de operador").fill("token-transitorio");
  await page.getByRole("button", { name: "Verificar y continuar" }).click();
  await expect(page.getByText("operadora", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cerrar sesión" }).click();
  await expect(page.getByRole("button", { name: "Iniciar sesión" })).toBeVisible();
  await expect(page.getByText("Consulta de solo lectura")).toBeVisible();

  const stored = await page.evaluate(() => Object.values(localStorage));
  expect(stored).not.toContain("token-transitorio");
});

test("ordena y pagina tareas actualizando la consulta persistente", async ({ page }) => {
  await installApi(page);
  await page.goto("/tasks");

  await page.getByRole("button", { name: "Tarea" }).click();
  await expect(page).toHaveURL(/sort=title/);
  await expect(page).toHaveURL(/direction=asc/);
  await page.getByRole("button", { name: "Tarea" }).click();
  await expect(page).toHaveURL(/direction=desc/);

  await page.getByRole("button", { name: "Página siguiente" }).click();
  await expect(page).toHaveURL(/page=2/);
  await expect(page.getByText(/Mostrando página 2 de 2/)).toBeVisible();
});

test("cambia el tema y conecta las rutas de gobierno sin crear acciones decorativas", async ({ page }) => {
  await installApi(page);
  await page.goto("/");

  await page.getByRole("button", { name: "Cambiar a modo oscuro" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(await page.evaluate(() => localStorage.getItem("h5-theme"))).toBe("dark");
  await expect(page.getByRole("link", { name: /Propuestas, módulo pendiente/ })).toHaveCount(0);
  await page.getByRole("link", { name: "Propuestas", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Propuestas", exact: true })).toBeVisible();
  await expect(page.getByText("Pendiente de integración")).toHaveCount(0);
});

test("pagina la cartera de proyectos usando la respuesta del backend", async ({ page }) => {
  await installApi(page, { projectPagination: true });
  await page.goto("/projects");

  await expect(page.getByRole("heading", { name: "Proyectos" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "HORMIGESTION", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Página siguiente de proyectos" }).click();
  await expect(page).toHaveURL(/page=2/);
  await expect(page.getByRole("heading", { name: "Proyecto secundario" })).toBeVisible();
});

test("expone desconocidos como desconocidos cuando falta evidencia de responsable y prioridad", async ({ page }) => {
  await installApi(page);
  await page.goto("/tasks?state=desconocido");

  await expect(page.locator(".task-table").getByText("Desconocido", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "Ver detalle de Validar sincronización", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Validar sincronización" })).toBeVisible();
  await expect(page.getByText("Prioridad desconocida")).toBeVisible();
  await expect(page.getByText("No observado", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("La respuesta no incluye información de evidencia para esta tarea.")).toBeVisible();
});

test("el foco de ruta no dibuja un marco negro y el enlace de salto conserva navegación por teclado", async ({ page }) => {
  await installApi(page);
  await page.goto("/");

  const outlineStyle = await page.locator("#main-content").evaluate((element) => getComputedStyle(element).outlineStyle);
  expect(outlineStyle).toBe("none");
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Saltar al contenido principal" })).toBeFocused();
  await page.keyboard.press("Enter");
  const main = page.locator("#main-content");
  await expect(main).toBeFocused();
  const focusTreatment = await main.evaluate((element) => getComputedStyle(element).boxShadow);
  expect(focusTreatment).not.toBe("none");
});

test("configuración distingue metadatos reales de preferencias locales y no expone secretos", async ({ page }) => {
  const apiMutations: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/v1/") && !["GET", "HEAD"].includes(request.method())) {
      apiMutations.push(`${request.method()} ${path}`);
    }
  });
  await installApi(page);
  await page.goto("/settings");

  await expect(page.getByRole("heading", { name: "Configuración", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Preferencias de interfaz" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Proyecto seleccionado" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "HORMIGESTION", exact: true })).toBeVisible();
  await expect(page.getByText("trello", { exact: true })).toBeVisible();
  await expect(page.getByText("Escrituras externas deshabilitadas", { exact: true })).toBeVisible();
  await expect(page.getByText("fixture", { exact: true })).toBeVisible();

  await page.getByRole("radio", { name: "Oscuro" }).check();
  await expect.poll(() => page.locator("html").getAttribute("data-theme")).toBe("dark");
  const credentialLikeKeys = await page.evaluate(() => Object.keys(localStorage).filter((key) => /token|secret|credential/i.test(key)));
  expect(credentialLikeKeys).toEqual([]);
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
  expect(apiMutations).toEqual([]);
});

test("las preferencias de configuración funcionan con teclado y en ancho móvil", async ({ page }) => {
  await installApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/settings");

  const lightChoice = page.getByRole("radio", { name: "Claro" });
  const darkChoice = page.getByRole("radio", { name: "Oscuro" });
  await lightChoice.focus();
  await page.keyboard.press("ArrowRight");
  await expect(darkChoice).toBeChecked();
  await expect.poll(() => page.locator("html").getAttribute("data-theme")).toBe("dark");
  const dimensions = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
  expect(dimensions.width).toBeLessThanOrEqual(dimensions.client);
});

test("la propuesta exige revisión local, autorización separada y simulación sin mutación", async ({ page }) => {
  const observer = await installApi(page, {
    authenticated: true,
    capabilities: ["operator_session", "governance:proposal_review", "governance:dry_run_authorization", "governance:dry_run"],
  });
  await page.goto("/proposals");
  await expect(page.getByRole("heading", { name: "Propuestas", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Abrir propuesta proposal-date-001" }).click();

  await expect(page.getByText("Valor observado", { exact: true })).toBeVisible();
  await expect(page.getByText("Valor propuesto", { exact: true })).toBeVisible();
  await expect(page.getByText("capture-current", { exact: true })).toBeVisible();
  await expect(page.getByText("Disponer de dos días adicionales para completar la tarea.")).toBeVisible();
  await expect(page.getByText("b".repeat(64), { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Aprobar para revisión local" }).click();
  const reviewDialog = page.getByRole("dialog", { name: "Confirmar aprobación de revisión local" });
  await expect(reviewDialog.getByText(/no autoriza una simulación ni ejecuta cambios/i)).toBeVisible();
  await reviewDialog.getByRole("button", { name: "Registrar aprobación offline" }).click();
  await expect(page.getByLabel("Estados independientes").getByText("Aprobada offline", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Autorizar simulación segura" }).click();
  const authorizationDialog = page.getByRole("dialog", { name: "Autorizar simulación" });
  await expect(authorizationDialog.getByText(/permite únicamente una simulación local/i)).toBeVisible();
  await authorizationDialog.getByRole("button", { name: "Autorizar simulación" }).click();
  await expect(page.getByText("Autorizada para simulación", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Simular propuesta autorizada" }).click();
  const simulationDialog = page.getByRole("dialog", { name: "Confirmar simulación local" });
  await simulationDialog.getByRole("button", { name: "Simular sin escrituras" }).click();
  await expect(page.getByText("SIMULATED", { exact: true })).toBeVisible();
  await expect(page.getByText("Estado de ejecución real: No ejecutada", { exact: true })).toBeVisible();
  await expect(page.getByText("Estado en vivo: Sin cambios", { exact: true })).toBeVisible();
  await expect(page.getByText("Ejecución real permitida: No", { exact: true })).toBeVisible();

  expect(observer.governanceRequests.map((item) => item.path)).toEqual([
    "/api/v1/projects/hormigestio/proposals/proposal-date-001/review",
    "/api/v1/projects/hormigestio/proposals/proposal-date-001/dry-run-authorization",
    "/api/v1/projects/hormigestio/proposals/proposal-date-001/dry-runs",
  ]);
  expect(observer.governanceRequests.map((item) => item.csrfToken)).toEqual(["csrf-fixture", "csrf-fixture", "csrf-fixture"]);
  expect(observer.governanceRequests.every((item) => Boolean(item.idempotencyKey))).toBe(true);
  expect(JSON.parse(observer.governanceRequests[0].body ?? "{}")).toMatchObject({ decision: "approve", expected_version: 2, capture_id: "capture-current", proposal_body_sha256: "b".repeat(64) });
  expect(JSON.parse(observer.governanceRequests[1].body ?? "{}")).toMatchObject({ authorize: true, expected_version: 3, capture_id: "capture-current", proposal_body_sha256: "b".repeat(64) });
  expect(JSON.parse(observer.governanceRequests[2].body ?? "{}")).toMatchObject({ expected_version: 4, permission_event_id: "proposal-authorization-001" });
  expect(observer.governanceRequests.some((item) => item.path.includes("execute"))).toBe(false);
});

test("la revisión de propuesta no aparece sin capability y un rechazo de autorización no permite simular", async ({ page }) => {
  const denied = await installApi(page, { authenticated: true, capabilities: ["operator_session"] });
  await page.goto("/proposals");
  await page.getByRole("link", { name: "Abrir propuesta proposal-date-001" }).click();
  await expect(page.getByRole("button", { name: "Aprobar para revisión local" })).toHaveCount(0);
  expect(denied.governanceRequests).toHaveLength(0);

  await page.goto("/");
  await page.reload();
});

test("los informes protegen Markdown, separan la decisión humana y descargan solo artefactos del backend", async ({ page }) => {
  const externalRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("beacon.invalid")) externalRequests.push(request.url());
  });
  const observer = await installApi(page, { authenticated: true, capabilities: ["operator_session", "reports:decide"] });
  await page.goto("/reports");
  await expect(page.getByRole("heading", { name: "Informes", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Abrir informe report-001" }).click();
  await expect(page.getByRole("heading", { name: "Informe report-001" })).toBeVisible();
  await expect(page.getByText("Resumen almacenado del informe.", { exact: true })).toBeVisible();
  await expect(page.getByText("No se registró CI.", { exact: true })).toBeVisible();
  await expect(page.locator(".safe-markdown img")).toHaveCount(0);
  await expect(page.locator(".safe-markdown [onerror]")).toHaveCount(0);
  const unsafeLink = page.locator(".safe-markdown a").filter({ hasText: "Enlace peligroso" });
  await expect(unsafeLink).toHaveCount(1);
  expect(await unsafeLink.getAttribute("href")).toBeNull();
  const safeLink = page.locator(".safe-markdown a").filter({ hasText: "Documentación" });
  await expect(safeLink).toHaveAttribute("href", "https://example.invalid/docs");
  await expect(safeLink).toHaveAttribute("target", "_blank");
  await expect(safeLink).toHaveAttribute("rel", /noopener noreferrer/);
  expect(externalRequests).toEqual([]);

  const markdownDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Descargar Markdown" }).click();
  expect((await markdownDownload).suggestedFilename()).toBe("report-report-001.md");
  const jsonDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Descargar JSON" }).click();
  expect((await jsonDownload).suggestedFilename()).toBe("report-report-001.json");

  const decision = page.getByRole("combobox", { name: "Decisión humana del informe" });
  await decision.selectOption("changes_requested");
  const reason = page.getByRole("textbox", { name: "Motivo de la decisión" });
  await expect(page.getByRole("button", { name: "Registrar decisión humana" })).toBeDisabled();
  await reason.fill("Aclarar la evidencia de cobertura.");
  await page.getByRole("button", { name: "Registrar decisión humana" }).click();
  const decisionDialog = page.getByRole("dialog", { name: "Confirmar decisión humana" });
  await decisionDialog.getByRole("button", { name: "Confirmar decisión humana" }).click();
  await expect(page.locator(".report-detail-heading").getByText("Cambios solicitados", { exact: true })).toBeVisible();
  expect(observer.reportDecisionRequests).toHaveLength(1);
  expect(observer.reportDecisionRequests[0].path).toBe("/api/v1/projects/hormigestio/reports/report-001/decision");
  expect(observer.reportDecisionRequests[0].csrfToken).toBe("csrf-fixture");
  expect(JSON.parse(observer.reportDecisionRequests[0].body ?? "{}")).toEqual({ expected_report_version: 1, expected_human_decision: "pending", decision: "changes_requested", reason: "Aclarar la evidencia de cobertura." });
  expect(observer.reportDecisionRequests[0].idempotencyKey).toBeTruthy();
  expect(observer.downloadRequests).toEqual(["report:markdown", "report:json"]);
  expect(observer.governanceRequests).toHaveLength(0);
});

test("las listas de propuestas e informes no convierten disponibilidad desconocida en una bandeja vacía", async ({ page }) => {
  await installApi(page, { proposalAvailability: "unknown", reportAvailability: "unknown" });
  await page.goto("/proposals");
  await expect(page.getByText("Disponibilidad de propuestas desconocida", { exact: true })).toBeVisible();
  await page.goto("/reports");
  await expect(page.getByText("Disponibilidad de informes desconocida", { exact: true })).toBeVisible();
});

test("calendario muestra el outbox real y exige preparación, confirmación e idempotencia para un ciclo local", async ({ page }) => {
  const observer = await installApi(page, {
    authenticated: true,
    capabilities: ["operator_session", "weekly_cycle:prepare", "weekly_cycle:confirm"],
  });
  await page.goto("/calendar");
  await expect(page.getByRole("heading", { name: "Calendario y reportes semanales" })).toBeVisible();
  await expect(page.getByText("Disparador automático: no verificado", { exact: true })).toBeVisible();
  await expect(page.getByText("America/Guayaquil", { exact: true })).toBeVisible();
  await expect(page.getByText("DELIVERED", { exact: true })).toBeVisible();
  expect(observer.weeklyCycleRequests).toHaveLength(0);

  await page.getByRole("link", { name: "Abrir entrega delivery-001" }).click();
  await expect(page.getByRole("heading", { name: "Entrega delivery-001" })).toBeVisible();
  await expect(page.getByText("Cuerpo ligado al recibo persistido.", { exact: true })).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Descargar recibo Markdown" }).click();
  expect((await download).suggestedFilename()).toBe("outbox-delivery-001.md");

  await page.getByRole("button", { name: "Preparar ciclo manual" }).click();
  const prepareDialog = page.getByRole("dialog", { name: "Preparar ciclo semanal local" });
  await prepareDialog.getByRole("button", { name: "Comprobar elegibilidad" }).click();
  await expect(page.getByRole("heading", { name: "Preparación del ciclo" })).toBeVisible();
  const consent = page.getByRole("checkbox", { name: "Confirmo ejecutar el ciclo local mediante el canal SQLite, sin llamadas remotas ni entregas externas" });
  await expect(consent).not.toBeChecked();
  await consent.check();
  await page.getByRole("button", { name: "Confirmar ciclo semanal" }).click();
  const confirmDialog = page.getByRole("dialog", { name: "Confirmar ciclo semanal" });
  await confirmDialog.getByRole("button", { name: "Ejecutar ciclo local" }).click();
  await expect(page.getByText("Trabajo completado", { exact: true })).toBeVisible();
  await expect(page.getByText("Omitido: la ventana aún no está vencida", { exact: true })).toBeVisible();
  await expect(page.getByText("Ejecutado", { exact: true })).toHaveCount(0);

  expect(observer.weeklyCycleRequests.map((item) => item.path)).toEqual([
    "/api/v1/projects/hormigestio/schedule/manual-cycle/prepare",
    "/api/v1/projects/hormigestio/schedule/manual-cycle/confirm",
  ]);
  expect(observer.weeklyCycleRequests.every((item) => item.csrfToken === "csrf-fixture")).toBe(true);
  expect(observer.weeklyCycleRequests[0].idempotencyKey).toBeNull();
  expect(observer.weeklyCycleRequests[1].idempotencyKey).toBeTruthy();
  expect(JSON.parse(observer.weeklyCycleRequests[0].body ?? "{}")).toEqual({ request_cycle: true });
  expect(JSON.parse(observer.weeklyCycleRequests[1].body ?? "{}")).toEqual({ preparation_id: "cycle-preparation-001", configuration_fingerprint: "c".repeat(64), confirm_cycle: true, remote_consent: false });
  expect(observer.downloadRequests).toEqual(["outbox:markdown"]);
});

test("un ciclo manual sin elegibilidad no permite confirmación", async ({ page }) => {
  await installApi(page, { authenticated: true, capabilities: ["weekly_cycle:prepare", "weekly_cycle:confirm"], cyclePrepareEligible: false });
  await page.goto("/calendar");
  await page.getByRole("button", { name: "Preparar ciclo manual" }).click();
  await page.getByRole("dialog", { name: "Preparar ciclo semanal local" }).getByRole("button", { name: "Comprobar elegibilidad" }).click();
  await expect(page.getByText("El servidor no autorizó la preparación del ciclo.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirmar ciclo semanal" })).toBeDisabled();
});

test("auditoría protegida filtra eventos y conserva autoría desconocida", async ({ page }) => {
  const observer = await installApi(page, { authenticated: true, capabilities: ["operator_session", "audit:read"] });
  await page.goto("/audit");
  await expect(page.getByRole("heading", { name: "Auditoría y actividad" })).toBeVisible();
  await expect(page.getByText("Sesión local verificada: operador-local", { exact: true })).toBeVisible();
  await expect(page.getByText("Actor no verificado", { exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: "Tipo de evento" }).fill("proposal_review");
  await page.getByRole("textbox", { name: "Desde (ISO 8601 con zona)" }).fill("2026-01-12T00:00:00-05:00");
  await page.getByRole("textbox", { name: "Hasta (ISO 8601 con zona)" }).fill("2026-01-12T23:59:59-05:00");
  await page.getByRole("button", { name: "Aplicar filtros de auditoría" }).click();
  await expect(page).toHaveURL(/event_type=proposal_review/);
  await expect(page).toHaveURL(/since=2026-01-12T00%3A00%3A00-05%3A00/);
  await expect(page.getByRole("link", { name: "Ver evento audit-legacy-002" })).toBeVisible();
  await page.getByRole("link", { name: "Ver evento audit-legacy-002" }).click();
  await expect(page.getByRole("heading", { name: "Evento audit-legacy-002" })).toBeVisible();
  await expect(page.getByText("Actor no verificado", { exact: true }).last()).toBeVisible();
  await expect(page.getByText(/"captured": true/)).toBeVisible();
  expect(observer.moduleReads.some((path) => path.includes("event_type=proposal_review") && path.includes("since=2026-01-12"))).toBe(true);
});

test("lecturas de auditoría y configuración no se consultan sin sus capabilities del servidor", async ({ page }) => {
  const observer = await installApi(page);
  await page.goto("/audit");
  await expect(page.getByRole("heading", { name: "Auditoría y actividad" })).toBeVisible();
  await expect(page.getByText("Se requiere una sesión local con la capacidad audit:read.", { exact: true })).toBeVisible();
  await page.goto("/settings");
  await expect(page.getByText("La configuración protegida requiere la capacidad configuration:read.", { exact: true })).toBeVisible();
  expect(observer.moduleReads.some((path) => path.includes("/audit"))).toBe(false);
  expect(observer.moduleReads.some((path) => path.includes("/configuration"))).toBe(false);
});

test("configuración protegida revela presencia y metadatos permitidos, nunca credenciales ni controles de edición", async ({ page }) => {
  const observer = await installApi(page, { authenticated: true, capabilities: ["operator_session", "configuration:read"] });
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Configuración general del proyecto" })).toBeVisible();
  await expect(page.getByText("org-fixture/gestion-fixture", { exact: true })).toBeVisible();
  await expect(page.getByText("board-fixture", { exact: true })).toBeVisible();
  await expect(page.getByText("Credencial Trello: configurada", { exact: true })).toBeVisible();
  await expect(page.getByText("gemini-fixture", { exact: true })).toBeVisible();
  await expect(page.getByText("policy-v2", { exact: true })).toBeVisible();
  await expect(page.getByText("America/Guayaquil", { exact: true })).toBeVisible();
  await expect(page.getByText("Ningún campo de configuración es editable desde esta interfaz.", { exact: true })).toBeVisible();
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Guardar configuración|Editar|Crear proyecto/ })).toHaveCount(0);
  const serializedUi = await page.locator("main").innerText();
  expect(serializedUi).not.toContain("api_token=");
  expect(serializedUi).not.toContain("secret-token");
  expect(observer.moduleReads.some((path) => path.endsWith("/configuration"))).toBe(true);
});
