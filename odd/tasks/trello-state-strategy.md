# Trello State Strategy Compatibility

## Scope and authorization
Add explicit `list_mapping` (default) and `date_fields` strategies to the existing MVP. Preserve architecture, domain, fixtures and the GET-only security boundary. Parent local commits are authorized after functional checks and exact native review acknowledgement. No actual developer dotenv/credential edits, live API/LLM calls, T8/T9 continuation, push, PR, merge or MVP-02.

Baseline: branch `mvp/real-sources`, commit `7f685cb`. Preserve unrelated untracked `docs/arquitectura-implementada.md`; exclude it from all compatibility commits and reviews.

## Design
- Explicit strategy selection; no heuristic detection of phase lists.
- `date_fields` uses the aware UTC collection timestamp, not the report cutoff. State, evidence provenance and cache reuse the same instant.
- Explicit `dueComplete=true` wins: completed. Completed plus blocked label or future start remains completed with contradiction.
- Explicit false: blocked label wins; otherwise future start means pending, present/past start means in progress; absent start means unknown. Missing completion signal remains unknown, never coerced to false.
- Missing new fields remain compatible with list mapping. Present non-boolean `dueComplete` (including null) is malformed. Start can be absent/null; present values must be nonempty timezone-aware ISO dates. Invalid payloads never publish partial caches.
- Evidence retains `due_complete`, normalized `start_date` and `start_raw`, with existing credential guards. No TaskCard expansion or overdue enum; existing due-date evaluation remains unchanged.
- `Settings.trello_state_strategy` defaults to `list_mapping`, validates the two strategies and normalizes case. YAML supports flat `trello_state_strategy` and nested `sources.trello.state_strategy`; environment uses `TRELLO_STATE_STRATEGY`.
- Preserve init/CLI override > OS environment > dotenv > YAML > defaults. Factory forwards the setting. Date mode requires no status list groups; existing list-mode validation, included-list filters and blocked labels are preserved.
- Same bounded GET-only collection requests, with additional requested card fields; no added HTTP request or remote mutation.

## Tasks
- [x] T0 — Isolate existing CLI tests from developer dotenv. Temporary empty dotenv fixtures only; production loading unchanged. Commit `d4de8d838ae02030d4e62918a683e81bdb233d62`.
- [x] T1 — Test-first rules A–J, payload validation, timestamp/provenance, atomic caches and default compatibility. Same coherent adapter/test commit `d4de8d838ae02030d4e62918a683e81bdb233d62`.
- [x] T2 — Test-first Settings/factory/source-check wiring and public examples. Commit `8de6c02f987424fc26a43510b3135535a817d6c5`.
- [x] T3 — Record final independently observed results and boundaries; independent passive tracker audit VERIFIED, no further source changes. Local closure commit is this document's initial Git commit (`git log --diff-filter=A -- odd/tasks/trello-state-strategy.md`); its exact hash is also retained in the Engram task mirror.

## Test-first evidence
### Adapter and isolation prerequisite
Original 32 Trello tests passed. Twelve new methods include requested A–J coverage and constructor/unknown cases. Before implementation, the 44-method run reported 38 errors because the new strategy constructor argument was unsupported. This is observed unsupported-feature RED, not an assertion-based behavioral RED claim. After adapter implementation, all 44 passed.

Four legacy CLI/pipeline tests then failed because `env_file=None` allowed repository dotenv loading. This was a test-isolation incompatibility, not an adapter regression. T0 added 14 test lines: scoped actual-main wrapping injects a temporary empty dotenv unless explicitly supplied or requesting help; the real-source pipeline CLI test passes its temporary empty dotenv directly. Existing generated-dotenv precedence tests and security assertions remain intact. No actual `.env` was manually read or modified.

Independent verifier `muyjlejj-p-9myd` ran ordinary commands, without global runner bypasses:

| Check | Observed result | Test runtime | Exit |
| --- | --- | --- | --- |
| `tests.test_trello` | 44 passed | 0.009s | 0 |
| `tests.test_config tests.test_cli_sources tests.test_real_source_pipeline` | 51 passed | 0.192s | 0 |
| Full `tests` discovery | 368 total: 366 passed, 2 skipped | 0.612s | 0 |
| `pruebas` discovery | 7 passed | 0.001s | 0 |
| `git diff --check` | Clean | — | 0 |
| `git status --short` | Five modified tracked files; two preserved untracked files | — | 0 |

Actual work unit: five files, 356 additions and 23 deletions, 379 diff lines. Native `review-9802685fd6449707`: medium, one reliability reviewer, approved; fresh STATUS and exact acknowledgement burned authority before commit. This is unit-specific, not whole-branch approval.

### Configuration and final source candidate
Eight new tests were added before production edits: five Settings tests, one factory test and two CLI tests. Actual RED: four errors and four failures for missing configuration field, ignored invalid strategy, wrong factory default behavior and phase cards remaining unknown. GREEN followed Settings/factory wiring. Tests cover default, OS/dotenv, flat/nested YAML, invalid types/values, precedence, date mode without groups and safe CLI failure.

Independent verifier `muyk4foe-r-7rcx` ran all six ordinary commands against the final source candidate:

| Check | Observed result | Test runtime | Exit |
| --- | --- | --- | --- |
| `tests.test_trello tests.test_config tests.test_cli_sources` | 96 passed | 0.159s | 0 |
| `tests.test_source_collection tests.test_real_source_pipeline` | 44 passed | 0.089s | 0 |
| Full `tests` discovery | 376 total: 374 passed, 2 opt-in LLM tests skipped | 0.686s | 0 |
| `pruebas` discovery | 7 passed | 0.001s | 0 |
| `git diff --check` | Clean | — | 0 |
| `git status --short` | Six modified tracked files; two preserved untracked files | — | 0 |

Phase-board source-check uses the real adapter with fake transport, no three state-list mappings, and two fake GETs. No real credentials, API or LLM calls were exercised. Actual work unit: six files, 198 additions and one deletion, 199 diff lines. Native `review-32b70ddcb34503b1`: medium, one reliability reviewer, approved; fresh STATUS and exact acknowledgement burned authority before commit. Source was not changed after this verification.

## Modified surfaces
- `src/agente_gestion/adapters/sources/trello.py`: state strategy, clock and evidence/validation.
- `src/agente_gestion/application/config.py`, `src/agente_gestion/adapters/sources/factory.py`: validated selection and wiring.
- `tests/test_trello.py`, `tests/test_config.py`, `tests/test_cli_sources.py`, `tests/test_real_source_pipeline.py`: regression/strategy tests and hermetic dotenv fixtures.
- `README.md`, `.env.example`: public usage examples; actual runtime configuration untouched.
- This task document: passive ODD continuity and final evidence.

## Boundaries and next step
No failing executed check remains. Two live-LLM opt-in tests were intentionally skipped. HORMIGESTION live compatibility, credentials and endpoint/model availability remain unverified; fake success is not live availability. Actual `.env` was not changed, so a deployment must explicitly select `TRELLO_STATE_STRATEGY=date_fields` (or the equivalent YAML setting) when phase lists require it. Unknown signals and invalid dates remain deliberate outcomes, not inferred states.

Final passive audit: source diff empty, commit identities/statistics and tracker facts independently confirmed, status contained only the two preserved untracked documents, all bounded Git checks exited zero. Full suites were not rerun after the passive tracker edit because the source candidate was unchanged. No meaningful test-first RED applies to this documentation-only closure.

Stop after local commits and tracker closure. Any live T8/T9 continuation is a separate user decision. Leave `docs/arquitectura-implementada.md` untracked and untouched.
