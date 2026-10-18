export type DomainTaskState =
  | "pending"
  | "en_curso"
  | "completada"
  | "bloqueada"
  | "desconocido";

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface TaskPlanSummary {
  project_id: string;
  capture_id: string;
  captured_at: string;
  cutoff_datetime: string;
  total_tasks: number;
  included_tasks: number;
  completed_tasks: number;
  progress_percentage: number | null;
  state_counts: Record<string, number>;
  overdue_count: number;
  due_soon_count: number;
  blocked_count: number;
  unknown_count: number;
  contradictory_count: number;
  overdue_task_ids: string[];
  due_soon_task_ids: string[];
  blocked_task_ids: string[];
  unknown_task_ids: string[];
  contradictory_task_ids: string[];
  identified_risks: string[];
}

export interface Project {
  id: string;
  name: string;
  source_mode: string;
  latest_capture_at: string | null;
  latest_run_id: string | null;
  latest_run_status: string | null;
  summary: TaskPlanSummary | null;
}

export interface TaskDetailView {
  id: string;
  title: string;
  state: DomainTaskState;
  list_id?: string | null;
  list_name?: string | null;
  assignee_ids: string[];
  start_date?: string | null;
  due_date?: string | null;
  due_complete?: boolean | null;
  priority?: string | null;
  labels: string[];
  is_included: boolean;
  is_archived?: boolean | null;
  has_contradiction: boolean;
  detail?: string | null;
  last_activity_at?: string | null;
  observed_fields?: string[] | null;
  is_overdue: boolean;
  is_due_soon: boolean;
  is_critical: boolean;
  explicit_dependencies: string[];
  dependency_impacts: string[];
}

export type EvidenceSource =
  | "trello"
  | "github"
  | "github_actions"
  | "ci_report"
  | "manual"
  | "synthetic";

export interface EvidenceProvenance {
  id: string;
  source: EvidenceSource | string;
  project_id: string;
  repository?: string | null;
  commit_sha?: string | null;
  file_path?: string | null;
  diff_side?: "OLD" | "NEW" | null;
  lines?: number[] | null;
  external_id?: string | null;
  timestamp: string;
}

export interface EvidenceItem {
  provenance: EvidenceProvenance;
  category: string;
  summary: string;
}

export interface TaskChange {
  id: string;
  task_id: string;
  kind: "field_changed" | "first_observed" | "not_observed";
  field?: string | null;
  before?: unknown;
  after?: unknown;
  previous_evidence_ids: string[];
  current_evidence_ids: string[];
  observed_at?: string | null;
  capture_id?: string | null;
  previous_capture_id?: string | null;
  current_capture_id?: string | null;
  actor: null;
}

/** Optional additions are kept optional until the backend publishes its final wire contract. */
export interface TaskDetailResponse extends TaskDetailView {
  evidence?: EvidenceItem[];
  history?: TaskChange[];
}

export interface CaptureSummary {
  capture_id: string;
  captured_at: string;
  summary: TaskPlanSummary;
}

export type ComparisonWarning =
  | "unknown_task_scope"
  | "limited_task_enumeration"
  | "conflicting_task_evidence"
  | string;

export interface TaskSnapshotComparison {
  project_id: string;
  previous_snapshot_id: string;
  current_snapshot_id: string;
  previous_captured_at: string;
  current_captured_at: string;
  changes: TaskChange[];
  warnings: ComparisonWarning[];
}

export type RunStatus =
  | "queued"
  | "collecting"
  | "analyzing"
  | "awaiting_human"
  | "completed"
  | "needs_attention"
  | "failed"
  | "cancelled"
  | "unknown";

export interface RunSummary {
  run_id: string;
  project_id: string;
  status: RunStatus;
  created_at: string;
  updated_at: string;
  snapshot_id: string;
  duration_seconds: number | null;
  is_simulated: boolean | null;
  report_id: string | null;
}

export interface RunDetail extends RunSummary {
  error_code: string | null;
}

export interface ProviderAttempt {
  provider: string;
  model: string;
  status: string;
  attempt_count: number;
  duration_seconds: number | null;
  repository_id?: string | null;
}

