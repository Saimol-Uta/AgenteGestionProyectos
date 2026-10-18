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
| Partial code evidence | Missing patch text keeps the repository collection incomplete even when a full-file read succeeds. Full-file content is evidence, not a diff; validated sibling diffs and evidence remain available without marking the collection complete. |
| CI trust gate | Missing-patch partial code may still query CI only after repository identity and the pinned SHA are trusted. Authentication, permission, provenance, and validation failures remain fail-closed for partial code and CI. |

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

## UI presentation and isolation (T9)

The existing project, analysis, quality, and report views consume the T7 repository-results contract; they do not create a repository primary or combine sibling coverage. Repository history is requested only with the existing `audit:read` capability. Configured repositories remain separate from observed access, availability, and evidence states, so configuration never implies permission.

| UI evidence | Presentation rule |
|---|---|
| Repository identity | Keep configured and historical identities distinct. Unrecoverable legacy origin remains unknown; contradictory or absent history is not assigned to a configured sibling. |
| CI and findings | Show each workflow, run ID, actual attempt, workflow commit, and repository-scoped findings independently. Repository filters use the existing findings endpoint query fields. |
| Metrics | Preserve nullable producer values and known zeroes. Show coverage only with its producer-reported basis; do not average repositories or infer a project-wide percentage. Uncertified metrics remain unavailable, not green. |
| Partial evidence | Keep valid sibling results visible when another repository is denied, unavailable, or incomplete. Unknown access and evidence states remain explicit. |
| Query freshness | A tracked analysis job becoming terminal invalidates the active project's repository-results queries along with the existing project summaries; no separate polling loop is added. |
| Query and selection scope | Bind cached data and selected finding details to project, run, and repository filter. Changing scope closes the old detail instead of carrying its excerpt into a new context. When row or filter scope identifies a repository, the finding detail must report that exact repository ID before its content or controls render. |
| Human decisions | Existing capability, confirmation, CSRF, idempotency, and action request bodies remain unchanged. The filter ID is only a read/cache scope, never an origin assertion or action field; unfiltered legacy unknown-origin behavior remains unchanged, and the server resolves origin from stored evidence. |

## T10 acceptance and compatibility matrix

This matrix separates retained stage evidence from the final T10 proof. The parent’s final full discovery (`8351b0d48414976a88e1`) passed 1017 tests with 0 failures, 0 errors and 1 intentional remote-Gemini skip (149.885s; wrapper 151.362s; exit 0). That immutable snapshot predates the final portability path change in the acceptance test; its CI schema bytes are identical to the new repository-local oracle fixture. The new portable-version focused proof (`f454844e9d5615e116d4`) passed 2/2 under the guarded runner. Historical failed discovery `c28fcbb24f784856b75f`, bounded regression `a041aa6d49dcf2e5030e`, causal follow-up `ffc51e1f490dedec4d0a`, and original focused proof `93b5914ea2c26c551900` remain preserved. The full suite was not rerun after the path-only portability change; no live/private interoperability or Python-version matrix is claimed.

