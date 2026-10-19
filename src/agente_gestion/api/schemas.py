"""Typed, deliberately allowlisted REST and OpenAPI models for H5."""

from datetime import datetime
import re
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator

from agente_gestion.application.tasks_query import TaskDetailView, TaskPlanSummary
from agente_gestion.domain.evidence import DiffSide, EvidenceSource
from agente_gestion.domain.task_comparison import TaskChange, TaskSnapshotComparison


T = TypeVar("T")
StageStatusValue = Literal[
    "pending", "running", "completed", "skipped", "failed", "needs_attention", "unknown",
]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class Page(ApiModel, Generic[T]):
    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class ErrorBody(ApiModel):
    code: str
    message: str


class ErrorEnvelope(ApiModel):
    error: ErrorBody


class ProjectView(ApiModel):
    id: str
    name: str
    source_mode: str
    latest_capture_at: datetime | None = None
    latest_run_id: str | None = None
    latest_run_status: str | None = None
    summary: TaskPlanSummary | None = None


class ConfiguredRepositoryView(ApiModel):
    repository_id: str
    owner: str
    repo: str
    branch: str | None = Field(default=None, exclude_if=lambda value: value is None)
    configured_commit_sha: str | None = Field(default=None, exclude_if=lambda value: value is None)
    quality_workflow_path: str | None = Field(default=None, exclude_if=lambda value: value is None)


class RepositoryIdentityView(ApiModel):
    repository_id: str
    owner: str
    repo: str


class RepositoryTestSuiteView(ApiModel):
    tests: int | None = None
    failed: int | None = None
    passed: int | None = None
    cancelled: int | None = None
    skipped: int | None = None
    todo: int | None = None
    success: bool | None = None


class RepositoryMetricsView(ApiModel):
    total_tests: int | None = None
    failed_tests: int | None = None
    coverage_percentage: float | None = None
    coverage_metric: str | None = None
    coverage_basis: str | None = None
    test_suites: dict[str, RepositoryTestSuiteView | None] | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    e2e_status: str | None = Field(default=None, exclude_if=lambda value: value is None)


class RepositoryArtifactValidationView(ApiModel):
    outcome: str
    source_metrics_certified: bool
    schema_valid: bool
    semantics_valid: bool
    identity_bound: bool
    workflow_status_matches: bool
    reason: str | None = None
    artifact_name: str | None = None
    source_document_metrics: RepositoryMetricsView | None = None


class RepositoryCIRunView(ApiModel):
    run_id: str
    workflow_name: str
    workflow_path: str | None = None
    commit_sha: str | None = None
    status: str
    actual_attempt: int | None = None
    artifact_id: str | None = None
    artifact_name: str | None = None
    timestamp: datetime | None = None
    repository: RepositoryIdentityView | None = None
    source_artifact_validation: RepositoryArtifactValidationView | None = None
    report_metrics_eligible: bool | None = None
    report_metrics: RepositoryMetricsView | None = None
    report_artifact_outcome: str | None = None
    report_artifact_reason: str | None = None


class RepositoryFindingView(ApiModel):
    finding_id: str
    repository_id: str
    commit_sha: str | None = None
    category: str | None = None
    severity: str | None = None
    rule_id: str | None = None
    file_path: str | None = None
    line: int | None = None
    confidence: float | None = None
    claim: str
    improvement_summary: str
    evidence: str = ""
    suggested_code: str = ""
    verification_status: Literal[
        "pending", "confirmed", "rejected", "insufficient", "unknown",
    ] = "unknown"
    verification_reason: str | None = None
    suggested_code_validation_status: str = "not_validated"


class RepositoryVerificationView(ApiModel):
    finding_id: str
    verdict: Literal["confirmed", "rejected", "insufficient"]
    reason: str
    verified_by: str
    evidence_ids: list[str]


