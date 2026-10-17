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
import time
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
from agente_gestion.domain.code import (
    CIRunResult,
    CiMetricsArtifactMetadata,
    CiMetricsArtifactValues,
)
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
_ARTIFACT_METADATA_PAGE_SIZE = 100
_ARTIFACT_METADATA_MAX_PAGES = 10
_ARTIFACT_METADATA_MAX_ITEMS = _ARTIFACT_METADATA_PAGE_SIZE * _ARTIFACT_METADATA_MAX_PAGES
_ARTIFACT_METADATA_MAX_BYTES = 2 * 1024 * 1024
_ARTIFACT_METADATA_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class ArtifactMetricsObservation:
    """Bounded, typed observation for one selected run or ambiguous workflow selection."""

    run_id: Optional[str]
    target_commit_sha: Optional[str]
    run_attempt: Optional[str]
    workflow_path: Optional[str]
    workflow_status: Optional[str]
    outcome: Optional[CiArtifactOutcome]
    certifies: bool
    reason: Optional[str]
    artifact_name: Optional[str]
    schema_valid: bool
    semantics_valid: bool
    identity_bound: bool
    workflow_status_matches: bool
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


def _select_quality_ci_run(
    runs: Sequence[CIRunResult],
    *,
    target_commit_sha: str,
    quality_workflow_path: Optional[str],
) -> tuple[Optional[CIRunResult], Optional[str]]:
    if not isinstance(target_commit_sha, str) or not target_commit_sha.strip():
        raise ValueError("target_commit_sha must be a pinned SHA string.")
    if quality_workflow_path is not None and (
        not isinstance(quality_workflow_path, str) or not quality_workflow_path
    ):
        raise ValueError("quality_workflow_path must be a nonempty workflow path or None.")

    target = target_commit_sha.strip().lower()
    candidates = [run for run in runs if run.commit_sha.lower() == target]
    if not candidates:
        return None, None

    if quality_workflow_path is None:
        workflow_paths = {run.workflow_path for run in candidates}
        if (None in workflow_paths or len(workflow_paths) != 1
                or any(not isinstance(path, str) or not path.strip() for path in workflow_paths)):
            return None, "ambiguous_quality_workflow"
        quality_workflow_path = next(iter(workflow_paths))

    candidates = [run for run in candidates if run.workflow_path == quality_workflow_path]
    if not candidates:
        return None, None

    # GitHubActionsSourceAdapter already orders runs newest-first by updated_at,
    # then run id. That chronology chooses the run; attempts only break ties for
    # duplicate rows carrying the same run id and can never revive an older run.
    newest_run_id = candidates[0].id
    same_run = [run for run in candidates if run.id == newest_run_id]
    chosen = same_run[0]
    chosen_attempt = chosen.run_attempt if chosen.run_attempt is not None else 0
    for candidate in same_run[1:]:
        candidate_attempt = candidate.run_attempt if candidate.run_attempt is not None else 0
        if candidate_attempt > chosen_attempt:
            chosen = candidate
            chosen_attempt = candidate_attempt
    return chosen, None


def select_quality_ci_run(
    runs: Sequence[CIRunResult],
    *,
    target_commit_sha: str,
    quality_workflow_path: Optional[str] = None,
) -> Optional[CIRunResult]:
    """Select the newest matching workflow run, comparing attempts only within that run.

    The caller-provided list is authoritative newest-first Actions chronology. If
    no workflow selector is configured, exactly one known target workflow path
    must be derivable; ambiguous or missing paths fail closed with no selection.
    """
    selected, _reason = _select_quality_ci_run(
        runs,
        target_commit_sha=target_commit_sha,
        quality_workflow_path=quality_workflow_path,
    )
    return selected


