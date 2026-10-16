"""Repository configuration, analysis state, and recorded provenance contracts.

These models describe configured identity and observations. They do not establish
that a repository was reachable, a commit was resolved, or evidence was validated.
"""

import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator


_REPOSITORY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_OWNER_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_FULL_SHA_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")


class RepositoryIdentity(BaseModel):
    """Stable configured repository identity, independent of run-specific state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: StrictStr = Field(min_length=1, max_length=128)
    owner: StrictStr = Field(min_length=1)
    repo: StrictStr = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _REPOSITORY_ID_RE.fullmatch(value):
            raise ValueError("Repository id must be a nonempty identifier using letters, digits, '.', '_' or '-'.")
        return value

    @field_validator("owner")
    @classmethod
    def validate_owner(cls, value: str) -> str:
        if not _OWNER_RE.fullmatch(value):
            raise ValueError("GitHub owner is not valid.")
        return value

    @field_validator("repo")
    @classmethod
    def validate_repo(cls, value: str) -> str:
        if not _REPO_RE.fullmatch(value) or value in {".", ".."}:
            raise ValueError("GitHub repository name is not valid.")
        return value


class RepositoryConfiguration(RepositoryIdentity):
    """Configured repository selector; commit_sha is an explicit pin, not resolution."""

    branch: StrictStr = Field(min_length=1)
    commit_sha: Optional[StrictStr] = None
    quality_workflow_path: Optional[StrictStr] = None

    @field_validator("branch")
    @classmethod
    def validate_branch(cls, value: str) -> str:
        if not value.strip() or value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("GitHub branch must be a nonempty ref without surrounding or embedded whitespace.")
        return value

    @field_validator("commit_sha")
    @classmethod
    def validate_commit_sha(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not _FULL_SHA_RE.fullmatch(value):
            raise ValueError("Repository commit_sha must be a full 40-character or 64-character hexadecimal SHA.")
        return value.lower()

    @field_validator("quality_workflow_path")
    @classmethod
    def validate_quality_workflow_path(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        prefix = ".github/workflows/"
        relative_path = value[len(prefix):] if value.startswith(prefix) else ""
        parts = relative_path.split("/") if relative_path else []
        if (not relative_path or value.startswith("/") or "\\" in value
                or any(part in {"", ".", ".."} for part in parts)):
            raise ValueError("quality_workflow_path must select a file under .github/workflows/.")
        return value


class CommitResolutionStatus(str, Enum):
    """Per-analysis state of resolving or recording the target commit."""

    pending = "pending"
    resolved = "resolved"
    failed = "failed"


class RepositoryAccessStatus(str, Enum):
    """Per-analysis permission observation; unknown is not treated as allowed."""

    unknown = "unknown"
    allowed = "allowed"
    denied = "denied"


class RepositoryAvailabilityStatus(str, Enum):
    """Per-analysis availability observation, separate from authentication."""

    unknown = "unknown"
    available = "available"
    unavailable = "unavailable"


class RepositoryAnalysisState(BaseModel):
    """Run-scoped repository status without asserting that evidence is trustworthy."""

    model_config = ConfigDict(extra="forbid")

    identity: RepositoryIdentity
    branch: StrictStr = Field(min_length=1)
    target_commit_sha: Optional[StrictStr] = None
    commit_resolution_status: CommitResolutionStatus = CommitResolutionStatus.pending
    access_status: RepositoryAccessStatus = RepositoryAccessStatus.unknown
    availability_status: RepositoryAvailabilityStatus = RepositoryAvailabilityStatus.unknown

    @field_validator("branch")
    @classmethod
    def validate_branch(cls, value: str) -> str:
        if not value.strip() or value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("Repository analysis branch must be a nonempty ref without whitespace.")
        return value

    @field_validator("target_commit_sha")
    @classmethod
    def validate_target_commit_sha(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not _FULL_SHA_RE.fullmatch(value):
            raise ValueError("target_commit_sha must be a full 40-character or 64-character hexadecimal SHA.")
        return value.lower()

    @model_validator(mode="after")
    def validate_resolution_state(self) -> "RepositoryAnalysisState":
        if self.commit_resolution_status == CommitResolutionStatus.resolved and self.target_commit_sha is None:
            raise ValueError("A resolved repository commit requires target_commit_sha.")
        return self


class RepositoryProvenance(BaseModel):
    """Optional recorded origin metadata; it is not a validation certificate."""

    repository_identity: Optional[RepositoryIdentity] = None
    commit_sha: Optional[StrictStr] = None
    source: Optional[StrictStr] = None
    workflow_path: Optional[StrictStr] = None
    run_id: Optional[StrictStr] = None
    run_attempt: Optional[int] = Field(default=None, ge=1)

    @field_validator("commit_sha")
    @classmethod
    def validate_commit_sha(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not _FULL_SHA_RE.fullmatch(value):
            raise ValueError("Provenance commit_sha must be a full 40-character or 64-character hexadecimal SHA.")
        return value.lower()


__all__ = [
    "CommitResolutionStatus",
    "RepositoryAccessStatus",
    "RepositoryAnalysisState",
    "RepositoryAvailabilityStatus",
    "RepositoryConfiguration",
    "RepositoryIdentity",
    "RepositoryProvenance",
]