export type StageStatus = "pending" | "running" | "completed" | "skipped" | "failed" | "needs_attention" | "unknown";

export interface StageView {
  stage_name: string;
  status: StageStatus;
  recorded: boolean;
  duration_seconds: number | null;
  completed_at: string | null;
  providers: ProviderAttempt[] | null;
  result_summary: string | null;
  error_code: string | null;
}

export interface RunStages {
  run_id: string;
  items: StageView[];
}

export type FindingVerificationStatus = "pending" | "confirmed" | "rejected" | "insufficient" | "unknown";

export interface FindingSummary {
  finding_id: string;
  run_id: string;
  repository_id?: string;
  repository_owner?: string;
  repository_name?: string;
  category: string | null;
  severity: string | null;
  rule_id: string | null;
  file_path: string | null;
  line: number | null;
  commit_sha: string | null;
  confidence: number | null;
  candidate: true;
  verification_status: FindingVerificationStatus;
}

export interface ConfiguredRepositoryView {
  repository_id: string;
  owner: string;
  repo: string;
  branch?: string;
  configured_commit_sha?: string;
  quality_workflow_path?: string;
}

export interface RepositoryIdentityView {
  repository_id: string;
  owner: string;
  repo: string;
}

export interface RepositoryTestSuiteView {
  tests: number | null;
  failed: number | null;
  passed: number | null;
  cancelled: number | null;
  skipped: number | null;
  todo: number | null;
  success: boolean | null;
}

export interface RepositoryMetricsView {
  total_tests: number | null;
  failed_tests: number | null;
  coverage_percentage: number | null;
  coverage_metric: string | null;
  coverage_basis: string | null;
  test_suites?: Record<string, RepositoryTestSuiteView | null> | null;
  e2e_status?: string | null;
}

export interface RepositoryArtifactValidationView {
  outcome: string;
  source_metrics_certified: boolean;
  schema_valid: boolean;
  semantics_valid: boolean;
  identity_bound: boolean;
  workflow_status_matches: boolean;
  reason: string | null;
  artifact_name: string | null;
  source_document_metrics: RepositoryMetricsView | null;
}

export interface RepositoryCIRunView {
  run_id: string;
  workflow_name: string;
  workflow_path: string | null;
  commit_sha: string | null;
  status: string;
  actual_attempt: number | null;
  artifact_id: string | null;
  artifact_name: string | null;
  timestamp: string | null;
  repository: RepositoryIdentityView | null;
  source_artifact_validation: RepositoryArtifactValidationView | null;
  report_metrics_eligible: boolean | null;
  report_metrics: RepositoryMetricsView | null;
  report_artifact_outcome: string | null;
  report_artifact_reason: string | null;
}

export interface RepositoryFindingView {
  finding_id: string;
  repository_id: string;
  commit_sha: string | null;
  category: string | null;
  severity: string | null;
  rule_id: string | null;
  file_path: string | null;
  line: number | null;
  confidence: number | null;
  claim: string;
  improvement_summary: string;
  verification_status: FindingVerificationStatus;
}

export interface RepositoryVerificationView {
  finding_id: string;
  verdict: "confirmed" | "rejected" | "insufficient";
  reason: string;
  verified_by: string;
  evidence_ids: string[];
}

export interface RepositoryHistoryView {
  capture_id: string;
  snapshot_id: string;
  source_scope: "repository" | "legacy" | "unknown";
  source_item_key?: string;
  analysis_run_id?: string;
  report_id?: string;
  repository: RepositoryIdentityView | null;
  branch: string | null;
  target_commit_sha: string | null;
  commit_resolution_status: "pending" | "resolved" | "failed" | "unknown";
  access_status: "unknown" | "allowed" | "denied";
  availability_status: "unknown" | "available" | "unavailable";
  source_complete: boolean | null;
  incomplete_reason: string | null;
  code_diff_count: number;
  coverage_percentage: number | null;
  coverage_basis: string | null;
  ci_runs: RepositoryCIRunView[];
  source_problems: string[];
  quality_status: StageStatus | null;
  quality_reason: string | null;
  verification_status: StageStatus | null;
  verification_reason: string | null;
  evaluation_status: StageStatus | null;
  evaluation_reason: string | null;
  findings: RepositoryFindingView[];
  verifications: RepositoryVerificationView[];
}

