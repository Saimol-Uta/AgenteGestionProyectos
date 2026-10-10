# Planning tracking and history

## Objective
Extend the existing analysis MVP with faithful activity metadata, deterministic deadline signals and reproducible planning history (REQ-01..04), without renaming agents or granting AI action authority.

## Scope and constraints
- Branch: mvp/real-sources; initial HEAD: 6f60007; initial worktree clean; local origin reference equal (no fetch performed).
- Single delegated writer; offline synthetic fixtures; .venv/bin/python; no dependency or credential changes.
- No live inference/source APIs, Trello writes, push, PR or merge. T9 real remains pending without explicit remote consent.
- Preserve run/source-check/decide, fixtures, list_mapping/date_fields, current SQLite records and quality grounding/budgets.
- Unknown priority remains null; labels are not priority unless explicitly configured.
- Work-unit commits include tests/docs. Commit identities are recorded after closure.
- H2 authorization/execution is not inferred from report decisions; H2/H3/H4 remain subsequent work.

## Tasks
- [x] H0 — Establish current offline baseline and hybrid invariants; document honest T9 state. Closed offline in f0feca8; real T9 PENDING. Route: delegated explorer + verifier (broad evidence and command execution).
- [x] H1A — Closed offline in 748379c after independent verification; native review declined for this candidate by human. Extend TaskCard and Trello/fixture normalization with observed assignees, start/completion/archive/activity metadata and unknown priority; preserve old payloads. Route: delegated writer (multi-file).
- [x] H1B — Closed offline in be369c5 after independent verification; native review declined for this candidate. Add configurable near-due metrics with exact Guayaquil boundaries, exclusions and report visibility; retain original state. Route: delegated writer (multi-file).
- [x] H1C — Pure comparator closed offline in939876f; native approved+acknowledged, independent checks green. Compare same-project snapshots deterministically with stable changes and conservative missing/incomplete semantics. Route: delegated writer (multi-file).
- [x] H1E — Closed offline in3b6f269, nativeapproved+acknowledged; ASSESSclosed. Integrate derived Trello state/list-name observations and task source/mapping identity/completeness into real collection, with mocked acceptance. Route: delegated writer (integration gap found by independent verifier).
- [x] H1D1 — Closed offline in0617ef1; nativeapproved+acknowledged and ASSESSclosed. Persist immutable content-addressed captures in existing SQLite, atomic safe backfill and read-only queries; preserve latest get_snapshot and all runs. Route: delegated writer (multi-file).
- [ ] H1D2 — In progress. Add read-only CLI history list/compare using explicit capture IDs; unavailable legacy/missing DB never creates/migrates data or artifacts. Route: delegated writer (multi-file).

## Acceptance and verification
Observe test-first RED/GREEN for new deterministic behavior. Each unit runs focused tests, tests/ regression, pruebas/test_*.py, and git diff --check. Native review uses only the user-owned switch and provider-issued routes; unavailable review is documented, not globally disabled. Excluded/completed/unknown cards are not deadline risks; progress denominator remains included cards only. Comparator never asserts deletion or actor attribution without evidence, rejects cross-project comparisons and never depends on LLMs.

## Evidence
- Initial git status: clean mvp/real-sources; git log reviewed (12 commits); local HEAD...origin counts 0/0.
- Mandatory docs and config/odd/tasks/src/tests/pruebas inspected by explorer mv2yzx6t-1-jp9g.
- Baseline verifier mv2z01dk-2-r5io: tests472 run/470pass/2opt-in skips, no failures; pruebas7/7 evaluator tests passed (no live/performance benchmark). RDD global on; clone unset. H0 writer mv2z9dtr-4-yi70 verified 87 focused tests plus unchanged baseline and documented T9 PENDING. Parent spot-check hybrid_pipeline8pass. Native ASSESS unassessable due untracked task document; independent verifier mv2zboyj-6-mrt3 confirmed 87 focused +7 evaluator pass, diff clean and Settings-presence only (Groq/Mistral keys absent; Gemini/Trello configured; GitHub token absent). H0 commit f0feca8b5a2df4715188ef879ebf5c54e7ec58df.
- Snapshot-history scout mv2z9v9j-5-tily found current snap-{project_id} upserts overwrite earlier snapshots. H1D must preserve immutable historical rows incrementally; task-source completeness is independent of overall CI completeness. Legacy identity/completeness is unknown.
- H2 scout found report approval is advisory, not action authorization; actor/authentication policy is a later boundary.