| Spec | Evidence and bounded verdict | Remaining limit / proof state |
|---|---|---|
| S1 — Audit and reuse | T1–T9 delivered as bounded work units on `feature/multirepo`; source composition, storage, API, report, and UI retain their existing adapters and contracts. | T10's isolated source map is recorded in this document and `odd/tasks/multirepo.md`; no new broad audit was run. |
| S2 — Configuration | T1 configuration/domain and T7 API-preflight work are complete; retained coverage is in `tests/test_multirepo_config.py` and `tests/test_multirepo_api_jobs.py`. Legacy singleton and independent repository pins remain supported; the phase-2 settings test passed in causal follow-up `ffc51e1f490dedec4d0a`. | Verified in the recorded Python/dependency environment; no minimum-version matrix was run. |
| S3 — Collection | T2 shared budgets and T4 production composition/selection corrections are complete; retained behavior tests are in `tests/test_multirepo_collection.py` and the source-adapter suites. | No live GitHub fetch is part of this proof. |
| S4 — CI v1 | T3 artifact validation and T4 Actions composition are covered by producer-shaped fixtures. The acceptance test now validates its generated artifact against independent repository-local oracle `tests/fixtures/ci-metrics.v1.schema.json`, copied byte-for-byte from the cached canonical schema. SHA-256 is `104d4b79b084e2c79fa91d62e3845dbf9e8c87085c3b20106f529b9bc9a005b4`; canonical Git blob is `09033371c03dd9458c45065b9fb1930bbff230ac`. | The full run used the pre-portability test path but identical schema bytes. Live private producer artifacts and credentials remain unavailable and intentionally unused. |
| S5 — Orchestration/reporting | T5 is complete; retained suites cover scoped outcomes, unknown values, and non-averaged coverage. The bounded acceptance run observes source collection through orchestration, reporting, and SQLite with cached producer-shaped inputs. | No live producer interoperability is claimed. |
| S6 — Storage/API | T6 and T7 are complete; storage/API suites and all eight selected analysis-job cases passed in the final full discovery. | The final run used the pre-portability snapshot; no database/service behavior changed in the path-only follow-up. |
| S7 — React | T9 completed with the exact 8/8 Playwright result, typecheck/build exit 0, and same-verifier bounded PASS (ODD L86). | Not rerun for T10; no UI changes are in scope. |
| S8 — Safety | Final full-discovery and portable-focused runner summaries show isolated environment, `EXTERNAL_WRITES_ENABLED=false`, dotenv disabled, original settings-loader/provenance, real clock and UUID, intact in-process and child socket guards, and zero socket attempts. Acceptance ports remain fake-only. | No live GitHub/Trello/SMTP/provider calls are authorized or represented. |
| S9 — Tests | The final immutable full discovery ran 1017 tests: 0 failures, 0 errors, 1 intentional remote-Gemini skip; 149.885s test time, 151.362s wrapper time, exit 0. Loader/setup errors were empty; environment, provenance, settings-loader, real-clock/UUID, guard-intactness, and zero parent/child socket-attempt checks passed. | Full snapshot `8351b0d48414976a88e1` predates the final oracle-path change. Portable focused snapshot `f454844e9d5615e116d4` independently passed the two acceptance cases with the byte-identical repository-local schema fixture. Historical failed runs remain below; no full rerun after the path-only change. |
| S10 — Acceptance | Final portable-version focused proof `f454844e9d5615e116d4`: both acceptance cases passed (0.990s test time; exit 0). They compose real settings/source factory, MultiRepo collector, orchestrator, reporting, and SQLite with cached producer-shaped fixtures and fake GET/binary/LLM ports. Existing assertions cover repository identity/SHA/metrics, isolation, private-frontend denial, partial/unknown reporting, role counts, transport behavior, and disclaimer semantics. The independent schema oracle is now a repository-local test fixture, not a `.runtime` dependency. | This is bounded cached-fixture/fake-port evidence, not live producer interoperability or private frontend access. The 1017-case full run used the prior test path with byte-identical schema content; it is not represented as executing the final path version. |
| S11 — Delivery | Work is retained in local feature-branch commits; no push, merge, publication, or external write was performed. | Push, pull-request creation, and merge require separate human authorization. |

### Final full-discovery record (pre-portability-path version)

The parent’s completed full discovery used immutable snapshot `8351b0d48414976a88e1`: 1017 tests, 0 failures, 0 errors, 1 intentional remote-Gemini skip, 149.885s test time, 151.362s wrapper time, exit 0. Summary: `.runtime/t10-acceptance/evidence/8351b0d48414976a88e1/discovery/summary.json`; parent outputs: `.runtime/t10-acceptance/parent-checks/final-discovery/discovery.{stdout,stderr,command.json,exit.txt}`. Loader errors were empty and top-level setup error was null. Parent/child socket attempts were empty, guards intact, and settings-loader provenance, environment, real clock and UUID checks passed. Stderr contained only the nonblocking Starlette/httpx warning and expected negative CLI diagnostics.