export interface RepositoryHistoryFilters {
  repository_id?: string;
  repository_owner?: string;
  repository_name?: string;
  commit_sha?: string;
  ci_run_id?: string;
  attempt?: number;
}

export type RunFindingFilters = Pick<RepositoryHistoryFilters, "repository_id">;

export interface RepositoryResultsCollection extends Paginated<RepositoryHistoryView> {
  project_id: string;
  availability: "available" | "unknown";
  message?: string | null;
  repositories: ConfiguredRepositoryView[];
}

export interface VerificationView {
  verdict: "confirmed" | "rejected" | "insufficient";
  reason: string;
  verified_by: string;
  evidence_ids: string[];
}

export interface EvidenceSummary {
  id: string;
  source: string;
  timestamp: string | null;
  repository: string | null;
  commit_sha: string | null;
  file_path: string | null;
  lines: number[] | null;
  category: string | null;
  summary: string;
}

export interface EvidenceCollection {
  availability: "available" | "unknown";
  items: EvidenceSummary[];
}

export interface CodeExcerpt {
  evidence_id: string;
  file_path: string;
  lines: number[];
  content: string;
  evidence_scope: "full_file" | "snippet" | "diff" | "truncated";
  is_partial: boolean;
  redacted: boolean;
}

export type CodeFindingDecision = "approve" | "reject" | "request_changes";
export type ExecutionMode = "live" | "sandbox";
export type FindingDecisionStatus = "approved" | "rejected" | "changes_requested" | "pending";

export interface DecideCodeFindingRequest {
  decision: CodeFindingDecision;
  suggested_code_override?: string;
  target_branch?: string;
  reason?: string;
}

export interface DecideCodeFindingResponse {
  decision_status: FindingDecisionStatus;
  suggested_code: string;
  improvement_summary: string;
  branch_name: string | null;
  branch_url: string | null;
  commit_sha_applied: string | null;
  execution_mode: ExecutionMode;
}

export interface FindingDetail extends FindingSummary {
  suggested_code?: string | null;
  improvement_summary?: string | null;
  decision_status?: FindingDecisionStatus | null;
  commit_sha_applied?: string | null;
  execution_mode?: ExecutionMode | null;
  branch_name?: string | null;
  branch_url?: string | null;
  pull_request_url?: string | null;
  claim: string | null;
  suggested_fix: string | null;
  evidence_ids: string[] | null;
  verification: VerificationView | null;
  evidence: EvidenceCollection;
  code_excerpt: CodeExcerpt | null;
}

export interface FindingCounts {
  candidate: number;
  confirmed: number;
  rejected: number;
  insufficient: number;
  pending: number;
  unknown: number;
}

export interface CIRunView {
  id: string;
  workflow_name: string;
  commit_sha: string;
  status: string;
  total_tests: number | null;
  failed_tests: number | null;
  coverage_percentage: number | null;
  timestamp: string | null;
}

export interface QualitySummary {
  project_id: string;
  run_id: string | null;
  stage_status: StageStatus | null;
  availability: "available" | "unknown";
  finding_counts: FindingCounts | null;
  ci: CIRunView | null;
}

export interface PolicyResult {
  rule_id: string;
  description: string;
  status: "pass" | "fail" | "unknown";
  mandatory: boolean;
  observed_value: string | number | boolean | null;
  reason: string | null;
}

export interface PoliciesSummary {
  project_id: string;
  run_id: string | null;
  availability: "available" | "unknown";
  items: PolicyResult[];
}

export interface CaptureProgressPoint {
  capture_id: string;
  captured_at: string;
  progress_percentage: number | null;
  total_tasks: number;
  completed_tasks: number;
  state_counts: Record<string, number>;
}

export interface RunProgressPoint {
  run_id: string;
  created_at: string;
  status: string;
  duration_seconds: number | null;
}

