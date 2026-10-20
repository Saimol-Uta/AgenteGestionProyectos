# HORMIGESTION: one real diagnostic analysis

## Outcome

One user-authorized analysis used real Gemini responses and current read-only
Trello/GitHub sources. It completed with `needs_attention`, not a successful
corrective-action circuit. No positive review, manager approval or publication
was fabricated to make the demonstration pass.

- Date: 2026-10-20, 11:50:42, America/Guayaquil.
- Run: `run-20261020-115042-eef6fc`.
- Job: `webjob-67ca1dd5f6faf4c41347f777`, completed, live analysis mode.
- Runtime: `.runtime/diagnostics/gemini-20261020T164148Z-SZ5z/`.
- Historical database and permanent HORMIGESTION configuration were not modified.
- Live analysis does **not** mean external action writes were enabled.

## Actual provider use

All calls used `gemini-3.5-flash-lite`; no FakeProvider stood in for these results.

| Stage | Prompt tokens | Completion tokens | Outcome |
| --- | ---: | ---: | --- |
| Monitoring | 1162 | 694 | complete |
| Quality: backend | 15768 | 429 | complete, one candidate |
| Quality: frontend | 11904 | 9 | complete, no candidates |
| Report | 3807 | 188 | complete |
| Verification | 0 | 0 | blocked locally before the provider |
| Independent patch review | 0 | 0 | no eligible verified correction |

Total: **32641 prompt and 1320 completion tokens**, four attempts, zero retries
or repairs. The configured ceiling was eight attempts, including retries and
repairs. That ceiling does not authorize another run after this analysis ended.
No monetary cost was exposed by the adapter; no price estimate is asserted.
Quality context/output limits were 128000/4096 tokens; Quality and Verification
timeouts were 120 seconds. Capture files never store authentication headers.

## Current source evidence

| Repository | Branch | Captured HEAD |
| --- | --- | --- |
| `Josu-F1/hormigestion-backend` | develop | `8b166e0045d3585f3b84228d59242892fc608f10` |
| `Josu-F1/hormigestion-frontend` | develop | `68e2b9a13af5193738bd6b4706b0c073918dab7c` |

Both repositories were complete **within the collector's HEAD-commit changed-file
scope**: four files, 18 HTTP requests and 213897 response bytes; budget not
exhausted. This was not a full repository-tree audit. The old SQL finding was
not reused because its file was not part of the current HEAD diff.

Trello open-card preflight returned 30 cards. The pipeline capture included
31 observed cards, of which 30 count toward progress; 18 were completed (60%).

## Monitoring and proposals

| Task | Observed condition | Interpretation |
| --- | --- | --- |
| `6a9582c16a2c16f6106609e2` Dashboard and reporting | In progress; due October 19 | Overdue active task |
| `6a9582c67ac8b41529deedc6` Administrator/client integration | In progress; due October 23 | Due within seven days |
| `6a958243ac18e7ebdc471d37` `(541) 555-0143` | Unknown; archived and excluded | Source-state uncertainty, not an actionable active deadline risk |

Real Gemini suggested reviewing the overdue task's state/date, monitoring the
integration activity and checking the unknown card's provenance. These were
**narrative** `suggested_actions`, not executable proposals.

No owner rule was configured or authorized. The proposals stage recorded
`offered=0`, `selected=0`, `saved=0`, `limitations=["missing_rules"]`;
`requested_changes=[]`. Consequently there was no technical proposal review,
dry-run or eligible Trello action. This is an explained zero result, not an
execution exception. No temporary rule was introduced merely to force an action.

## Quality, Verification and eligibility

The backend candidate was
`backend::8b166e0045d3585f3b84228d59242892fc608f10::CQ-001`, in `src/app.ts`.
Gemini claimed `z.int()` was invalid and proposed `z.number().int()`, with 0.98
confidence. Frontend Quality returned no candidates.

The candidate is contradicted by independently retrieved evidence:

