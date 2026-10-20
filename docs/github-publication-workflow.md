# G3 — Publication workflow: from a human approval to a pull request

This document describes the service workflow that sits between a human approval
and a pull request: what is persisted, in which order, what a retry does and
what is never claimed. The adapter seam itself is described in
[`github-sandbox-publication.md`](github-sandbox-publication.md); this document
covers the service, the durable ledger and the read helper.

## 1. Scope

| Concern | Owner |
|---------|-------|
| Branch creation (`create_verification_branch`) | existing adapter behaviour, preserved |
| Pull request publication (`create_pull_request`) | adapter seam, `docs/github-sandbox-publication.md` |
| Ordering, durability, recovery and audit | this document |
| HTTP response schema and interface | parallel API/UI unit |

The service never calls GitHub, a model or any other external API. It only uses
the `CodeActionPort` it was given.

## 2. The flow

```
human approval
  └─ approval gate (confirmed defect + exact patch + favorable review)
      └─ idempotency reservation (durable, before any side effect)
          └─ branch creation (exact reviewed patch)
              └─ branch checkpoint + review identity  ← durable, before the PR
                  └─ pull request publication (develop)
                      └─ ledger transition + audit + decision receipt
```

The order is the guarantee. The checkpoint is written **before** the pull
request exists, so a process that dies in the middle of the publication leaves
enough durable state to recover exactly one pull request, and never a second
branch, a second commit or a second human decision.

## 3. The decision receipt

`FindingDecisionService.decide` returns the existing keys unchanged and adds the
publication part of the receipt:

| Key | Values | Meaning |
|-----|--------|---------|
| `publication_status` | `not_requested` \| `published` \| `partial` | `partial` means the branch exists but no pull request was confirmed |
| `publication_error_code` | `str \| null` | why the publication was not confirmed |
| `publication_error_message` | `str \| null` | the same reason, in the language of the adapter |
| `pull_request_number` | `int \| null` | the number of the confirmed pull request |
| `pull_request_url` | `str \| null` | `https://github.com/...` for a live one, `sandbox://pulls/...` for a simulated one |
| `pull_request_state` | `open` \| `simulated` \| `null` | a simulated pull request is never presented as open |
| `pull_request_simulated` | `bool \| null` | `True` only for a simulated publication |
| `patch_review_id` | `str \| null` | the review that authorized the approval |
| `patch_sha256` | `str \| null` | the digest of the exact patch that was applied |

A rejection or a request for changes never publishes: `publication_status` is
`not_requested` and every other publication key is `null`.

## 4. What is never claimed

A publication is reported as `published` only when the typed result of the port
matches everything that was authorized:

| Check | Refusal code |
|-------|--------------|
| The result is a `PullRequestExecutionResult` | `resultado_de_pull_request_invalido` |
| The execution mode is the mode of the branch | `modo_de_publicacion_inconsistente` |
| The repository is the authorized repository | `publicacion_no_coincidente` |
| The base is the protected `develop` | `rama_base_no_protegida` |
| The head is the branch that was created | `rama_cabecera_inconsistente` |
| The head commit is the reviewed commit | `commit_de_cabecera_inconsistente` |
| The state agrees with the mode (`simulated` / `open`) | `estado_de_pull_request_inconsistente` |
| The URL belongs to the repository and the number | `url_de_pull_request_invalida` |
| The head branch is not a protected branch | `rama_cabecera_invalida` |

An injected adapter is never trusted because it says it published: the mode, the
repository, the head, the commit and the URL are all checked against the
authorization. The service never merges, never pushes and never writes to a
protected branch.

While the external writes are disabled, the adapter only produces a simulated
publication: the state is `simulated`, the URL uses the `sandbox://` scheme and
no remote call happens.

## 5. The durable ledger

The ledger lives in the table `finding_publications`, created lazily on the
first write, so an existing database is never migrated just because it was
opened. One finding has one publication row, identified by
`sha256(project|run|finding)`.

| Column | Meaning |
|--------|---------|
| `stage` | `branch_created` → `published` \| `partial` |
| `repository`, `base_branch`, `head_branch`, `head_commit_sha` | the exact publication identity |
| `branch_url`, `branch_execution_mode` | the branch that was created |
| `patch_sha256`, `patch_review_id` | the identity of the review that authorized it |
| `pull_request_number`, `pull_request_url`, `pull_request_state`, `pull_request_simulated` | the confirmed publication |
| `publication_error_code`, `publication_error_message` | why it is not confirmed |
| `attempts` | how many publication attempts were made |

