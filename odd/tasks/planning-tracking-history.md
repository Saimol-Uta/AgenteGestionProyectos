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
- [ ] H0 — Establish current offline baseline and hybrid invariants; document honest T9 state. In progress. Route: delegated explorer + verifier (broad evidence and command execution).
- [ ] H1A — Extend TaskCard and Trello/fixture normalization with observed assignees, start/completion/archive/activity metadata and unknown priority; preserve old payloads. Route: delegated writer (multi-file).
- [ ] H1B — Add configurable near-due metrics with exact Guayaquil boundaries, exclusions and report visibility; retain original state. Route: delegated writer (multi-file).
- [ ] H1C — Compare same-project snapshots deterministically with stable changes and conservative missing/incomplete semantics. Route: delegated writer (multi-file).
- [ ] H1D — Expose reproducible persisted snapshot history/comparison via existing SQLite and CLI; test legacy compatibility. Route: delegated writer (multi-file).

## Acceptance and verification
Observe test-first RED/GREEN for new deterministic behavior. Each unit runs focused tests, tests/ regression, pruebas/test_*.py, and git diff --check. Native review uses only the user-owned switch and provider-issued routes; unavailable review is documented, not globally disabled. Excluded/completed/unknown cards are not deadline risks; progress denominator remains included cards only. Comparator never asserts deletion or actor attribution without evidence, rejects cross-project comparisons and never depends on LLMs.

## Evidence
- Initial git status: clean mvp/real-sources; git log reviewed (12 commits); local HEAD...origin counts 0/0.
- Mandatory docs and config/odd/tasks/src/tests/pruebas inspected by explorer mv2yzx6t-1-jp9g.
- Baseline verifier mv2z01dk-2-r5io: tests472 run/470pass/2opt-in skips, no failures; pruebas7/7 evaluator tests passed (no live/performance benchmark). RDD global on; clone unset. H0 writer mv2z9dtr-4-yi70 verified 87 focused tests plus unchanged baseline and documented T9 PENDING. Parent spot-check hybrid_pipeline8pass. Native ASSESS unassessable due untracked task document; independent verifier mv2zboyj-6-mrt3 pending.
- Snapshot-history scout mv2z9v9j-5-tily found current snap-{project_id} upserts overwrite earlier snapshots. H1D must preserve immutable historical rows incrementally; task-source completeness is independent of overall CI completeness. Legacy identity/completeness is unknown.
- H2 scout found report approval is advisory, not action authorization; actor/authentication policy is a later boundary.

## Next step
Finish H0 baseline before source mutations; implement H1A against existing contracts, then continue H1B/C/D in priority order.

Locator: odd/tasks/planning-tracking-history.md
