"""Composite source adapter coordinating multi-provider snapshot and evidence retrieval (T5a)."""

from datetime import datetime, timezone
import inspect
import time
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

from agente_gestion.domain.code import CIRunResult, CodeDiffSnippet
from agente_gestion.domain.evidence import EvidenceItem, EvidenceSource
from agente_gestion.domain.policies import QualityRule
from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.domain.tasks import TaskCard
from agente_gestion.ports.source_collection import (
    SourceCollectionPort,
    SourceCollectionResult,
    SourceErrorInfo,
    SourceExecutionRecord,
    SourceExecutionStatus,
)
from agente_gestion.ports.source_errors import (
    SourceError,
    SourceErrorCategory,
    SourceProvider,
)
from agente_gestion.ports.sources import (
    CISourcePort,
    CodeSourcePort,
    PolicySourcePort,
    TaskSourcePort,
)

DEFAULT_TIMEZONE = ZoneInfo("America/Guayaquil")


def _canonicalize_error_info(
    provider: SourceProvider,
    exc: Exception,
) -> SourceErrorInfo:
    """Derive sanitized, provider-consistent SourceErrorInfo without raw diagnostics."""
    if isinstance(exc, SourceError):
        # Mismatched provider reattributed canonically to the active provider
        return SourceErrorInfo(provider=provider, category=exc.category)

    if isinstance(exc, (TimeoutError,)):
        return SourceErrorInfo(provider=provider, category=SourceErrorCategory.timeout)
    if isinstance(exc, (ConnectionError, OSError)):
        return SourceErrorInfo(provider=provider, category=SourceErrorCategory.network)
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return SourceErrorInfo(
            provider=provider, category=SourceErrorCategory.malformed_payload
        )

    return SourceErrorInfo(
        provider=provider, category=SourceErrorCategory.malformed_payload
    )


