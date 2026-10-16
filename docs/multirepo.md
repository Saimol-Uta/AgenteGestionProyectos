# MultiRepo domain contracts

These additive contracts preserve independent repository identity and nullable provenance without changing historical singleton payloads. Collection configuration is delivered in a separate work unit.

## Public domain contracts

The shared types live in `agente_gestion.domain.repositories`:

| Type | Purpose and public fields |
|------|---------------------------|
| `RepositoryIdentity` | Stable `id`, `owner`, and `repo`; no run-specific SHA. |
| `RepositoryConfiguration` | Identity plus `branch`, optional `commit_sha`, and optional `quality_workflow_path`. |
| `RepositoryAnalysisState` | Run-scoped `identity`, `branch`, `target_commit_sha`, `commit_resolution_status`, `access_status`, and `availability_status`. |
| `CommitResolutionStatus` | `pending`, `resolved`, or `failed`. A resolved state requires a target SHA. |
| `RepositoryAccessStatus` | `unknown`, `allowed`, or `denied`; unknown is not approval. |
| `RepositoryAvailabilityStatus` | `unknown`, `available`, or `unavailable`, separate from access. |
| `RepositoryProvenance` | Optional recorded `repository_identity`, `commit_sha`, `source`, `workflow_path`, `run_id`, and `run_attempt`; metadata alone does not validate evidence. |

The six extended models add only optional metadata or empty-by-default containers, so older singleton inputs continue to load without fabricated provenance:

- `CodeDiffSnippet`: optional `repository_identity` and `provenance`.
- `CIRunResult`: optional repository identity/provenance, `workflow_path`, `run_attempt`, `artifact_id`, and `artifact_name`.
- `CandidateFinding`: optional `repository_identity` and `provenance`.
- `WeeklySnapshot`: empty-by-default `repository_results` of `RepositorySnapshotResult`.
- `SourceCollectionResult`: empty-by-default `repository_results` of `RepositorySourceCollectionResult`; each result can retain scoped diffs, CI runs, evidence, execution records, analysis state, and nullable completeness.
- `FinalReport`: empty-by-default `repository_results` of `RepositoryReportResult`, with independent CI, policy, finding, and nullable coverage fields.

Absent historical identity, SHA, workflow, attempt, or provenance remains `None`. Empty per-repository result collections do not change singleton serialization fields. Repository result containers allow partial outcomes; their presence is not proof that a source or artifact was successfully validated.

## Scope

These types and containers alone do not resolve commits, check permissions, collect sources, validate CI artifacts, evaluate findings, persist results, or authorize actions. Missing provenance stays unknown; a container is not certification. Configuration and runtime wiring are separate work units.
