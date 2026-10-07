"""Evidence and provenance contracts for project auditability."""

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class EvidenceSource(str, Enum):
    trello = "trello"
    github = "github"
    github_actions = "github_actions"
    ci_report = "ci_report"
    manual = "manual"
    synthetic = "synthetic"


class DiffSide(str, Enum):
    OLD = "OLD"
    NEW = "NEW"


class EvidenceProvenance(BaseModel):
    """Immutable origin tracking for any piece of evidence."""
    id: str = Field(description="Unique identifier of this evidence record")
    source: EvidenceSource = Field(description="Origin system / tool")
    project_id: str = Field(description="Identifier of the project")
    repository: Optional[str] = Field(default=None, description="Repository identifier if applicable")
    commit_sha: Optional[str] = Field(default=None, description="Exact Git commit SHA if applicable")
    file_path: Optional[str] = Field(default=None, description="Relative file path if applicable")
    diff_side: Optional[DiffSide] = Field(default=None, description="Side of diff (OLD or NEW)")
    lines: Optional[list[int]] = Field(default=None, description="Relevant line numbers")
    external_id: Optional[str] = Field(default=None, description="ID in the external system (e.g. Trello card ID, PR ID)")
    timestamp: datetime = Field(description="Time when the event occurred or snapshot was captured")
    payload: dict[str, Any] = Field(default_factory=dict, description="Raw or normalized payload content")


class EvidenceItem(BaseModel):
    """Domain model of an item of evidence."""
    provenance: EvidenceProvenance
    category: str = Field(description="Evidence category e.g. task, diff, ci_run, rule_check")
    summary: str = Field(description="Human-readable brief summary")
