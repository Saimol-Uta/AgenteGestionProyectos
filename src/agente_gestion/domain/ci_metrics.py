"""Typed, identity-bound CI metrics artifact outcomes."""

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional


class CiWorkflowStatus(str, Enum):
    """Final Actions conclusion represented by the v1 metrics document."""

    success = "success"
    failure = "failure"
    cancelled = "cancelled"


class CiArtifactOutcome(str, Enum):
    """Non-secret, machine-readable result of an artifact validation attempt."""

    validated = "validated"
    artifact_missing = "artifact_missing"
    artifact_expired = "artifact_expired"
    permission_denied = "permission_denied"
    authentication_required = "authentication_required"
    rate_limited = "rate_limited"
    transport_error = "transport_error"
    redirect_rejected = "redirect_rejected"
    redirect_loop = "redirect_loop"
    redirect_limit_exceeded = "redirect_limit_exceeded"
    limit_exceeded = "limit_exceeded"
    timeout = "timeout"
    invalid_context = "invalid_context"
    invalid_metadata = "invalid_metadata"
    source_sha_unproven = "source_sha_unproven"
    identity_mismatch = "identity_mismatch"
    workflow_status_mismatch = "workflow_status_mismatch"
    invalid_archive = "invalid_archive"
    invalid_json = "invalid_json"
    invalid_schema = "invalid_schema"
    invalid_semantics = "invalid_semantics"


@dataclass(frozen=True)
class CiMetricsValidationContext:
    """Authoritative identity and final workflow conclusion selected by the caller."""

    repository: str
    target_source_sha: str
    workflow_run_id: str
    workflow_run_attempt: str
    workflow_status: CiWorkflowStatus | str


@dataclass(frozen=True)
class CiArtifactMetadata:
    """Minimal GitHub artifact metadata; arbitrary download URLs are intentionally absent."""

    artifact_id: int
    name: str
    expired: bool
    workflow_run_id: str
    workflow_head_sha: Optional[str]
    workflow_repository: Optional[str] = None
    workflow_run_attempt: Optional[str] = None

    @classmethod
    def from_github_payload(cls, payload: Any) -> "CiArtifactMetadata":
        """Extract only identity-bearing fields from a GitHub artifact API object."""
        if not isinstance(payload, dict):
            raise ValueError("Invalid artifact metadata.")
        artifact_id = payload.get("id")
        name = payload.get("name")
        expired = payload.get("expired")
        workflow = payload.get("workflow_run")
        if (type(artifact_id) is not int or artifact_id <= 0 or artifact_id > (2**63 - 1)
                or not isinstance(name, str) or len(name) > 128
                or type(expired) is not bool or not isinstance(workflow, dict)):
            raise ValueError("Invalid artifact metadata.")
        run_id = workflow.get("id")
        if type(run_id) is int and 0 < run_id <= (2**63 - 1):
            run_id = str(run_id)
        elif not isinstance(run_id, str):
            raise ValueError("Invalid artifact metadata.")
        head_sha = workflow.get("head_sha")
        if head_sha is not None and not isinstance(head_sha, str):
            raise ValueError("Invalid artifact metadata.")
        workflow_attempt = workflow.get("run_attempt")
        if type(workflow_attempt) is int and workflow_attempt > 0:
            workflow_attempt = str(workflow_attempt)
        elif workflow_attempt is not None and not isinstance(workflow_attempt, str):
            raise ValueError("Invalid artifact metadata.")
        head_repository = workflow.get("head_repository")
        repository = None
        if head_repository is not None:
            if not isinstance(head_repository, dict):
                raise ValueError("Invalid artifact metadata.")
            repository = head_repository.get("full_name")
            if repository is not None and not isinstance(repository, str):
                raise ValueError("Invalid artifact metadata.")
        return cls(
            artifact_id=artifact_id,
            name=name,
            expired=expired,
            workflow_run_id=run_id,
            workflow_head_sha=head_sha,
            workflow_repository=repository,
            workflow_run_attempt=workflow_attempt,
        )


@dataclass(frozen=True)
class CiMetricsTestSuite:
    tests: Optional[int]
    failed: Optional[int]
    passed: Optional[int]
    cancelled: Optional[int]
    skipped: Optional[int]
    todo: Optional[int]
    success: Optional[bool]


@dataclass(frozen=True, repr=False)
class CiMetricsDocument:
    """Schema-valid v1 document values; unknown metric values remain ``None``."""

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
    test_suites: Optional[Mapping[str, Optional[CiMetricsTestSuite]]] = None
    e2e_status: Optional[str] = None
    extensions: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), repr=False,
    )


@dataclass(frozen=True)
class CiArtifactReceipt:
    """Observed bounded resource use; contains no URL, headers, token, or body."""

    http_calls: int = 0
    redirects: int = 0
    compressed_bytes: int = 0
    expanded_bytes: int = 0
    archive_entries: int = 0
    hop_statuses: tuple[Optional[int], ...] = ()


@dataclass(frozen=True)
class CiMetricsArtifactResult:
    """Document validity, identity binding, and workflow outcome remain separate."""

    outcome: CiArtifactOutcome
    workflow_status: Optional[CiWorkflowStatus] = None
    schema_valid: bool = False
    semantics_valid: bool = False
    identity_bound: bool = False
    workflow_status_matches: bool = False
    metrics: Optional[CiMetricsDocument] = None
    green_eligible: bool = False
    receipt: CiArtifactReceipt = field(default_factory=CiArtifactReceipt)

    @property
    def document_valid(self) -> bool:
        return self.schema_valid and self.semantics_valid

    @property
    def certifies_metrics(self) -> bool:
        return (
            self.outcome == CiArtifactOutcome.validated
            and self.document_valid
            and self.identity_bound
            and self.workflow_status_matches
            and self.metrics is not None
        )


__all__ = [
    "CiArtifactMetadata",
    "CiArtifactOutcome",
    "CiArtifactReceipt",
    "CiMetricsArtifactResult",
    "CiMetricsDocument",
    "CiMetricsTestSuite",
    "CiMetricsValidationContext",
    "CiWorkflowStatus",
]