This full run predates the final portability-path change in `tests/test_multirepo_acceptance.py`: its snapshot resolved the oracle from `.runtime/multirepo-contract/`. The portable focused snapshot below uses the new repository-local test fixture. These oracle files are byte-identical (SHA-256 `104d4b79b084e2c79fa91d62e3845dbf9e8c87085c3b20106f529b9bc9a005b4`); the core and other Python test source bytes did not change. The 1017-test result is not represented as executing the final test path. No full rerun was performed.

### Retained full-discovery classification

The earlier failed output is `.runtime/t10-acceptance/evidence/c28fcbb24f784856b75f/discovery/stdout.txt` (summary: `summary.json`). Classifications below retain that historical run and its follow-up causes; the later full pass is recorded above. None of the old unresolved outcomes is called “pre-existing” or a product defect without causal evidence.

| Retained record(s) | Observed result and classification |
|---|---|
| **15 errors — `fixtures/fixture_1_standard` omitted from snapshot:** `tests.test_cli.CLITestCase`: `test_cli_env_file_option_loads_and_cli_overrides_beat_it`, `test_cli_explicit_fixture_flag_preserves_custom_project_id`, `test_cli_fake_run_generates_report_and_summary`, `test_cli_human_decision_registration`; `tests.test_cli_sources.CLISourceCheckTestCase.test_cli_fixture_override_takes_precedence_over_config_real`; `tests.test_e2e.FakeE2ETestCase`: `test_e2e_1_positive_flow`, `test_e2e_3_no_escalation_routing`, `test_e2e_5_cloud_unavailable_needs_attention`, `test_e2e_6_invalid_citations_semantic_validation_recovery`, `test_e2e_7_bounded_budget`, `test_e2e_8_idempotence_and_bounded_recovery`, `test_e2e_9_human_decision_flow`; `tests.test_sources_contracts`: `TestEvaluatorMissingCoverageInvariance.test_missing_coverage_percentage_results_in_unknown`, `TestPolicySourcePortAndFixtures.test_existing_fixture_snapshot_behavior_unchanged`, `TestPolicySourcePortAndFixtures.test_fixture_adapter_implements_policy_source_port`. | All 15 traces ended at `FixtureSourcesAdapter.load_fixture` with `FileNotFoundError`. This tracked root file was absent from snapshot `c28fcbb24f784856b75f`; the corrected runner copies it, and all 15 cases passed in selected regression `a041aa6d49dcf2e5030e`. |
| **2 errors — `fixtures/fixture_2_ci_mismatch` omitted:** `tests.test_domain.DomainMetricsAndPoliciesTestCase.test_fixture_ci_mismatch_behavior` and `tests.test_e2e.FakeE2ETestCase.test_e2e_4_wrong_sha_ci_mismatch`. | Both were `FileNotFoundError` from the same omitted tracked root fixture; both passed in regression `a041aa6d49dcf2e5030e` after the corrected runner copied it. |
| **2 errors — `fixtures/fixture_3_empty_and_unknown` omitted:** `tests.test_domain.DomainMetricsAndPoliciesTestCase.test_fixture_empty_and_unknown_metrics` and `tests.test_e2e.FakeE2ETestCase.test_e2e_2_no_code_routing`. | Both were `FileNotFoundError` from the same omitted tracked root fixture; both passed in regression `a041aa6d49dcf2e5030e` after the corrected runner copied it. |
| **1 error — `fixtures/fixture_4_mandatory_unknown_policies` omitted:** `tests.test_domain.DomainMetricsAndPoliciesTestCase.test_fixture_mandatory_unknown_policy_evaluation`. | `FileNotFoundError` from the omitted tracked root fixture; the case passed in regression `a041aa6d49dcf2e5030e` after the corrected runner copied it. |
| **2 errors — default fixture resolution in the omitted-fixture snapshot:** `tests.test_cli.CLITestCase.test_cli_default_run_without_fixture_uses_default_fixture_and_preserves_project_id` and `tests.test_monitoring_rule_config.MonitoringRuleConfigTests.test_real_fake_pipeline_keeps_sparse_fixture_inactive_and_legacy_cache`. | The old snapshot raised `FileNotFoundError` for `fixtures/proyecto-demo` and `fixtures/owner-project`. Both selected cases passed in `a041aa6d49dcf2e5030e` after the runner copied tracked root fixtures; no new fixture files or assertion changes were needed. |
| **1 loader error:** `tests.test_monitoring_gemini_runner`. | Import failed at `from test_actions import _make_run_payload` because snapshot `tests/` was not on `sys.path`. The corrected runner adds that path; all 14 module tests passed in the `a041aa6d49dcf2e5030e` regression. |
| **1 settings error:** `tests.test_phase2_settings.Phase2SettingsTests.test_environment_loads_live_actions_priority_mapping_and_smtp`. | `load_api_settings(env_file=None)` raised `ValueError: Invalid GitHub repository collection configuration` because this legacy-singleton test supplied `GITHUB_REPOSITORY=owner/repository` while inheriting the default HORMIGESTION MultiRepo YAML. With only the input changed to a temporary explicit legacy fixture project with a different project ID, the same test passed in `ffc51e1f490dedec4d0a`; original environment/security assertions remain unchanged. |
| **2 fixture-related failures:** `tests.test_domain.DomainMetricsAndPoliciesTestCase.test_fixtures_adapter_loads_four_marked_fixtures` and `tests.test_cli_sources.CLISourceCheckTestCase.test_cli_source_check_fixtures_mode`. | The adapter listed zero fixtures; source-check returned exit 1 instead of success. Both passed in the corrected 55-case regression with the tracked fixtures included. |
| **1 additional fixture-path CLI failure:** `tests.test_unified_runtime_config.UnifiedRuntimeConfigTests.test_legacy_owner_with_full_repository_alias_preserves_singleton_api_and_cli`. | Expected source-check exit 0, got 1 in the snapshot without root fixtures; the test passed in regression `a041aa6d49dcf2e5030e` after tracked fixture copying. |
| **2 T10 acceptance failures:** `tests.test_multirepo_acceptance.MultiRepoAcceptanceTests.test_one_hormigestion_run_keeps_ci_metrics_provenance_findings_and_report_independent` and `test_private_frontend_denial_keeps_backend_and_marks_report_partial_unknown`. | Both were caused by the test fake returning different non-Git blob SHAs for commit-tree and `/contents`. Production correctly withheld full-file evidence. The corrected fake was subsequently verified by the separate 2/2 focused pass (`93b5914ea2c26c551900`); the original failures are preserved as historical evidence. |
| **1 approval failure:** `tests.test_phase2_demo_seed.Phase2DemoSeedTests.test_public_history_technical_review_and_source_approval_are_real_and_local`. | Expected HTTP 200 but received 409 `fuente_no_verificable`; the seed stores synthetic source `acme/h5-test` but the app had no matching server repository/ref configuration. The test now seeds first and constructs the app with explicit synthetic singleton binding `acme/h5-test` / `main` and `external_writes_enabled=false`; the unchanged public approval route/assertions passed in `ffc51e1f490dedec4d0a`. No origin inference or service bypass is used. |
| **2 source-check diagnostic failures:** `tests.test_cli_sources.CLISourceCheckTestCase.test_cli_source_check_cross_provider_secret_in_sha_is_guarded` and `test_cli_source_check_real_isolated_actions_requires_commit_sha`. | Both emitted no expected stdout; the Actions test’s no-transport assertion passed. Regression stderr had generic configuration-validation errors. Each temporary singleton YAML omitted `project.id`, so `_yaml_configuration_paths` layered the default two-repository HORMIGESTION config. Adding distinct synthetic project IDs isolated the intended legacy inputs; both tests passed in `ffc51e1f490dedec4d0a` with their secret/SHA non-leak, malformed-payload and no-transport assertions unchanged. This confirms the test-input layering cause; no product-code change was needed. |
| **1 fake real-source pipeline failure:** `tests.test_real_source_pipeline.RealSourcePipelineTestCase.test_cli_run_real_sources_fake_llm_succeeds`. | Expected CLI exit 0, got 2, with generic configuration-validation stderr. Its singleton YAML omitted `project.id` and inherited the default MultiRepo YAML; declaring its synthetic project ID `test-proj` isolated the expected legacy config. The fake transport and original success assertions remain; the case passed in `ffc51e1f490dedec4d0a`. |
| **8 web analysis-job failures:** `tests.test_web_api_jobs.WebApiAnalysisJobTests`: `test_executor_failure_is_terminal_and_does_not_expose_exception_text`, `test_missing_credentials_execute_deterministic_pipeline_and_persist_run_and_report`, `test_partial_credentials_select_mixed_mode_and_remote_components_require_consent`, `test_preflight_is_protected_and_does_not_create_database_or_job_ledger`, `test_queue_capacity_rejects_a_second_project_while_worker_is_active`, `test_real_mode_missing_credentials_is_eligible_for_sandbox_prepare`, `test_remote_consent_idempotency_and_polling`, `test_server_remote_permission_cannot_be_overridden_by_client_consent`. | Preflight returned `eligible: false`; confirm paths returned 409 `analisis_no_preparado` (“effective configuration is not ready”). The old snapshot omitted root `fixtures/`; all eight selected cases passed in regression `a041aa6d49dcf2e5030e` after the corrected runner supplied tracked fixtures. This confirms the snapshot-fixture correction for those cases; no live or remote execution was involved. |
| **1 skipped test:** `tests.test_real_models.RealModelsTestCase.test_real_gemini_remote_smoke`. | Intentional opt-in skip: `RUN_REAL_LLM_TESTS=1` was not set. Do not enable it; live provider activity is outside this acceptance proof. |

