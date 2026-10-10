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
- [ ] H1B — In progress; functional checks green, review/commit pending. Add configurable near-due metrics with exact Guayaquil boundaries, exclusions and report visibility; retain original state. Route: delegated writer (multi-file).
- [ ] H1C — Compare same-project snapshots deterministically with stable changes and conservative missing/incomplete semantics. Route: delegated writer (multi-file).
- [ ] H1D — Expose reproducible persisted snapshot history/comparison via existing SQLite and CLI; test legacy compatibility. Route: delegated writer (multi-file).

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
- Eleven hermetic tests cover boundaries, offsets, zero window, exclusions, contradictions, config/CLI/hash/report/context/budget behavior; unit9files277diff lines. No live calls. Native review/commit pending.

## Next step
Review/commit H1B: configurable due_soon_days default7, nonnegative; America/Guayaquil cutoff <= due <= cutoff+days; overdue due<cutoff. Exclude completed/unknown/excluded/archived cards and preserve state/denominator. Add settings/CLI/hash/report/context wiring without increasing budgets. Then H1C/D.

Locator: odd/tasks/planning-tracking-history.md