A published row is never downgraded, and the checkpoint is never rewritten: a
second checkpoint for the same finding with another branch or commit is refused
with `publicacion_en_conflicto`.

## 6. Reading the publication

```python
from agente_gestion.application.finding_publication import latest_finding_publication

receipt = latest_finding_publication(storage, project_id=..., run_id=..., finding_id=...)
```

The helper returns the receipt of the finding or `None` when it was never
published. It is read-only: it never writes, never creates a branch and never
publishes anything. The receipt carries the same keys as the decision receipt
plus the branch and the repository that were used.

## 7. A partial publication and its retry

When the branch exists but the pull request is not confirmed — an uncertain
answer from GitHub, a refused publication, an adapter without the publication
operation — the decision still succeeds:

- `decision_status` is `approved`;
- `publication_status` is `partial` with the exact `publication_error_code`;
- the branch, the branch URL and the applied commit are preserved;
- the ledger keeps the checkpoint and records the cause;
- the audit log records the attempt as denied.

The same idempotency key replays that receipt. The replay revalidates the
current source and the current review (a changed patch, an unfavorable review
or a stale source fails closed and publishes nothing) and then attempts **only**
the pull request, reusing the persisted branch and commit. A successful
recovery appends a new audit event and refreshes the replayable receipt; the
immutable decision ledger keeps exactly one human decision.

A different idempotency key cannot start a second publication: the approval is
already recorded, so the reservation is refused.

## 8. The abandoned attempt and its lease

A process can die between the branch checkpoint and the pull request. The
request stays `processing` and the ledger keeps the checkpoint, so the attempt
is recoverable — but only by the **same** request.

Every attempt holds a bounded lease (`DECISION_LEASE_SECONDS`, 60 seconds by
default) that is written with the reservation:

| Situation | Answer |
|-----------|--------|
| The same key arrives while the lease is alive | `409 decision_en_curso`, with the instant the lease expires |
| The same key arrives after the lease expired | the attempt is resumed: the checkpoint is reused and only the pull request is published |
| Another key arrives while the lease is alive | `409 decision_en_curso` |
| Another key arrives after the lease expired | `409 decision_abandonada`: the abandoned approval owns a branch, so only its own key can recover it |

The lease is what keeps an active attempt from being stolen: the recovery is
never immediate and never unconditional, it waits for the lease to expire. The
refusal says when that is, so the interface can explain the wait instead of
guessing.

A resumed attempt revalidates everything before it publishes: the current
source, the current review (an unfavorable or superseded review fails closed),
the actor and the request hash. The branch and the commit are the ones the
checkpoint recorded, so nothing is created twice; the human decision is taken
once and the immutable ledger keeps a single row.

## 9. The multirepo path reviews the correction too

The independent review of the exact patch is part of the chain that unlocks an
approval, so the repository-scoped path produces it as well: every finding
that a repository stage verified is reviewed by the same independent verifier
with the complete, immutable source of its own repository. A file captured
from another repository never becomes a patch base, and a finding whose source
is not complete has no identifiable patch and therefore no review — the
approval stays blocked, which is the honest answer.

## 10. The interface keeps the idempotency key

A retry after a reload or in another tab is the same request, so the
interface keeps the idempotency key of the approval in narrowly scoped browser
storage. The scope is the actor, the project, the repository, the run, the
finding and the digest of the edited correction; the correction itself never
travels in clear, and no token or session secret is stored. A browser that
refuses the storage keeps working with the key in memory.

## 11. Audit

| Event | Permission | Decision |
|-------|------------|----------|
| The human decision | `web:finding_decision` | `granted` |
| A confirmed publication | `web:finding_publication` | `granted` |
| A refused or uncertain publication | `web:finding_publication` | `denied` |

The decision and its publication audit are written in the same transaction, so
a decision is never recorded without the fate of its publication. The
publication event identifier is `audit-<event>-publication-<attempt>`, so every
attempt of the same request is distinguishable and never collides.

## 12. Known limits

- The service trusts the adapter for the existence of the branch; it never
  re-reads the sandbox tree or the remote branch.
- The publication base is always `develop`, independently of the branch base of
  the origin. The branch is still created from the branch base that the origin
  fixed.
- The ledger holds one publication per finding. A second corrective cycle for
  the same finding needs a new run.
- The lease is not renewed while the attempt works. An attempt that outlives
  the lease can be taken over by its own key; the publication stays unique
  because the adapter recovers an existing pull request instead of opening a
  second one.
