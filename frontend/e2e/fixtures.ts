import { appendFileSync } from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";
import { expect, type Page, type Route } from "@playwright/test";
import type {
  ExecuteProposalRequest,
  ProjectSummaryView,
  ProposalDecisionHistoryEntry,
  ProposalDetail,
  ProposalExecutionReceipt,
  ProposalSummary,
} from "../src/api/types";

export const managerCredentials = {
  email: "gestor@hormigestion.ec",
  password: "HormiGestion2026!",
} as const;

export const summary: ProjectSummaryView = {
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
  risks: [
    {
      risk_id: "risk-fixture-overdue",
      project_id: "hormigestio",
      capture_id: "capture-current",
      kind: "overdue",
      task_ids: ["task-1"],
      task_count: 1,
      summary: "1 tarea(s) vencida(s) no completada(s).",
      basis: "Tareas no completadas cuyo vencimiento es anterior al corte de la captura.",
      source: "deterministic",
      cutoff_datetime: "2026-01-11T23:59:59-05:00",
      task_titles: { "task-1": "Implementar API de pagos" },
      proposals: [
        {
          proposal_id: "proposal-date-001",
          task_id: "task-1",
          capture_id: "capture-current",
          field: "due_date",
          review_status: "pending",
          authorization_status: "not_authorized",
          execution_status: "not_executed",
          freshness: "current",
          technical_status: "ready",
        },
      ],
    },
    {
      risk_id: "risk-fixture-due-soon",
      project_id: "hormigestio",
      capture_id: "capture-current",
      kind: "due_soon",
      task_ids: ["task-3"],
      task_count: 1,
      summary: "1 tarea(s) próxima(s) a vencer en la ventana de 7 días.",
      basis: "Tareas no completadas cuyo vencimiento cae dentro de los 7 días posteriores al corte.",
      source: "deterministic",
      cutoff_datetime: "2026-01-11T23:59:59-05:00",
      task_titles: { "task-3": "Validar sincronización 1" },
      proposals: [],
    },
  ],
  proposals_context: {
    run_id: "run-current",
    capture_id: "capture-current",
    diagnostic: {
      outcome: "no_candidates",
      offered: 0,
      selected: 0,
      saved: 0,
      technical_ready: 0,
      technical_blocked: 0,
      limitation_codes: ["unknown_task"],
      failure_codes: [],
      discarded: 0,
    },
  },
};

/** Capture that supports a task detail; the current capture is used when none is requested. */
export function requestedTaskCapture(url: URL): string {
  return url.searchParams.get("capture_id") ?? "capture-current";
}

/** Deterministic risks affecting a task, resolved against the capture that supports the response. */
export function taskRisksFor(taskId: string, captureId: string) {
  return summary.risks
    .filter((risk) => risk.task_ids.includes(taskId))
    .map((risk) => ({
      ...risk,
      capture_id: captureId,
      proposals: (risk.proposals ?? []).map((proposal) => ({ ...proposal, capture_id: "capture-current" })),
    }));
}


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
    assignee_names: { "operator-1": "Ada Lovelace" },
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
  attention: [],
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
  attention: [
    {
      reason_code: "repository_needs_attention",
      kind: "attention",
      scope: "repository",
      stage_name: "quality",
      repository_id: "backend",
      message: "Repository source or model output was incomplete; retained only identity-matched findings.",
    },
  ],
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
    {
      stage_name: "proposals",
      status: "skipped",
      recorded: true,
      duration_seconds: 0.4,
      completed_at: "2026-01-12T10:05:35-05:00",
      providers: null,
      result_summary: null,
      error_code: null,
      proposals: {
        outcome: "no_candidates",
        offered: 0,
        selected: 0,
        saved: 0,
        technical_ready: 0,
        technical_blocked: 0,
        limitation_codes: ["unknown_task"],
        failure_codes: [],
        discarded: 0,
      },
    },
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