class RepositoryHistoryView(ApiModel):
    capture_id: str
    snapshot_id: str
    source_scope: Literal["repository", "legacy", "unknown"]
    source_item_key: str | None = Field(default=None, exclude_if=lambda value: value is None)
    analysis_run_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    report_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    repository: RepositoryIdentityView | None = None
    branch: str | None = None
    target_commit_sha: str | None = None
    commit_resolution_status: Literal["pending", "resolved", "failed", "unknown"]
    access_status: Literal["unknown", "allowed", "denied"]
    availability_status: Literal["unknown", "available", "unavailable"]
    source_complete: bool | None = None
    incomplete_reason: str | None = None
    code_diff_count: int = Field(ge=0)
    coverage_percentage: float | None = None
    coverage_basis: str | None = None
    ci_runs: list[RepositoryCIRunView]
    source_problems: list[str]
    quality_status: StageStatusValue | None = None
    quality_reason: str | None = None
    verification_status: StageStatusValue | None = None
    verification_reason: str | None = None
    evaluation_status: StageStatusValue | None = None
    evaluation_reason: str | None = None
    findings: list[RepositoryFindingView]
    verifications: list[RepositoryVerificationView]


class RepositoryResultsCollection(ApiModel):
    project_id: str
    availability: Literal["available", "unknown"]
    message: str | None = Field(default=None, exclude_if=lambda value: value is None)
    repositories: list[ConfiguredRepositoryView]
    items: list[RepositoryHistoryView]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class CaptureHistoryView(ApiModel):
    capture_id: str
    captured_at: datetime
    summary: TaskPlanSummary


class EvidenceProvenanceView(ApiModel):
    id: str
    source: EvidenceSource
    project_id: str
    repository: str | None = None
    commit_sha: str | None = None
    file_path: str | None = None
    diff_side: DiffSide | None = None
    lines: list[int] | None = None
    external_id: str | None = None
    timestamp: datetime


class EvidenceView(ApiModel):
    provenance: EvidenceProvenanceView
    category: str
    summary: str


class TaskHistoryEntry(TaskChange):
    """Canonical domain change plus capture context, never an actor assertion."""

    observed_at: datetime | None = None
    capture_id: str | None = None
    previous_capture_id: str | None = None
    current_capture_id: str | None = None


class TaskDetailResponse(TaskDetailView):
    evidence: list[EvidenceView] = Field(default_factory=list)
    history: list[TaskHistoryEntry] = Field(default_factory=list)


class AuthSessionView(ApiModel):
    authenticated: bool
    actor: str | None = None
    csrf_token: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    login_available: bool
    email: str | None = None
    notification_email: str | None = None
    role: str | None = None


class LoginRequest(ApiModel):
    token: StrictStr | None = Field(default=None, max_length=512)
    email: StrictStr | None = Field(default=None, max_length=254)
    password: StrictStr | None = Field(default=None, max_length=512)


class SystemHealth(ApiModel):
    status: str
    version: str
    external_writes_enabled: bool = False


class RunSummary(ApiModel):
    run_id: str
    project_id: str
    status: Literal[
        "queued", "collecting", "analyzing", "awaiting_human", "completed",
        "needs_attention", "failed", "cancelled", "unknown",
    ]
    created_at: datetime
    updated_at: datetime
    snapshot_id: str
    duration_seconds: float | None = None
    is_simulated: bool | None = None
    report_id: str | None = None


class RunDetail(RunSummary):
    error_code: str | None = None


class ProviderAttempt(ApiModel):
    provider: str
    model: str
    status: str
    attempt_count: int = Field(ge=0)
    duration_seconds: float | None = None
    repository_id: str | None = None


class StageView(ApiModel):
    stage_name: str
    status: StageStatusValue
    recorded: bool
    duration_seconds: float | None = None
    completed_at: datetime | None = None
    providers: list[ProviderAttempt] | None = None
    result_summary: str | None = None
    error_code: str | None = None


class RunStages(ApiModel):
    run_id: str
    items: list[StageView]


