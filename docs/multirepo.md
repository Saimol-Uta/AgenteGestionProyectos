# MultiRepo collection: independent pins and bounded budgets

MultiRepo collection pins each configured repository independently for one run, keeps code and CI results in repository-scoped containers, and applies shared project plus per-repository source budgets. Its Actions source now selects and validates CI metrics artifacts through the real analysis factory path. Existing singleton and fixture collection keep their legacy paths.

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

Actions workflow metadata is not an artifact or metrics certificate. The T4 MultiRepo wrapper uses the newest-first run chronology, applies retry-attempt comparisons only within a single run ID, and never replaces a newer pending or failed run with an older green one. Each configured `quality_workflow_path` flows through the repository factory; without a selector, competing or unknown workflow paths produce an explicit noncertifying result. Artifact metadata is read from bounded canonical endpoint pages, and all JSON/binary requests share the existing per-project and per-repository ledger. T3's [CI metrics artifact core](ci-metrics.md) validates the bounded reports-tree ZIP, producer schema, semantic relationships, and full repository/SHA/run/attempt provenance before typed metrics are attached to `CIRunResult` and scoped collection results. Coverage basis and unknown `null` values remain producer-specific; no integration metric or global repository is invented.

## Public result contracts

`RepositoryIdentity`, `RepositoryConfiguration`, `RepositoryAnalysisState`, and `RepositoryProvenance` are defined in `agente_gestion.domain.repositories`. `SourceCollectionResult.repository_results` and `WeeklySnapshot.repository_results` contain scoped repository results; the legacy top-level code/CI lists remain empty for a MultiRepo result rather than assigning one repository as primary. Shared task/policy data remains project-scoped. Budget receipts are additive and omitted when absent, so old singleton result serialization retains its prior shape.

Repository provenance is source metadata, not proof that an artifact or metric was validated. Historical records without recoverable origin remain unknown. A non-target CI run can be preserved for display, but the explicit non-target marker does not validate it.

## Repository analysis, evaluation, and reporting

When `WeeklySnapshot.repository_results` is nonempty, the orchestrator analyzes each pinned repository serially. Project tasks, policies, and the monitoring draft remain shared and are fetched/generated once; each quality, verifier, and policy-evaluation context receives only that repository's code, target SHA, CI, and evidence. Namespaced stage keys (`quality:<repository-id>`, `verifier:<repository-id>`, `evaluation:<repository-id>`) retain auditable per-repository outcomes while the legacy `quality` and `verifier` stage summaries remain available.

| Evidence | T5 behavior |
|---|---|
| Identity and pin | Source snippets and CI must match the configured identity and fixed target SHA. Foreign model finding identity/provenance and verifier references are rejected rather than relabeled. Finding and evidence references include repository and SHA namespaces. |
| CI metric eligibility | Counts and coverage are used only from certified `ci-metrics.v1` values matching repository, SHA, run, attempt, artifact name, and workflow status. Workflow outcome remains separately visible; a valid failed workflow cannot evaluate as green. |
| Safe report projection | If repository-analysis eligibility rejects certified artifact metrics, the public report omits those nested values, clears `certifies_metrics`, and leaves numeric metrics `null`. A workflow-status mismatch also clears `workflow_status_matches` and gives a report-projection reason. The original collected snapshot is unchanged; document validation remains distinct from report eligibility. Valid sibling metrics remain certified. |
| Missing or partial data | Unknown values stay `null`; known zero counts remain zero. Denied, unavailable, incomplete, or budget-limited repositories keep separate status/reason fields without discarding valid sibling results or treating unknown as approval. |
| Coverage and summaries | Each repository retains its exact producer coverage basis, including frontend exclusions and E2E status. The consolidated report includes per-repository JSON and Markdown details but does not average coverage or select a project-wide primary repository. |
| Compatibility | An empty `repository_results` collection uses the pre-existing monorepository flow and report shape. Human decisions remain pending until explicitly recorded; this analysis path adds no code-action or notification execution. |

The MultiRepo analysis shares the existing run-level call coordinator. Per-repository requests are sequential; quality/verifier output reservations are divided from their existing stage caps, and each prompt retains the existing context-window and timeout bounds. Context estimates and provider-measured token usage remain distinct telemetry; this is not a provider tokenizer guarantee. A budget-exhausted repository is explicitly marked for attention while already collected analysis remains reportable.

