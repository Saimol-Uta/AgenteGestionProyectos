# Phase 2: human-approved actions and manager email

Phase 2 connects code suggestions, separate verification branches, reviewed Trello actions and manager email to the web application. Fixture mode is safe for demonstrations: it must never be described as a real GitHub branch, a real Trello change or an email delivered to an inbox.

## One operational workflow

The supported workflow is **Executor → automatic technical Reviewer → human Approver → protected execution**. Browser-only approvals, illustrative code fallbacks and manual review/authorization/dry-run mutation sequences are retired. Missing or incomplete source requires a new complete capture; it is not repaired by fabricating an excerpt-only branch artifact.

## Safety boundaries

- An authenticated, project-scoped operator session, allowed Origin, CSRF token and `Idempotency-Key` are required for mutations. Request bodies cannot supply their own actor or execution grant. The session advertises `governance:proposal_execute`, `quality:decide` and `notifications:email_send` only under the existing governance-enabled and executable configured-project gate; the former manual capabilities are retired.
- Technical review and dry-run are automatic; human approval is still required before execution.
- Live GitHub/Trello writes require `EXTERNAL_WRITES_ENABLED=true`, real source mode and provider credentials. An unsuccessful live call is an error, not permission to silently claim sandbox success.
- Code approval requires the complete captured source file, one exact unambiguous excerpt match, and validation of the complete patched file. Missing/partial source, unavailable syntax validation, changed or ambiguous excerpts are refused before branch creation. No arbitrary code is appended and no excerpt-only artifact is accepted.
- Trello card reads must prove the configured board identity; absent, empty or mismatching board IDs reject the operation before mutation. Priority maps to configured label IDs; unrelated labels are preserved and missing configuration rejects the operation before mutation.
- SQLite audit, proposal execution and decision histories remain append-only, including the human decision reason. Historical report JSON is not rewritten to pretend that a later human decision existed at report time. Regenerated demonstration data starts from the current schema and complete sources; incomplete legacy fixture history is not carried forward.
- SMTP success means the relay accepted the message, not proof of inbox arrival. Missing SMTP configuration produces a local HTML preview, explicitly labeled as simulated email. A configured transport failure produces `FAILED` / `smtp_email_failed`, never simulated success.
- With `MANAGER_EMAIL` configured, pipeline composition sends a run-keyed notification after persisting a completed analysis with findings, blocked/overdue tasks or new proposals. Configured CLI weekly cycles also notify. The existing web weekly-cycle confirmation promises SQLite-only/no external deliveries and retains that contract; use the separate recipient-confirmed email action for that web cycle.

## Endpoints

All paths start with `/api/v1`.

| Method and resource | Purpose | Body |
| --- | --- | --- |
| `POST /runs/{run_id}/findings/{finding_id}/decision` | Approve code and create a verification branch, reject, or request changes | `decision`, `reason`, optional `suggested_code_override`, `target_branch` |
| `POST /projects/{project_id}/proposals/{proposal_id}/execute` | Human decision and reviewed execution | `decision`, `reason`, optional `modified_due_date`, `modified_priority`, `modified_assignee_ids` |
| `POST /projects/{project_id}/notifications/email` | Send the current executive summary or resend an outbox receipt | optional `recipient_email`, `outbox_delivery_id` |

Finding decisions are `approve`, `reject`, `request_changes`; persisted statuses are `approved`, `rejected`, `changes_requested` (or `pending` before a decision). Proposal decisions are `approve_and_execute`, `modify_and_execute`, `reject`. The frontend uses these backend contracts, never a local simulated approval or `approved_branch_created` alias. Manual `/review`, `/dry-run-authorization` and `/dry-runs` mutation sequences are not a supported alternative to `/execute`. Follow the returned `html_preview_url` to view simulated email; do not construct arbitrary filesystem URLs.

## Operator configuration

The API reads **process environment variables**, not `.env` files automatically. Use your existing environment loader or export variables in the shell before starting the API. Never paste tokens into the browser or commit a populated `.env`.