class FindingSummary(ApiModel):
    finding_id: str
    run_id: str
    repository_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    repository_owner: str | None = Field(default=None, exclude_if=lambda value: value is None)
    repository_name: str | None = Field(default=None, exclude_if=lambda value: value is None)
    category: str | None = None
    severity: str | None = None
    rule_id: str | None = None
    file_path: str | None = None
    line: int | None = None
    commit_sha: str | None = None
    confidence: float | None = None
    claim: str = ""
    evidence: str = ""
    suggested_code: str = ""
    improvement_summary: str = ""
    decision_status: Literal["pending", "approved", "rejected", "changes_requested", "failed"] = "pending"
    branch_name: str | None = None
    branch_url: str | None = None
    commit_sha_applied: str | None = None
    candidate: Literal[True] = True
    verification_status: Literal[
        "pending", "confirmed", "rejected", "insufficient", "unknown",
    ] = "unknown"
    verification_reason: str | None = None
    suggested_code_validation_status: str = "not_validated"


class VerificationView(ApiModel):
    verdict: Literal["confirmed", "rejected", "insufficient"]
    reason: str
    verified_by: str
    evidence_ids: list[str]


class EvidenceSummary(ApiModel):
    id: str
    source: str
    timestamp: datetime | None = None
    repository: str | None = None
    commit_sha: str | None = None
    file_path: str | None = None
    lines: list[int] | None = None
    category: str | None = None
    summary: str


class EvidenceCollection(ApiModel):
    availability: Literal["available", "unknown"]
    items: list[EvidenceSummary]


class CodeExcerpt(ApiModel):
    evidence_id: str
    file_path: str
    lines: list[int]
    content: str
    evidence_scope: Literal["full_file", "snippet", "diff", "truncated"]
    is_partial: bool
    redacted: bool