export interface RunSeries {
  total: number;
  by_status: Record<string, number>;
  series: RunProgressPoint[];
}

export interface ExecutiveReportSummary {
  report_id: string;
  run_id: string | null;
  cutoff_datetime: string;
  recommendation: string;
  human_decision: string;
  summary: string;
}

export interface ExecutiveSummary {
  project_id: string;
  capture_id: string | null;
  task_summary: TaskPlanSummary | null;
  capture_series: CaptureProgressPoint[];
  quality: QualitySummary | null;
  policies: PoliciesSummary;
  latest_report: ExecutiveReportSummary | null;
  runs: RunSeries;
}

export interface PreflightCheck {
  code: string;
  status: "ready" | "incomplete" | "unknown";
}

export interface ProviderPreflight {
  stage: string;
  provider: string;
  model: string;
  transport: "local" | "remote" | "unknown";
  credential_status: "configured" | "missing" | "not_required";
}

export type AnalysisExecutionMode = "live" | "sandbox" | "mixed";

export interface AnalysisPreparation {
  preparation_id: string;
  project_id: string;
  expires_at: string;
  configuration_fingerprint: string;
  eligible: boolean;
  ready?: boolean;
  execution_mode?: AnalysisExecutionMode;
  fallback_reasons?: string[];
  capabilities: string[];
  preflight: {
    source_mode: string;
    configuration_status: "ready" | "incomplete" | "unknown";
    checks: PreflightCheck[];
  };
  remote: {
    required: boolean;
    server_allowed: boolean;
    availability: "not_checked";
    providers: ProviderPreflight[];
  };
}

export type AnalysisJobStatus = "queued" | "running" | "completed" | "failed" | "interrupted";

export interface AnalysisJob {
  job_id: string;
  project_id: string;
  status: AnalysisJobStatus;
  created_at: string;
  updated_at: string;
  run_id: string | null;
  error_code: string | null;
  remote_consent: boolean;
  execution_mode?: AnalysisExecutionMode;
  fallback_reasons?: string[];
}

export interface ConfirmAnalysisResponse {
  job: AnalysisJob;
  reused: boolean;
  poll_url: string;
  poll_after_ms: number;
}

export interface OperatorSession {
  authenticated: boolean;
  actor: string | null;
  csrf_token: string | null;
  capabilities: string[];
  login_available: boolean;
  email?: string | null;
  notification_email?: string | null;
  role?: string | null;
}

export interface SystemHealth {
  status: string;
  version: string;
  external_writes_enabled: boolean;
}

export interface ApiProblem {
  code: string;
  message: string;
}

export type ProposalField = string;
export type ProposalValue = string | string[] | null;

export interface ProposalEvidence {
  evidence_id: string;
  category: string;
  summary: string;
}

export type ProposalExecutionStatus = "not_executed" | "EXECUTED" | "ALREADY_APPLIED" | "DENIED" | "CONFLICT" | "FAILED" | "unknown";

export interface ProposalTechnicalReview {
  review_id: string;
  technical_status: "ready" | "blocked";
  technical_errors: string[];
  dry_run: {
    status: "SIMULATED" | "denied";
    reasons: string[];
    field: string | null;
    before: ProposalValue;
    after: ProposalValue;
    live_state_changed: false;
  };
  human_approval_status: "required";
  execution_authorized: false;
  recorded_at: string;
}

export interface ProposalSummary {
  proposal_id: string;
  project_id: string;
  task_id: string;
  capture_id: string;
  field: ProposalField;
  current_value: ProposalValue;
  proposed_value: ProposalValue;
  rationale: string;
  expected_impact: string;
  evidence: ProposalEvidence[];
  proposal_body_sha256: string;
  version: number | null;
  technical_eligibility: "eligible" | "ineligible" | "unknown";
  technical_errors: string[];
  technical_review?: ProposalTechnicalReview | null;
  freshness: "current" | "stale" | "unknown";
  execution_status: ProposalExecutionStatus;
}

export type ProposalDetail = ProposalSummary;

export interface ProposalCollection extends Paginated<ProposalSummary> {
  availability: "available" | "unknown";
}