def _bounded_payload(
    observation: ArtifactMetricsObservation,
) -> dict[str, Any]:
    """Project the observation into a bounded, non-secret evidence payload."""
    receipt = observation.receipt
    bounded: dict[str, Any] = {
        "run_id": observation.run_id,
        "target_commit_sha": observation.target_commit_sha,
        "run_attempt": observation.run_attempt,
        "workflow_path": observation.workflow_path,
        "workflow_status": observation.workflow_status,
        "certifies_metrics": observation.certifies,
        "schema_valid": observation.schema_valid,
        "semantics_valid": observation.semantics_valid,
        "identity_bound": observation.identity_bound,
        "workflow_status_matches": observation.workflow_status_matches,
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
        deadline = time.monotonic() + _ARTIFACT_METADATA_TIMEOUT_SECONDS
        collected: list[Mapping[str, Any]] = []
        seen_ids: dict[int, str] = {}
        total_count: Optional[int] = None
        total_bytes = 0

        for page in range(1, _ARTIFACT_METADATA_MAX_PAGES + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, CiArtifactOutcome.timeout, total_count, total_bytes, "artifact_metadata_timeout"
            try:
                response = self._metadata_transport.get(
                    url=url,
                    params={"per_page": _ARTIFACT_METADATA_PAGE_SIZE, "page": page},
                    headers=self._metadata_headers(),
                    timeout_seconds=remaining,
                )
            except SourceError as exc:
                reason = (
                    "artifact_metadata_timeout" if exc.category is SourceErrorCategory.timeout
                    else "artifact_metadata_incomplete"
                    if exc.category is SourceErrorCategory.limit_exceeded
                    else None
                )
                return None, _outcome_from_category(exc.category), total_count, total_bytes, reason
            except Exception:
                return None, CiArtifactOutcome.transport_error, total_count, total_bytes, None

            if time.monotonic() >= deadline:
                return None, CiArtifactOutcome.timeout, total_count, total_bytes, "artifact_metadata_timeout"
            status_code = getattr(response, "status_code", None)
            body = getattr(response, "body", None)
            bytes_received = getattr(response, "bytes_received", 0)
            if (status_code != 200 or type(bytes_received) is not int or bytes_received < 0
                    or not isinstance(body, Mapping)):
                return None, CiArtifactOutcome.invalid_metadata, total_count, total_bytes, None
            total_bytes += bytes_received
            if total_bytes > _ARTIFACT_METADATA_MAX_BYTES:
                return (None, CiArtifactOutcome.limit_exceeded, total_count, total_bytes,
                        "artifact_metadata_incomplete")

            raw_rows = body.get("artifacts")
            raw_total = body.get("total_count")
            if not isinstance(raw_rows, list) or type(raw_total) is not int or raw_total < 0:
                return None, CiArtifactOutcome.invalid_metadata, total_count, total_bytes, None
            if total_count is None:
                total_count = raw_total
                required_pages = max(1, (total_count + _ARTIFACT_METADATA_PAGE_SIZE - 1)
                                     // _ARTIFACT_METADATA_PAGE_SIZE)
                if (total_count > _ARTIFACT_METADATA_MAX_ITEMS
                        or required_pages > _ARTIFACT_METADATA_MAX_PAGES):
                    return (None, CiArtifactOutcome.limit_exceeded, total_count, total_bytes,
                            "artifact_metadata_incomplete")
            elif raw_total != total_count:
                return (None, CiArtifactOutcome.invalid_metadata, total_count, total_bytes,
                        "artifact_metadata_incomplete")

            expected_rows = min(
                _ARTIFACT_METADATA_PAGE_SIZE,
                max(0, total_count - (page - 1) * _ARTIFACT_METADATA_PAGE_SIZE),
            )
            if len(raw_rows) != expected_rows:
                return (None, CiArtifactOutcome.invalid_metadata, total_count, total_bytes,
                        "artifact_metadata_incomplete")
            for row in raw_rows:
                if (not isinstance(row, Mapping) or not isinstance(row.get("name"), str)
                        or not row["name"] or len(row["name"]) > 128):
                    return None, CiArtifactOutcome.invalid_metadata, total_count, total_bytes, None
                artifact_id = row.get("id")
                if type(artifact_id) is not int or artifact_id <= 0:
                    return None, CiArtifactOutcome.invalid_metadata, total_count, total_bytes, None
                previous_name = seen_ids.get(artifact_id)
                if previous_name is not None:
                    if previous_name == expected_name or row["name"] == expected_name:
                        return (None, CiArtifactOutcome.identity_mismatch, total_count, total_bytes,
                                "ambiguous_artifact_rows")
                    return (None, CiArtifactOutcome.invalid_metadata, total_count, total_bytes,
                            "duplicate_artifact_metadata")
                seen_ids[artifact_id] = row["name"]
                collected.append(row)

            if page >= required_pages:
                if len(collected) != total_count:
                    return (None, CiArtifactOutcome.invalid_metadata, total_count, total_bytes,
                            "artifact_metadata_incomplete")
                return collected, None, total_count, total_bytes, None

        return (None, CiArtifactOutcome.limit_exceeded, total_count, total_bytes,
                "artifact_metadata_incomplete")

    def _observe_run(
        self,
        run: CIRunResult,
    ) -> ArtifactMetricsObservation:
        workflow_status = _FINAL_STATUS_TO_DOCUMENT_STATUS.get(_normalise_status(run.status))
        base = ArtifactMetricsObservation(
            run_id=run.id,
            target_commit_sha=run.commit_sha.lower(),
            run_attempt=str(run.run_attempt) if run.run_attempt is not None else None,
            workflow_path=run.workflow_path,
            workflow_status=run.status,
            outcome=None,
            certifies=False,
            reason=None,
            artifact_name=None,
            schema_valid=False,
            semantics_valid=False,
            identity_bound=False,
            workflow_status_matches=False,
            metrics=None,
            receipt=None,
        )
        if workflow_status is None:
            return dataclass_replace(
                base, outcome=CiArtifactOutcome.invalid_context, reason="workflow_not_final",
            )
        if run.run_attempt is None:
            return dataclass_replace(
                base, outcome=CiArtifactOutcome.invalid_context, reason="attempt_unknown",
            )
        expected_name = f"ci-quality-{run.commit_sha.lower()}-{run.id}-{run.run_attempt}"
        url = self._artifact_list_url(run.id)
        raw_rows, category_outcome, _raw_total, _bytes, query_reason = self._query_artifact_metadata(
            url, expected_name,
        )
        if category_outcome is not None:
            return dataclass_replace(
                base, outcome=category_outcome, reason=query_reason, artifact_name=expected_name,
            )
        matching_rows = [
            row for row in raw_rows
            if isinstance(row, Mapping) and row.get("name") == expected_name
        ]
        if len(matching_rows) == 0:
            return dataclass_replace(
                base,
                outcome=CiArtifactOutcome.artifact_missing,
                reason="artifact_missing",
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
        certifies = bool(result.certifies_metrics)
        metrics = result.metrics if certifies else None
        return dataclass_replace(
            base,
            outcome=result.outcome,
            certifies=certifies,
            reason=None if certifies else result.outcome.value,
            artifact_name=expected_name,
            schema_valid=result.schema_valid,
            semantics_valid=result.semantics_valid,
            identity_bound=result.identity_bound,
            workflow_status_matches=result.workflow_status_matches,
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
        selected_index = None
        if target is not None:
            selected, selection_reason = _select_quality_ci_run(
                runs,
                target_commit_sha=target,
                quality_workflow_path=self._quality_workflow_path,
            )
            if selected is not None:
                selected_index = next(index for index, run in enumerate(runs) if run is selected)
                observation = self._observe_run(selected)
                metadata = CiMetricsArtifactMetadata(
                    outcome=observation.outcome or CiArtifactOutcome.invalid_context,
                    certifies_metrics=observation.certifies,
                    reason=observation.reason,
                    artifact_name=observation.artifact_name,
                    schema_valid=observation.schema_valid,
                    semantics_valid=observation.semantics_valid,
                    identity_bound=observation.identity_bound,
                    workflow_status_matches=observation.workflow_status_matches,
                    receipt=observation.receipt,
                    metrics=(CiMetricsArtifactValues.from_validated_document(observation.metrics)
                             if observation.metrics is not None else None),
                )
                updates: dict[str, Any] = {"metrics_artifact": metadata}
                if metadata.certifies_metrics and metadata.metrics is not None:
                    updates.update({
                        "total_tests": metadata.metrics.total_tests,
                        "failed_tests": metadata.metrics.failed_tests,
                        "coverage_percentage": metadata.metrics.coverage_percentage,
                    })
                selected = selected.model_copy(update=updates)
                self._remember_observation(project_id, commit_sha, observation)
            elif selection_reason is not None:
                self._remember_observation(project_id, commit_sha, ArtifactMetricsObservation(
                    run_id=None,
                    target_commit_sha=target.lower(),
                    run_attempt=None,
                    workflow_path=None,
                    workflow_status=None,
                    outcome=CiArtifactOutcome.invalid_context,
                    certifies=False,
                    reason=selection_reason,
                    artifact_name=None,
                    schema_valid=False,
                    semantics_valid=False,
                    identity_bound=False,
                    workflow_status_matches=False,
                    metrics=None,
                    receipt=None,
                ))
        if selected is None or selected_index is None:
            return runs
        return [selected if index == selected_index else run for index, run in enumerate(runs)]

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
        evidence_id = (
            f"github_actions_artifact_{observation.run_id}"
            if observation.run_id is not None else "github_actions_artifact_selection"
        )
        provenance = EvidenceProvenance(
            id=evidence_id,
            source=EvidenceSource.github_actions,
            project_id=project_id,
            repository=self._repository_full_name,
            commit_sha=(observation.metrics.commit_sha if observation.metrics is not None
                        else observation.target_commit_sha),
            external_id=observation.run_id,
            timestamp=datetime.now(timezone.utc),
            payload=bounded,
        )
        subject = f"run {observation.run_id}" if observation.run_id is not None else "workflow selection"
        summary = (
            f"CI artifact metrics observation for {subject}: "
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
