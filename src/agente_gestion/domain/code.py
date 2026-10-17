"""Code diff snippets and CI run entities with exact commit matching."""

import ast
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from agente_gestion.domain.ci_metrics import (
    CiArtifactOutcome,
    CiArtifactReceipt,
    CiMetricsDocument,
    CiWorkflowStatus,
)
from agente_gestion.domain.repositories import RepositoryIdentity, RepositoryProvenance


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def validate_source_syntax(file_path: str, content: str) -> Literal["valid", "invalid", "unsupported"]:
    """Validate supported source syntax without executing project code."""
    suffix = file_path.rsplit(".", 1)[-1].lower() if "." in file_path.rsplit("/", 1)[-1] else ""
    if suffix in {"py", "pyw"}:
        try:
            ast.parse(content, filename=file_path)
        except (SyntaxError, ValueError):
            return "invalid"
        return "valid"
    if suffix == "json":
        try:
            json.loads(content, parse_constant=_reject_json_constant)
        except ValueError:
            return "invalid"
        return "valid"
    return "unsupported"


class CodeDiffSnippet(BaseModel):
    """Scoped snippet of code changes from a commit or PR."""
    repository: str = Field(description="Repository full name or ID")
    pull_request_id: Optional[str] = Field(default=None, description="PR ID if associated")
    commit_sha: str = Field(description="Exact commit SHA where snippet originates")
    file_path: str = Field(description="Path to modified file")
    diff_side: Optional[str] = Field(default="NEW", description="Diff side: NEW or OLD")
    lines: list[int] = Field(description="Line numbers included in the snippet")
    content: str = Field(description="Content lines of the snippet")
    evidence_scope: Literal["full_file", "snippet", "diff", "truncated"] = "snippet"
    is_complete: bool = Field(default=False, description="True only for a verified complete file")
    start_line: Optional[int] = Field(default=None, ge=1)
    end_line: Optional[int] = Field(default=None, ge=1)
    total_lines: Optional[int] = Field(default=None, ge=0)
    truncated: bool = False
    source_sha256: Optional[str] = Field(default=None, description="SHA-256 of the exact complete source bytes")
    source_blob_sha: Optional[str] = Field(default=None, description="Provider blob identifier at the pinned commit")
    repository_identity: Optional[RepositoryIdentity] = None
    provenance: Optional[RepositoryProvenance] = None

    @model_validator(mode="after")
    def validate_coverage(self):
        # Diff line sets may be disjoint: the range is only an envelope, not coverage.
        if any(line < 1 for line in self.lines) or self.lines != sorted(set(self.lines)):
            raise ValueError("Evidence lines must be positive, unique and ordered")
        first = min(self.lines) if self.lines else None
        last = max(self.lines) if self.lines else None
        if self.start_line is not None and self.start_line != first:
            raise ValueError("start_line must match visible lines")
        if self.end_line is not None and self.end_line != last:
            raise ValueError("end_line must match visible lines")
        self.start_line, self.end_line = first, last
        if self.total_lines is not None and last is not None and last > self.total_lines:
            raise ValueError("Visible lines exceed total_lines")
        if self.source_sha256 is not None:
            if (not _SHA256_RE.fullmatch(self.source_sha256)
                    or hashlib.sha256(self.content.encode("utf-8")).hexdigest() != self.source_sha256):
                raise ValueError("source_sha256 must match the exact content")
        if self.source_blob_sha is not None and not _SHA_RE.fullmatch(self.source_blob_sha):
            raise ValueError("source_blob_sha must be a full hexadecimal object identifier")
        if self.evidence_scope == "truncated":
            self.truncated = True
        if self.is_complete or self.evidence_scope == "full_file":
            if (self.evidence_scope != "full_file" or not self.is_complete or self.truncated
                    or self.total_lines is None
                    or self.lines != list(range(1, self.total_lines + 1))
                    or len(self.content.splitlines()) != self.total_lines):
                raise ValueError("Complete file requires untruncated content and every file line")
        if self.repository_identity is not None and "/" in self.repository:
            if self.repository != f"{self.repository_identity.owner}/{self.repository_identity.repo}":
                raise ValueError("Code snippet repository name does not match repository_identity.")
        if self.provenance is not None:
            if (self.provenance.repository_identity is not None and self.repository_identity is not None
                    and self.provenance.repository_identity != self.repository_identity):
                raise ValueError("Code snippet provenance repository does not match repository_identity.")
            if self.provenance.commit_sha is not None and self.provenance.commit_sha != self.commit_sha:
                raise ValueError("Code snippet provenance commit does not match commit_sha.")
        return self

    @model_serializer(mode="wrap")
    def serialize_optional_repository_metadata(self, handler):
        data = handler(self)
        if self.repository_identity is None:
            data.pop("repository_identity", None)
        if self.provenance is None:
            data.pop("provenance", None)
        return data


