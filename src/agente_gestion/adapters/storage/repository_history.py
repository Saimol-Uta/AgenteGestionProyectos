"""Identity extraction and read-only projection helpers for SQLite repository history."""

from dataclasses import dataclass
import hashlib
import json
from typing import Iterator, Optional

from agente_gestion.domain.code import CIRunResult, CodeDiffSnippet
from agente_gestion.domain.repository_history import (
    RepositoryArtifactIdentity,
    RepositoryArtifactObservation,
    RepositoryHistoryFilter,
)
from agente_gestion.domain.repositories import RepositoryIdentity
from agente_gestion.domain.snapshot import RepositorySnapshotResult, WeeklySnapshot


@dataclass(frozen=True)
class RepositorySourceScope:
    """One repository result or one legacy/unknown source item; unknowns never coalesce."""

    source_scope: str
    source_item_key: Optional[str]
    repository_identity: Optional[RepositoryIdentity]
    target_commit_sha: Optional[str]
    repository_result: Optional[RepositorySnapshotResult]
    legacy_code_diffs: tuple[CodeDiffSnippet, ...] = ()
    legacy_ci_runs: tuple[CIRunResult, ...] = ()


def _identity_key(identity: RepositoryIdentity) -> tuple[str, str, str]:
    return identity.id, identity.owner.casefold(), identity.repo.casefold()


def explicit_repository_identity(item: CodeDiffSnippet | CIRunResult) -> Optional[RepositoryIdentity]:
    """Return only identity persisted on the item itself, never its container/config."""
    identity = item.repository_identity
    provenance = item.provenance
    origin_identity = provenance.repository_identity if provenance is not None else None
    if identity is not None and origin_identity is not None and identity != origin_identity:
        raise ValueError("Stored item repository identity conflicts with its provenance.")
    return identity or origin_identity


def _artifact_observation(
    project_id: str,
    ci_run: CIRunResult,
    *,
    enclosing_identity: Optional[RepositoryIdentity] = None,
) -> Optional[RepositoryArtifactObservation]:
    identity = explicit_repository_identity(ci_run)
    if identity is not None and enclosing_identity is not None:
        if _identity_key(identity) != _identity_key(enclosing_identity):
            raise ValueError("CI run repository identity conflicts with its repository result.")

    has_artifact_fields = ci_run.artifact_id is not None or ci_run.artifact_name is not None
    if not has_artifact_fields:
        return None
    # Incomplete historical/partial metadata remains visible in the saved snapshot, but
    # it is not indexed as a complete artifact identity and is never completed from config.
    if (identity is None or ci_run.run_attempt is None
            or ci_run.artifact_id is None or ci_run.artifact_name is None):
        return None

    artifact_identity = RepositoryArtifactIdentity(
        project_id=project_id,
        repository_identity=identity,
        commit_sha=ci_run.commit_sha,
        ci_run_id=ci_run.id,
        actual_attempt=ci_run.run_attempt,
        artifact_id=ci_run.artifact_id,
        artifact_name=ci_run.artifact_name,
    )
    metadata = ci_run.metrics_artifact
    if metadata is not None and metadata.artifact_name is not None:
        if metadata.artifact_name != artifact_identity.artifact_name:
            raise ValueError("CI artifact metadata name conflicts with its Actions artifact identity.")
    return RepositoryArtifactObservation(
        identity=artifact_identity,
        source_metrics_artifact=metadata,
    )


