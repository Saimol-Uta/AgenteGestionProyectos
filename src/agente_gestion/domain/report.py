"""Monitoring draft, human decision, and final report domain models."""

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict, StrictStr, model_validator
from agente_gestion.domain.action_proposal import ProposalField, ProposalValue, _value

from agente_gestion.domain.tasks import TaskState, DeterministicTaskMetrics
from agente_gestion.domain.code import CIRunResult
from agente_gestion.domain.findings import CandidateFinding, FindingVerification
from agente_gestion.domain.policies import PolicyEvaluationResult
from agente_gestion.domain.repositories import RepositoryAnalysisState
from agente_gestion.domain.run import StageStatus


class HumanDecisionState(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    changes_requested = "changes_requested"
    postponed = "postponed"


class TaskStateClaim(BaseModel):
    """Structured task assertions checked against the deterministic snapshot."""
    task_id: str = Field(min_length=1)
    state: TaskState


class DomainProposalRule(BaseModel):
    """Owner-supplied constraints, not authorization or authenticated identity."""
    model_config = ConfigDict(extra="forbid")
    rule_id: StrictStr
    task_ids: list[StrictStr]
    field: ProposalField
    allowed_values: list[ProposalValue]
    rationale: StrictStr
    expected_impact: StrictStr

    @model_validator(mode="before")
    @classmethod
    def strict_values(cls, data):
        data = dict(data)
        for key in ("rule_id", "rationale", "expected_impact"):
            if type(data.get(key)) is not str or not data[key].strip():
                raise ValueError("Nonempty rule identifiers and explanations required")
        ids = data.get("task_ids")
        if type(ids) is not list or not ids or any(type(i) is not str or not i.strip() for i in ids):
            raise ValueError("Explicit eligible task IDs required")
        values = data.get("allowed_values")
        if type(values) is not list or not values:
            raise ValueError("Explicit alternatives required")
        data["allowed_values"] = [_value(data.get("field"), v) for v in values]
        if TaskState.unknown in data["allowed_values"]:
            raise ValueError("Unknown state is not a goal")
        return data


class DomainProposalRuleSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: StrictStr = Field(min_length=1)
    rules: list[DomainProposalRule]


class MonitoringRequestedChange(BaseModel):
    """Canonical recommendation only; future capture-bound review is required."""
    model_config = ConfigDict(extra="forbid")
    project_id: StrictStr
    rule_id: StrictStr
    task_id: StrictStr
    field: ProposalField
    current_value: ProposalValue
    proposed_value: ProposalValue
    evidence_ids: list[StrictStr]
    rationale: StrictStr
    expected_impact: StrictStr

    @model_validator(mode="before")
    @classmethod
    def strict_values(cls, data):
        data = dict(data)
        for key in ("current_value", "proposed_value"):
            data[key] = _value(data.get("field"), data[key])
        return data


class MonitoringDraft(BaseModel):
    """Initial progress assessment drafted by monitoring agent (elaborador)."""
    summary: str = Field(description="Narrative progress summary")
    task_state_claims: list[TaskStateClaim] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list, description="Identified project risks")
    suggested_actions: list[str] = Field(default_factory=list, description="Actionable recommendations")
    referenced_task_ids: list[str] = Field(default_factory=list, description="Task IDs explicitly referenced")
    evidence_ids: list[str] = Field(default_factory=list, description="Referenced evidence IDs")
    ci_status_note: Optional[str] = Field(default=None, description="Notes on CI test execution")
    requested_changes: list[MonitoringRequestedChange] = Field(default_factory=list)
    proposal_limitations: list[str] = Field(default_factory=list)
    claimed_progress_percentage: Optional[float] = Field(default=None, description="Optional claimed progress percentage")
    claimed_completed_cards: Optional[int] = Field(default=None, description="Optional claimed completed cards count")
    claimed_total_cards: Optional[int] = Field(default=None, description="Optional claimed total cards count")


class RepositoryReportResult(BaseModel):
    """Repository-isolated report payload; unknown evidence remains explicitly null."""

    repository_state: RepositoryAnalysisState
    ci_result: Optional[CIRunResult] = None
    policy_evaluations: Optional[PolicyEvaluationResult] = None
    confirmed_findings: list[CandidateFinding] = Field(default_factory=list)
    rejected_findings: list[CandidateFinding] = Field(default_factory=list)
    insufficient_findings: list[CandidateFinding] = Field(default_factory=list)
    pending_findings: list[CandidateFinding] = Field(default_factory=list)
    verifications: list[FindingVerification] = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )
    coverage_percentage: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    coverage_scope: Optional[str] = None
    source_is_complete: Optional[bool] = None
    source_problems: list[str] = Field(default_factory=list, exclude_if=lambda value: not value)
    quality_status: Optional[StageStatus] = None
    quality_reason: Optional[str] = None
    verification_status: Optional[StageStatus] = None
    verification_reason: Optional[str] = None
    evaluation_status: Optional[StageStatus] = None
    evaluation_reason: Optional[str] = None

    @model_validator(mode="after")
    def validate_repository_scope(self) -> "RepositoryReportResult":
        identity = self.repository_state.identity
        target_sha = self.repository_state.target_commit_sha
        findings = [
            *self.confirmed_findings,
            *self.rejected_findings,
            *self.insufficient_findings,
            *self.pending_findings,
        ]
        items = list(findings)
        if self.ci_result is not None:
            items.append(self.ci_result)
        for item in items:
            if item.repository_identity is not None and item.repository_identity != identity:
                raise ValueError("Repository report item identity does not match repository_state.")
            if target_sha is not None and item.commit_sha.lower() != target_sha:
                raise ValueError("Repository report item commit does not match target_commit_sha.")
        finding_ids = [item.id for item in findings]
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("Repository report finding IDs must be unique within the repository result.")
        if any(item.finding_id not in set(finding_ids) for item in self.verifications):
            raise ValueError("Repository report verification references an unknown finding ID.")
        return self


class FinalReport(BaseModel):
    """Complete synthesized weekly report awaiting human manager decision."""
    project_id: str
    cutoff_datetime: datetime
    metrics: DeterministicTaskMetrics
    ci_result: Optional[CIRunResult] = None
    confirmed_findings: list[CandidateFinding] = Field(default_factory=list)
    rejected_findings: list[CandidateFinding] = Field(default_factory=list)
    insufficient_findings: list[CandidateFinding] = Field(default_factory=list)
    pending_findings: list[CandidateFinding] = Field(default_factory=list)
    policy_evaluations: PolicyEvaluationResult
    recommendation: str = Field(description="Advisory recommendation based strictly on policies")
    human_decision: HumanDecisionState = Field(default=HumanDecisionState.pending)
    summary: str = Field(description="Validated final summary")
    recommendations: list[str] = Field(default_factory=list)
    report_id: Optional[str] = None
    report_version: int = 1
    run_id: Optional[str] = None
    decision_reason: Optional[str] = None
    decision_timestamp: Optional[datetime] = None
    providers_used: dict[str, str] = Field(default_factory=dict)
    candidate_findings: list[CandidateFinding] = Field(default_factory=list)
    all_evidence_ids: list[str] = Field(default_factory=list)
    repository_results: list[RepositoryReportResult] = Field(
        default_factory=list,
        exclude_if=lambda results: not results,
    )