class CiMetricsArtifactSuite(BaseModel):
    """Public typed view of one producer-reported test suite."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tests: Optional[int] = None
    failed: Optional[int] = None
    passed: Optional[int] = None
    cancelled: Optional[int] = None
    skipped: Optional[int] = None
    todo: Optional[int] = None
    success: Optional[bool] = None


class CiMetricsArtifactValues(BaseModel):
    """Copy-safe typed values from the already schema-validated producer document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository: str
    commit_sha: str
    workflow_run_id: str
    workflow_run_attempt: Optional[str]
    status: CiWorkflowStatus
    total_tests: Optional[int]
    failed_tests: Optional[int]
    coverage_percentage: Optional[int | float]
    coverage_metric: Optional[str]
    coverage_basis: str
    test_suites: Optional[dict[str, Optional[CiMetricsArtifactSuite]]] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    e2e_status: Optional[str] = Field(default=None, exclude_if=lambda value: value is None)
    extensions: dict[str, Any] = Field(default_factory=dict, exclude_if=lambda value: not value)

    @classmethod
    def from_validated_document(cls, document: CiMetricsDocument) -> "CiMetricsArtifactValues":
        suites = None
        if document.test_suites is not None:
            suites = {
                name: None if suite is None else CiMetricsArtifactSuite(
                    tests=suite.tests,
                    failed=suite.failed,
                    passed=suite.passed,
                    cancelled=suite.cancelled,
                    skipped=suite.skipped,
                    todo=suite.todo,
                    success=suite.success,
                )
                for name, suite in document.test_suites.items()
            }
        return cls(
            repository=document.repository,
            commit_sha=document.commit_sha,
            workflow_run_id=document.workflow_run_id,
            workflow_run_attempt=document.workflow_run_attempt,
            status=document.status,
            total_tests=document.total_tests,
            failed_tests=document.failed_tests,
            coverage_percentage=document.coverage_percentage,
            coverage_metric=document.coverage_metric,
            coverage_basis=document.coverage_basis,
            test_suites=suites,
            e2e_status=document.e2e_status,
            extensions=dict(document.extensions),
        )


