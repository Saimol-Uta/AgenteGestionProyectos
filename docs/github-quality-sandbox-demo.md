# G4 — Quality centre: review, eligibility, human decision and publication

This document describes the interface that closes the loop between a detected
defect and a published correction: the manager sees the exact patch review,
the eligibility answered by the backend, their own decision, the branch and
commit, and the pull request receipt. A simulated pull request never appears
as a real one.

## 1. Quick path

1. Open **Centro de calidad de código** and select a candidate finding.
2. Read the chain: detection, confirmed defect, exact patch review,
   eligibility, human decision, branch/commit/pull request.
3. The **Aprobar y crear rama** button is enabled only when the backend
   declares the action eligible and the manager session allows it.
4. On approval the backend creates the branch and the commit, and the receipt
   reports the pull request as *simulated* in sandbox mode.

## 2. The four facts are separate

| Fact | Source | Shown as |
|------|--------|----------|
| Exact patch review | `patch_review` in the finding detail | review id, patch SHA-256, verdict, reason, reviewer, bound repository/commit/file |
| Eligibility | `approval_eligibility` in the finding detail | eligible or blocked, with the backend reason and its Spanish explanation |
| Human decision | `POST .../decision` | approved / rejected / changes requested, branch, commit |
| Publication | `publication` in the detail and in the decision response | published / partial / not requested, pull request number, state, error |

The interface never interprets the authorization rules. It asks the shared
service (`FindingDecisionService.approval_eligibility`) and shows the answer.

## 3. API surface

| Endpoint | Purpose |
|----------|---------|
| `GET /api/v1/runs/{run_id}/findings/{finding_id}` | Detail with `patch_review`, `approval_eligibility` and `publication` |
| `POST /api/v1/runs/{run_id}/findings/{finding_id}/approval-eligibility` | Eligibility of an edited correction (`{"suggested_code_override": "..."}`), manager session required |
| `POST /api/v1/runs/{run_id}/findings/{finding_id}/decision` | The human decision, with the publication receipt in the response |

The eligibility read is side-effect free: it records no decision, writes no
audit entry and never creates a branch or a pull request.

## 4. Eligibility is the backend policy

The origin is configured exactly as the decision endpoint configures it, so
the interface answers the same question the approval gate answers:

- the finding context must be verifiable against the stored run, report,
  snapshot and source;
- the defect must be confirmed by an independent verification of that run;
- the exact patch must be identifiable on the complete source;
- an independent favorable review must cover that exact patch, that
  execution, that repository, that file, that origin commit and that original
  source.

A missing or changed link blocks the approval and reports why. A confirmed
defect, a valid syntax or approved tests are not a review of the correction.

## 5. Editing the correction invalidates the shown eligibility

When the manager edits the suggested code, the patch changes and the review
that covered the original patch stops applying. The interface then asks the
backend for the eligibility of the edited code and shows that answer; the
review of another patch is never presented as a review of the edit.

## 6. Publication and partial failure

| State | Meaning | Interface |
|-------|---------|-----------|
| `not_requested` | No publication was attempted | The receipt says so explicitly |
| `published` | The pull request exists | Number, state and reference; a sandbox one is marked *simulado* and is not a link |
| `partial` | The branch and the commit exist, the pull request failed | The error code and message, plus **Reintentar la publicación** |

The retry reuses the **same idempotency key**: it recovers the publication
instead of asking for a second human approval.

A sandbox pull request carries a `sandbox://` address and the state
`simulated`. The interface renders it as plain text, never as a clickable
link, so it can never be read as a real pull request.

## 7. Integrated sandbox proof

The integrated demonstration runs the real React application against the real
FastAPI application, the real SQLite storage and the real GitHub sandbox
adapter. No API is mocked and no credential is used.

```bash
cd frontend
H5_DEMO_PYTHON=../.venv/bin/python \
  ../.venv/bin/python -m playwright test --config=e2e-backend/g3sandbox.config.ts
```

The server (`tests/g3_sandbox_demo_server.py`) seeds one isolated run in a
temporary SQLite database, with a confirmed defect and an independent
favorable review of the exact patch. It refuses to start unless
`EXTERNAL_WRITES_ENABLED=false` and no credential variable is present, and it
never touches the project database or `runs/`.

The publication receipt of that proof is **pending** until the parent task
integrates the publication service (`application/finding_publication.py`): the
run reports it as an explicit annotation instead of pretending it exists.

## 8. Checklist

- [ ] The detail exposes the review, the eligibility and the publication.
- [ ] The approval is enabled only when the backend says the action is eligible.
- [ ] A manager session without `quality:decide` never sees the approval.
- [ ] An edited correction is evaluated again by the backend.
- [ ] A simulated pull request is marked as simulated and is not clickable.
- [ ] A partial publication offers a retry with the same idempotency key.
- [ ] No external write, no real Gemini, no push and no merge.