## Persisted repository history (T6)

`StoragePort.query_repository_history(RepositoryHistoryFilter(...))` is an optional storage capability; it does not make non-SQLite `StoragePort` implementations abstract. SQLite returns one record per captured repository result. Legacy items with explicit identity are grouped by repository identity; items without identity remain separate unknown records. Records include capture ID, fixed SHA when available, exact analysis-run/report references when recoverable, source artifacts, scoped report results, and explicitly attributable evidence. Use `get_snapshot_capture(capture_id)` and `get_report(report_id)` for the original capture/report and human-decision state.

Filters are AND-combined: `project_id`, `repository_id`, a paired `repository_owner`/`repository_name`, full `commit_sha`, `ci_run_id`, and/or `attempt`. At least one filter is required. Run and attempt filters match the same stored Actions run; `attempt` is the actual `CIRunResult.run_attempt`, never an optional attempt copied from the producer document. Example:

```python
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.domain.repository_history import RepositoryHistoryFilter

store = SQLiteStorageAdapter("path/to/an-existing-migrated.sqlite", read_only=True)
rows = store.query_repository_history(RepositoryHistoryFilter(
    project_id="Hormigestion",
    repository_id="backend",
    commit_sha="<full-40-or-64-character-sha>",
    ci_run_id="38833954051",
    attempt=2,
))
```

Artifact identities are keyed by project, repository ID and owner/name, full commit SHA, Actions run ID, actual attempt, artifact ID, and the validated artifact name. Re-saving the same capture/artifact is idempotent; distinct captures and analysis runs remain distinct, and re-observations link to each capture without overwriting prior evidence or human decisions. Raw source artifact validation is indexed separately from T5 report eligibility: a source document that validated cannot upgrade a report projection that was rejected. Partial repository states and unknown metrics remain scoped and nullable.

Writable SQLite initialization adds artifact-identity/observation tables and indexes, then indexes only complete, validated provenance already explicit in immutable captures. It does not rewrite snapshots, reports, findings, evidence, verifications, or decision rows, and never fills historical identity from present project settings. Read-only initialization performs no migration or directory creation. On a pre-T6 schema, `query_repository_history` raises `NotImplementedError` explaining that a writable SQLite migration is required; existing snapshot/report reads remain available. Historical records without an unambiguous validated repository identity are returned with identity `None`, not assigned a synthetic primary repository.

## API history, filters, and preparation (T7)

The versioned API adds a project-scoped repository-history view without changing the existing monorepository payloads:

| Method and path | Access | Behavior |
|---|---|---|
| `GET /api/v1/projects/{project_id}/repository-results` | Operator session with `audit:read` and project scope | Returns the currently configured repository collection plus paginated persisted history. |
| `GET /api/v1/runs/{run_id}/findings` | Existing public read access | Keeps the existing paginated findings view and accepts optional repository/commit/CI filters. |

Both routes accept the AND-combined filters `repository_id`, the paired `repository_owner` and `repository_name`, full `commit_sha` (40 or 64 hexadecimal characters), `ci_run_id`, and positive `attempt`. They also accept `page` (default 1, minimum 1) and `page_size` (default 25, range 1–100). CI run and attempt filters refer to the same stored run and use its actual attempt. Supplying only one owner/name field, or any invalid filter, returns HTTP 422; the repository-history route uses `filtros_multirepo_invalidos` for filter-shape errors. Missing project scope returns 403; a missing configured project returns 404.

The repository-history response keeps `repositories` (current safe configuration) separate from `items` (historical observations). Each item retains its exact project/capture/repository identity when known, commit-resolution/access/availability states, CI attempts, source problems, findings, and verification state. Source artifact validation and report eligibility remain separate: report metrics and coverage are exposed only when the report-side artifact certifies them. Uncertified or unavailable values stay `null`; known zeroes stay zero. Unknown legacy origin stays unknown, and no configured repository is selected as a historical primary.

If the existing read-only storage adapter does not expose the T6 history capability, the route still returns HTTP 200 with `availability: "unknown"`, an explanatory `message`, and an empty history page. It does not create a database, migrate a legacy database, or write during GET. Unauthenticated requests return 401; sessions without project scope return 403. Public findings preserve their existing access contract; repository fields are included only when origin is recoverable. Evidence and code excerpts are filtered to the finding's project, repository, commit, path, and line where those values are available.