export type ProposalExecutionDecision = "approve_and_execute" | "modify_and_execute" | "reject";

export interface ExecuteProposalRequest {
  decision: ProposalExecutionDecision;
  modified_due_date?: string;
  modified_priority?: string;
  modified_assignee_ids?: string[];
  reason: string;
}

export interface ProposalExecutionResult {
  status: string;
  verification_status: string;
  [key: string]: unknown;
}

export interface ExecuteProposalResponse {
  proposal_id: string;
  decision: ProposalExecutionDecision;
  execution: ProposalExecutionResult;
  execution_mode: ExecutionMode;
}

export interface ReportMetrics {
  total_cards: number;
  included_cards: number;
  completed_cards: number;
  progress_percentage: number | null;
  overdue_card_ids: string[];
  due_soon_card_ids: string[];
  blocked_card_ids: string[];
  unknown_card_ids: string[];
  contradictory_card_ids: string[];
}

export type ReportHumanDecision = "pending" | "approved" | "rejected" | "changes_requested" | "postponed";

export interface ReportSummary {
  report_id: string;
  project_id: string;
  run_id: string | null;
  report_version: number;
  created_at: string;
  cutoff_datetime: string;
  recommendation: string;
  human_decision: ReportHumanDecision;
  decision_reason: string | null;
  decision_timestamp: string | null;
  summary: string;
  metrics: ReportMetrics;
  risk_task_ids: string[];
  recommendations: string[];
  policy_results: PolicyResult[];
}

export interface ReportDecisionEvent {
  decision_id: string;
  report_id: string;
  run_id: string | null;
  decision: ReportHumanDecision;
  reason: string | null;
  recorded_at: string;
  actor_id: null;
  actor_verification: "unknown";
}

export interface ReportDetail extends ReportSummary {
  decision_history: ReportDecisionEvent[];
  markdown_text: string | null;
}

export interface ReportCollection extends Paginated<ReportSummary> {
  availability: "available" | "unknown";
}

export interface ReportDecisionResponse {
  report: ReportDetail;
  decision_event: ReportDecisionEvent;
  reused: boolean;
  audit_event_id: string;
}

export interface AuditEvent {
  event_id: string;
  project_id: string;
  event_type: string;
  occurred_at: string;
  resource_type: string | null;
  resource_id: string | null;
  outcome: string;
  summary: string;
  actor_id: string | null;
  actor_verification: "verified_local_session" | "unknown";
  source: string;
}

export interface AuditEventDetail extends AuditEvent {
  details: Record<string, unknown>;
}

export interface AuditCollection extends Paginated<AuditEvent> {
  availability: "available" | "unknown";
}

export interface WeeklyScheduleView {
  project_id: string;
  weekday: number;
  local_time: string;
  timezone: "America/Guayaquil";
  window_rule: "previous_scheduled_week";
}

export interface WeeklyWindowView {
  window_id: string;
  window_start_local: string;
  window_start_utc: string;
  window_end_local: string;
  window_end_utc: string;
  cutoff_local: string;
  cutoff_utc: string;
  next_boundary_local: string;
  next_boundary_utc: string;
}

export interface OutboxReceiptSummary {
  delivery_channel?: ManagerEmailDeliveryChannel | null;
  delivery_id: string;
  message_id: string;
  project_id: string;
  window_id: string;
  channel: string;
  status: "DELIVERED" | "SKIPPED_DUPLICATE" | "FAILED";
  dispatched_at: string;
  artifact_available: boolean;
  report_id: string | null;
}

export interface OutboxMetrics {
  total_tasks: number | null;
  included_tasks: number | null;
  completed_tasks: number | null;
  progress_percentage: number | null;
  overdue_count: number | null;
  due_soon_count: number | null;
  blocked_count: number | null;
  unknown_count: number | null;
  contradictory_count: number | null;
}

export interface OutboxMessageView {
  message_id: string;
  project_id: string;
  window_id: string;
  report_id: string | null;
  channel: "file" | "sqlite" | "console" | "all";
  subject: string;
  summary: string;
  body_markdown: string;
  metrics: OutboxMetrics;
  risks: string[];
  recipient: string | null;
  created_at: string;
}

