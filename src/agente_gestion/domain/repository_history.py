"""Validated, optional query contracts for persisted repository history."""

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator

from agente_gestion.domain.code import CIRunResult, CiMetricsArtifactMetadata, CodeDiffSnippet
from agente_gestion.domain.evidence import EvidenceItem
from agente_gestion.domain.report import RepositoryReportResult
from agente_gestion.domain.repositories import RepositoryIdentity
from agente_gestion.domain.snapshot import RepositorySnapshotResult


_FULL_SHA_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
_POSITIVE_ID_RE = re.compile(r"^[1-9][0-9]{0,19}$")


def _same_repository_identity(left: RepositoryIdentity, right: RepositoryIdentity) -> bool:
    return (
        left.id == right.id
        and left.owner.casefold() == right.owner.casefold()
        and left.repo.casefold() == right.repo.casefold()
    )


class RepositoryArtifactIdentity(BaseModel):
    """Complete source identity for one GitHub Actions artifact observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: StrictStr = Field(min_length=1, max_length=256)
    repository_identity: RepositoryIdentity
    commit_sha: StrictStr
    ci_run_id: StrictStr
    actual_attempt: StrictInt = Field(ge=1)
    artifact_id: StrictStr
    artifact_name: StrictStr

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("project_id must be a nonempty identifier without surrounding whitespace.")
        return value

    @field_validator("commit_sha")
    @classmethod
    def validate_commit_sha(cls, value: str) -> str:
        if not _FULL_SHA_RE.fullmatch(value):
            raise ValueError("Artifact commit_sha must be a full 40-character or 64-character SHA.")
        return value.lower()

    @field_validator("ci_run_id", "artifact_id")
    @classmethod
    def validate_numeric_id(cls, value: str) -> str:
        if not _POSITIVE_ID_RE.fullmatch(value) or int(value) > (2**63 - 1):
            raise ValueError("Actions run and artifact IDs must be positive canonical decimal identifiers.")
        return value

    @field_validator("artifact_name")
    @classmethod
    def validate_artifact_name(cls, value: str) -> str:
        if not value or value != value.strip() or len(value) > 128:
            raise ValueError("Artifact name must be a nonempty canonical name of at most 128 characters.")
        return value

    @model_validator(mode="after")
    def validate_artifact_binding(self) -> "RepositoryArtifactIdentity":
        expected = f"ci-quality-{self.commit_sha}-{self.ci_run_id}-{self.actual_attempt}"
        if self.artifact_name != expected:
            raise ValueError("Artifact name does not match the repository, commit, run and actual attempt identity.")
        return self


class RepositoryArtifactObservation(BaseModel):
    """Raw source-side validation only; report eligibility is stored separately."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: RepositoryArtifactIdentity
    source_metrics_artifact: Optional[CiMetricsArtifactMetadata] = None


class RepositoryHistoryFilter(BaseModel):
    """AND-combined filters for the optional repository history query."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: Optional[StrictStr] = None
    repository_id: Optional[StrictStr] = None
    repository_owner: Optional[StrictStr] = None
    repository_name: Optional[StrictStr] = None
    commit_sha: Optional[StrictStr] = None
    ci_run_id: Optional[StrictStr] = None
    attempt: Optional[StrictInt] = Field(default=None, ge=1)

    @field_validator("project_id", "repository_id", "repository_owner", "repository_name", "ci_run_id")
    @classmethod
    def validate_nonempty_filter(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and (not value.strip() or value != value.strip()):
            raise ValueError("History filters must be nonempty and have no surrounding whitespace.")
        return value

    @field_validator("commit_sha")
    @classmethod
    def validate_commit_filter(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            if not _FULL_SHA_RE.fullmatch(value):
                raise ValueError("commit_sha filter must be a full 40-character or 64-character SHA.")
            return value.lower()
        return value

    @model_validator(mode="after")
    def validate_filter_shape(self) -> "RepositoryHistoryFilter":
        if (self.repository_owner is None) != (self.repository_name is None):
            raise ValueError("Repository owner and name filters must be provided together.")
        if not any(value is not None for value in self.model_dump().values()):
            raise ValueError("At least one repository history filter is required.")
        return self


class RepositoryHistoryRecord(BaseModel):
    """One immutable capture/repository result, optionally tied to its exact run/report."""

    model_config = ConfigDict(extra="forbid")

    project_id: StrictStr
    capture_id: StrictStr
    snapshot_id: StrictStr
    source_scope: Literal["repository", "legacy", "unknown"]
    source_item_key: Optional[StrictStr] = None
    repository_identity: Optional[RepositoryIdentity] = None
    target_commit_sha: Optional[StrictStr] = None
    repository_result: Optional[RepositorySnapshotResult] = None
    legacy_code_diffs: list[CodeDiffSnippet] = Field(default_factory=list)
    legacy_ci_runs: list[CIRunResult] = Field(default_factory=list)
    analysis_run_id: Optional[StrictStr] = None
    report_id: Optional[StrictStr] = None
    report_result: Optional[RepositoryReportResult] = None
    artifact_observations: list[RepositoryArtifactObservation] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)

    @field_validator("target_commit_sha")
    @classmethod
    def validate_target_sha(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            if not _FULL_SHA_RE.fullmatch(value):
                raise ValueError("target_commit_sha must be a full 40-character or 64-character SHA.")
            return value.lower()
        return value

    @model_validator(mode="after")
    def validate_record_scope(self) -> "RepositoryHistoryRecord":
        if self.repository_result is not None:
            state = self.repository_result.repository_state
            if self.source_scope != "repository":
                raise ValueError("Scoped repository results require repository source scope.")
            if self.repository_identity != state.identity or self.target_commit_sha != state.target_commit_sha:
                raise ValueError("History record identity or fixed SHA does not match its repository result.")
            if self.legacy_code_diffs or self.legacy_ci_runs:
                raise ValueError("Scoped history cannot mix legacy top-level source items.")
        elif self.source_scope == "repository":
            raise ValueError("Repository source scope requires a stored repository result.")

        if self.report_result is not None:
            if self.repository_identity is None:
                raise ValueError("A report result cannot be attached to unknown repository identity.")
            report_state = self.report_result.repository_state
            if not _same_repository_identity(report_state.identity, self.repository_identity):
                raise ValueError("Report result repository identity does not match the history record.")
            if self.target_commit_sha is not None and report_state.target_commit_sha != self.target_commit_sha:
                raise ValueError("Report result target SHA does not match the history record.")

        for observation in self.artifact_observations:
            if observation.identity.project_id != self.project_id:
                raise ValueError("Artifact observation project does not match the history record.")
            if not _same_repository_identity(
                observation.identity.repository_identity, self.repository_identity,
            ):
                raise ValueError("Artifact observation repository does not match the history record.")
            if (self.target_commit_sha is not None
                    and observation.identity.commit_sha != self.target_commit_sha):
                raise ValueError("Artifact observation commit does not match the fixed repository SHA.")
        return self


__all__ = [
    "RepositoryArtifactIdentity",
    "RepositoryArtifactObservation",
    "RepositoryHistoryFilter",
    "RepositoryHistoryRecord",
]
