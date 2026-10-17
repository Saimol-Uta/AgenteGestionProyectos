"""Sequential repository-isolated composition over the existing bounded source adapters."""

from datetime import datetime, timezone
import hashlib
import time
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from agente_gestion.adapters.sources.actions import GitHubActionsSourceAdapter
from agente_gestion.adapters.sources.actions_artifacts import ArtifactEnabledCISource
from agente_gestion.adapters.sources.artifacts import CiMetricsArtifactValidator
from agente_gestion.adapters.sources.binary_transport import BoundedBinaryGetTransport
from agente_gestion.adapters.sources.collection_budget import (
    BudgetedHttpGetTransport,
    CollectionBudgetLedger,
)
from agente_gestion.adapters.sources.composite import CompositeSourcesAdapter
from agente_gestion.adapters.sources.github import GitHubCodeSourceAdapter
from agente_gestion.adapters.sources.http_transport import BoundedGetTransport, HttpGetTransportPort
from agente_gestion.application.config import Settings
from agente_gestion.domain.code import CIRunResult, CodeDiffSnippet
from agente_gestion.domain.evidence import EvidenceItem, EvidenceSource
from agente_gestion.domain.repositories import (
    CommitResolutionStatus,
    RepositoryAccessStatus,
    RepositoryAnalysisState,
    RepositoryAvailabilityStatus,
    RepositoryConfiguration,
    RepositoryIdentity,
    RepositoryProvenance,
)
from agente_gestion.domain.source_limits import MIB
from agente_gestion.ports.source_collection import (
    RepositorySourceCollectionResult,
    SourceCollectionPort,
    SourceCollectionResult,
    SourceErrorInfo,
    SourceExecutionRecord,
    SourceExecutionStatus,
)
from agente_gestion.ports.source_artifacts import BinaryGetTransportPort
from agente_gestion.ports.source_errors import SourceError, SourceErrorCategory, SourceProvider
from agente_gestion.ports.sources import CISourcePort, CodeSourcePort, PolicySourcePort, TaskSourcePort
from agente_gestion.domain.snapshot import RepositorySnapshotResult, WeeklySnapshot


class _UnavailableCodeSource(CodeSourcePort):
    def __init__(self, category: SourceErrorCategory):
        self.category = category
        self.last_collection_complete = True
        self.last_resolved_commit_sha = None

    def get_diffs(self, project_id: str, commit_sha: Optional[str] = None) -> list[CodeDiffSnippet]:
        raise SourceError(SourceProvider.github, self.category) from None

    def get_evidence(self, project_id: str, commit_sha: Optional[str] = None) -> list[EvidenceItem]:
        raise SourceError(SourceProvider.github, self.category) from None


class _UnavailableCISource(CISourcePort):
    def __init__(self, category: SourceErrorCategory):
        self.category = category

    def get_ci_results(self, project_id: str, commit_sha: Optional[str] = None) -> list[CIRunResult]:
        raise SourceError(SourceProvider.github_actions, self.category) from None

    def get_evidence(self, project_id: str, commit_sha: Optional[str] = None) -> list[EvidenceItem]:
        raise SourceError(SourceProvider.github_actions, self.category) from None


class _UnavailableTaskSource(TaskSourcePort):
    def __init__(self, category: SourceErrorCategory):
        self.category = category

    def get_tasks(self, project_id: str):
        raise SourceError(SourceProvider.trello, self.category) from None


class _UnavailablePolicySource(PolicySourcePort):
    def __init__(self, category: SourceErrorCategory):
        self.category = category

    def get_policies(self, project_id: str):
        raise SourceError(SourceProvider.local_policy, self.category) from None


class _CachedTaskSource(TaskSourcePort):
    """Read-only project-wide task result reused by every repository composite."""

    def __init__(self, tasks: list, evidence: list[EvidenceItem]):
        self._tasks = [item.model_copy(deep=True) for item in tasks]
        self._evidence = [item.model_copy(deep=True) for item in evidence]

    def get_tasks(self, project_id: str):
        return [item.model_copy(deep=True) for item in self._tasks]

    def get_evidence(self, project_id: str):
        return [item.model_copy(deep=True) for item in self._evidence]


