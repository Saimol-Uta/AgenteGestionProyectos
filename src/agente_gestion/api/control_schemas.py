"""Allowlisted typed contracts for T4/T5 governance and reporting resources."""

from datetime import datetime
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr

from agente_gestion.api.schemas import ApiModel, Page, PolicyResult, ProviderPreflight
from agente_gestion.domain.tasks import TaskState


ProposalValue = datetime | TaskState | StrictStr | list[StrictStr] | None


class ProposalEvidence(ApiModel):
    evidence_id: str
    category: str
    summary: str


class ProposalTechnicalDryRun(ApiModel):
    status: Literal["SIMULATED", "denied"]
    reasons: list[str]
    field: str | None = None
    before: ProposalValue = None
    after: ProposalValue = None
    live_state_changed: Literal[False] = False


class ProposalTechnicalReviewView(ApiModel):
    review_id: str
    technical_status: Literal["ready", "blocked"]
    technical_errors: list[str]
    dry_run: ProposalTechnicalDryRun
    human_approval_status: Literal["required"] = "required"
    execution_authorized: Literal[False] = False
    recorded_at: datetime


class ProposalSummary(ApiModel):
    proposal_id: str
    project_id: str
    task_id: str
    capture_id: str
    field: Literal["start_date", "due_date", "priority", "assignee_ids", "state"]
    current_value: ProposalValue
    proposed_value: ProposalValue
    rationale: str
    expected_impact: str
    evidence: list[ProposalEvidence]
    proposal_body_sha256: str
    version: int | None = Field(default=None, ge=0)
    technical_eligibility: Literal["eligible", "ineligible", "unknown"]
    technical_errors: list[str]
    technical_review: ProposalTechnicalReviewView | None = None
    freshness: Literal["current", "stale", "unknown"]
    review_status: Literal["pending", "approved_offline", "rejected", "unknown"]
    authorization_status: Literal[
        "not_authorized", "authorized_dry_run", "denied", "revoked", "unknown",
    ]
    execution_status: Literal[
        "not_executed", "EXECUTED", "ALREADY_APPLIED", "DENIED", "CONFLICT", "FAILED", "unknown",
    ]


class ProposalDetail(ProposalSummary):
    pass


class ProposalCollection(ApiModel):
    availability: Literal["available", "unknown"]
    items: list[ProposalSummary]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class ReportMetrics(ApiModel):
    total_cards: int = Field(ge=0)
    included_cards: int = Field(ge=0)
    completed_cards: int = Field(ge=0)
    progress_percentage: float | None = None
    overdue_card_ids: list[str]
    due_soon_card_ids: list[str]
    blocked_card_ids: list[str]
    unknown_card_ids: list[str]
    contradictory_card_ids: list[str]


class ReportSummary(ApiModel):
    report_id: str
    project_id: str
    run_id: str | None = None
    report_version: int = Field(ge=1)
    created_at: datetime
    cutoff_datetime: datetime
    recommendation: str
    human_decision: Literal["pending", "approved", "rejected", "changes_requested", "postponed"]
    decision_reason: str | None = None
    decision_timestamp: datetime | None = None
    summary: str
    metrics: ReportMetrics
    risk_task_ids: list[str]
    recommendations: list[str]
    policy_results: list[PolicyResult]


class ReportDecisionEvent(ApiModel):
    decision_id: str
    report_id: str
    run_id: str | None = None
    decision: Literal["pending", "approved", "rejected", "changes_requested", "postponed"]
    reason: str | None = None
    recorded_at: datetime
    actor_id: None = None
    actor_verification: Literal["unknown"] = "unknown"


class ReportDetail(ReportSummary):
    decision_history: list[ReportDecisionEvent]
    markdown_text: str | None = None


class ReportCollection(ApiModel):
    availability: Literal["available", "unknown"]
    items: list[ReportSummary]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class ReportDecisionRequest(ApiModel):
    expected_report_version: StrictInt = Field(ge=1)
    expected_human_decision: Literal["pending"]
    decision: Literal["approved", "rejected", "changes_requested", "postponed"]
    reason: StrictStr | None = Field(default=None, max_length=2000)


class ReportDecisionResponse(ApiModel):
    report: ReportDetail
    decision_event: ReportDecisionEvent
    reused: bool
    audit_event_id: str


class AuditEvent(ApiModel):
    event_id: str
    project_id: str
    event_type: str
    occurred_at: datetime
    resource_type: str | None = None
    resource_id: str | None = None
    outcome: str
    summary: str
    actor_id: str | None = None
    actor_verification: Literal["verified_local_session", "unknown"] = "unknown"
    source: Literal["engine", "web", "outbox", "scheduler"]


class AuditEventDetail(AuditEvent):
    details: dict[str, str | int | float | bool | None]


class AuditCollection(ApiModel):
    availability: Literal["available", "unknown"]
    items: list[AuditEvent]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class WeeklyScheduleView(ApiModel):
    project_id: str
    weekday: int = Field(ge=0, le=6)
    local_time: str
    timezone: Literal["America/Guayaquil"]
    window_rule: Literal["previous_scheduled_week"]