Example filters:

```text
GET /api/v1/projects/HORMIGESTION/repository-results?repository_id=frontend&ci_run_id=38833954051&attempt=2
GET /api/v1/runs/run-123/findings?repository_owner=Josu-F1&repository_name=hormigestion-backend&commit_sha=<full-sha>
```

Analysis preparation keeps the existing session, expiry, remote-consent, idempotency, and audit controls. Its per-repository preflight lists configured identity, branch, configured (not resolved) SHA, workflow selector, credential presence, and the explicit initial states `commit_resolution_status: pending`, `access_status: unknown`, and `availability_status: unknown`; it performs no repository access check. The configuration fingerprint includes the complete ordered repository collection and every repository selector. Changing a sibling repository, branch, pin, or workflow therefore invalidates the prepared configuration; confirmation returns HTTP 409 (`configuracion_cambiada`) and cannot use a fingerprint for another repository. No credential or private filesystem path is returned.

## Finding decisions and origin-bound code actions (T8)

`POST /api/v1/runs/{run_id}/findings/{finding_id}/decision` retains the existing operator session, `quality:decide` project capability, allowed-origin/CSRF checks, explicit human decision, and required `Idempotency-Key`. It accepts the stored finding ID (including a MultiRepo namespace such as `frontend:f-123`), decision, optional code override, optional target branch, and reason. Repository and commit are not request fields: the server binds each decision to the project/run/finding in the stored report and resolves repository identity, full target SHA, file path, captured full-source SHA-256, excerpt hash, and stored base ref from the report and snapshot.

The public finding list and detail (`GET /api/v1/runs/{run_id}/findings` and `GET /api/v1/runs/{run_id}/findings/{finding_id}`) preserve the same legacy or namespaced finding ID end-to-end. Finding IDs use a finding-specific, colon-delimited grammar; the generic project/run/path and authorization guards remain unchanged, and malformed finding IDs stay rejected.

| Decision | Required stored facts | Side effect |
|---|---|---|
| `approve` | One unambiguous stored repository origin; matching current project repository ID/owner/name/base branch; exact finding/run/project and commit; complete captured file with a valid source hash; unique exact excerpt; supported syntax | Creates a new branch only on that repository and pinned source SHA. A changed live base ref is refused before remote write. When live execution is not selected, the sandbox is repository-scoped. |
| `reject`, `request_changes` | Exact stored project/run/finding decision target; any available repository/SHA origin is retained, but unknown historical source is not guessed | Records the decision/audit only; it does not call the code-action port. |

Ambiguous or contradictory provenance, changed repository configuration, a mismatching source hash/excerpt, incomplete source, or unsupported syntax fails closed before approval reservation. A live base ref that no longer equals the pinned source commit is detected after the durable request is reserved but before any remote branch/file write, and its failure is replayable. A client cannot redirect an action by supplying a different repository or SHA. For namespaced finding IDs, generated branch names are safe Git ref components; isolated sandbox outputs are namespaced by repository identity so equal path/branch names in sibling repositories do not collide.

Decision receipts bind the idempotency request to the stored origin as well as the decision body. Repeating the same scoped request reuses its receipt without calling the adapter again; reusing its key for a different finding/repository is a conflict. Result and audit-detail projections include repository ID (when known), owner/name, full source commit SHA, and scoped finding ID. Historical receipts without origin are never upgraded from present configuration; an old approval receipt without a verifiable origin is not replayed as a successful approval. Unknown historical origins remain unknown, and no UI or additional approval path is introduced here.

## Verification and next stage

The focused deterministic tests use fake JSON and binary GET transports, synthetic producer-shaped ZIP fixtures, socket denial before application imports, and `EXTERNAL_WRITES_ENABLED=false`. The artifact validator's binary port is wrapped by the existing `BudgetedHttpGetTransport`/`CollectionBudgetLedger`; each bounded request/redirect hop and observed byte is charged without resetting repository counters or relaxing JSON GET checks. Review the public integration test through `create_analysis_composite_source` and `SourceCollectionResult.repository_results`, then use the focused T4 selection/pagination tests and retained T3/T2 suites.