| Variable | Purpose / safe default |
| --- | --- |
| `H5_OPERATOR_TOKEN` | Operator login token, at least 32 characters; generate privately |
| `H5_OPERATOR_ACTOR` | Server-derived audit actor |
| `H5_GOVERNANCE_ENABLED` | Enable protected governance routes; default `false` |
| `H5_ANALYSIS_ENABLED` | Enable analysis prepare/confirm; default `false` |
| `AGENTE_CONFIG_PATH` | Existing project/source configuration file |
| `AGENTE_DB_PATH`, `AGENTE_RUNS_DIR`, `H5_OUTBOX_DIR` | SQLite, run artifacts and outbox locations |
| `EXTERNAL_WRITES_ENABLED` | Explicit GitHub/Trello live-write gate; default `false` |
| `GITHUB_TOKEN` | Fine-grained token scoped to the intended repository, Contents read/write |
| `GITHUB_REPOSITORY` | `owner/repository`; must match the finding's repository |
| `GITHUB_BASE_BRANCH` | Base branch, default `main`; set the actual branch name |
| `BRANCHES_DIR` | Local branch artifact root, default `.runtime/branches` |
| `TRELLO_API_KEY`, `TRELLO_TOKEN` | Trello credentials scoped to the intended board/cards |
| `TRELLO_BOARD_IDS` | JSON map from logical project ID to authorized Trello board ID; required for live writes |
| `TRELLO_PRIORITY_LABELS` | JSON map from project ID to priority to label ID |
| `MANAGER_EMAIL` | Default manager recipient; the UI can confirm/override per send |
| `SMTP_HOST`, `SMTP_PORT` | SMTP relay and port; Gmail: `smtp.gmail.com`, `587` |
| `SMTP_USER`, `SMTP_PASSWORD` | SMTP account and Gmail app password, not the normal account password |
| `SMTP_USE_TLS` | STARTTLS; default `true` |

Example non-secret configuration:

```sh
export EXTERNAL_WRITES_ENABLED=false
export H5_GOVERNANCE_ENABLED=true
export MANAGER_EMAIL='manager@example.org'
export TRELLO_BOARD_IDS='{"PROJECT_ID":"AUTHORIZED_BOARD_ID"}'
export TRELLO_PRIORITY_LABELS='{"PROJECT_ID":{"alta":"HIGH_LABEL_ID","media":"MEDIUM_LABEL_ID","baja":"LOW_LABEL_ID"}}'
```

For Gmail, enable two-step verification and create an app password if allowed by the account/organization. Configure the SMTP variables above, with `SMTP_USER` as the sending Gmail address and `SMTP_PASSWORD` as that app password. If the organization disallows app passwords, use an approved SMTP relay instead. Do not disable TLS to work around authentication failure.

## Demonstration in front of the instructor

Use two terminals from the repository root. The fixture demo creates a disposable database, with external writes disabled and a fixed test clock; it does not run real provider analysis.

```sh
# Terminal 1: isolated demonstration API
.venv/bin/python scripts/serve_web_demo.py --dataset fixture --port 8000

# Terminal 2: frontend
cd frontend
npm run dev -- --host 127.0.0.1
```

1. Open `http://127.0.0.1:5173/`. Select the fixture project `H5-TEST` and sign in with the fixture-only token documented in `scripts/serve_web_demo.py` (`h5-fixture-operator-only-never-use-in-production`). Never use this public demonstration token against a real database.
2. In Quality, inspect the complete captured file, corrected code and technical explanation from a newly generated run. Approve only a suggestion validated against the full file; an override cannot bypass absent source or syntax validation. Show the complete patched sandbox artifact and the backend status `approved`. Do not claim the reference URL proves a remotely created GitHub branch in sandbox mode.
3. In Proposals, inspect technical review and dry-run, then approve a current supported proposal. Each immutable proposal addresses one field: edit the date, priority or assignees matching that proposal. Demonstrate an edited date proposal and a separate assignee proposal. For priority, configure the actual project label map first; an absent map should visibly reject the request without changing a card.
4. In Calendar or Settings, confirm the manager email and send the executive summary. With no SMTP configuration, show `local_outbox_simulated_email`, open the returned HTML preview and inspect the Outbox receipt. With configured SMTP, show `smtp_email_sent` and verify the inbox separately.
5. Open the audit screen and show the server-derived actor, timestamps, retained human reason and execution/decision records. Compare the two newly generated project snapshots and inspect historical charts. Repeating a request with the same idempotency key must not create another branch, Trello mutation or SMTP send.
6. For a live demonstration, use repositories/boards you control, configure real source mode and scoped credentials, and explicitly enable external writes. Show a separate GitHub verification branch (not a commit on the base branch), Trello readback and an SMTP receipt. Do not test live permissions against third-party repositories.

## Verification

The operator explicitly selected the repository's existing `unittest` runner instead of installing unavailable `pytest`.

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s pruebas -p 'test_*.py' -v
cd frontend
npm run typecheck
npm run build
npx playwright test
```

For a real local HTTP/browser check, start a **fresh disposable fixture server** and the frontend first, then run `node scripts/check_web_governance.mjs`. Use `--help` for offline usage without launching a browser or contacting an API. The command discovers generated run/report/proposal IDs, checks two comparable captures, executes sandbox code/proposal decisions and verifies the human reason through public audit. It refuses a non-fixture project; it does not test live providers. Its actual execution result must be recorded separately from syntax/help checks.

Results belong in the feature log, not in an unverified checklist. See `odd/tasks/backend-fullstack-phase2.md` and the focused quality, Trello, email and frontend guides for implementation evidence.