## H1A verified evidence
- Writer mv2zki38-7-fcxr observed RED before source changes (9 new contract tests); new tests now pass.
- Initial regression was corrected after explicit narrow test-scope approval: focused94/94pass, full481run/479pass/2skip, pruebas7/7pass, diff check clean.
- Existing test_evidence_provenance_and_payload_allowlisting rejects newly required normalized metadata keys. No test removed or weakened; Human selected approve for tests/test_trello.py allowlist-only scope; continuation mv2zpk6a-9-io4h completed exactly four allowlist additions, retained all assertions and updated requirements roadmap. Six-file unit250diff lines plus parent tracker. Native START returned declined_this_candidate for e37930c1… with no lineage. ASSESS required independent verification; mv2zwgok-a-4ckj confirmed94focused/full481run479pass2skip/pruebas7pass and no severe findings. Parent spotmetadata9pass. Commit748379c9588cf280a53220b041567d30e1f87c9e.

## H1B verification
- Writer mv30241r-b-zm5q observed RED128tests with1failure/18errors before source; GREEN128focused, full492run490pass2skip, pruebas7pass, diffcheckclean.
- Eleven hermetic tests cover boundaries, offsets, zero window, exclusions, contradictions, config/CLI/hash/report/context/budget behavior; unit9files277diff lines. No live calls. Native START declined3adf9d5d…/no lineage; ASSESS fallback independent mv30am9d-c-1t0u reproduced128focused/full492run490pass2skip/pruebas7pass, no severe findings. Parent11pass; stageddiffcheck also covers new test. Commit be369c565432afc7723a5277f13e0157b93a2c8d. Nonblocking: extremely large due_soon_days may overflow datetime; SQLite ResourceWarning persists.

## H1C verification and integration gap
- Writer mv30ijg6-d-8c0w RED missing module, GREEN56focused/full506run504pass2skip/pruebas7pass; parent14pass and stageddiffcheck covers newfiles.
- Native review-954ed245636ee126 approved1c463393… then exact acknowledgement burned authority. Advisory malformed-evidence-mask is nonblocking laterhardening, no correction route.
- Postack ASSESS unassessable due untrackedfiles required independent mv30srj6-e-ti8y: checks green, no unit regression. Verifier retained stdout confirmed Ran506tests/OK(skipped=2):504pass2skip.
- Pure comparator commit939876ff55af877e2833936144bada6db22f92cd produces exactly4fields in A/B synthetic snapshot test; no physical deletion/creation/actor claims.
- Integration gap: current Trello observed_fields omits derived state/list_name and Composite does not fill task_source_identity/tasks_complete. H1E now resolves this before storage/CLI; H1C alone is not end-to-end historical tracking.

## H1E scope clarification
- Writer mv312gl9-g-vyzk stopped before edits because parent incorrectly assumed a mapped_state alias. Existing payload already carries state/list_name; only masks are missing. Human selected A: preserve existing contract without new alias. Continuation mv317rkf-i-ce3g implements original remaining integration scope.

## H1E verification
- Continuation mv317rkf-i-ce3g observed RED144tests14failures before source; GREEN144focused/full515run513pass2skip/pruebas7pass/diffcheckclean. Nine mocked integrationtests,6files264diff lines.
- Existing state/list_name keys retained, masks added; factory whitelist hashes provider/effectiveboard/strategy/canonicalmapping scope only (no credentials/fullSettings). Successful empty tasks complete=true, failure=false, independentofCI. Native review-f43517d0a645c0f4 approved f770d12a… and exactack burned8cea5476…; ASSESS medium/consumedtrue/closedderived, no separate verifier needed. Parent9pass; stageddiffclean. Commit3b6f269fffa4813ee0fb3c9679e9499507b902ad.

## H1D1 verification
- Worker mv31f5wx-j-06sb RED52tests16errors before source; GREEN53focused/full527run525pass2skip/pruebas7pass. Twelve temporary-DB tests;7files410diff lines, coherent migration+storage+tests (advisory size heuristic, not split by filetype).
- ExactJSON SHA256 identities, serialize-once, capture/latest atomic write, explicit DDL/index/backfill transaction, UTC ordering, integrity and readonly noncreation verified. Old overwrittenversions unrecoverable; hashes prove consistency not authorship. No real DB opened. Native review-cba6493ce9630bd3 approved44f875f5… then exactackburn35755582…; ASSESS medium/consumedtrue/closedderived, no separate verifier. Parent12pass/stageddiffclean; commit0617ef18c0bd0d4a6bf63a12444b6fe907da9b4b.

## Next step
H1D2 CLI: history list PROJECT --db-path and history compare PROJECT PREVIOUS_CAPTURE CURRENT_CAPTURE --db-path, JSON metadata/deltas with explicit capture IDs. Open SQLite read_only=True only; never compose orchestrator/artifacts or create/migrate files. Missing/legacy/corrupt/foreign capture fail cleanly. Synthetic persisted A/B must yield exactly4changes and leave database/schema/artifacts unchanged. Update validation/architecture/roadmap with current totals and pending T9/H2. Then advance next safe H2 increment; authorization is not report approval.

Locator: odd/tasks/planning-tracking-history.md