/** A recorded independent review of one exact patch, as the backend exposes it. */
export const patchReview = {
  review_id: "patch-review-9f2c1d4b8a7e6f50",
  project_id: "hormigestio",
  run_id: "run-current",
  finding_id: "finding-candidate",
  repository: "owner/project",
  commit_sha: "0123456789abcdef0123456789abcdef01234567",
  file_path: "src/token.ts",
  patch_sha256: "7a2085093d082e2732cdace7384dc271b03ed6ef6756dcac8f9d0b13f82d458a",
  original_sha256: "1c1f0a4b2f5b3d6e8a9c0b1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f",
  verdict: "favorable" as const,
  reason: "La corrección resuelve el defecto sin alterar el contexto del archivo completo.",
  reviewed_by: "verifier_agent",
  evidence_ids: ["evidence-code-1"],
  reviewed_at: "2026-01-12T10:05:00-05:00",
};

/** The authorization fact of one finding, answered by the shared policy. */
export const approvalEligibility = {
  eligible: true,
  authorized: true,
  context_valid: true,
  defect_confirmed: true,
  blocked_reason: null,
  blocked_reason_message: null,
  authorization_reason: null,
  patch_sha256: patchReview.patch_sha256,
  review_id: patchReview.review_id,
  review: patchReview,
};

/** The authorization fact of a finding whose exact patch has no review. */
export const blockedApprovalEligibility = {
  eligible: false,
  authorized: false,
  context_valid: true,
  defect_confirmed: true,
  blocked_reason: "revision_no_corresponde",
  blocked_reason_message:
    "La revisión disponible juzgó otro parche, archivo, repositorio, commit o ejecución.",
  authorization_reason: null,
  patch_sha256: "c3b0f2d1a4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f9",
  review_id: null,
  review: null,
};

export const backendRepositorySha = "a".repeat(40);

/** Backend-shaped repository history for offline MultiRepo UI coverage. */
export const multiRepoResults = {
  project_id: "hormigestio",
  availability: "available",
  repositories: [
    { repository_id: "backend", owner: "Josu-F1", repo: "hormigestion-backend", branch: "develop", configured_commit_sha: null, quality_workflow_path: ".github/workflows/ci.yml" },
    { repository_id: "frontend", owner: "Josu-F1", repo: "hormigestion-frontend", branch: "develop", configured_commit_sha: null, quality_workflow_path: ".github/workflows/ci-quality.yml" },
  ],
  items: [
    {
      capture_id: "capture-current", snapshot_id: "snapshot-current", source_scope: "repository", source_item_key: "backend:develop",
      analysis_run_id: "run-current", report_id: "report-current",
      repository: { repository_id: "backend", owner: "Josu-F1", repo: "hormigestion-backend" },
      branch: "develop", target_commit_sha: backendRepositorySha, commit_resolution_status: "resolved",
      access_status: "allowed", availability_status: "available", source_complete: true, incomplete_reason: null,
      code_diff_count: 2, coverage_percentage: 0, coverage_basis: "Backend source line coverage; generated files excluded",
      ci_runs: [{
        run_id: "38833954051", workflow_name: "Backend quality", workflow_path: ".github/workflows/ci.yml",
        commit_sha: backendRepositorySha, status: "completed", actual_attempt: 2, artifact_id: "artifact-backend-1",
        artifact_name: `ci-quality-${backendRepositorySha}-38833954051-2`, timestamp: "2026-01-12T10:06:00Z",
        repository: { repository_id: "backend", owner: "Josu-F1", repo: "hormigestion-backend" },
        source_artifact_validation: {
          outcome: "validated", source_metrics_certified: true, schema_valid: true, semantics_valid: true,
          identity_bound: true, workflow_status_matches: true, reason: null,
          artifact_name: `ci-quality-${backendRepositorySha}-38833954051-2`,
          source_document_metrics: { total_tests: 1, failed_tests: 0, coverage_percentage: 0, coverage_metric: "line", coverage_basis: "Backend source line coverage; generated files excluded", test_suites: null, e2e_status: null },
        },
        report_metrics_eligible: true,
        report_metrics: { total_tests: 1, failed_tests: 0, coverage_percentage: 0, coverage_metric: "line", coverage_basis: "Backend source line coverage; generated files excluded", test_suites: null, e2e_status: null },
        report_artifact_outcome: "validated", report_artifact_reason: null,
      }],
      source_problems: [], quality_status: "completed", quality_reason: null,
      verification_status: "completed", verification_reason: null,
      evaluation_status: "completed", evaluation_reason: null,
      findings: [], verifications: [],
    },
    {
      capture_id: "capture-current", snapshot_id: "snapshot-current", source_scope: "repository", source_item_key: "frontend:develop",
      analysis_run_id: "run-current", report_id: "report-current",
      repository: { repository_id: "frontend", owner: "Josu-F1", repo: "hormigestion-frontend" },
      branch: "develop", target_commit_sha: null, commit_resolution_status: "failed",
      access_status: "denied", availability_status: "unavailable", source_complete: false,
      incomplete_reason: "Repository access was denied.", code_diff_count: 0,
      coverage_percentage: null, coverage_basis: null, ci_runs: [],
      source_problems: ["Repository access was denied."], quality_status: "needs_attention",
      quality_reason: "Repository access was denied.", verification_status: "needs_attention",
      verification_reason: "No repository evidence was available.", evaluation_status: "needs_attention",
      evaluation_reason: "Evidence is insufficient.", findings: [], verifications: [],
    },
  ],
  total: 2, page: 1, page_size: 100,
};

