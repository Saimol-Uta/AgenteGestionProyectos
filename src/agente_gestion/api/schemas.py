"""Typed, deliberately allowlisted REST and OpenAPI models for H5."""

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

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


class LoginRequest(ApiModel):
    token: StrictStr = Field(min_length=1, max_length=512)


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
    category: str | None = None
    severity: str | None = None
    rule_id: str | None = None
    file_path: str | None = None
    line: int | None = None
    commit_sha: str | None = None
    confidence: float | None = None
    candidate: Literal[True] = True
    verification_status: Literal[
        "pending", "confirmed", "rejected", "insufficient", "unknown",
    ] = "unknown"


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


class ConfirmAnalysisRequest(ApiModel):
    preparation_id: str = Field(min_length=16, max_length=128)
    configuration_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_analysis: Literal[True]
    remote_consent: StrictBool


class PreflightCheck(ApiModel):
    code: str
    status: Literal["ready", "incomplete", "unknown"]


class ProviderPreflight(ApiModel):
    stage: str
    provider: str
    model: str
    transport: Literal["local", "remote", "unknown"]
    credential_status: Literal["configured", "missing", "not_required"]


class RemotePreflight(ApiModel):
    required: bool
    server_allowed: bool
    availability: Literal["not_checked"] = "not_checked"
    providers: list[ProviderPreflight]


class AnalysisPreflight(ApiModel):
    source_mode: str
    configuration_status: Literal["ready", "incomplete", "unknown"]
    checks: list[PreflightCheck]


class AnalysisPreparation(ApiModel):
    preparation_id: str
    project_id: str
    expires_at: datetime
    configuration_fingerprint: str
    eligible: bool
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
    stage_progress_available: bool = False


class ConfirmAnalysisResponse(ApiModel):
    job: AnalysisJobView
    reused: bool
    poll_url: str
    poll_after_ms: int = Field(ge=250, le=30000)