### Parent selected regression and remaining five cases

The parent’s one `regression` run is retained at `.runtime/t10-acceptance/parent-checks/regression/` and `.runtime/t10-acceptance/evidence/a041aa6d49dcf2e5030e/regression/`: 55 tests, 4 failures, 1 error, 1 skip; exit 1; command duration 16.596s (test-runner duration 15.151s). All 14 monitoring-runner tests passed after adding snapshot `tests/` to `sys.path`; the fixture-loading, fixture inventory/source-check, legacy alias, Web API jobs, source-contract, and both MultiRepo acceptance cases also passed. Socket-attempt lists were empty; guards, snapshot provenance, unchanged settings loader, environment isolation, real clock and UUID checks passed. Stderr contains the FastAPI/httpx deprecation warning and three `ERROR: Configuration validation failed.` lines; the latter are retained as evidence, not suppressed.

The five cases that failed in the parent regression were:

1. `tests.test_cli_sources.CLISourceCheckTestCase.test_cli_source_check_cross_provider_secret_in_sha_is_guarded` — expected `malformed_payload` absent from captured stdout.
2. `tests.test_cli_sources.CLISourceCheckTestCase.test_cli_source_check_real_isolated_actions_requires_commit_sha` — expected `failed` absent from captured stdout.
3. `tests.test_phase2_settings.Phase2SettingsTests.test_environment_loads_live_actions_priority_mapping_and_smtp` — `load_api_settings(env_file=None)` raised `Invalid GitHub repository collection configuration` for legacy `GITHUB_REPOSITORY=owner/repository` alongside the default HORMIGESTION collection.
4. `tests.test_phase2_demo_seed.Phase2DemoSeedTests.test_public_history_technical_review_and_source_approval_are_real_and_local` — HTTP 409 `fuente_no_verificable`; source repo is synthetic `acme/h5-test`, but the app had no matching server repository/ref binding.
5. `tests.test_real_source_pipeline.RealSourcePipelineTestCase.test_cli_run_real_sources_fake_llm_succeeds` — CLI exit 2 instead of 0.