- Pinned backend `package.json` declares `zod: ^4.1.0`.
- [Zod API](https://zod.dev/api) and [Zod 4](https://zod.dev/v4) document `z.int()`
  as a supported integer schema returning a `ZodNumber`.
- The excerpt occurs at line 25, although Gemini cited line 34.

This is a **documentation-based false-positive assessment**, not a Gemini
Verification response. The actual run still has zero verification and patch
review rows. Its candidate remains pending and unchanged.

Approval eligibility: `false`, `defect_confirmed=false`, reason
`defecto_no_confirmado`, `patch_review=null`. The effective proposed-content hash
was `47f38c6327edf51aabe49ac575e8f082114c1403ebd9c27d83deae1d33371b3b`.
An approval attempt correctly returned HTTP 409
`revision_independiente_requerida`, without a decision, branch, commit or PR.

## Local defects and minimal corrections

| Symptom | Cause | Correction | Proof |
| --- | --- | --- | --- |
| Consent named Groq although Quality used Gemini | Hard-coded Quality preflight provider | Match the existing factory's provider/model/credential selection | RED for Gemini/Mistral; 21 related tests passed |
| Verification failed before any model call | Generic ContextBuilder limit4096 applied to8039-token evidence despite the model's larger window | Derive input budget from provider limits, reserving instructions/schema/envelope/output; honor explicit local caps | 48 related tests; actual-source offline replay and refusal cases |
| Two repositories caused React duplicate-key errors | Both observations keyed only by shared capture ID | Add repository source-item identity to the key | Browser RED, then18 analysis tests and typecheck; real-data reload without console errors |

The same budget correction applies to independent patch review. No code evidence
is clipped, no explicit local cap is relaxed and no authorization check is removed.

The corrected actual-source replay used **one local deterministic insufficient
response**, explicitly not a real model verdict. It reached the provider with
8212 estimated context tokens, effective budget1045931, intact264-line source
and matching SHA-256. Small-model and explicit4096-token limits still refused
before provider invocation. The replay made zero network calls.

Closing regression: **94 focused backend tests passed**. Four additional
independent factory/safety probes were retained as regression tests and passed.
Independent verification also passed baseline/candidate differential probes,
the real-source offline replay,18 browser tests and typecheck. No full suite
was repeatedly executed.

## Sandbox actions and safety

There were **no eligible real outputs**, so no positive Trello execution or
GitHub PR was simulated from this analysis. A labelled copy exercised a
simulated manager rejection: HTTP200, one decision and one audit row,
`publication_status=not_requested`, no branch or PR. The original isolated run
still has zero decisions, audit rows and proposals.

The mutation log contains four Google generateContent requests and zero Trello
or GitHub writes. RDD stayed disabled. No push, merge, permanent rule or global
write permission was activated. Historical `.runtime/agente.db` SHA-256 remained
`3fa433ff19bfca792bb9956c8bc984c12c0792fa7d7adf80c25bf9fb99494150`.

## React evidence and reproducibility

Artifacts remain under the isolated runtime, not committed with project code:

- `REPORT.md`: full operational report with the real/replay distinction.
- `artifacts/gemini/calls.jsonl`: four real attempts and token telemetry.
- `artifacts/verifier_budget_replay_after_fix.json`: offline correction proof.
- `artifacts/zod_technical_assessment.json`: dependency/version/documentation proof.
- `screenshots/16-analysis-after-key-fix.png`: real analysis result after UI fix.
- `screenshots/16d-analysis-reload-persistence.png`: persisted state after reload.
- `screenshots/17-quality-blocked.png`: candidate and blocked authorization.
- `screenshots/18-proposals-missing-rules.png`: correctly empty proposal inbox.
- `screenshots/19-copy-audit.png`: rejection audit on the labelled copy only.

The source and captured model results remain available for deterministic
reproduction. A consumed single-run sentinel prevents another real analysis.

## Remaining authorization boundary

There is no captured real verifier response to reuse. A new explicit permission
is required to validate the corrected Verification path with Gemini. The useful
next request is one bounded Verification invocation with the pinned Zod-version
evidence, not another complete pipeline or a fabricated favorable patch review.

Real Trello correction additionally needs a justified, explicitly authorized
owner rule and exact card change. Real GitHub publication additionally needs a
genuine confirmed defect, favorable exact-patch review, human approval, fresh
source checks and separately authorized resource-scoped writes. None of those
permissions is inferred from this diagnostic analysis.
