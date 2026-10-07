"""Monitoring draft, human decision, and final report domain models."""

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

from agente_gestion.domain.tasks import TaskState, DeterministicTaskMetrics
from agente_gestion.domain.code import CIRunResult
from agente_gestion.domain.findings import CandidateFinding
from agente_gestion.domain.policies import PolicyEvaluationResult


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


class MonitoringDraft(BaseModel):
    """Initial progress assessment drafted by monitoring agent (elaborador)."""
    summary: str = Field(description="Narrative progress summary")
    task_state_claims: list[TaskStateClaim] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list, description="Identified project risks")
    suggested_actions: list[str] = Field(default_factory=list, description="Actionable recommendations")
    referenced_task_ids: list[str] = Field(default_factory=list, description="Task IDs explicitly referenced")
    evidence_ids: list[str] = Field(default_factory=list, description="Referenced evidence IDs")
    ci_status_note: Optional[str] = Field(default=None, description="Notes on CI test execution")
    claimed_progress_percentage: Optional[float] = Field(default=None, description="Optional claimed progress percentage")
    claimed_completed_cards: Optional[int] = Field(default=None, description="Optional claimed completed cards count")
    claimed_total_cards: Optional[int] = Field(default=None, description="Optional claimed total cards count")


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