export const secondaryRepositoryResults = {
  project_id: secondProject.id, availability: "unknown",
  message: "Repository history is unavailable.", repositories: [], items: [], total: 0, page: 1, page_size: 100,
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
  review_status: "pending",
  authorization_status: "not_authorized",
  execution_status: "not_executed",
  risk_refs: [
    {
      risk_id: "risk-fixture-overdue",
      kind: "overdue",
      task_id: "task-1",
      capture_id: "capture-current",
      resolved: true,
    },
  ],
};

export const proposalDetail: ProposalDetail = {
  ...proposalSummary,
  decision_history: [],
  decision_history_availability: "available",
  original_proposal_id: null,
  derived_proposal_ids: [],
  relationship_availability: "available",
  latest_receipt: null,
  receipt_availability: "available",
};

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

const localBrowserOrigin = "http://127.0.0.1:4173";
const localBrowserWebSocketOrigin = "ws://127.0.0.1:4173";
const browserNetworkAttempts = new WeakMap<Page, string[]>();

export async function installBrowserNetworkGuard(page: Page) {
  const attempts: string[] = [];
  browserNetworkAttempts.set(page, attempts);
  const recordAttempt = (transport: string, url: string) => {
    const entry = `${transport} ${url}`;
    attempts.push(entry);
    const logPath = process.env.ISOLATION_NETWORK_LOG;
    if (logPath) {
      const scratchRoot = process.env.ISOLATION_SCRATCH_ROOT;
      const relativeLogPath = scratchRoot ? relative(resolve(scratchRoot), resolve(logPath)) : "";
      if (!scratchRoot || !relativeLogPath || relativeLogPath === ".." || relativeLogPath.startsWith(`..${sep}`) || isAbsolute(relativeLogPath)) {
        throw new Error("The browser network log must stay inside the authorized scratch area.");
      }
      appendFileSync(resolve(logPath), `${JSON.stringify({ transport, url })}\n`, "utf8");
    }
  };

  await page.context().route("**/*", async (route) => {
    const requestUrl = route.request().url();
    if (new URL(requestUrl).origin === localBrowserOrigin) return route.continue();
    recordAttempt("http", requestUrl);
    return route.abort();
  });
  await page.context().routeWebSocket("**/*", async (webSocket) => {
    if (new URL(webSocket.url()).origin === localBrowserWebSocketOrigin) {
      webSocket.connectToServer();
      return;
    }
    recordAttempt("websocket", webSocket.url());
    await webSocket.close({ code: 1008, reason: "External networking is disabled in offline UI tests." });
  });
  return attempts;
}

export function assertNoBrowserNetworkAttempts(page: Page) {
  expect(browserNetworkAttempts.get(page) ?? []).toEqual([]);
}

async function json(route: Route, value: unknown, status = 200) {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
}

function proposalSummaryView(proposal: ProposalDetail): ProposalSummary {
  const summary: ProposalDetail = { ...proposal };
  delete summary.decision_history;
  delete summary.decision_history_availability;
  delete summary.original_proposal_id;
  delete summary.derived_proposal_ids;
  delete summary.relationship_availability;
  delete summary.latest_receipt;
  delete summary.receipt_availability;
  return summary;
}