The single `causal-five` run used only these five tests and passed all five (0 failures, 0 errors, 0 skips; 0.755s; exit 0). Exact output and summary are retained at `.runtime/t10-acceptance/evidence/ffc51e1f490dedec4d0a/causal-five/`; snapshot manifest SHA-256 is `5800a70362f94953630df68da0ea02643316b363f4e5f6fc6d72aec59b5c1895`. This confirms the test-input causes: each custom singleton YAML now declares a project ID different from default HORMIGESTION, so the real loader does not layer that unrelated MultiRepo config; the settings test uses an explicit temporary legacy YAML with a distinct ID; and the demo app explicitly binds synthetic source `acme/h5-test` to `main` before request handling. The real source-check, config loader, approval route, and fake pipeline all remain exercised. Assertions and expected results were not changed, no shared helper or production file changed, and no product defect was reproduced.

### Portable acceptance proof and reproducible commands

The canonical contract remains cached under `.runtime/multirepo-contract/` for provenance, but the test oracle is the independent repository-local fixture `tests/fixtures/ci-metrics.v1.schema.json`; `tests/test_multirepo_acceptance.py` no longer reads `.runtime` or repository history. The fixture is byte-identical to the canonical source: SHA-256 `104d4b79b084e2c79fa91d62e3845dbf9e8c87085c3b20106f529b9bc9a005b4`, canonical Git blob `09033371c03dd9458c45065b9fb1930bbff230ac`. The runner explicitly adds both `tests/test_multirepo_acceptance.py` and the schema fixture to snapshot inputs; the four root fixtures were not changed. The snapshot still includes cached contract inputs as separate provenance records.

