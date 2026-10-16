"""Deterministic quality-workflow selection and bounded ci-metrics.v1 artifact integration.

T4 extension of the GitHub Actions source: the run list stays authoritative for
workflow state, and a selected run may additionally bind its published
``ci-quality-<SHA>-<run_id>-<attempt>`` artifact through the T3 validator.
Metrics are attached only when the validator certifies them; otherwise the
typed observation explains the missing evidence without inventing values.
"""

from collections import OrderedDict
from dataclasses import dataclass, replace as dataclass_replace
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from agente_gestion.adapters.sources.artifacts import CiMetricsArtifactValidator
from agente_gestion.adapters.sources.binary_transport import API_ORIGIN
from agente_gestion.domain.ci_metrics import (
    CiArtifactOutcome,
    CiArtifactReceipt,
    CiMetricsDocument,
    CiMetricsValidationContext,
    CiWorkflowStatus,
)
from agente_gestion.domain.code import CIRunResult
from agente_gestion.domain.evidence import EvidenceItem, EvidenceProvenance, EvidenceSource
from agente_gestion.domain.repositories import RepositoryIdentity
from agente_gestion.ports.source_errors import (
    SourceError,
    SourceErrorCategory,
    SourceProvider,
)
from agente_gestion.ports.sources import CISourcePort

_FINAL_STATUS_TO_DOCUMENT_STATUS = {
    "success": CiWorkflowStatus.success,
    "failure": CiWorkflowStatus.failure,
    "cancelled": CiWorkflowStatus.cancelled,
}

_OBSERVATION_CACHE_LIMIT = 8


@dataclass(frozen=True)
class ArtifactMetricsObservation:
    """Bounded, typed observation for one selected Actions run artifact."""

    run_id: str
    run_attempt: Optional[str]
    workflow_path: Optional[str]
    workflow_status: Optional[str]
    outcome: Optional[CiArtifactOutcome]
    certifies: bool
    reason: Optional[str]
    artifact_name: Optional[str]
    metrics: Optional[CiMetricsDocument]
    receipt: Optional[CiArtifactReceipt]


def _normalise_status(status: Optional[str]) -> Optional[str]:
    if not isinstance(status, str):
        return None
    value = status.strip().lower()
    return value or None


def _validate_repository_full_name(repository_full_name: str) -> tuple[str, str]:
    if (not isinstance(repository_full_name, str) or repository_full_name.count("/") != 1):
        raise ValueError("repository_full_name must be 'owner/repo'.")
    owner, repo = repository_full_name.split("/", 1)
    if (not owner or not repo or any(character.isspace() for character in repository_full_name)
            or any(character in owner for character in "./:")
            or repo in {".", ".."}):
        raise ValueError("repository_full_name must be a plain owner/repo pair.")
    return owner, repo


def select_quality_ci_run(
    runs: Sequence[CIRunResult],
    *,
    target_commit_sha: str,
    quality_workflow_path: Optional[str] = None,
) -> Optional[CIRunResult]:
    """Deterministically select the quality run to bind_ci evidence to.

    Candidates are runs whose commit matches the pinned target SHA (case-folded)
    filtered by the optional quality workflow path. The selection prefers the
    highest run attempt published for the chosen workflow and keeps the newest
    entry on ties. Failures, cancellations and pending states are never swapped
    for older successful runs: the newest matching attempt always decides.
    """
    if not isinstance(target_commit_sha, str) or not target_commit_sha.strip():
        raise ValueError("target_commit_sha must be a pinned SHA string.")
    target = target_commit_sha.strip().lower()
    candidates = [run for run in runs if run.commit_sha.lower() == target]
    if quality_workflow_path is not None:
        if not isinstance(quality_workflow_path, str) or not quality_workflow_path:
            raise ValueError("quality_workflow_path must be a nonempty workflow path or None.")
        candidates = [run for run in candidates if run.workflow_path == quality_workflow_path]
    if not candidates:
        return None

    def _attempt_key(run: CIRunResult) -> int:
        return run.run_attempt if run.run_attempt is not None else 0

    chosen = candidates[0]
    chosen_attempt = _attempt_key(chosen)
    for candidate in candidates[1:]:
        candidate_attempt = _attempt_key(candidate)
        if candidate_attempt > chosen_attempt:
            chosen = candidate
            chosen_attempt = candidate_attempt
    return chosen