export async function installApi(page: Page, options: {
  loginStatus?: number;
  projectStatus?: number;
  projectPagination?: boolean;
  projectsPayload?: unknown;
  repositoryResultsPayload?: unknown;
  repositoryResultsByProject?: Record<string, unknown>;
  summaryPayload?: unknown;
  historyPayload?: unknown;
  tasksPayload?: unknown;
  taskDetailPayload?: unknown;
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
  proposalPendingActionableCount?: number | null;
  emptyProposals?: boolean;
  reportAvailability?: "available" | "unknown";
  outboxAvailability?: "available" | "unknown";
  scheduleAvailability?: "configured" | "unknown";
  auditStatus?: number;
  configurationStatus?: number;
  requireLogin?: boolean;
  findingDetailPayload?: unknown;
  approvalEligibilityPayload?: unknown;
  approvalEligibilityStatus?: number;
  decisionPayload?: unknown;
  decisionStatus?: number;
  decisionPublicationInDetail?: boolean;
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
  const initialProposal = { ...(options.proposalFixture ?? proposalDetail) };
  const proposalRecords = new Map<string, ProposalDetail>([[initialProposal.proposal_id, initialProposal]]);
  const currentAuditItems = [...auditCollection.items];
  const executedProposalResponses = new Map<string, unknown>();
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
  const taskDetailReads: string[] = [];
  const downloadRequests: string[] = [];
  const findingDecisionRequests: Array<{
    path: string;
    body: string | null;
    csrfToken: string | null;
    idempotencyKey: string | null;
  }> = [];
  const findingApprovalRequests: Array<{ path: string; body: string | null; csrfToken: string | null }> = [];
  let lastDecisionPayload: Record<string, unknown> | null = null;
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
      await json(route, options.projectsPayload ?? (options.projectPagination
        ? { items: pageNumber === 1 ? [project] : [secondProject], total: 101, page: pageNumber, page_size: 100 }
        : { items: [project], total: 1, page: 1, page_size: 100 }), options.projectStatus ?? 200);
      return;
    }

    const projectDetailMatch = path.match(/^\/api\/v1\/projects\/([^/]+)$/);
    if (projectDetailMatch && request.method() === "GET") {
      const projectId = decodeURIComponent(projectDetailMatch[1]);
      const selected = [project, secondProject].find((item) => item.id === projectId);
      await json(route, selected ?? { error: { code: "project_not_found", message: "Proyecto no encontrado." } }, selected ? 200 : 404);
      return;
    }

    const repositoryResultsMatch = path.match(/^\/api\/v1\/projects\/([^/]+)\/repository-results$/);
    if (repositoryResultsMatch && request.method() === "GET") {
      const projectId = decodeURIComponent(repositoryResultsMatch[1]);
      const payload = options.repositoryResultsByProject?.[projectId]
        ?? options.repositoryResultsPayload
        ?? { project_id: projectId, availability: "unknown", message: "Repository history is unavailable.", repositories: [], items: [], total: 0, page: 1, page_size: 100 };
      await json(route, payload);
      return;
    }

    if (path.endsWith("/projects/hormigestio/proposals") && request.method() === "GET") {
      const available = options.proposalAvailability ?? "available";
      const items = options.emptyProposals ? [] : [...proposalRecords.values()].map(proposalSummaryView);
      const pending = options.proposalPendingActionableCount ?? (options.emptyProposals ? 0 : items.length);
      await json(route, { availability: available, items: available === "available" ? items : [], total: available === "available" ? items.length : 0, page: 1, page_size: 25, pending_actionable_count: pending });
      return;
    }

    const proposalDetailMatch = path.match(/^\/api\/v1\/projects\/hormigestio\/proposals\/([^/]+)$/);
    if (proposalDetailMatch && request.method() === "GET") {
      const found = proposalRecords.get(decodeURIComponent(proposalDetailMatch[1]));
      if (!found) {
        await json(route, { error: { code: "proposal_not_found", message: "Propuesta no encontrada." } }, 404);
        return;
      }
      const canReadLedger = authenticated && sessionCapabilities.includes("audit:read");
      await json(route, canReadLedger ? found : proposalSummaryView(found));
      return;
    }

    const proposalExecutionMatch = path.match(/^\/api\/v1\/projects\/hormigestio\/proposals\/([^/]+)\/execute$/);
    if (proposalExecutionMatch && request.method() === "POST") {
      expect(request.headers()["x-csrf-token"]).toBe("csrf-fixture");
      const idempotencyKey = request.headers()["idempotency-key"] ?? "";
      expect(idempotencyKey).toBeTruthy();
      if (executedProposalResponses.has(idempotencyKey)) {
        await json(route, executedProposalResponses.get(idempotencyKey));
        return;
      }
      const proposalId = decodeURIComponent(proposalExecutionMatch[1]);
      const proposal = proposalRecords.get(proposalId);
      if (!proposal) {
        await json(route, { error: { code: "proposal_not_found", message: "Propuesta no encontrada." } }, 404);
        return;
      }
      const payload = JSON.parse(request.postData() ?? "{}") as ExecuteProposalRequest;
      if (options.proposalExecutionStatus && options.proposalExecutionStatus !== 200) {
        await json(route, { error: { code: "proposal_execution_unavailable", message: "La ejecución no está disponible por el servidor." } }, options.proposalExecutionStatus);
        return;
      }
      const modified = payload.decision === "modify_and_execute";
      const derivedId = modified ? "proposal-derived-001" : proposalId;
      const defaultStatus = payload.decision === "reject" ? "DENIED" : "SIMULATED";
      const result = options.proposalExecutionBody ?? {
        proposal_id: derivedId,
        decision: payload.decision,
        execution: { status: defaultStatus, verification_status: payload.decision === "reject" ? "not_required" : "verified" },
        execution_mode: "sandbox",
      };
      const resultValue = result as { proposal_id?: string; execution?: { status?: unknown; verification_status?: unknown; reasons?: unknown } ; execution_mode?: unknown };
      const rawStatus = typeof resultValue.execution?.status === "string" ? resultValue.execution.status.toUpperCase() : "unknown";
      const executionStatus: ProposalDetail["execution_status"] = rawStatus === "SIMULATED" || rawStatus === "EXECUTED"
        ? "EXECUTED"
        : ["ALREADY_APPLIED", "DENIED", "CONFLICT", "FAILED", "unknown"].includes(rawStatus)
          ? rawStatus as ProposalDetail["execution_status"] : "unknown";
      const receiptStatus: ProposalExecutionReceipt["status"] = executionStatus === "not_executed" ? "unknown" : executionStatus;
      const rawVerification = resultValue.execution?.verification_status;
      const verificationStatus: ProposalExecutionReceipt["verification_status"] = ["verified", "verification_failed", "verification_unsupported", "unknown"].includes(String(rawVerification))
        ? rawVerification as ProposalExecutionReceipt["verification_status"] : "unknown";
      const rawMode = resultValue.execution_mode;
      const executionMode: ProposalExecutionReceipt["execution_mode"] = rawMode === "live" || rawMode === "sandbox" ? rawMode : null;
      const receiptProposalId = modified ? resultValue.proposal_id ?? derivedId : proposalId;
      const auditEventId = `proposal-execution-${receiptProposalId}`;
      const event: ProposalDecisionHistoryEntry = {
        event_id: `exec-web-${receiptProposalId}`,
        decision: payload.decision,
        proposal_id: receiptProposalId,
        related_proposal_id: modified ? proposalId : null,
        actor_id: authenticated ? "gestora-e2e-fixture" : null,
        actor_verification: authenticated ? "verified_local_session" : "unknown",
        reason: payload.reason,
        recorded_at: "2026-01-12T10:30:00-05:00",
        execution_mode: executionMode,
        execution_status: receiptStatus,
        verification_status: verificationStatus,
        reasons: Array.isArray(resultValue.execution?.reasons) ? resultValue.execution.reasons as string[] : [],
        audit_event_id: auditEventId,
      };
      const receipt: ProposalExecutionReceipt = {
        execution_id: `execution-${receiptProposalId}`,
        proposal_id: receiptProposalId,
        original_proposal_id: modified ? proposalId : null,
        decision: payload.decision,
        execution_mode: executionMode,
        status: receiptStatus,
        verification_status: verificationStatus,
        reasons: event.reasons,
        actor_id: event.actor_id,
        actor_verification: authenticated ? "verified_local_session" : "unknown",
        reason: payload.reason,
        recorded_at: event.recorded_at!,
        audit_event_id: auditEventId,
      };
      if (modified) {
        const modifiedValue = payload.modified_due_date ?? payload.modified_priority ?? payload.modified_assignee_ids ?? proposal.proposed_value;
        const original = {
          ...proposal,
          decision_history: [...(proposal.decision_history ?? []), event],
          decision_history_availability: "available" as const,
          derived_proposal_ids: [...new Set([...(proposal.derived_proposal_ids ?? []), receiptProposalId])],
          relationship_availability: "available" as const,
        };
        const derived: ProposalDetail = {
          ...proposal,
          proposal_id: receiptProposalId,
          proposed_value: modifiedValue,
          proposal_body_sha256: "e".repeat(64),
          review_status: "pending",
          authorization_status: "not_authorized",
          execution_status: executionStatus,
          decision_history: [event],
          decision_history_availability: "available",
          original_proposal_id: proposalId,
          derived_proposal_ids: [],
          relationship_availability: "available",
          latest_receipt: receipt,
          receipt_availability: "available",
        };
        proposalRecords.set(proposalId, original);
        proposalRecords.set(receiptProposalId, derived);
      } else {
        proposalRecords.set(proposalId, {
          ...proposal,
          execution_status: executionStatus,
          decision_history: [...(proposal.decision_history ?? []), event],
          decision_history_availability: "available",
          latest_receipt: receipt,
          receipt_availability: "available",
        });
      }
      executedProposalResponses.set(idempotencyKey, result);
      currentAuditItems.push({
        ...auditEvent,
        event_id: auditEventId,
        event_type: "proposal_execution",
        resource_type: "proposal",
        resource_id: receiptProposalId,
        summary: `Decisión ${payload.decision} recibida por el backend.`,
      });
      await json(route, result);
      return;
    }

    if (path.endsWith("/projects/hormigestio/reports") && request.method() === "GET") {
      const available = options.reportAvailability ?? "available";
      await json(route, { availability: available, items: available === "available" ? [currentReport] : [], total: available === "available" ? 1 : 0, page: Number(url.searchParams.get("page") ?? 1), page_size: 25 });
      return;
    }

    if (path.endsWith("/projects/hormigestio/reports/report-current") && request.method() === "GET") {
      await json(route, { ...reportDetail, report_id: "report-current", run_id: "run-current" });
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

    if (path.endsWith("/runs/run-previous") && request.method() === "GET") {
      await json(route, runPrevious);
      return;
    }

    if (path.endsWith("/runs/run-current/stages")) {
      await json(route, stageRecords);
      return;
    }

    if (path.endsWith("/runs/run-current/findings/finding-candidate/approval-eligibility")
      && request.method() === "POST") {
      findingApprovalRequests.push({
        path,
        body: request.postData(),
        csrfToken: request.headers()["x-csrf-token"] ?? null,
      });
      if (options.approvalEligibilityStatus && options.approvalEligibilityStatus !== 200) {
        await json(route, { error: { code: "eligibility_unavailable", message: "La elegibilidad no está disponible." } }, options.approvalEligibilityStatus);
        return;
      }
      await json(route, {
        finding_id: "finding-candidate",
        run_id: "run-current",
        patch_review: options.approvalEligibilityPayload
          ? (options.approvalEligibilityPayload as { review?: unknown }).review ?? null
          : patchReview,
        approval_eligibility: options.approvalEligibilityPayload ?? approvalEligibility,
        publication: null,
      });
      return;
    }

    if (path.endsWith("/runs/run-current/findings/finding-candidate/decision")
      && request.method() === "POST") {
      findingDecisionRequests.push({
        path,
        body: request.postData(),
        csrfToken: request.headers()["x-csrf-token"] ?? null,
        idempotencyKey: request.headers()["idempotency-key"] ?? null,
      });
      if (options.decisionStatus && options.decisionStatus !== 200) {
        await json(route, { error: { code: "decision_refused", message: "El backend rechazó la decisión." } }, options.decisionStatus);
        return;
      }
      const decisionPayload = options.decisionPayload ?? {
        reused: false,
        audit_event_id: "audit-finding-g4-0001",
        decision_status: "approved",
        branch_name: "quality/finding-candidate-g4",
        branch_url: "sandbox://branches/quality/finding-candidate-g4",
        commit_sha_applied: "d".repeat(64),
        suggested_code: "const token = readToken().trim();",
        improvement_summary: "Normaliza el valor de entrada.",
        execution_mode: "sandbox",
        publication_status: "published",
        publication_error_code: null,
        publication_error_message: null,
        pull_request_number: 4321,
        pull_request_url: "sandbox://pulls/owner/project/4321",
        pull_request_state: "simulated",
        pull_request_simulated: true,
        patch_review_id: patchReview.review_id,
        patch_sha256: patchReview.patch_sha256,
      };
      lastDecisionPayload = decisionPayload as Record<string, unknown>;
      await json(route, decisionPayload);
      return;
    }

    if (path.endsWith("/runs/run-current/findings/finding-candidate")) {
      const detailPayload = (options.findingDetailPayload ?? findingDetail) as Record<string, unknown>;
      if (options.decisionPublicationInDetail && lastDecisionPayload) {
        // The detail reports the publication of the last decision, so a
        // reload shows the same partial publication the manager saw, with the
        // decision that was already recorded.
        await json(route, {
          ...detailPayload,
          decision_status: lastDecisionPayload.decision_status ?? detailPayload.decision_status,
          branch_name: lastDecisionPayload.branch_name ?? detailPayload.branch_name,
          branch_url: lastDecisionPayload.branch_url ?? detailPayload.branch_url,
          commit_sha_applied:
            lastDecisionPayload.commit_sha_applied ?? detailPayload.commit_sha_applied,
          execution_mode: lastDecisionPayload.execution_mode ?? detailPayload.execution_mode,
          publication: {
            publication_status: lastDecisionPayload.publication_status ?? "not_requested",
            publication_error_code: lastDecisionPayload.publication_error_code ?? null,
            publication_error_message: lastDecisionPayload.publication_error_message ?? null,
            pull_request_number: lastDecisionPayload.pull_request_number ?? null,
            pull_request_url: lastDecisionPayload.pull_request_url ?? null,
            pull_request_state: lastDecisionPayload.pull_request_state ?? null,
            pull_request_simulated: lastDecisionPayload.pull_request_simulated ?? null,
            patch_review_id: lastDecisionPayload.patch_review_id ?? null,
            patch_sha256: lastDecisionPayload.patch_sha256 ?? null,
          },
        });
        return;
      }
      await json(route, detailPayload);
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
      taskDetailReads.push(`${path}${url.search}`);
      await json(route, options.taskDetailPayload ?? {
        ...tasks[0],
        capture_id: requestedTaskCapture(url),
        risks: taskRisksFor("task-1", requestedTaskCapture(url)),
        evidence: [
          {
            provenance: { id: "evidence-1", source: "trello", project_id: "hormigestio", external_id: "task-1", timestamp: "2026-01-11T10:00:00-05:00" },
            category: "task",
            summary: "Prueba observada en el tablero",
          },
          {
            provenance: { id: "evidence-2", source: "trello", project_id: "hormigestio", external_id: "task-1", timestamp: summary.captured_at },
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
      const taskId = decodeURIComponent(taskDetailMatch[1]);
      const task = allTasks.find((item) => item.id === taskId);
      if (task) {
        taskDetailReads.push(`${path}${url.search}`);
        await json(route, {
          ...task,
          capture_id: requestedTaskCapture(url),
          risks: taskRisksFor(taskId, requestedTaskCapture(url)),
        });
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
      await json(route, options.tasksPayload ?? {
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
    taskDetailReads,
    downloadRequests,
    findingDecisionRequests,
    findingApprovalRequests,
    get jobPollRequests() { return jobPollRequests; },
    get analysisJobListReads() { return analysisJobListReads; },
  };
}

