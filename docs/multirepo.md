# MultiRepo collection: independent pins and bounded budgets

MultiRepo collection pins each configured repository independently for one run, keeps code and CI results in repository-scoped containers, and applies shared project plus per-repository source budgets. Existing singleton collection stays on its legacy path. T2 collection still does not select or download CI artifacts; T3 now provides a standalone artifact-validation core, pending T4 integration.

## Quick path

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

A repository entry requires a unique `id`, `owner`, `repo`, and `branch`. An optional `commit_sha` is an explicit 40- or 64-character hexadecimal pin. Without it, collection resolves that repository's configured branch at the start of each run. Pins are not shared across the collection.

`Settings.from_env_and_file` keeps its existing precedence: explicit overrides, process environment, dotenv, layered YAML, then defaults. Budget values can be set in `sources.github.collection_budget` (`sources.github.budget` is an accepted alias) or with matching environment variables such as `MULTIREPO_PROJECT_MAX_HTTP_CALLS` and `MULTIREPO_REPOSITORY_MAX_ESTIMATED_TOKENS`.

## Collection behavior

| Boundary | Behavior |
|---|---|
| Branch pinning | A fresh GitHub code adapter resolves each branch once. Diff requests then use that full SHA, and Actions receives the same target. Reusing the MultiRepo collector starts a new run and resolves branch tips again. |
| Explicit pin | The configured repository SHA is used only for that repository. No branch-tip request is made for that pin. |
| Contradictory response | If a diff response SHA differs from its fixed target, that repository's code collection fails; it is never silently repinned. |
| CI mismatch | A returned run keeps its actual SHA and repository. Non-target run metadata is marked `certifies_target: false`, excluded from the snapshot's target-run list, and cannot certify the current target. |
| Repository isolation | Every scoped diff/run and execution record carries the matching repository identity and SHA/provenance. Evidence IDs are prefixed with the repository ID, including same-path/same-SHA cases. Existing contradictory child identity is rejected. |
| Shared project inputs | Task/Trello and policy sources are fetched once per project collection and their valid results remain available when one repository fails. |
| Partial failure | A denied, unavailable, malformed, or over-budget repository is reported independently; results already collected for other repositories are retained. A 404 means not found, not proof that permissions were denied. |

The collection path has no arbitrary primary repository and does not expose a shared `target_commit_sha`. The legacy `GITHUB_OWNER`, `GITHUB_REPO`/`GITHUB_REPOSITORY`, `GITHUB_BRANCH`/`GITHUB_REF`, and `GITHUB_COMMIT_SHA` fields remain supported when no repository collection is configured. When a collection is present, legacy values may corroborate exactly one matching entry but cannot choose one or distribute a shared SHA. In real-source mode, direct singleton code/CI factory calls reject a collection to avoid selecting an implicit primary; fixture mode keeps its fixture-only adapter even when a collection is configured.

## Budget defaults and accounting

The defaults below apply only to MultiRepo collection; legacy singleton adapters keep their existing request, file, response, and timeout limits.

| Budget | Project | Per repository |
|---|---:|---:|
| HTTP GET calls | 128 | 48 |
| Changed files | 6,000 | 3,000 |
| Actual response bytes | 64 MiB | 30 MiB |
| Elapsed time | 300 seconds | 180 seconds |
| Estimated source-context tokens | 100,000 | 50,000 |

All permitted HTTP GET requests, including shared project sources, count against the project call/deadline/byte caps; code and Actions requests also count against their repository caps. The bounded JSON transport charges decoded bytes as streamed, before retaining each chunk, while preserving its HTTPS origin checks, per-response cap, sanitized errors, and reject-all-redirect behavior. `Content-Length` is not treated as decoded-body length or an EOF marker; JSON is parsed only after the stream ends. To distinguish exact-cap EOF from overflow, the transport may read at most one positive, bounded extra chunk; empty chunks do not consume that probe, and deadlines are checked on every iteration. Every yielded byte is charged, but an overflow chunk is never retained. Aggregate exhaustion receipts identify only project/repository byte caps actually reached, not a response-local cap. Caller-supplied GET transports remain supported and are charged from their reported `HttpResponse.bytes_received`.

The file count is the number of changed paths observed in code-diff evidence. Token units use the existing serialized-context estimator (approximately three characters per token); they are **estimates**, not model token usage. Receipts expose HTTP calls, changed files, response bytes, elapsed seconds, estimated token units, and exhaustion state. If a cap stops further retrieval, available partial results stay in the result and the source records show the failure.

## Access gate and evidence limits

`source_mode: fixtures` keeps the fixture-only path. In the analysis factory, remote sources still require `allow_remote_ai` and non-fake mode; MultiRepo remote collection also requires the existing GitHub token gate. When that gate is closed, fixture results remain unscoped and are not relabeled as evidence from configured private repositories. Operational defaults remain unchanged, including `external_writes_enabled: true` in project configuration and the API default `True`. Run tests and task analyses with `EXTERNAL_WRITES_ENABLED=false`; collection adapters expose GET only. Existing human approval, authorization, idempotency, and audit controls remain in place.

Actions workflow metadata is not an artifact or metrics certificate. This T2 collection unit does not select run attempts, fetch binary artifacts, infer test/coverage metrics from a green workflow, or rewrite workflows. The standalone T3 artifact core in [CI metrics artifacts](ci-metrics.md) accepts a bounded reports-tree ZIP with exactly one safe `ci-metrics.json` document, validates every member, and checks suite aggregates and coverage field relationships without converting unknowns to zero. Deterministic run selection and integration into `GitHubActionsSourceAdapter` and the MultiRepo factory remain T4 work.

## Public result contracts

`RepositoryIdentity`, `RepositoryConfiguration`, `RepositoryAnalysisState`, and `RepositoryProvenance` are defined in `agente_gestion.domain.repositories`. `SourceCollectionResult.repository_results` and `WeeklySnapshot.repository_results` contain scoped repository results; the legacy top-level code/CI lists remain empty for a MultiRepo result rather than assigning one repository as primary. Shared task/policy data remains project-scoped. Budget receipts are additive and omitted when absent, so old singleton result serialization retains its prior shape.

Repository provenance is source metadata, not proof that an artifact or metric was validated. Historical records without recoverable origin remain unknown. A non-target CI run can be preserved for display, but the explicit non-target marker does not validate it.

## Verification and next stage

The focused deterministic tests use fake GET transports, in-memory artifact fixtures, socket denial before application imports, and `EXTERNAL_WRITES_ENABLED=false`. T3's binary port can be wrapped by the shared `BudgetedHttpGetTransport`/`CollectionBudgetLedger` to account each bounded redirect hop and its observed bytes without relaxing the current JSON GET transport. T4 must reuse the existing ledger and must not reset repository counters.
