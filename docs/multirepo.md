# MultiRepo budgeted source transport

Projects can declare several independent GitHub repositories without choosing a primary one. Configuration, model contracts, and source budget settings are available. A reusable ledger and bounded JSON transport now enforce quotas when explicitly composed. Automatic MultiRepo branch pinning/factory wiring, CI artifacts, orchestration, storage, and API endpoints remain future work.

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
    collection_budget:
      project_max_http_calls: 128
      repository_max_http_calls: 48
      project_max_files: 6000
      repository_max_files: 3000
      project_max_response_bytes: 67108864
      repository_max_response_bytes: 31457280
      project_timeout_seconds: 300
      repository_timeout_seconds: 180
      project_max_estimated_tokens: 100000
      repository_max_estimated_tokens: 50000
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

## Collection budget settings

Declare source budgets in `sources.github.collection_budget` (`sources.github.budget` is an accepted alias). Every key in the YAML example maps to an environment variable with the `MULTIREPO_` prefix and uppercase spelling, for example `MULTIREPO_PROJECT_MAX_HTTP_CALLS`. Existing settings precedence is unchanged.

| Budget | Project | Per repository |
|---|---:|---:|
| HTTP GET calls | 128 | 48 |
| Changed files | 6,000 | 3,000 |
| Actual response bytes | 64 MiB | 30 MiB |
| Elapsed time | 300 seconds | 180 seconds |
| Estimated source-context tokens | 100,000 | 50,000 |

Integer caps must be positive integers, not booleans or fractional values. Timeout budgets must be positive finite numbers. Legacy singleton serialization omits the new budget fields when no repository collection is configured.

`agente_gestion.domain.source_limits.SourceBudgetReceipt` defines an additive usage record for HTTP calls, changed files, streamed bytes, elapsed seconds, estimated tokens, and exhaustion. Token units are serialized-source estimates, not measured model usage. The runtime hooks below enforce these quotas; automatic MultiRepo collection is added by the next unit.

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

## Budgeted JSON transport

`agente_gestion.adapters.sources.collection_budget.CollectionBudgetLedger` keeps one project ledger plus independent repository counters. `BudgetedHttpGetTransport` reserves each GET, passes a request-scoped hook to `BoundedGetTransport`, and returns project/repository receipts. Caller-injected GET ports remain supported and are charged using `HttpResponse.bytes_received`.

The JSON transport preserves HTTPS origin validation, sanitized errors, per-response limits, and rejection of redirects. It accounts for actual decoded bytes before retaining chunks, not declared `Content-Length`, and parses JSON only after iterator EOF. At an exact cap it may read one bounded positive extra chunk, at most `min(65536, stream_limit)`; empty chunks do not spend that probe. Every yielded byte is charged and an overflow chunk is discarded. Deadlines are checked before every iterator advancement, including after empty chunks. A new request at an exhausted byte budget is rejected before network access.

Aggregate byte receipts identify only project/repository caps actually reached; a response-local limit alone does not exhaust those aggregate scopes. Exact-cap equality exhausts the reached scope even if that final request succeeds. Errors close the response/session and clear the active budget context. Legacy adapters retain their configured singleton limits.

The nine focused streaming tests cover exact caps, empty chunks, gzip decoded-body handling, scope-specific receipts, positive overflow, deadlines, cleanup, and pre-request rejection. Runtime MultiRepo composition and CI metrics remain separate later units.

## Not implemented in this stage

The domain models define status vocabulary and additive data shapes; the budgeted transport above adds explicit quota enforcement. Automatic collection does not yet resolve branch tips, test repository permissions, mark repositories available, download or validate artifacts, evaluate findings, aggregate reports, or persist/API-serve repository results. Later stages must keep each resolved SHA and its evidence scoped to its repository and preserve unknown metrics as null.

This stage adds no CLI commands or options. The public settings and domain contract tests are the intended regression coverage for shared configuration parsing.