The portability-only source delta is the schema path in the fake artifact port: `.runtime/multirepo-contract/ci-metrics.v1.schema.json` → `tests/fixtures/ci-metrics.v1.schema.json`. No assertion, expected value, test data, product validator, or production source changed. The final focused snapshot records test source SHA-256 `7c6db0a86f83601aab0582afa0139cf5c15b992fb608fcec2f780706cdda1114`, fixture SHA-256 `104d4b79b084e2c79fa91d62e3845dbf9e8c87085c3b20106f529b9bc9a005b4`, runner SHA-256 `d49b170f9b820cc50f1d82ecdb35fc5d4e7a01a8e1fca03baa71c28be14c2a9c`, and manifest SHA-256 `7edf4e2870fc1a7f5c6728cadbb9a71f8fd13e6294f26bf4eccc65a93d742ea9` (snapshot `f454844e9d5615e116d4`).

The guarded portable-version focused proof ran exactly two acceptance cases: 2 passed, 0 failures/errors/skips, 0.990s test time (1.763s runner), exit 0. Full summary/stdout/stderr are under `.runtime/t10-acceptance/evidence/f454844e9d5615e116d4/focused/`. Environment isolation, dotenv disablement, `EXTERNAL_WRITES_ENABLED=false`, owned HOME/TMP/cache, provenance/settings-loader checks, real clock/UUID, and intact in-process/child socket guards passed; both socket-attempt lists were empty and stderr was empty. This focused run validates the final portable test version; the full discovery above validates the immediately prior path version using identical schema bytes.

Ordinary checkout reproduction (from the repository root, with project test dependencies available; this is not the guarded safety proof):

```sh
PYTHONPATH=src .venv/bin/python -m unittest -v tests.test_multirepo_acceptance
```

Guarded immutable-snapshot reproduction used for the observed portable proof:

```sh
env -i PATH="$PATH" HOME="$PWD/.runtime/t10-acceptance/owned/home" TMPDIR="$PWD/.runtime/t10-acceptance/owned/tmp" XDG_CACHE_HOME="$PWD/.runtime/t10-acceptance/owned/cache" PYTHON_DOTENV_DISABLED=true EXTERNAL_WRITES_ENABLED=false PYTHONDONTWRITEBYTECODE=1 .venv/bin/python .runtime/t10-acceptance/runner.py focused
```

Historical focused `93b5914ea2c26c551900`, five-case causal `ffc51e1f490dedec4d0a`, 55-case regression `a041aa6d49dcf2e5030e`, and failed discovery `c28fcbb24f784856b75f` remain retained and unchanged. No full discovery, UI/build, or legacy four-test rerun was performed in this portability follow-up. Live/private producer access and the minimum-Python/dependency matrix remain untested; cached/fake evidence is not represented as live coverage.