class WeeklyWindowView(ApiModel):
    window_id: str
    window_start_local: datetime
    window_start_utc: datetime
    window_end_local: datetime
    window_end_utc: datetime
    cutoff_local: datetime
    cutoff_utc: datetime
    next_boundary_local: datetime
    next_boundary_utc: datetime


class ScheduleRemoteView(ApiModel):
    required: Literal[False] = False
    server_allowed: Literal[False] = False
    availability: Literal["not_checked"] = "not_checked"
    providers: list[ProviderPreflight] = Field(default_factory=list)


class OutboxReceiptSummary(ApiModel):
    delivery_channel: Literal["local_outbox_simulated_email", "smtp_email_sent", "smtp_email_failed"] | None = None
    delivery_id: str
    message_id: str
    project_id: str
    window_id: str
    channel: str
    status: Literal["DELIVERED", "SKIPPED_DUPLICATE", "FAILED"]
    dispatched_at: datetime
    artifact_available: bool
    report_id: str | None = None


class OutboxMetrics(ApiModel):
    total_tasks: int | None = None
    included_tasks: int | None = None
    completed_tasks: int | None = None
    progress_percentage: float | None = None
    overdue_count: int | None = None
    due_soon_count: int | None = None
    blocked_count: int | None = None
    unknown_count: int | None = None
    contradictory_count: int | None = None


class OutboxMessageView(ApiModel):
    message_id: str
    project_id: str
    window_id: str
    report_id: str | None
    channel: Literal["file", "sqlite", "console", "all"]
    subject: str
    summary: str
    body_markdown: str
    metrics: OutboxMetrics
    risks: list[str]
    recipient: str | None = None
    created_at: datetime


class OutboxDetail(ApiModel):
    receipt: OutboxReceiptSummary
    message: OutboxMessageView


class OutboxCollection(ApiModel):
    availability: Literal["available", "unknown"]
    items: list[OutboxReceiptSummary]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class ScheduleView(ApiModel):
    project_id: str
    availability: Literal["configured", "unknown"]
    enabled: bool
    schedule: WeeklyScheduleView | None = None
    planned_window: WeeklyWindowView | None = None
    is_due: bool | None = None
    is_dispatched: bool | None = None
    outbox_history: Literal["available", "unknown"] = "unknown"
    can_run_manually: bool
    last_receipt: OutboxReceiptSummary | None = None
    automatic_trigger_status: Literal["not_verified"] = "not_verified"
    remote: ScheduleRemoteView = Field(default_factory=ScheduleRemoteView)


class ScheduleCyclePrepareRequest(ApiModel):
    request_cycle: Literal[True]


class ScheduleCyclePreflight(ApiModel):
    schedule_status: Literal["configured", "incomplete", "invalid", "disabled"]
    window_id: str | None = None
    has_captures: bool | None
    outbox_history: Literal["available", "unknown"]
    report_binding: Literal["available", "unknown", "mismatch"]
    can_run: bool


class ScheduleCyclePreparation(ApiModel):
    preparation_id: str
    project_id: str
    expires_at: datetime
    configuration_fingerprint: str
    eligible: bool
    capabilities: list[str]
    preflight: ScheduleCyclePreflight
    remote: ScheduleRemoteView = Field(default_factory=ScheduleRemoteView)


class ScheduleCycleConfirmRequest(ApiModel):
    preparation_id: StrictStr = Field(min_length=16, max_length=128)
    configuration_fingerprint: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_cycle: Literal[True]
    remote_consent: StrictBool = False


class WeeklyCycleJob(ApiModel):
    job_id: str
    project_id: str
    status: Literal["queued", "running", "completed", "failed", "interrupted"]
    created_at: datetime
    updated_at: datetime
    outcome: Literal[
        "EXECUTED", "SKIPPED_NOT_DUE", "SKIPPED_ALREADY_DISPATCHED", "FAILED", "unknown",
    ] = "unknown"
    window_id: str | None = None
    delivery_id: str | None = None
    error_code: str | None = None
    remote_consent: Literal[False] = False


class WeeklyCycleConfirmResponse(ApiModel):
    job: WeeklyCycleJob
    reused: bool
    poll_url: str
    poll_after_ms: int = Field(ge=250, le=30000)


class WeeklyCycleJobCollection(ApiModel):
    availability: Literal["available", "unknown"]
    items: list[WeeklyCycleJob]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class RepositoryConfiguration(ApiModel):
    configured: bool
    owner: str | None = None
    name: str | None = None
    branch: str | None = None
    commit_sha: str | None = None


class TrelloConfiguration(ApiModel):
    configured: bool
    board_id: str | None = None
    credential_status: Literal["configured", "missing", "unknown"]


class PoliciesConfiguration(ApiModel):
    configured: bool | None = None
    version: str | None = None


class ProjectConfiguration(ApiModel):
    project_id: str
    availability: Literal["available", "unknown"]
    source_mode: Literal["fixtures", "real", "stored"]
    timezone: str | None = None
    due_soon_days: int | None = None
    repository: RepositoryConfiguration
    trello: TrelloConfiguration
    providers: list[ProviderPreflight]
    policies: PoliciesConfiguration
    schedule: WeeklyScheduleView | None = None
    schedule_status: Literal["configured", "not_configured", "incomplete", "invalid"]
    capabilities: list[str]
    editable_fields: list[str] = Field(default_factory=list)
