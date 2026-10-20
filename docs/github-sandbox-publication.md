# G3 — Sandbox publication of pull requests

This document describes the publication seam added on top of `GitHubCodeActionAdapter`:
how a reviewed branch becomes a pull request, what is simulated, and what is still
disabled. It covers the adapter unit only; the service, API and UI wiring is owned by
the parent task.

## 1. Scope

The seam opens one pull request for a branch that already exists and was already
authorized. It never creates a branch, never merges, never pushes and never touches a
protected branch. The branch creation behaviour of the adapter is unchanged.

| Concern | Owner |
|---------|-------|
| Branch creation (`create_verification_branch`) | existing adapter behaviour, preserved |
| Pull request publication (`create_pull_request`) | this document |
| Durable receipt, audit trail and recovery policy | parent task (service seam) |
| UI that distinguishes review, authorization, branch and pull request | parent task |

## 2. Contract

```python
create_pull_request(
    *,
    repo: str,
    base_branch: str,
    branch_result: BranchExecutionResult,
    finding_id: str,
    actor: str,
    patch_sha256: str,
) -> PullRequestExecutionResult
```

`PullRequestExecutionResult` is a frozen model with `extra="forbid"`:

| Field | Meaning |
|-------|---------|
| `execution_mode` | `live` or `sandbox` |
| `pull_request_number` | GitHub number, or a deterministic simulated number |
| `pull_request_url` | `https://github.com/...` for live, `sandbox://pulls/...` for sandbox |
| `state` | `open` for live, `simulated` for sandbox |
| `repository` | `owner/repository` |
| `base_branch` | protected base, always `develop` |
| `head_branch` | branch that carries the reviewed change |
| `head_commit_sha` | commit of `branch_result`, preserved verbatim |
| `recovered` | `True` when an existing pull request was reused instead of created |

The identifiers of `branch_result` (`branch_name`, `commit_sha_applied`) are preserved
in the pull request result, so a publication can always be traced back to the exact
branch and commit that were authorized.

## 3. Rules that are refused before any write

| Rule | Error code | Status |
|------|------------|--------|
| The base branch is not `develop` | `rama_base_no_protegida` | 422 |
| The head branch is `main`, `master`, `develop` or the base itself | `rama_cabecera_invalida` | 422 |
| The repository does not match the configured destination | `fuente_no_verificable` | 409 |
| The reviewed patch digest is not a SHA-256 | `parche_invalido` | 422 |
| The finding identifier or the actor is invalid | `hallazgo_invalido` / `actor_invalido` | 422 |
| A live branch is published while the live gate is closed | `publicacion_no_habilitada` | 503 |

## 4. Sandbox publication

A sandbox branch produces an explicitly simulated pull request. It can never be read
as a real one: the URL uses the `sandbox://` scheme and the state is `simulated`.

- The number is deterministic: `1000 + sha256(identity)[:8] mod 9000`, where the
  identity is the canonical JSON of repository, base branch, head branch, head commit,
  reviewed patch digest and finding identifier.
- The URL is `sandbox://pulls/{owner}/{repository}/{number}`.
- A durable manifest is written under `{branches_dir}/.pull_requests/{sha256}.json`.
  It records the identity and the published result, and it is written once with
  `O_EXCL` inside the sandbox safe root.

Replay is idempotent: the same identity returns the same number, the same URL and
`recovered=True`, without writing a second manifest and without touching the network.

A second publication for the same repository, base branch and head branch under a
different identity is refused with `pull_request_duplicada` (409). The publication is
never duplicated.

## 5. Live publication (disabled)

The live seam is only reachable when the existing gate is open: `external_writes_enabled`
is true, the project mode is `real`, a token is configured and the repository matches
the configured destination. It is off by default and no test performs a real write.

When the gate is open, the adapter performs, in order:

1. `GET /repos/{repo}` — the token must have push permission.
2. `GET /repos/{repo}/git/ref/heads/{head}` — the head branch must still point at the
   reviewed commit, otherwise `pull_request_cabeza_inconsistente` (409).
3. `GET /repos/{repo}/pulls?state=all&head={owner}:{head}&base=develop` — recovery
   lookup for the exact head, base and repository.
4. `POST /repos/{repo}/pulls` — only when no matching pull request exists.

Recovery returns the existing pull request with `recovered=True` and never posts a
second one. If an existing pull request for the same head and base is bound to another
commit, the adapter refuses with `pull_request_cabeza_inconsistente` instead of
opening a duplicate.

## 6. Uncertain outcomes

An uncertain outcome is never reported as success and never triggers a blind second
`POST`:

| Situation | Error code | Status |
|-----------|------------|--------|
| The transport failed while creating the pull request | `pull_request_no_confirmado` | 503 |
| GitHub answered with a 5xx status | `pull_request_no_confirmado` | 503 |
| GitHub rejected the creation (4xx) | `pull_request_no_creado` | 409 |
| The pull request listing could not be read | `github_no_disponible` | 503 |

After an uncertain outcome the branch and the commit stay untouched. The next attempt
always starts with the recovery lookup, so a pull request that was actually created is
recovered instead of duplicated.

## 7. Known limits

- The sandbox seam trusts `branch_result` as the evidence that the branch exists; the
  port contract does not carry the file path, so the sandbox tree is not re-read.
- The reviewed patch digest is bound to the publication identity and reported in the
  pull request body; the adapter does not recompute it from the remote content.
- The live seam is exercised only with an injected fake HTTP transport; no test
  performs a real GitHub write.