def extract_artifact_observations(
    snapshot: WeeklySnapshot,
    *,
    tolerate_unindexed_historical_data: bool = False,
) -> list[RepositoryArtifactObservation]:
    """Extract only complete, validated source artifact identities from a capture."""
    candidates: list[tuple[CIRunResult, Optional[RepositoryIdentity]]] = []
    for scoped in snapshot.repository_results:
        for ci_run in scoped.ci_runs:
            candidates.append((ci_run, scoped.repository_state.identity))
    for ci_run in snapshot.ci_runs:
        candidates.append((ci_run, None))

    observations: dict[str, RepositoryArtifactObservation] = {}
    ambiguous_keys: set[str] = set()
    for ci_run, enclosing_identity in candidates:
        key: Optional[str] = None
        try:
            observation = _artifact_observation(
                snapshot.project_id, ci_run, enclosing_identity=enclosing_identity,
            )
            if observation is None:
                continue
            key = artifact_identity_key(observation.identity)
            if key in ambiguous_keys:
                continue
            previous = observations.get(key)
            if previous is not None and previous != observation:
                raise ValueError("A capture contains conflicting observations for one artifact identity.")
            observations[key] = observation
        except (TypeError, ValueError):
            if not tolerate_unindexed_historical_data:
                raise
            if key is not None:
                observations.pop(key, None)
                ambiguous_keys.add(key)
    return [observations[key] for key in sorted(observations)]


def artifact_identity_key(identity: RepositoryArtifactIdentity) -> str:
    """Stable hash of the complete, validated composite source identity."""
    data = identity.model_dump(mode="json")
    data["repository_identity"]["owner"] = data["repository_identity"]["owner"].casefold()
    data["repository_identity"]["repo"] = data["repository_identity"]["repo"].casefold()
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def iter_repository_source_scopes(snapshot: WeeklySnapshot) -> Iterator[RepositorySourceScope]:
    """Yield scoped results and legacy items without joining unknown origins together."""
    if snapshot.repository_results:
        for result in snapshot.repository_results:
            state = result.repository_state
            yield RepositorySourceScope(
                source_scope="repository",
                source_item_key=None,
                repository_identity=state.identity,
                target_commit_sha=state.target_commit_sha,
                repository_result=result,
            )
        return

    known: dict[tuple[str, str, str], tuple[RepositoryIdentity, list[CodeDiffSnippet], list[CIRunResult]]] = {}
    unknown: list[tuple[str, int, CodeDiffSnippet | CIRunResult]] = []
    for index, item in enumerate(snapshot.code_diffs):
        identity = explicit_repository_identity(item)
        if identity is None:
            unknown.append(("code", index, item))
            continue
        key = _identity_key(identity)
        current = known.setdefault(key, (identity, [], []))
        current[1].append(item)
    for index, item in enumerate(snapshot.ci_runs):
        identity = explicit_repository_identity(item)
        if identity is None:
            unknown.append(("ci", index, item))
            continue
        key = _identity_key(identity)
        current = known.setdefault(key, (identity, [], []))
        current[2].append(item)

    for key in sorted(known):
        identity, code_diffs, ci_runs = known[key]
        scope_key = json.dumps(key, separators=(",", ":"))
        yield RepositorySourceScope(
            source_scope="legacy",
            source_item_key=f"identity:{scope_key}",
            repository_identity=identity,
            target_commit_sha=None,
            repository_result=None,
            legacy_code_diffs=tuple(code_diffs),
            legacy_ci_runs=tuple(ci_runs),
        )

    for kind, index, item in unknown:
        item_key = f"{kind}:{index}"
        if isinstance(item, CodeDiffSnippet):
            yield RepositorySourceScope(
                source_scope="unknown",
                source_item_key=item_key,
                repository_identity=None,
                target_commit_sha=None,
                repository_result=None,
                legacy_code_diffs=(item,),
            )
        else:
            yield RepositorySourceScope(
                source_scope="unknown",
                source_item_key=item_key,
                repository_identity=None,
                target_commit_sha=None,
                repository_result=None,
                legacy_ci_runs=(item,),
            )

    if not known and not unknown:
        yield RepositorySourceScope(
            source_scope="unknown",
            source_item_key=None,
            repository_identity=None,
            target_commit_sha=None,
            repository_result=None,
        )