class CompositeSourcesAdapter(SourceCollectionPort):
    """
    Coordinates data and evidence collection across isolated source adapters.

    Enforces:
    - Bounded sequential retrieval (Policy -> Tasks -> Code -> CI).
    - Strict target commit SHA pinning from code resolution to CI retrieval.
    - Honest availability reporting: fetch failures leave empty containers
      representing unavailable data (not known zeros), and snapshot is marked incomplete.
    - Zero remote retry loops beyond individual adapter contracts.
    - Timezone-aware cutoff enforcement and capture timestamp in actual UTC retrieval.
    - Protection against credential echoes into normalized domain entities.
    - Isolation against caller mutation via deep copies.
    """

    def __init__(
        self,
        task_source: TaskSourcePort,
        code_source: CodeSourcePort,
        ci_source: CISourcePort,
        policy_source: PolicySourcePort,
        default_commit_sha: Optional[str] = None,
        default_timezone: Optional[ZoneInfo] = None,
        known_secret_values: Optional[Iterable[str]] = None,
    ):
        self._task_source = task_source
        self._code_source = code_source
        self._ci_source = ci_source
        self._policy_source = policy_source
        self._default_commit_sha = default_commit_sha
        self._timezone = default_timezone or DEFAULT_TIMEZONE

        secrets_set = set()
        if known_secret_values:
            for s in known_secret_values:
                if isinstance(s, str) and s.strip():
                    secrets_set.add(s.strip())
        self._known_secrets = frozenset(secrets_set)

        self._last_result: Optional[SourceCollectionResult] = None
        self._last_resolved_commit_sha: Optional[str] = None
        self._last_collection_complete: bool = False
        self._collected_params: Optional[tuple[str, datetime, Optional[str]]] = None

    @property
    def last_resolved_commit_sha(self) -> Optional[str]:
        """The commit SHA resolved in the most recent collection attempt, if successful."""
        return self._last_resolved_commit_sha

    @property
    def last_collection_complete(self) -> bool:
        """True if the most recent collection completed across all sources without error."""
        return self._last_collection_complete

    def __repr__(self) -> str:
        """Safe representation without credentials."""
        return (
            f"CompositeSourcesAdapter("
            f"task_source={self._task_source.__class__.__name__}, "
            f"code_source={self._code_source.__class__.__name__}, "
            f"ci_source={self._ci_source.__class__.__name__}, "
            f"policy_source={self._policy_source.__class__.__name__})"
        )

    def _check_no_secrets(self, value: Any, provider: SourceProvider) -> None:
        """Reject normalized objects echoing configured known secrets."""
        if not self._known_secrets:
            return

        if isinstance(value, str):
            for secret in self._known_secrets:
                if secret in value:
                    raise SourceError(
                        provider, SourceErrorCategory.malformed_payload
                    ) from None
        elif isinstance(value, dict):
            for k, v in value.items():
                self._check_no_secrets(k, provider)
                self._check_no_secrets(v, provider)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                self._check_no_secrets(item, provider)
        elif hasattr(value, "model_dump"):
            self._check_no_secrets(value.model_dump(), provider)

    def collect_snapshot(
        self,
        project_id: str,
        cutoff_datetime: datetime,
        commit_sha: Optional[str] = None,
    ) -> SourceCollectionResult:
        """
        Execute bounded retrieval across sources and return composite result.

        Parameters:
            project_id: Target project identifier.
            cutoff_datetime: Timezone-aware report cutoff timestamp.
            commit_sha: Optional target commit SHA override.
        """
        # Validate timezone-awareness
        if cutoff_datetime.tzinfo is None or cutoff_datetime.tzinfo.utcoffset(cutoff_datetime) is None:
            raise ValueError("cutoff_datetime must be timezone-aware.")

        # Guard project configuration against credential echo
        if self._known_secrets:
            for secret in self._known_secrets:
                if secret in project_id:
                    raise SourceError(
                        SourceProvider.local_policy,
                        SourceErrorCategory.configuration,
                    ) from None

        # Check if this instance was already collected
        if self._collected_params is not None:
            norm_sha = commit_sha.lower() if commit_sha else None
            prev_sha = self._collected_params[2].lower() if self._collected_params[2] else None
            if (
                project_id == self._collected_params[0]
                and cutoff_datetime == self._collected_params[1]
                and norm_sha == prev_sha
            ):
                return self._last_result.model_copy(deep=True)
            raise ValueError(
                "CompositeSourcesAdapter is designed for single-run collection; "
                "differing requests must be executed on a fresh adapter instance."
            )

        # Reset per-collection tracking state (prevents stale SHA retention across runs)
        self._last_resolved_commit_sha = None
        self._last_collection_complete = False

        # Invalidate provider caches to ensure fresh collection
        for port in (
            self._task_source,
            self._code_source,
            self._ci_source,
            self._policy_source,
        ):
            if hasattr(port, "invalidate_cache") and callable(
                getattr(port, "invalidate_cache")
            ):
                try:
                    port.invalidate_cache(project_id)
                except Exception:
                    pass

        records: list[SourceExecutionRecord] = []
        combined_evidence: list[EvidenceItem] = []
        failure_reasons: list[str] = []

        # 1. Local Policy
        policies: list[QualityRule] = []
        t0 = time.perf_counter()
        try:
            raw_policies = self._policy_source.get_policies(project_id)
            for rule in raw_policies:
                self._check_no_secrets(rule, SourceProvider.local_policy)
            policies = [p.model_copy(deep=True) for p in raw_policies]
            records.append(
                SourceExecutionRecord(
                    provider=SourceProvider.local_policy,
                    status=SourceExecutionStatus.success,
                    duration_seconds=max(0.0, time.perf_counter() - t0),
                    attempt_count=1,
                )
            )
        except Exception as exc:
            err_info = _canonicalize_error_info(SourceProvider.local_policy, exc)
            records.append(
                SourceExecutionRecord(
                    provider=SourceProvider.local_policy,
                    status=SourceExecutionStatus.failed,
                    duration_seconds=max(0.0, time.perf_counter() - t0),
                    attempt_count=1,
                    error=err_info,
                )
            )
            failure_reasons.append(err_info.message)

        # 2. Tasks & Evidence (Trello)
        tasks: list[TaskCard] = []
        t0 = time.perf_counter()
        try:
            raw_tasks = self._task_source.get_tasks(project_id)
            for t in raw_tasks:
                self._check_no_secrets(t, SourceProvider.trello)

            raw_task_ev: list[EvidenceItem] = []
            if hasattr(self._task_source, "get_evidence") and callable(
                getattr(self._task_source, "get_evidence")
            ):
                raw_task_ev = self._task_source.get_evidence(project_id)
                for ev in raw_task_ev:
                    self._check_no_secrets(ev, SourceProvider.trello)
                    if ev.provenance.project_id != project_id:
                        raise SourceError(
                            SourceProvider.trello,
                            SourceErrorCategory.malformed_payload,
                        )

            # Both data and evidence validated successfully
            tasks = [t.model_copy(deep=True) for t in raw_tasks]
            combined_evidence.extend([e.model_copy(deep=True) for e in raw_task_ev])
            records.append(
                SourceExecutionRecord(
                    provider=SourceProvider.trello,
                    status=SourceExecutionStatus.success,
                    duration_seconds=max(0.0, time.perf_counter() - t0),
                    attempt_count=1,
                )
            )
        except Exception as exc:
            err_info = _canonicalize_error_info(SourceProvider.trello, exc)
            records.append(
                SourceExecutionRecord(
                    provider=SourceProvider.trello,
                    status=SourceExecutionStatus.failed,
                    duration_seconds=max(0.0, time.perf_counter() - t0),
                    attempt_count=1,
                    error=err_info,
                )
            )
            failure_reasons.append(err_info.message)

        # 3. Code Diffs & Evidence (GitHub)
        code_diffs: list[CodeDiffSnippet] = []
        pinned_sha: Optional[str] = None
        target_sha = commit_sha or self._default_commit_sha
        t0 = time.perf_counter()
        code_succeeded = False
        try:
            raw_diffs = self._code_source.get_diffs(project_id, commit_sha=target_sha)

            # Resolve pinned commit SHA strictly
            if (
                hasattr(self._code_source, "last_resolved_commit_sha")
                and self._code_source.last_resolved_commit_sha
            ):
                pinned_sha = self._code_source.last_resolved_commit_sha
            elif target_sha:
                pinned_sha = target_sha

            # Explicit SHA override must match resolved SHA
            if (
                commit_sha
                and pinned_sha
                and commit_sha.lower() != pinned_sha.lower()
            ):
                raise SourceError(
                    SourceProvider.github,
                    SourceErrorCategory.malformed_payload,
                )

            if pinned_sha:
                self._check_no_secrets(pinned_sha, SourceProvider.github)

            # Check code completeness (e.g. missing patch metadata for binary/large files)
            if hasattr(self._code_source, "last_collection_complete"):
                if not self._code_source.last_collection_complete:
                    raise SourceError(
                        SourceProvider.github,
                        SourceErrorCategory.limit_exceeded,
                    )

            for d in raw_diffs:
                self._check_no_secrets(d, SourceProvider.github)
                if pinned_sha and d.commit_sha.lower() != pinned_sha.lower():
                    raise SourceError(
                        SourceProvider.github,
                        SourceErrorCategory.malformed_payload,
                    )

            raw_code_ev: list[EvidenceItem] = []
            if hasattr(self._code_source, "get_evidence") and callable(
                getattr(self._code_source, "get_evidence")
            ):
                sig = inspect.signature(self._code_source.get_evidence)
                if "commit_sha" in sig.parameters:
                    raw_code_ev = self._code_source.get_evidence(
                        project_id, commit_sha=pinned_sha
                    )
                else:
                    raw_code_ev = self._code_source.get_evidence(project_id)

                for ev in raw_code_ev:
                    self._check_no_secrets(ev, SourceProvider.github)
                    if ev.provenance.project_id != project_id:
                        raise SourceError(
                            SourceProvider.github,
                            SourceErrorCategory.malformed_payload,
                        )
                    if (
                        pinned_sha
                        and ev.provenance.commit_sha
                        and ev.provenance.commit_sha.lower() != pinned_sha.lower()
                    ):
                        raise SourceError(
                            SourceProvider.github,
                            SourceErrorCategory.malformed_payload,
                        )

            code_diffs = [d.model_copy(deep=True) for d in raw_diffs]
            combined_evidence.extend([e.model_copy(deep=True) for e in raw_code_ev])
            records.append(
                SourceExecutionRecord(
                    provider=SourceProvider.github,
                    status=SourceExecutionStatus.success,
                    duration_seconds=max(0.0, time.perf_counter() - t0),
                    attempt_count=1,
                )
            )
            code_succeeded = True
            self._last_resolved_commit_sha = pinned_sha
        except Exception as exc:
            self._last_resolved_commit_sha = None
            code_succeeded = False
            err_info = _canonicalize_error_info(SourceProvider.github, exc)
            records.append(
                SourceExecutionRecord(
                    provider=SourceProvider.github,
                    status=SourceExecutionStatus.failed,
                    duration_seconds=max(0.0, time.perf_counter() - t0),
                    attempt_count=1,
                    error=err_info,
                )
            )
            failure_reasons.append(err_info.message)

        # 4. CI Runs & Evidence (GitHub Actions)
        ci_runs: list[CIRunResult] = []
        ci_skipped = False
        if not (code_succeeded and pinned_sha):
            # Cannot query CI without validated successful code unit and pinned target SHA
            ci_skipped = True
            failure_reasons.append(
                "CI collection skipped due to missing validated code unit or pinned target commit SHA dependency."
            )
        else:
            t0 = time.perf_counter()
            try:
                sig = inspect.signature(self._ci_source.get_ci_results)
                if "commit_sha" in sig.parameters:
                    raw_ci_runs = self._ci_source.get_ci_results(
                        project_id, commit_sha=pinned_sha
                    )
                else:
                    raw_ci_runs = self._ci_source.get_ci_results(project_id)

                for r in raw_ci_runs:
                    self._check_no_secrets(r, SourceProvider.github_actions)

                raw_ci_ev: list[EvidenceItem] = []
                if hasattr(self._ci_source, "get_evidence") and callable(
                    getattr(self._ci_source, "get_evidence")
                ):
                    sig_ev = inspect.signature(self._ci_source.get_evidence)
                    if "commit_sha" in sig_ev.parameters:
                        raw_ci_ev = self._ci_source.get_evidence(
                            project_id, commit_sha=pinned_sha
                        )
                    else:
                        raw_ci_ev = self._ci_source.get_evidence(project_id)

                    runs_by_id: dict[str, CIRunResult] = {str(r.id): r for r in raw_ci_runs}
                    run_ids_with_evidence: set[str] = set()

                    for ev in raw_ci_ev:
                        self._check_no_secrets(ev, SourceProvider.github_actions)
                        if ev.provenance.project_id != project_id:
                            raise SourceError(
                                SourceProvider.github_actions,
                                SourceErrorCategory.malformed_payload,
                            )
                        if ev.provenance.source != EvidenceSource.github_actions:
                            raise SourceError(
                                SourceProvider.github_actions,
                                SourceErrorCategory.malformed_payload,
                            )

                        if ev.category == "ci_run":
                            # Must correspond to an actual returned run
                            run_id = ev.provenance.external_id
                            if not run_id or str(run_id) not in runs_by_id:
                                # Orphan evidence or missing run identity
                                raise SourceError(
                                    SourceProvider.github_actions,
                                    SourceErrorCategory.malformed_payload,
                                )
                            corresp_run = runs_by_id[str(run_id)]
                            # Per-run evidence must match CORRESPONDING ACTUAL returned run SHA
                            if (
                                not ev.provenance.commit_sha
                                or ev.provenance.commit_sha.lower() != corresp_run.commit_sha.lower()
                            ):
                                raise SourceError(
                                    SourceProvider.github_actions,
                                    SourceErrorCategory.malformed_payload,
                                )
                            run_ids_with_evidence.add(str(run_id))
                        else:
                            # Summary evidence: may declare requested target or one of the run SHAs
                            if ev.provenance.commit_sha:
                                ev_sha = ev.provenance.commit_sha.lower()
                                allowed_shas = {pinned_sha.lower()} | {r.commit_sha.lower() for r in raw_ci_runs}
                                if ev_sha not in allowed_shas:
                                    raise SourceError(
                                        SourceProvider.github_actions,
                                        SourceErrorCategory.malformed_payload,
                                    )

                    # Check that all returned runs have corresponding ci_run evidence
                    if len(run_ids_with_evidence) != len(runs_by_id):
                        raise SourceError(
                            SourceProvider.github_actions,
                            SourceErrorCategory.malformed_payload,
                        )

                ci_runs = [r.model_copy(deep=True) for r in raw_ci_runs]
                combined_evidence.extend([e.model_copy(deep=True) for e in raw_ci_ev])
                records.append(
                    SourceExecutionRecord(
                        provider=SourceProvider.github_actions,
                        status=SourceExecutionStatus.success,
                        duration_seconds=max(0.0, time.perf_counter() - t0),
                        attempt_count=1,
                    )
                )

                # Check if at least one run matches the exact target commit SHA
                has_target_match = any(
                    r.commit_sha.lower() == pinned_sha.lower() for r in ci_runs
                )
                if not has_target_match:
                    failure_reasons.append(
                        "No continuous integration runs found matching the target commit SHA."
                    )
            except Exception as exc:
                err_info = _canonicalize_error_info(SourceProvider.github_actions, exc)
                records.append(
                    SourceExecutionRecord(
                        provider=SourceProvider.github_actions,
                        status=SourceExecutionStatus.failed,
                        duration_seconds=max(0.0, time.perf_counter() - t0),
                        attempt_count=1,
                        error=err_info,
                    )
                )
                failure_reasons.append(err_info.message)

        # Deduplicate / namespace cross-provider evidence IDs legitimately
        reserved_ids: set[str] = {item.provenance.id for item in combined_evidence}
        allocated_ids: set[str] = set()
        normalized_evidence: list[EvidenceItem] = []

        for item in combined_evidence:
            orig_id = item.provenance.id
            if orig_id not in allocated_ids:
                allocated_ids.add(orig_id)
                normalized_evidence.append(item)
            else:
                prov_source = item.provenance.source.value
                candidate = f"{prov_source}:{orig_id}"
                if candidate in reserved_ids or candidate in allocated_ids:
                    ordinal = 2
                    while (
                        f"{prov_source}:{orig_id}:{ordinal}" in reserved_ids
                        or f"{prov_source}:{orig_id}:{ordinal}" in allocated_ids
                    ):
                        ordinal += 1
                    candidate = f"{prov_source}:{orig_id}:{ordinal}"
                allocated_ids.add(candidate)
                new_prov = item.provenance.model_copy(update={"id": candidate})
                normalized_evidence.append(
                    item.model_copy(deep=True, update={"provenance": new_prov})
                )

        # Determine snapshot completeness
        is_complete = (
            len(failure_reasons) == 0
            and not ci_skipped
            and all(r.status == SourceExecutionStatus.success for r in records)
        )
        incomplete_reason = "; ".join(failure_reasons) if not is_complete else None

        captured_at = datetime.now(timezone.utc)
        snapshot = WeeklySnapshot(
            id=f"snap-{project_id}",
            project_id=project_id,
            cutoff_datetime=cutoff_datetime,
            captured_at=captured_at,
            tasks=tasks,
            code_diffs=code_diffs,
            ci_runs=ci_runs,
            policies=policies,
            is_complete=is_complete,
            incomplete_reason=incomplete_reason,
        )

        effective_sha = pinned_sha if code_succeeded else None

        result = SourceCollectionResult(
            snapshot=snapshot,
            evidence=normalized_evidence,
            records=records,
            target_commit_sha=effective_sha,
        )

        self._collected_params = (project_id, cutoff_datetime, commit_sha)
        self._last_result = result.model_copy(deep=True)
        self._last_collection_complete = is_complete

        return result.model_copy(deep=True)

    def get_evidence(
        self,
        project_id: str,
        commit_sha: Optional[str] = None,
    ) -> list[EvidenceItem]:
        """
        Retrieve normalized evidence from the cached last collection.

        Does not perform network refetches or cache invalidation.
        """
        if (
            self._last_result is not None
            and self._last_result.snapshot.project_id == project_id
        ):
            evidence = self._last_result.evidence
            if commit_sha:
                target = commit_sha.lower()
                evidence = [
                    e
                    for e in evidence
                    if e.provenance.commit_sha
                    and e.provenance.commit_sha.lower() == target
                ]
            return [e.model_copy(deep=True) for e in evidence]
        return []
