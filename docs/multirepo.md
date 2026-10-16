# MultiRepo configuration and domain contracts

Projects can declare several independent GitHub repositories without choosing a primary one. Stage 1 adds configuration and model contracts only: commit resolution at analysis time, collection, CI artifact validation, orchestration, storage, and API endpoints remain future work.

## Configure repositories

Use `sources.github.repositories` in project YAML, or set `GITHUB_REPOSITORIES` to JSON with the same entry shape:

```yaml
sources:
  github:
    repositories:
      - id: backend
        owner: Josu-F1
        repo: hormigestion-backend
        branch: develop
        quality_workflow_path: .github/workflows/ci.yml
      - id: frontend
        owner: Josu-F1
        repo: hormigestion-frontend
        branch: develop
        quality_workflow_path: .github/workflows/ci-quality.yml
```

Each entry requires a unique `id`, `owner`, `repo`, and `branch`. `commit_sha` is optional and, when supplied, must be a full 40- or 64-character hexadecimal SHA. It is an explicit configuration pin; omitting it does not resolve a branch or claim that a commit was analyzed. `quality_workflow_path` is an optional path selector under `.github/workflows/`.

`config/hormigestion.yaml` uses the two entries above, both on `develop`, and does not carry forward the old fixed SHA. Existing operational defaults, including `external_writes_enabled: true`, are unchanged. Tests and local verification for this stage run with `EXTERNAL_WRITES_ENABLED=false` and block network sockets.

## Precedence and legacy settings

Configuration keeps the existing source precedence: explicit `Settings.from_env_and_file` overrides, process environment, dotenv, layered YAML, then defaults. `GITHUB_REPOSITORIES` uses that same environment precedence over YAML.

The legacy singleton variables remain supported when no repository collection is configured:

- `GITHUB_OWNER`
- `GITHUB_REPO` / `GITHUB_REPOSITORY`
- `GITHUB_BRANCH` / `GITHUB_REF`
- `GITHUB_COMMIT_SHA`

Without a collection, the legacy `GITHUB_REPOSITORY=owner/repository` form keeps its historical normalization: the owner encoded in the full repository value is used, even if a separate `GITHUB_OWNER` differs. When a collection is present, raw legacy owner/repository contradictions are rejected before normalization; any supplied legacy values must identify exactly one entry and match its configured values. They cannot replace collection entries, pick a primary repository, or distribute one legacy SHA across entries. A legacy SHA cannot match an entry whose `commit_sha` is unset. Ambiguous or contradictory coexistence is rejected.

`Settings.github_repositories` exposes the validated tuple or `None` when no collection was supplied. The API settings loader exposes the same entries on `ProjectConfig.repositories`; a multi-repository project does not populate the legacy `ApiSettings.github_repository` field with an arbitrary entry.

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

## Not implemented in this stage

The models define status vocabulary and additive data shapes only. They do not resolve branch tips, test repository permissions, mark repositories available, download or validate artifacts, evaluate findings, aggregate reports, or persist/API-serve repository results. Later stages must keep each resolved SHA and its evidence scoped to its repository and preserve unknown metrics as null.

This stage adds no CLI commands or options. The public settings and domain contract tests are the intended regression coverage for shared configuration parsing.