class _CachedPolicySource(PolicySourcePort):
    """Read-only project-wide policy result reused by every repository composite."""

    def __init__(self, policies: list):
        self._policies = [item.model_copy(deep=True) for item in policies]

    def get_policies(self, project_id: str):
        return [item.model_copy(deep=True) for item in self._policies]


class _BudgetedCodeSource(CodeSourcePort):
    """Keep the existing GitHub code adapter while accounting its scoped output."""

    def __init__(self, source: GitHubCodeSourceAdapter, ledger: CollectionBudgetLedger,
                 identity: RepositoryIdentity):
        self._source = source
        self._ledger = ledger
        self._identity = identity
        self._diffs: list[CodeDiffSnippet] = []
        self._token_exhausted = False

    @property
    def last_resolved_commit_sha(self):
        return self._source.last_resolved_commit_sha

    @property
    def last_collection_complete(self):
        return self._source.last_collection_complete

    def get_diffs(self, project_id: str, commit_sha: Optional[str] = None) -> list[CodeDiffSnippet]:
        self._diffs = self._source.get_diffs(project_id, commit_sha=commit_sha)
        return [item.model_copy(deep=True) for item in self._diffs]

    def get_evidence(self, project_id: str, commit_sha: Optional[str] = None) -> list[EvidenceItem]:
        evidence = self._source.get_evidence(project_id, commit_sha=commit_sha)
        changed_paths = {
            item.provenance.file_path
            for item in evidence
            if item.provenance.source == EvidenceSource.github
            and item.category == "diff"
            and item.provenance.file_path is not None
        }
        self._ledger.consume_files(self._identity, len(changed_paths))
        from agente_gestion.agents.context import estimate_tokens_from_data
        estimated = estimate_tokens_from_data({
            "code_diffs": [item.model_dump(mode="json") for item in self._diffs],
            "evidence": [item.model_dump(mode="json") for item in evidence],
        })
        self._token_exhausted = not self._ledger.consume_estimated_tokens(self._identity, estimated)
        return evidence


class _BudgetedCISource(CISourcePort):
    """Keep Actions retrieval scoped to the pinned code SHA and shared budget."""

    def __init__(self, source: CISourcePort, ledger: CollectionBudgetLedger,
                 identity: RepositoryIdentity):
        self._source = source
        self._ledger = ledger
        self._identity = identity
        self._runs: list[CIRunResult] = []

    def get_ci_results(self, project_id: str, commit_sha: Optional[str] = None) -> list[CIRunResult]:
        if self._ledger.token_budget_exhausted(self._identity):
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.limit_exceeded) from None
        self._runs = self._source.get_ci_results(project_id, commit_sha=commit_sha)
        return [item.model_copy(deep=True) for item in self._runs]

    def get_evidence(self, project_id: str, commit_sha: Optional[str] = None) -> list[EvidenceItem]:
        evidence = self._source.get_evidence(project_id, commit_sha=commit_sha)
        from agente_gestion.agents.context import estimate_tokens_from_data
        estimated = estimate_tokens_from_data({
            "ci_runs": [item.model_dump(mode="json") for item in self._runs],
            "evidence": [item.model_dump(mode="json") for item in evidence],
        })
        self._ledger.consume_estimated_tokens(self._identity, estimated)
        return evidence


def _category(exc: Exception, default: SourceErrorCategory = SourceErrorCategory.malformed_payload) -> SourceErrorCategory:
    return exc.category if isinstance(exc, SourceError) else default


