"""Snapshot of collected raw inputs at a given weekly cutoff."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, model_validator

from agente_gestion.domain.tasks import TaskCard
from agente_gestion.domain.code import CodeDiffSnippet, CIRunResult
from agente_gestion.domain.policies import QualityRule
from agente_gestion.domain.repositories import RepositoryAnalysisState


class RepositorySnapshotResult(BaseModel):
    """Repository-scoped snapshot payload that can retain partial source outcomes."""

    repository_state: RepositoryAnalysisState
    code_diffs: list[CodeDiffSnippet] = Field(default_factory=list)
    ci_runs: list[CIRunResult] = Field(default_factory=list)
    is_complete: Optional[bool] = None
    incomplete_reason: Optional[str] = None

    @model_validator(mode="after")
    def validate_repository_scope(self) -> "RepositorySnapshotResult":
        identity = self.repository_state.identity
        target_sha = self.repository_state.target_commit_sha
        for item in [*self.code_diffs, *self.ci_runs]:
            if item.repository_identity is not None and item.repository_identity != identity:
                raise ValueError("Repository snapshot item identity does not match repository_state.")
            if target_sha is not None and item.commit_sha.lower() != target_sha:
                raise ValueError("Repository snapshot item commit does not match target_commit_sha.")
        return self


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
    repository_results: list[RepositorySnapshotResult] = Field(
        default_factory=list,
        exclude_if=lambda results: not results,
    )

    @model_validator(mode="after")
    def validate_repository_completeness(self) -> "WeeklySnapshot":
        if self.is_complete and any(result.is_complete is not True for result in self.repository_results):
            raise ValueError("A complete snapshot cannot contain an incomplete repository result.")
        return self


class SnapshotCapture(BaseModel):
    """Content-addressed stored capture; identity hashes original JSON, not this model."""
    capture_id: str
    content_hash: str
    snapshot: WeeklySnapshot