def _bounded_payload(
    observation: ArtifactMetricsObservation,
) -> dict[str, Any]:
    """Project the observation into a bounded, non-secret evidence payload."""
    receipt = observation.receipt
    bounded: dict[str, Any] = {
        "run_id": observation.run_id,
        "run_attempt": observation.run_attempt,
        "workflow_path": observation.workflow_path,
        "workflow_status": observation.workflow_status,
        "certifies_metrics": observation.certifies,
        "artifact_name": observation.artifact_name,
        "reason": observation.reason,
        "outcome": observation.outcome.value if observation.outcome is not None else None,
        "receipt": None if receipt is None else {
            "http_calls": receipt.http_calls,
            "redirects": receipt.redirects,
            "compressed_bytes": receipt.compressed_bytes,
            "expanded_bytes": receipt.expanded_bytes,
            "archive_entries": receipt.archive_entries,
            "hop_statuses": list(receipt.hop_statuses),
        },
    }
    return bounded


def _outcome_from_category(category: SourceErrorCategory) -> CiArtifactOutcome:
    return {
        SourceErrorCategory.authentication: CiArtifactOutcome.authentication_required,
        SourceErrorCategory.permission_denied: CiArtifactOutcome.permission_denied,
        SourceErrorCategory.rate_limit: CiArtifactOutcome.rate_limited,
        SourceErrorCategory.not_found: CiArtifactOutcome.artifact_missing,
        SourceErrorCategory.limit_exceeded: CiArtifactOutcome.limit_exceeded,
        SourceErrorCategory.timeout: CiArtifactOutcome.timeout,
        SourceErrorCategory.malformed_payload: CiArtifactOutcome.invalid_metadata,
        SourceErrorCategory.network: CiArtifactOutcome.transport_error,
        SourceErrorCategory.configuration: CiArtifactOutcome.invalid_context,
    }.get(category, CiArtifactOutcome.transport_error)