def project_source_scope(
    scope: RepositorySourceScope, filters: RepositoryHistoryFilter,
) -> RepositorySourceScope:
    """Return a non-mutating source projection narrowed by the requested commit/CI filters."""
    ci_runs = list(scope.repository_result.ci_runs) if scope.repository_result else list(scope.legacy_ci_runs)
    code_diffs = list(scope.repository_result.code_diffs) if scope.repository_result else list(scope.legacy_code_diffs)
    if filters.ci_run_id is not None:
        ci_runs = [run for run in ci_runs if run.id == filters.ci_run_id]
    if filters.attempt is not None:
        ci_runs = [run for run in ci_runs if run.run_attempt == filters.attempt]
    if filters.commit_sha is not None:
        ci_runs = [run for run in ci_runs if run.commit_sha.lower() == filters.commit_sha]
        if filters.ci_run_id is None and filters.attempt is None:
            code_diffs = [diff for diff in code_diffs if diff.commit_sha.lower() == filters.commit_sha]

    if scope.repository_result is not None:
        result = RepositorySnapshotResult(
            repository_state=scope.repository_result.repository_state,
            code_diffs=code_diffs,
            ci_runs=ci_runs,
            is_complete=scope.repository_result.is_complete,
            incomplete_reason=scope.repository_result.incomplete_reason,
        )
        return RepositorySourceScope(
            source_scope=scope.source_scope,
            source_item_key=scope.source_item_key,
            repository_identity=scope.repository_identity,
            target_commit_sha=scope.target_commit_sha,
            repository_result=result,
        )
    return RepositorySourceScope(
        source_scope=scope.source_scope,
        source_item_key=scope.source_item_key,
        repository_identity=scope.repository_identity,
        target_commit_sha=scope.target_commit_sha,
        repository_result=None,
        legacy_code_diffs=tuple(code_diffs),
        legacy_ci_runs=tuple(ci_runs),
    )


def scope_matches_filters(
    scope: RepositorySourceScope,
    filters: RepositoryHistoryFilter,
    *,
    project_id: str,
) -> bool:
    if filters.project_id is not None and filters.project_id != project_id:
        return False
    identity = scope.repository_identity
    if filters.repository_id is not None:
        if identity is None or identity.id != filters.repository_id:
            return False
    if filters.repository_owner is not None:
        if (identity is None
                or identity.owner.casefold() != filters.repository_owner.casefold()
                or identity.repo.casefold() != filters.repository_name.casefold()):
            return False

    ci_runs = list(scope.repository_result.ci_runs) if scope.repository_result else list(scope.legacy_ci_runs)
    code_diffs = list(scope.repository_result.code_diffs) if scope.repository_result else list(scope.legacy_code_diffs)
    if filters.ci_run_id is not None:
        ci_runs = [run for run in ci_runs if run.id == filters.ci_run_id]
    if filters.attempt is not None:
        ci_runs = [run for run in ci_runs if run.run_attempt == filters.attempt]
    if filters.commit_sha is not None:
        if filters.ci_run_id is not None or filters.attempt is not None:
            ci_runs = [run for run in ci_runs if run.commit_sha.lower() == filters.commit_sha]
            if not ci_runs:
                return False
        elif scope.target_commit_sha is not None:
            if scope.target_commit_sha != filters.commit_sha:
                commits = {run.commit_sha.lower() for run in ci_runs}
                commits.update(diff.commit_sha.lower() for diff in code_diffs)
                if filters.commit_sha not in commits:
                    return False
        else:
            commits = {run.commit_sha.lower() for run in ci_runs}
            commits.update(diff.commit_sha.lower() for diff in code_diffs)
            if filters.commit_sha not in commits:
                return False
    if filters.ci_run_id is not None or filters.attempt is not None:
        if not ci_runs:
            return False
    return True


__all__ = [
    "RepositorySourceScope",
    "artifact_identity_key",
    "explicit_repository_identity",
    "extract_artifact_observations",
    "iter_repository_source_scopes",
    "scope_matches_filters",
]
