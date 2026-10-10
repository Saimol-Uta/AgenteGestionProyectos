"""Snapshot of collected raw inputs at a given weekly cutoff."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

from agente_gestion.domain.tasks import TaskCard
from agente_gestion.domain.code import CodeDiffSnippet, CIRunResult
from agente_gestion.domain.policies import QualityRule


class WeeklySnapshot(BaseModel):
    """Immutable state snapshot collected for a project cut."""
    id: str = Field(description="Unique snapshot identifier")
    project_id: str
    cutoff_datetime: datetime
    captured_at: datetime
    tasks: list[TaskCard] = Field(default_factory=list)
    code_diffs: list[CodeDiffSnippet] = Field(default_factory=list)
    ci_runs: list[CIRunResult] = Field(default_factory=list)
    policies: list[QualityRule] = Field(default_factory=list)
    is_complete: bool = Field(default=True, description="Whether all sources responded without error")
    incomplete_reason: Optional[str] = None
    task_source_identity: Optional[str] = Field(
        default=None, description="Task source + mapper scope/version; None means legacy unknown",
    )
    tasks_complete: Optional[bool] = Field(
        default=None, description="Task enumeration completeness, independent of CI/other sources",
    )