def _error_record(
    provider: SourceProvider,
    category: SourceErrorCategory,
    identity: RepositoryIdentity,
    target_sha: Optional[str],
) -> SourceExecutionRecord:
    return SourceExecutionRecord(
        provider=provider,
        status=SourceExecutionStatus.failed,
        duration_seconds=0.0,
        error=SourceErrorInfo(provider=provider, category=category),
        repository_identity=identity,
        target_commit_sha=target_sha,
    )


def _copy_repository_record(
    record: SourceExecutionRecord,
    identity: RepositoryIdentity,
    target_sha: Optional[str],
) -> SourceExecutionRecord:
    if record.repository_identity is not None and record.repository_identity != identity:
        raise SourceError(record.provider, SourceErrorCategory.malformed_payload) from None
    if (record.target_commit_sha is not None and target_sha is not None
            and record.target_commit_sha != target_sha):
        raise SourceError(record.provider, SourceErrorCategory.malformed_payload) from None
    return record.model_copy(update={
        "repository_identity": identity,
        "target_commit_sha": target_sha,
    })


def _decorate_code(
    item: CodeDiffSnippet,
    identity: RepositoryIdentity,
    target_sha: str,
) -> CodeDiffSnippet:
    full_name = f"{identity.owner}/{identity.repo}"
    if item.repository != full_name or item.commit_sha.lower() != target_sha:
        raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
    if item.repository_identity is not None and item.repository_identity != identity:
        raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
    current = item.provenance
    if current is not None and (
        (current.repository_identity is not None and current.repository_identity != identity)
        or (current.commit_sha is not None and current.commit_sha != target_sha)
        or (current.source is not None and current.source != SourceProvider.github.value)
    ):
        raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
    provenance = RepositoryProvenance(
        repository_identity=identity,
        commit_sha=target_sha,
        source=SourceProvider.github.value,
    )
    return item.model_copy(update={"repository_identity": identity, "provenance": provenance})


def _decorate_ci(
    item: CIRunResult,
    identity: RepositoryIdentity,
    target_sha: str,
) -> CIRunResult:
    actual_sha = item.commit_sha.lower()
    if item.repository_identity is not None and item.repository_identity != identity:
        raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None
    current = item.provenance
    if current is not None and (
        (current.repository_identity is not None and current.repository_identity != identity)
        or (current.commit_sha is not None and current.commit_sha != actual_sha)
        or (current.source is not None and current.source != SourceProvider.github_actions.value)
        or (current.run_id is not None and current.run_id != item.id)
        or (current.workflow_path is not None and item.workflow_path is not None
            and current.workflow_path != item.workflow_path)
        or (current.run_attempt is not None and item.run_attempt is not None
            and current.run_attempt != item.run_attempt)
    ):
        raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None
    provenance = RepositoryProvenance(
        repository_identity=identity,
        commit_sha=actual_sha,
        source=SourceProvider.github_actions.value,
        workflow_path=item.workflow_path,
        run_id=item.id,
        run_attempt=item.run_attempt,
        certifies_target=False if actual_sha != target_sha else None,
    )
    return item.model_copy(update={"repository_identity": identity, "provenance": provenance})


def _namespace_evidence(
    item: EvidenceItem,
    identity: RepositoryIdentity,
    target_sha: str,
    expected_source: EvidenceSource,
) -> EvidenceItem:
    full_name = f"{identity.owner}/{identity.repo}"
    current = item.provenance
    provider = (SourceProvider.github_actions if expected_source == EvidenceSource.github_actions
                else SourceProvider.github)
    if (current.source != expected_source or current.repository != full_name
            or current.commit_sha is None):
        raise SourceError(provider, SourceErrorCategory.malformed_payload) from None
    actual_sha = current.commit_sha.lower()
    non_target_ci = (
        expected_source == EvidenceSource.github_actions
        and actual_sha != target_sha
        and current.payload.get("is_target_match") is False
    )
    if actual_sha != target_sha and not non_target_ci:
        raise SourceError(provider, SourceErrorCategory.malformed_payload) from None
    payload = dict(current.payload)
    embedded_identity = payload.get("repository_identity")
    expected_identity = identity.model_dump(mode="json")
    if embedded_identity is not None and embedded_identity != expected_identity:
        raise SourceError(provider, SourceErrorCategory.malformed_payload) from None
    payload["repository_identity"] = expected_identity
    prefix = f"{identity.id}:"
    namespaced_id = current.id if current.id.startswith(prefix) else f"{prefix}{current.id}"
    provenance = current.model_copy(update={
        "id": namespaced_id,
        "repository": full_name,
        "commit_sha": actual_sha,
        "payload": payload,
    })
    return item.model_copy(update={"provenance": provenance})