class FindingDecisionRequest(ApiModel):
    decision: Literal["approve", "reject", "request_changes"]
    suggested_code_override: StrictStr | None = Field(default=None, max_length=65536)
    target_branch: StrictStr | None = Field(default=None, min_length=1, max_length=240)
    reason: StrictStr | None = Field(default=None, max_length=2000)

    @field_validator("target_branch")
    @classmethod
    def valid_git_branch(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (value == "@" or value.startswith(("/", "-")) or value.endswith(("/", "."))
                or ".." in value or "@{" in value or "//" in value
                or any(ord(ch) <= 32 or ord(ch) == 127 or ch in "~^:?*[\\\\" for ch in value)
                or any(not part or part.startswith(".") or part.endswith(".lock") for part in value.split("/"))):
            raise ValueError("El nombre de rama no es válido.")
        return value


class FindingDecisionResponse(ApiModel):
    reused: bool = False
    audit_event_id: str
    decision_status: Literal["approved", "rejected", "changes_requested"]
    branch_name: str | None = None
    branch_url: str | None = None
    commit_sha_applied: str | None = None
    suggested_code: str
    improvement_summary: str
    execution_mode: Literal["live", "sandbox"]
    repository_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    repository_owner: str | None = Field(default=None, exclude_if=lambda value: value is None)
    repository_name: str | None = Field(default=None, exclude_if=lambda value: value is None)
    source_commit_sha: str | None = Field(default=None, exclude_if=lambda value: value is None)


class FindingDetail(FindingSummary):
    claim: str | None = None
    suggested_fix: str | None = None
    evidence_ids: list[str] | None = None
    verification: VerificationView | None = None
    evidence: EvidenceCollection
    code_excerpt: CodeExcerpt | None = None


class FindingCounts(ApiModel):
    candidate: int = Field(ge=0)
    confirmed: int = Field(ge=0)
    rejected: int = Field(ge=0)
    insufficient: int = Field(ge=0)
    pending: int = Field(ge=0)
    unknown: int = Field(ge=0)


class CIRunView(ApiModel):
    id: str
    workflow_name: str
    commit_sha: str
    status: str
    total_tests: int | None = None
    failed_tests: int | None = None
    coverage_percentage: float | None = None
    timestamp: datetime | None = None


class QualitySummary(ApiModel):
    project_id: str
    run_id: str | None = None
    stage_status: StageStatusValue | None = None
    availability: Literal["available", "unknown"]
    finding_counts: FindingCounts | None = None
    ci: CIRunView | None = None


class PolicyResult(ApiModel):
    rule_id: str
    description: str
    status: Literal["pass", "fail", "unknown"]
    mandatory: bool
    observed_value: str | float | int | bool | None = None
    reason: str | None = None
    repository_id: str | None = None


class PoliciesSummary(ApiModel):
    project_id: str
    run_id: str | None = None
    availability: Literal["available", "unknown"]
    items: list[PolicyResult]


class CaptureProgressPoint(ApiModel):
    capture_id: str
    captured_at: datetime
    progress_percentage: float | None = None
    total_tasks: int
    completed_tasks: int
    state_counts: dict[str, int]


class RunProgressPoint(ApiModel):
    run_id: str
    created_at: datetime
    status: str
    duration_seconds: float | None = None


class RunSeries(ApiModel):
    total: int = Field(ge=0)
    by_status: dict[str, int]
    series: list[RunProgressPoint]


class ExecutiveReportSummary(ApiModel):
    report_id: str
    run_id: str | None = None
    cutoff_datetime: datetime
    recommendation: str
    human_decision: str
    summary: str


class ExecutiveSummary(ApiModel):
    project_id: str
    capture_id: str | None = None
    task_summary: TaskPlanSummary | None = None
    capture_series: list[CaptureProgressPoint]
    quality: QualitySummary | None = None
    policies: PoliciesSummary
    latest_report: ExecutiveReportSummary | None = None
    runs: RunSeries


class PrepareAnalysisRequest(ApiModel):
    request_analysis: Literal[True]


class ProjectBoundPrepareAnalysisRequest(PrepareAnalysisRequest):
    project_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ConfirmAnalysisRequest(ApiModel):
    preparation_id: str = Field(min_length=16, max_length=128)
    configuration_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_analysis: Literal[True]
    remote_consent: StrictBool


class ProjectBoundConfirmAnalysisRequest(ConfirmAnalysisRequest):
    project_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class PreflightCheck(ApiModel):
    code: str
    status: Literal["ready", "incomplete", "unknown"]


class ProviderPreflight(ApiModel):
    stage: str
    provider: str
    model: str
    transport: Literal["local", "remote", "unknown"]
    credential_status: Literal["configured", "missing", "not_required"]
    repository_id: str | None = Field(default=None, exclude_if=lambda value: value is None)


class RepositoryPreflight(ApiModel):
    repository_id: str
    owner: str
    repo: str
    branch: str | None = Field(default=None, exclude_if=lambda value: value is None)
    configured_commit_sha: str | None = Field(default=None, exclude_if=lambda value: value is None)
    quality_workflow_path: str | None = Field(default=None, exclude_if=lambda value: value is None)
    credential_status: Literal["configured", "missing"]
    commit_resolution_status: Literal["pending"] = "pending"
    access_status: Literal["unknown"] = "unknown"
    availability_status: Literal["unknown"] = "unknown"


class RemotePreflight(ApiModel):
    required: bool
    server_allowed: bool
    availability: Literal["not_checked"] = "not_checked"
    providers: list[ProviderPreflight]


class AnalysisPreflight(ApiModel):
    source_mode: str
    configuration_status: Literal["ready", "incomplete", "unknown"]
    checks: list[PreflightCheck]
    repositories: list[RepositoryPreflight] = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )
    proposal_rules_status: Literal["configured", "not_configured", "invalid"] = Field(
        default="not_configured",
        description=(
            "Owner rules availability for this project. Absent rules never block the "
            "analysis; they only mean no corrective proposal will be published."
        ),
    )


class AnalysisPreparation(ApiModel):
    preparation_id: str
    project_id: str
    expires_at: datetime
    configuration_fingerprint: str
    eligible: bool
    ready: bool
    execution_mode: Literal["live", "sandbox", "mixed"]
    fallback_reasons: list[str]
    capabilities: list[str]
    preflight: AnalysisPreflight
    remote: RemotePreflight


class AnalysisJobView(ApiModel):
    job_id: str
    project_id: str
    status: Literal["queued", "running", "completed", "failed", "interrupted"]
    created_at: datetime
    updated_at: datetime
    run_id: str | None = None
    error_code: str | None = None
    remote_consent: bool
    execution_mode: Literal["live", "sandbox", "mixed"]
    fallback_reasons: list[str]
    stage_progress_available: bool = False


class ConfirmAnalysisResponse(ApiModel):
    job: AnalysisJobView
    reused: bool
    poll_url: str
    poll_after_ms: int = Field(ge=250, le=30000)
