# Corrective comments: append, never replace task fields

Comment proposals extend the existing proposal/rule/review/execution circuit.
`field=comment` requires `operation=append_comment`, an exact nonblank text of at
most 2000 characters and explicit `comment_context`. Legacy field-update proposals
omit these optional fields, preserving their stored serialization and hashes.

`comment_context` binds the configured priority label ID, expected official due
date and separate manual human-confirmation evidence ID. Task evidence must show
the active included card, observed high-priority label, overdue date and
`due_complete=false`. Human evidence has category `human_confirmation`, source
`manual`, exact project/card identity, `implementation_developed=true`,
`validation_pending=true` and `confirmed_by`. It is not an authenticated approval
or a Trello snapshot observation. The initial rule is opt-in and card-scoped;
never activate it for every overdue task.

The append port returns a native action ID and reads that exact action back.
Sandbox IDs start with `sandbox-comment-` and do not indicate publication.
The live adapter's append gate is off unless the execution service explicitly
selects live mode under authorized configuration. Tests inject HTTP responses;
they do not prove a production Trello write.

Primary API references: [append comment](https://developer.atlassian.com/cloud/trello/rest/api-group-cards/)
and [read action](https://developer.atlassian.com/cloud/trello/rest/api-group-actions/).
The append endpoint is `POST /1/cards/{id}/actions/comments`, not a card PUT.
No provider idempotency guarantee is assumed.

## Execution and recovery

Existing server-session authorization, proposal digests, technical review, decision
ledger and execution audit apply unchanged. Edited text creates a derived proposal
with a new digest and technical review before the manager's execution decision.
Content and card identity are part of the approval binding; whitespace is preserved.

The existing execution ledger reserves `comment:<original-proposal-id>` as the
logical append resource. Identical request keys replay the durable receipt; a
new key or modified variant cannot append the same logical action again. Separate
legitimate follow-ups require their own proposal identity and evidence, not retries.

POST timeout, missing native ID, mismatched text or failed read-back leaves an
uncertain outcome. A native ID is retained when available. No automatic retry or
sandbox fallback follows an attempted live append. Inspect/reconcile the provider
action manually before authorizing any new action; there is no automatic reset
endpoint. A crash after reservation without an outcome also remains blocked.
Pending or uncertain comment delivery additionally blocks every new comment
proposal on that card, even with a new proposal/key/capture identity. A completed
comment does not block a legitimate later independent follow-up.

## Initial rule and sandbox proof

`config/rules/hormigestion-dashboard-validation.json` is an **opt-in rule**, not
enabled in the permanent project configuration. It targets only card
`6a9582c16a2c16f6106609e2` and configured high label
`6a957e9173d4d13d0c5db616`, with official due date `2026-10-19` and the exact
user-supplied comment. The rule carries the owner's confirmation as separate
manual evidence. Its timestamp is a frozen fixture/evidence-record timestamp,
not proof of an authenticated manager session. A manager still must approve the
effective text through the existing server-session route.

The deterministic test source uses the provided Dashboard case and IDs; it is
not a fresh Trello collection or evidence that functional tests have passed.
No new model call is needed for this proof. Existing acceptance criteria remain
validation targets, not invented test results.

Focused checks:

```sh
EXTERNAL_WRITES_ENABLED=false ALLOW_REMOTE_AI=false .venv/bin/python -m unittest \
  tests.test_corrective_comments tests.test_comment_execution tests.test_comment_api -q
```

For the real local React/FastAPI/SQLite sandbox test, create an isolated temporary
directory outside the checkout and pass its absolute path as
`COMMENT_SANDBOX_ROOT` to the existing installed Playwright CLI:
`node node_modules/@playwright/test/cli.js test -c e2e-backend/comments.config.ts`
(working directory `frontend`). The test starts its own localhost servers with
writes disabled and intercepted Python external requests. It edits the proposed
text, reviews the effective version, executes in sandbox and verifies the receipt
and human decision history after browser reload. No API response is mocked in
this integrated test. Separate backend tests exercise unchanged approval,
rejection, idempotency, crash and uncertain-read recovery.

Observed proof: 94 scoped backend regressions passed before the final uncertainty
hardening; final 17 comment-focused tests passed afterwards. Six existing-field
browser regressions, typecheck and the integrated comment browser case passed.
The independent verifier passed S1-S8, including 33 bounded probes and legacy
service/API differentials; the card-wide uncertainty correction was rechecked.
No global suite or real Trello publication was run.

Retained local evidence: `.runtime/diagnostics/trello-comment-circuit/` contains
the browser SQLite backup, screenshot and summary: two proposals (original and
modified), two technical reviews, one human decision, one execution, one audit
event, ID `sandbox-comment-1`, mode `sandbox`, verification `verified`.

## Before any real publication

1. Obtain separate permission to read current official card/list/label state with
   the intended credential/session; capture fresh source and revalidate the rule
   and outstanding human confirmation. No stale sandbox evidence authorizes live.
2. Show the exact final comment and target card; obtain a fresh authenticated
   human approval bound to that immutable submission and its review.
3. Request explicit, separate authorization for one comment on that card using
   the named credential/session, with narrowly scoped write configuration. Local
   sandbox approval does not grant this authorization or enable external writes.
4. After an authorized append, retain the native action ID and verify exact text
   and card/board identity by read-back. If uncertain, stop and reconcile before
   attempting any further comment; never reset the ledger blindly.

There is no automatic dependency activation, closure, reassignment, date update,
GitHub publication or further feature in this delivery.
