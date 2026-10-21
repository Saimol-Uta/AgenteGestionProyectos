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