class ArtifactEnabledCISource(CISourcePort):
    """Read-only CI source that enriches Actions runs with validated artifact metrics."""

    def __init__(
        self,
        runs_source: CISourcePort,
        artifact_validator: CiMetricsArtifactValidator,
        metadata_transport,
        *,
        repository_full_name: str,
        token: Optional[str] = None,
        quality_workflow_path: Optional[str] = None,
        identity: Optional[RepositoryIdentity] = None,
    ):
        if isinstance(runs_source, ArtifactEnabledCISource):
            raise ValueError("ArtifactEnabledCISource must not wrap another artifact source.")
        self._runs_source = runs_source
        self._validator = artifact_validator
        self._metadata_transport = metadata_transport
        owner, repo = _validate_repository_full_name(repository_full_name)
        self._owner = owner
        self._repo = repo
        self._repository_full_name = f"{owner}/{repo}"
        self._token = token if token is None else str(token)
        self._quality_workflow_path = quality_workflow_path
        self._identity = identity
        self._observations: "OrderedDict[tuple[str, Optional[str]], ArtifactMetricsObservation]" = OrderedDict()

    @property
    def quality_workflow_path(self):
        return self._quality_workflow_path

    def invalidate_cache(self, project_id: Optional[str] = None) -> None:
        invalidate = getattr(self._runs_source, "invalidate_cache", None)
        if callable(invalidate):
            invalidate(project_id)
        if project_id is None:
            self._observations.clear()
        else:
            for key in [key for key in self._observations if key[0] == project_id]:
                self._observations.pop(key, None)

    def _artifact_list_url(self, run_id: str) -> str:
        return f"{API_ORIGIN}/repos/{self._owner}/{self._repo}/actions/runs/{run_id}/artifacts"

    def _metadata_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _query_artifact_metadata(self, url: str, expected_name: str):
        try:
            response = self._metadata_transport.get(
                url=url,
                params={"name": expected_name},
                headers=self._metadata_headers(),
            )
        except SourceError as exc:
            return None, _outcome_from_category(exc.category), None, None
        except Exception:
            return None, CiArtifactOutcome.transport_error, None, None
        body = getattr(response, "body", None)
        bytes_received = getattr(response, "bytes_received", 0)
        if bytes_received is None or not isinstance(bytes_received, int) or bytes_received < 0:
            return None, CiArtifactOutcome.invalid_metadata, None, None
        if not isinstance(body, Mapping):
            return None, CiArtifactOutcome.invalid_metadata, None, bytes_received
        raw_rows = body.get("artifacts")
        raw_total = body.get("total_count")
        if not isinstance(raw_rows, list) or type(raw_total) is not int:
            return None, CiArtifactOutcome.invalid_metadata, None, bytes_received
        return raw_rows, None, raw_total, bytes_received

    def _observe_run(
        self,
        run: CIRunResult,
    ) -> ArtifactMetricsObservation:
        workflow_status = _FINAL_STATUS_TO_DOCUMENT_STATUS.get(_normalise_status(run.status))
        base = ArtifactMetricsObservation(
            run_id=run.id,
            run_attempt=str(run.run_attempt) if run.run_attempt is not None else None,
            workflow_path=run.workflow_path,
            workflow_status=run.status,
            outcome=None,
            certifies=False,
            reason=None,
            artifact_name=None,
            metrics=None,
            receipt=None,
        )
        if workflow_status is None:
            return dataclass_replace(base, reason="workflow_not_final")
        if run.run_attempt is None:
            return dataclass_replace(base, reason="attempt_unknown")
        expected_name = f"ci-quality-{run.commit_sha.lower()}-{run.id}-{run.run_attempt}"
        url = self._artifact_list_url(run.id)
        raw_rows, category_outcome, _raw_total, _bytes = self._query_artifact_metadata(
            url, expected_name,
        )
        if category_outcome is not None:
            return dataclass_replace(base, outcome=category_outcome, artifact_name=expected_name)
        matching_rows = [
            row for row in raw_rows
            if isinstance(row, Mapping) and row.get("name") == expected_name
        ]
        if len(matching_rows) == 0:
            return dataclass_replace(
                base,
                outcome=CiArtifactOutcome.artifact_missing,
                artifact_name=expected_name,
            )
        if len(matching_rows) > 1:
            return dataclass_replace(
                base,
                outcome=CiArtifactOutcome.identity_mismatch,
                artifact_name=expected_name,
                reason="ambiguous_artifact_rows",
            )
        metadata_row = matching_rows[0]
        context = CiMetricsValidationContext(
            repository=self._repository_full_name,
            target_source_sha=run.commit_sha.lower(),
            workflow_run_id=run.id,
            workflow_run_attempt=str(run.run_attempt),
            workflow_status=workflow_status,
        )
        result = self._validator.validate(context, metadata_row, token=self._token)
        metrics = result.metrics if result.outcome is CiArtifactOutcome.validated else None
        certifies = bool(result.certifies_metrics)
        return dataclass_replace(
            base,
            outcome=result.outcome,
            certifies=certifies,
            reason=None if result.outcome is CiArtifactOutcome.validated else result.outcome.value,
            artifact_name=expected_name,
            metrics=metrics,
            receipt=result.receipt,
        )

    def get_ci_results(self, project_id: str, commit_sha: Optional[str] = None) -> list[CIRunResult]:
        if not isinstance(project_id, str) or not project_id.strip():
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
        runs = self._runs_source.get_ci_results(project_id, commit_sha=commit_sha)
        target = commit_sha
        if not isinstance(target, str) or not target.strip():
            resolved = getattr(self._runs_source, "target_commit_sha", None)
            target = resolved if isinstance(resolved, str) and resolved.strip() else None
        selected = None
        if target is not None:
            selected = select_quality_ci_run(
                runs,
                target_commit_sha=target,
                quality_workflow_path=self._quality_workflow_path,
            )
            if selected is not None:
                observation = self._observe_run(selected)
                if observation.certifies and observation.metrics is not None:
                    selected = selected.model_copy(update={
                        "total_tests": observation.metrics.total_tests,
                        "failed_tests": observation.metrics.failed_tests,
                        "coverage_percentage": observation.metrics.coverage_percentage,
                    })
                self._remember_observation(project_id, commit_sha, observation)
        return [self._maybe_replace(run, selected) for run in runs]

    @staticmethod
    def _maybe_replace(run: CIRunResult, selected: Optional[CIRunResult]) -> CIRunResult:
        if selected is not None and run.id == selected.id:
            return selected
        return run

    def _remember_observation(
        self,
        project_id: str,
        commit_sha: Optional[str],
        observation: ArtifactMetricsObservation,
    ) -> None:
        key = (project_id, commit_sha)
        self._observations.pop(key, None)
        self._observations[key] = observation
        while len(self._observations) > _OBSERVATION_CACHE_LIMIT:
            self._observations.popitem(last=False)

    def get_evidence(self, project_id: str, commit_sha: Optional[str] = None) -> list:
        evidence = self._runs_source.get_evidence(project_id, commit_sha=commit_sha)
        observation = self._observations.get((project_id, commit_sha))
        if observation is None:
            return evidence
        bounded = _bounded_payload(observation)
        provenance = EvidenceProvenance(
            id=f"github_actions_artifact_{observation.run_id}",
            source=EvidenceSource.github_actions,
            project_id=project_id,
            repository=self._repository_full_name,
            commit_sha=observation.metrics.commit_sha if observation.metrics is not None else None,
            external_id=observation.run_id,
            timestamp=datetime.now(timezone.utc),
            payload=bounded,
        )
        summary = (
            f"CI artifact metrics observation run {observation.run_id}: "
            f"outcome={bounded['outcome']}, certifies_metrics={bounded['certifies_metrics']}"
        )
        evidence.append(EvidenceItem(
            provenance=provenance,
            category="ci_artifact_metrics",
            summary=summary,
        ))
        return evidence


__all__ = [
    "ArtifactEnabledCISource",
    "ArtifactMetricsObservation",
    "select_quality_ci_run",
]