class CiMetricsArtifactMetadata(BaseModel):
    """Typed artifact validation and producer metrics attached to one Actions run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: CiArtifactOutcome
    certifies_metrics: bool = False
    reason: Optional[str] = None
    artifact_name: Optional[str] = None
    schema_valid: bool = False
    semantics_valid: bool = False
    identity_bound: bool = False
    workflow_status_matches: bool = False
    receipt: Optional[CiArtifactReceipt] = None
    metrics: Optional[CiMetricsArtifactValues] = None

    @model_validator(mode="after")
    def validate_certificate(self) -> "CiMetricsArtifactMetadata":
        if self.certifies_metrics and (
            self.outcome != CiArtifactOutcome.validated
            or not self.schema_valid
            or not self.semantics_valid
            or not self.identity_bound
            or not self.workflow_status_matches
            or self.metrics is None
        ):
            raise ValueError("Certified CI metrics require a validated, identity-bound document.")
        if self.metrics is not None and not self.certifies_metrics:
            raise ValueError("Uncertified artifact observations cannot expose metric values.")
        return self


class CIRunResult(BaseModel):
    """Result of automated CI workflow run for a specific commit."""
    id: str = Field(description="CI workflow run identifier")
    workflow_name: str = Field(description="Name of CI workflow e.g. tests, lint")
    commit_sha: str = Field(description="Exact commit SHA evaluated by this CI run")
    status: str = Field(description="Status e.g. success, failure, neutral, cancelled")
    total_tests: Optional[int] = Field(default=None, description="Total tests run if reported")
    failed_tests: Optional[int] = Field(default=None, description="Failed tests count if reported")
    coverage_percentage: Optional[float] = Field(default=None, description="Reported test coverage percentage")
    timestamp: Optional[datetime] = Field(default=None, description="Completion timestamp")
    repository_identity: Optional[RepositoryIdentity] = None
    workflow_path: Optional[str] = None
    run_attempt: Optional[int] = Field(default=None, ge=1)
    artifact_id: Optional[str] = None
    artifact_name: Optional[str] = None
    metrics_artifact: Optional[CiMetricsArtifactMetadata] = None
    provenance: Optional[RepositoryProvenance] = None

    @model_validator(mode="after")
    def validate_repository_provenance(self) -> "CIRunResult":
        if self.provenance is not None:
            if (self.provenance.repository_identity is not None and self.repository_identity is not None
                    and self.provenance.repository_identity != self.repository_identity):
                raise ValueError("CI provenance repository does not match repository_identity.")
            if self.provenance.commit_sha is not None and self.provenance.commit_sha != self.commit_sha:
                raise ValueError("CI provenance commit does not match commit_sha.")
            if self.provenance.workflow_path is not None and self.workflow_path is not None:
                if self.provenance.workflow_path != self.workflow_path:
                    raise ValueError("CI provenance workflow does not match workflow_path.")
            if self.provenance.run_id is not None and self.provenance.run_id != self.id:
                raise ValueError("CI provenance run does not match id.")
            if self.provenance.run_attempt is not None and self.run_attempt is not None:
                if self.provenance.run_attempt != self.run_attempt:
                    raise ValueError("CI provenance attempt does not match run_attempt.")
        metadata = self.metrics_artifact
        if metadata is not None:
            document = metadata.metrics
            if document is not None:
                if document.commit_sha.lower() != self.commit_sha.lower():
                    raise ValueError("CI artifact metrics commit does not match the run.")
                if document.workflow_run_id != self.id:
                    raise ValueError("CI artifact metrics run does not match the run id.")
                if self.run_attempt is None:
                    raise ValueError("CI artifact metrics require a known run attempt.")
                if (document.workflow_run_attempt is not None
                        and document.workflow_run_attempt != str(self.run_attempt)):
                    raise ValueError("CI artifact metrics attempt does not match the run attempt.")
                if (self.repository_identity is not None
                        and document.repository.casefold()
                        != f"{self.repository_identity.owner}/{self.repository_identity.repo}".casefold()):
                    raise ValueError("CI artifact metrics repository does not match repository_identity.")
                expected_artifact_name = (
                    f"ci-quality-{self.commit_sha.lower()}-{self.id}-{self.run_attempt}"
                )
                if metadata.artifact_name != expected_artifact_name:
                    raise ValueError("CI artifact name does not match the run identity.")
        return self

    @model_serializer(mode="wrap")
    def serialize_optional_repository_metadata(self, handler):
        data = handler(self)
        for field_name in (
            "repository_identity", "workflow_path", "run_attempt", "artifact_id", "artifact_name",
            "metrics_artifact", "provenance",
        ):
            if getattr(self, field_name) is None:
                data.pop(field_name, None)
        return data


def match_ci_for_commit(ci_runs: list[CIRunResult], commit_sha: str) -> Optional[CIRunResult]:
    """
    Returns the CI run result strictly matching the target commit SHA.
    Enforces invariant: a CI run for a different SHA never certifies the current change.
    """
    for run in ci_runs:
        if run.commit_sha == commit_sha:
            return run
    return None