class MultiRepoSourcesAdapter(SourceCollectionPort):
    """Sequentially collect independent pinned repository results plus project-wide sources."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: Optional[HttpGetTransportPort],
        task_source_factory: Callable[[HttpGetTransportPort], TaskSourcePort],
        policy_source_factory: Callable[[], PolicySourcePort],
        task_source_identity: Optional[str] = None,
        default_timezone: Optional[ZoneInfo] = None,
        clock: Optional[Callable[[], float]] = None,
        binary_transport: Optional[BinaryGetTransportPort] = None,
    ):
        if not settings.github_repositories:
            raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None
        self._settings = settings
        self._repositories = tuple(settings.github_repositories)
        self._transport = transport
        self._binary_transport = binary_transport
        self._task_source_factory = task_source_factory
        self._policy_source_factory = policy_source_factory
        self._task_source_identity = task_source_identity
        self._timezone = default_timezone
        self._clock = clock or time.monotonic

    def _ledger(self) -> CollectionBudgetLedger:
        settings = self._settings
        return CollectionBudgetLedger(
            project_max_http_calls=settings.multirepo_project_max_http_calls,
            repository_max_http_calls=settings.multirepo_repository_max_http_calls,
            project_max_files=settings.multirepo_project_max_files,
            repository_max_files=settings.multirepo_repository_max_files,
            project_max_response_bytes=settings.multirepo_project_max_response_bytes,
            repository_max_response_bytes=settings.multirepo_repository_max_response_bytes,
            project_timeout_seconds=settings.multirepo_project_timeout_seconds,
            repository_timeout_seconds=settings.multirepo_repository_timeout_seconds,
            project_max_estimated_tokens=settings.multirepo_project_max_estimated_tokens,
            repository_max_estimated_tokens=settings.multirepo_repository_max_estimated_tokens,
            default_request_timeout_seconds=settings.http_timeout_seconds,
            per_response_max_bytes=settings.http_max_response_bytes,
            clock=self._clock,
        )

    def _raw_transport(self, provider: SourceProvider) -> HttpGetTransportPort:
        if self._transport is not None:
            return self._transport
        return BoundedGetTransport(
            provider,
            timeout_seconds=self._settings.http_timeout_seconds,
            max_response_bytes=self._settings.http_max_response_bytes,
        )

    def _budgeted_transport(
        self, ledger: CollectionBudgetLedger, provider: SourceProvider,
        identity: Optional[RepositoryIdentity] = None,
    ) -> BudgetedHttpGetTransport:
        return BudgetedHttpGetTransport(self._raw_transport(provider), ledger, provider, identity)

    def _raw_binary_transport(self) -> BinaryGetTransportPort:
        if self._binary_transport is not None:
            return self._binary_transport
        return BoundedBinaryGetTransport(
            max_response_bytes=min(8 * MIB, self._settings.http_max_response_bytes),
            timeout_seconds=min(15.0, self._settings.http_timeout_seconds),
        )

    def _budgeted_binary_transport(
        self, ledger: CollectionBudgetLedger, identity: RepositoryIdentity,
    ) -> BudgetedHttpGetTransport:
        return BudgetedHttpGetTransport(
            self._raw_binary_transport(), ledger, SourceProvider.github_actions, identity,
        )

    def _project_context(self, project_id: str, cutoff: datetime, ledger: CollectionBudgetLedger):
        try:
            tasks = self._task_source_factory(self._budgeted_transport(ledger, SourceProvider.trello))
        except Exception as exc:
            tasks = _UnavailableTaskSource(_category(exc))
        try:
            policies = self._policy_source_factory()
        except Exception as exc:
            policies = _UnavailablePolicySource(_category(exc))
        shared = CompositeSourcesAdapter(
            task_source=tasks,
            code_source=_UnavailableCodeSource(SourceErrorCategory.configuration),
            ci_source=_UnavailableCISource(SourceErrorCategory.configuration),
            policy_source=policies,
            default_timezone=self._timezone,
            known_secret_values=self._settings.get_known_secret_values(),
            task_source_identity=self._task_source_identity,
        ).collect_snapshot(project_id, cutoff)
        project_records = [
            record for record in shared.records
            if record.provider in {SourceProvider.trello, SourceProvider.local_policy}
        ]
        task_evidence = [
            item.model_copy(deep=True) for item in shared.evidence
            if item.provenance.source == EvidenceSource.trello
        ]
        from agente_gestion.agents.context import estimate_tokens_from_data
        estimate = estimate_tokens_from_data({
            "tasks": [item.model_dump(mode="json") for item in shared.snapshot.tasks],
            "policies": [item.model_dump(mode="json") for item in shared.snapshot.policies],
            "evidence": [item.model_dump(mode="json") for item in task_evidence],
        })
        ledger.consume_estimated_tokens(None, estimate)
        return (
            shared.snapshot.tasks,
            shared.snapshot.policies,
            task_evidence,
            project_records,
            shared.snapshot.tasks_complete,
            self._task_source_identity,
        )

    def _make_repository_sources(
        self,
        repository: RepositoryConfiguration,
        identity: RepositoryIdentity,
        ledger: CollectionBudgetLedger,
    ) -> tuple[CodeSourcePort, CISourcePort, Optional[str], Optional[SourceErrorCategory], bool, bool]:
        target_sha = repository.commit_sha
        try:
            if ledger.token_budget_exhausted(identity):
                raise SourceError(SourceProvider.github, SourceErrorCategory.limit_exceeded) from None
            remaining_files = ledger.reserve_file_work(identity, SourceProvider.github)
            max_files = min(3000, remaining_files)
            max_total_bytes = min(10 * MIB, self._settings.http_max_response_bytes,
                                  ledger.remaining_response_bytes(identity))
            if max_total_bytes <= 0:
                raise SourceError(SourceProvider.github, SourceErrorCategory.limit_exceeded) from None
            code_adapter = GitHubCodeSourceAdapter(
                owner=repository.owner,
                repo=repository.repo,
                token=(self._settings.github_token.get_secret_value()
                       if self._settings.github_token else None),
                branch=repository.branch,
                transport=self._budgeted_transport(ledger, SourceProvider.github, identity),
                max_files=max_files,
                max_total_bytes=max_total_bytes,
            )
            access_confirmed = False
            if target_sha is None:
                target_sha = code_adapter.resolve_commit_sha()
                access_confirmed = True
            if repository.commit_sha is not None and target_sha != repository.commit_sha:
                raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
            resolved = target_sha is not None
        except Exception as exc:
            category = _category(exc, SourceErrorCategory.configuration)
            resolved = target_sha is not None
            return (
                _UnavailableCodeSource(category),
                _UnavailableCISource(SourceErrorCategory.configuration),
                target_sha,
                category,
                resolved,
                False,
            )

        code_source: CodeSourcePort = _BudgetedCodeSource(code_adapter, ledger, identity)
        try:
            token = (self._settings.github_token.get_secret_value()
                     if self._settings.github_token else None)
            ci_adapter = GitHubActionsSourceAdapter(
                owner=repository.owner,
                repo=repository.repo,
                token=token,
                target_commit_sha=target_sha,
                transport=self._budgeted_transport(ledger, SourceProvider.github_actions, identity),
            )
            artifact_source = ArtifactEnabledCISource(
                ci_adapter,
                CiMetricsArtifactValidator(self._budgeted_binary_transport(ledger, identity)),
                self._budgeted_transport(ledger, SourceProvider.github_actions, identity),
                repository_full_name=f"{repository.owner}/{repository.repo}",
                token=token,
                quality_workflow_path=repository.quality_workflow_path,
                identity=identity,
            )
            ci_source: CISourcePort = _BudgetedCISource(artifact_source, ledger, identity)
        except Exception as exc:
            ci_source = _UnavailableCISource(_category(exc, SourceErrorCategory.configuration))
        return code_source, ci_source, target_sha, None, True, access_confirmed

    def _collect_repository(
        self,
        repository: RepositoryConfiguration,
        project_id: str,
        cutoff: datetime,
        tasks: list,
        policies: list,
        task_evidence: list[EvidenceItem],
        ledger: CollectionBudgetLedger,
        task_source_identity: Optional[str],
    ) -> tuple[RepositorySourceCollectionResult, RepositorySnapshotResult, list[EvidenceItem]]:
        identity = RepositoryIdentity(id=repository.id, owner=repository.owner, repo=repository.repo)
        ledger.begin_repository(identity)
        state = RepositoryAnalysisState(identity=identity, branch=repository.branch)
        code_source, ci_source, target_sha, resolution_error, resolved, access_confirmed = self._make_repository_sources(
            repository, identity, ledger,
        )
        if resolved and target_sha is not None:
            state = state.model_copy(update={
                "target_commit_sha": target_sha,
                "commit_resolution_status": CommitResolutionStatus.resolved,
                "access_status": (RepositoryAccessStatus.allowed if access_confirmed
                                  else RepositoryAccessStatus.unknown),
                "availability_status": (RepositoryAvailabilityStatus.available if access_confirmed
                                         else RepositoryAvailabilityStatus.unknown),
            })
        elif resolution_error is not None:
            access = (RepositoryAccessStatus.denied
                      if resolution_error in {SourceErrorCategory.authentication, SourceErrorCategory.permission_denied}
                      else RepositoryAccessStatus.unknown)
            availability = (RepositoryAvailabilityStatus.unavailable
                            if resolution_error in {SourceErrorCategory.not_found, SourceErrorCategory.timeout,
                                                    SourceErrorCategory.network, SourceErrorCategory.rate_limit}
                            else RepositoryAvailabilityStatus.unknown)
            state = state.model_copy(update={
                "commit_resolution_status": CommitResolutionStatus.failed,
                "access_status": access,
                "availability_status": availability,
            })

        shared_tasks = _CachedTaskSource(tasks, task_evidence)
        shared_policies = _CachedPolicySource(policies)
        composite = CompositeSourcesAdapter(
            task_source=shared_tasks,
            code_source=code_source,
            ci_source=ci_source,
            policy_source=shared_policies,
            default_commit_sha=target_sha,
            default_timezone=self._timezone,
            known_secret_values=self._settings.get_known_secret_values(),
            task_source_identity=task_source_identity,
        )
        collected = composite.collect_snapshot(project_id, cutoff, commit_sha=target_sha)
        raw_code_record = next((item for item in collected.records if item.provider == SourceProvider.github), None)
        raw_ci_record = next((item for item in collected.records if item.provider == SourceProvider.github_actions), None)
        code_diffs: list[CodeDiffSnippet] = []
        ci_runs: list[CIRunResult] = []
        scoped_evidence: list[EvidenceItem] = []
        records: list[SourceExecutionRecord] = []
        reason_parts: list[str] = []

        if raw_code_record is not None:
            try:
                code_record = _copy_repository_record(raw_code_record, identity, target_sha)
                records.append(code_record)
                if code_record.status == SourceExecutionStatus.failed:
                    reason_parts.append(code_record.error.message)
                else:
                    if target_sha is None:
                        raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
                    code_diffs = [_decorate_code(item, identity, target_sha)
                                  for item in collected.snapshot.code_diffs]
                    scoped_evidence.extend(
                        _namespace_evidence(item, identity, target_sha, EvidenceSource.github)
                        for item in collected.evidence
                        if item.provenance.source == EvidenceSource.github
                    )
            except Exception as exc:
                category = _category(exc)
                records = [item for item in records if item.provider != SourceProvider.github]
                records.append(_error_record(SourceProvider.github, category, identity, target_sha))
                code_diffs = []
                scoped_evidence = []
                reason_parts.append(SourceErrorInfo(provider=SourceProvider.github, category=category).message)

        if raw_ci_record is not None:
            try:
                ci_record = _copy_repository_record(raw_ci_record, identity, target_sha)
                if ci_record.status == SourceExecutionStatus.success and target_sha is not None:
                    ci_runs = [_decorate_ci(item, identity, target_sha) for item in collected.snapshot.ci_runs]
                    scoped_evidence.extend(
                        _namespace_evidence(item, identity, target_sha, EvidenceSource.github_actions)
                        for item in collected.evidence
                        if item.provenance.source == EvidenceSource.github_actions
                    )
                    if not any(item.commit_sha == target_sha for item in ci_runs):
                        ci_record = SourceExecutionRecord(
                            provider=SourceProvider.github_actions,
                            status=SourceExecutionStatus.failed,
                            duration_seconds=ci_record.duration_seconds,
                            attempt_count=ci_record.attempt_count,
                            error=SourceErrorInfo(
                                provider=SourceProvider.github_actions,
                                category=SourceErrorCategory.not_found,
                            ),
                            repository_identity=identity,
                            target_commit_sha=target_sha,
                        )
                records.append(ci_record)
                if ci_record.status == SourceExecutionStatus.failed:
                    reason_parts.append(ci_record.error.message)
            except Exception as exc:
                category = _category(exc)
                records = [item for item in records if item.provider != SourceProvider.github_actions]
                records.append(_error_record(SourceProvider.github_actions, category, identity, target_sha))
                ci_runs = []
                scoped_evidence = [item for item in scoped_evidence
                                   if item.provenance.source != EvidenceSource.github_actions]
                reason_parts.append(SourceErrorInfo(
                    provider=SourceProvider.github_actions, category=category,
                ).message)

        if not resolved and raw_code_record is not None and raw_code_record.error is not None:
            reason_parts.append(raw_code_record.error.message)
        if target_sha is not None and code_diffs and any(item.commit_sha.lower() != target_sha for item in code_diffs):
            raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

        snapshot_ci = [item.model_copy(deep=True) for item in ci_runs if target_sha and item.commit_sha == target_sha]
        token_or_other_budget_exhaustion = ledger.repository_receipt(identity).exhausted
        if token_or_other_budget_exhaustion:
            reason_parts.append("Repository source budget exhausted.")
        complete = (
            resolved
            and any(item.status == SourceExecutionStatus.success and item.provider == SourceProvider.github
                    for item in records)
            and any(item.status == SourceExecutionStatus.success and item.provider == SourceProvider.github_actions
                    for item in records)
            and bool(snapshot_ci)
            and not token_or_other_budget_exhaustion
        )
        reason = "; ".join(dict.fromkeys(reason_parts)) if reason_parts else None
        code_record = next((record for record in records if record.provider == SourceProvider.github), None)
        access_status = state.access_status
        availability_status = state.availability_status
        if code_record is not None and code_record.status == SourceExecutionStatus.success:
            access_status = RepositoryAccessStatus.allowed
            availability_status = RepositoryAvailabilityStatus.available
        elif code_record is not None and code_record.error is not None:
            category = code_record.error.category
            if category in {SourceErrorCategory.authentication, SourceErrorCategory.permission_denied}:
                access_status = RepositoryAccessStatus.denied
                availability_status = RepositoryAvailabilityStatus.unknown
            elif category in {SourceErrorCategory.not_found, SourceErrorCategory.timeout,
                              SourceErrorCategory.network, SourceErrorCategory.rate_limit}:
                availability_status = RepositoryAvailabilityStatus.unavailable
        state = state.model_copy(update={
            "access_status": access_status,
            "availability_status": availability_status,
        })
        receipt = ledger.repository_receipt(identity)
        source_result = RepositorySourceCollectionResult(
            repository_state=state,
            code_diffs=code_diffs,
            ci_runs=ci_runs,
            evidence=scoped_evidence,
            records=records,
            is_complete=complete,
            incomplete_reason=reason,
            budget_receipt=receipt,
        )
        snapshot_result = RepositorySnapshotResult(
            repository_state=state,
            code_diffs=code_diffs,
            ci_runs=snapshot_ci,
            is_complete=complete,
            incomplete_reason=reason,
        )
        return source_result, snapshot_result, scoped_evidence

    def collect_snapshot(
        self,
        project_id: str,
        cutoff_datetime: datetime,
        commit_sha: Optional[str] = None,
    ) -> SourceCollectionResult:
        if cutoff_datetime.tzinfo is None or cutoff_datetime.tzinfo.utcoffset(cutoff_datetime) is None:
            raise ValueError("cutoff_datetime must be timezone-aware.")
        if commit_sha is not None:
            raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None
        ledger = self._ledger()
        (tasks, policies, task_evidence, project_records, tasks_complete,
         task_source_identity) = self._project_context(project_id, cutoff_datetime, ledger)
        repository_results: list[RepositorySourceCollectionResult] = []
        snapshot_results: list[RepositorySnapshotResult] = []
        all_evidence = [item.model_copy(deep=True) for item in task_evidence]
        for repository in self._repositories:
            source_result, snapshot_result, evidence = self._collect_repository(
                repository, project_id, cutoff_datetime, tasks, policies, task_evidence,
                ledger, task_source_identity,
            )
            repository_results.append(source_result)
            snapshot_results.append(snapshot_result)
            all_evidence.extend(evidence)

        seen_ids: set[str] = set()
        unique_evidence: list[EvidenceItem] = []
        for item in all_evidence:
            evidence_id = item.provenance.id
            if evidence_id in seen_ids:
                suffix = hashlib.sha256(
                    f"{item.provenance.source.value}:{evidence_id}".encode("utf-8")
                ).hexdigest()[:12]
                item = item.model_copy(deep=True, update={
                    "provenance": item.provenance.model_copy(update={"id": f"{item.provenance.source.value}:{evidence_id}:{suffix}"}),
                })
                evidence_id = item.provenance.id
            seen_ids.add(evidence_id)
            unique_evidence.append(item)

        all_complete = (
            all(record.status == SourceExecutionStatus.success for record in project_records)
            and all(item.is_complete is True for item in repository_results)
            and not ledger.receipt().exhausted
        )
        reasons = [
            record.error.message for record in project_records if record.error is not None
        ] + [item.incomplete_reason for item in repository_results if item.incomplete_reason]
        snapshot = WeeklySnapshot(
            id=f"snap-{project_id}",
            project_id=project_id,
            cutoff_datetime=cutoff_datetime,
            captured_at=datetime.now(timezone.utc),
            tasks=tasks,
            code_diffs=[],
            ci_runs=[],
            policies=policies,
            is_complete=all_complete,
            incomplete_reason="; ".join(dict.fromkeys(reasons)) if reasons else None,
            task_source_identity=task_source_identity,
            tasks_complete=tasks_complete,
            repository_results=snapshot_results,
        )
        return SourceCollectionResult(
            snapshot=snapshot,
            evidence=unique_evidence,
            records=project_records,
            repository_results=repository_results,
            budget_receipt=ledger.receipt(),
        )


__all__ = ["MultiRepoSourcesAdapter"]