export interface OutboxDetail {
  receipt: OutboxReceiptSummary;
  message: OutboxMessageView;
}

export interface SendManagerEmailRequest {
  recipient_email?: string;
  outbox_delivery_id?: string;
}

export type ManagerEmailDeliveryChannel = "local_outbox_simulated_email" | "smtp_email_sent" | "smtp_email_failed";

export interface SendManagerEmailResponse {
  delivery_id: string;
  delivery_channel: ManagerEmailDeliveryChannel;
  recipient_email: string;
  html_preview_url: string | null;
  status: string;
}

export interface OutboxCollection extends Paginated<OutboxReceiptSummary> {
  availability: "available" | "unknown";
}

export interface ScheduleView {
  project_id: string;
  availability: "configured" | "unknown";
  enabled: boolean;
  schedule: WeeklyScheduleView | null;
  planned_window: WeeklyWindowView | null;
  is_due: boolean | null;
  is_dispatched: boolean | null;
  outbox_history: "available" | "unknown";
  can_run_manually: boolean;
  last_receipt: OutboxReceiptSummary | null;
  automatic_trigger_status: "not_verified";
  remote: { required: false; server_allowed: false; availability: "not_checked"; providers: [] };
}

export interface ScheduleCyclePreparation {
  preparation_id: string;
  project_id: string;
  expires_at: string;
  configuration_fingerprint: string;
  eligible: boolean;
  capabilities: string[];
  preflight: {
    schedule_status: "configured" | "incomplete" | "invalid" | "disabled";
    window_id: string | null;
    has_captures: boolean | null;
    outbox_history: "available" | "unknown";
    report_binding: "available" | "unknown" | "mismatch";
    can_run: boolean;
  };
  remote: { required: false; server_allowed: false; availability: "not_checked"; providers: [] };
}

export type WeeklyCycleJobStatus = "queued" | "running" | "completed" | "failed" | "interrupted";
export type WeeklyCycleOutcome = "EXECUTED" | "SKIPPED_NOT_DUE" | "SKIPPED_ALREADY_DISPATCHED" | "FAILED" | "unknown";

export interface WeeklyCycleJob {
  job_id: string;
  project_id: string;
  status: WeeklyCycleJobStatus;
  created_at: string;
  updated_at: string;
  outcome: WeeklyCycleOutcome;
  window_id: string | null;
  delivery_id: string | null;
  error_code: string | null;
  remote_consent: false;
}

export interface WeeklyCycleConfirmResponse {
  job: WeeklyCycleJob;
  reused: boolean;
  poll_url: string;
  poll_after_ms: number;
}

export interface WeeklyCycleJobCollection extends Paginated<WeeklyCycleJob> {
  availability: "available" | "unknown";
}

export interface RepositoryConfiguration {
  configured: boolean;
  owner: string | null;
  name: string | null;
  branch: string | null;
  commit_sha: string | null;
}

export interface TrelloConfiguration {
  configured: boolean;
  board_id: string | null;
  credential_status: "configured" | "missing" | "unknown";
}

export interface ProjectConfiguration {
  project_id: string;
  availability: "available" | "unknown";
  source_mode: "fixtures" | "real" | "stored";
  timezone: string | null;
  due_soon_days: number | null;
  repository: RepositoryConfiguration;
  trello: TrelloConfiguration;
  providers: ProviderPreflight[];
  policies: { configured: boolean | null; version: string | null };
  schedule: WeeklyScheduleView | null;
  schedule_status: "configured" | "not_configured" | "incomplete" | "invalid";
  capabilities: string[];
  editable_fields: [];
}

export interface DownloadedFile {
  blob: Blob;
  filename: string;
}

export interface TaskQuery {
  capture_id?: string;
  q?: string;
  state?: DomainTaskState;
  assignee_id?: string;
  list_name?: string;
  critical_only?: boolean;
  overdue_only?: boolean;
  due_soon_only?: boolean;
  sort?: "due_date" | "title";
  direction?: "asc" | "desc";
  page: number;
  page_size: number;
}
