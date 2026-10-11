# Action proposals

## Objective
Advance H2/REQ-08 with structured, capture-bound action proposals and explicit evidence validation, preserving all agent roles and human control. This document is not an execution authorization.

## Scope and constraints
- Continue mvp/real-sources from H1 CLI commit a79300b; offline only, .venv/bin/python, one source writer at a time.
- Preserve existing run/source-check/decide, providers, storage, grounding, policies and context budgets. No dependencies, credentials, live API/inference, Trello writes, push/PR/merge.
- Snapshot version is exact stored JSON capture digest, not snapshot.id or parsed-model reserialization. Evidence is explicit, run-scoped; IDs are EvidenceItem.provenance.id.
- Exact fields only: start_date/due_date/priority/assignee_ids/state. Unknown remains unknown; no label-derived priorities, invented actors or evidence associations.
- Initial H2A proposals remain pending, not_authorized and not_executed. Technical validation/approval cannot grant execution authority.
- Date values retain existing datetime representation; numeric/boolean/date-only coercion is rejected. Do not invent timezone normalization or execution semantics. Future date/execution policy remains unresolved.
- Authentication, authorized actors, review transition policy and specific action consent must be resolved before permission or execution is implemented.

## Tasks
- [x] H2A — Closed offline in 214a9256c97adff9d546869d99a272a5800ebc22; native approved+acknowledged. New pure proposal schema and validate_action_proposal(proposal,capture,evidence_items); field-specific values, immutable capture/project/task binding, explicitly observed current fact and exact supporting evidence. No review transitions or executor. Route: delegated writer (new source/test files).
- [x] H2B1 — Closed offline in 2cabd581763a0d2abf84c9790827ccf246521e76; native approved+acknowledged. Persist immutable proposals plus exact referenced sanitized submission evidence, optional storage port APIs, idempotency/conflict guards and read-only catalogue queries. Route: one delegated writer; no CLI/roles/review/auth/executor changes.
- [x] H2B2a — Closed offline in 4d0d0868185386f702e6e28493ba8cf10d56203f; native approved+acknowledged. Read-only CLI proposals list PROJECT and show PROJECT PROPOSAL_ID --db-path, explicit JSON projection, no raw supporting DTO/payload/URL/snippet export; preserve existing commands and nonmutation. Route: one delegated writer after mapped scope.
- [ ] H2B2b — In progress, read-only scope mapping; no writer active. Integrate structured proposals and consultative review with existing roles; resolve indispensable review-transition policy first, no authority by report/technical approval.
- [ ] H2C — Pending product/security authorization. Define authenticated actor/role and specific-action authorization boundary before any executor; explicit human decisions required for real Trello actions.

## Acceptance and checks
Observe test-first RED/GREEN; tests reject wrong field/value, stale capture/hash, missing/unobserved target fact, wrong/duplicate evidence ID, wrong project/task/category and fabricated authorization/execution. Cover all exact proposal fields, explicit null versus unknown/empty, deterministic serialization and nonmutation. Run focused and full tests plus offline evaluator checks; git diff --check. Native review stays on, per exact current candidate consent and provider bindings. Each closed unit has a local Conventional Commit, identity recorded here.

## Evidence
- H1 closed offline:534tests532pass2opt-in skips +7 evaluator checks (not live performance benchmarks). Real T9PENDING. Existing SQLiteResourceWarning nonblocking.
- Scout mv31ql95-l-i5bn mapped minimal domain/action_proposal.py + tests/test_action_proposals.py; evidence IDs are provenance.id and captures bind exact storedJSON hash. HumanDecisionState has no established transition/authentication policy, so H2A does not invent it.

## H2A verification
- Writer mv333rkp-n-96x8: RED42tests1expected importerror; GREEN47focused/full540run538pass2skip/pruebas7pass; gitdiffcheckclean. New source/test308lines,6tests withsubcases; parent6pass. Pure schema and validator enforce observed current fact plus matching field evidence, wrongcapture/project/task/IDs and fabricatedpermissions rejected; original models unchanged. Native review-7c4b7c55ce601d69 approved6a936068…; exact acknowledgement burned911f2a66…; ASSESSmedium/consumedtrue/closedderived (no separate verifier). Commit214a9256c97adff9d546869d99a272a5800ebc22.
- Supporting evidence is explicit metadata, not proof of membership in a capture; storage-verification assumed for SnapshotCapture. Review/authentication/executionpolicy remains unimplemented. No API/network/provider calls or role changes.

## H2B1 mapped scope
- Read-only mv336qdt-o-t4q4 completed: exact surfaces ports/storage.py, adapters/storage/sqlite.py and new tests/test_action_proposal_storage.py. No source changes. Parent cancelled nothing: cancellation raced with already completed scout; original final report recovered. Supplemental read-only continuation mv33kt1a-p-u5im is not a source writer.
- APIs save_action_proposal(proposal,supporting_evidence), get_action_proposal(id), list_action_proposals(project), optional/nonabstract; reads return proposal plus frozen evidence. Catalogue keyed proposal_id, exact canonical submission SHA; sameID+samebody idempotent, differentbody rejected. Capture lookup/validation/insert one transaction.
- Add idempotent catalogue DDL/index in a new explicit BEGIN IMMEDIATE after existing history migration/backfill, not base executescript; never alter original snapshot/run/evidence behavior. Read-only never creates/migrates. Missing catalogue clearly unsupported.
- Freeze only explicitly referenced sanitized DTOs, not mutable evidence(id,run_id) rows. Digest proves byte consistency, not authorship, current live state, or capture association. Preserve pending/not_authorized/not_executed.
- Target250-350source/testlines; >400stop/split. RED/GREEN new storage suite, existing storage/history/proposal/CLI/hybrid checks, fulltests,7evaluators, whitespace/native review then localcommit. No real DB/API/credentials or publishing.

## H2B1 verification
- Writer mv33p5we-q-sk2c completed3allowedpaths; source/test360addedlines (soft350 exceeded10, below400hardstop);7tempfiletests. REDmissingAPIs14expectederrors, subsequentreadonlyPermissionError/binarytamper RED failures; GREEN49focused/full547run545pass2skip/pruebas7pass; gitdiffcheckclean; parent7pass.
- Optional typedtuple port APIs; separate transactional catalogue migration; capture verification/save atomic; exactbodySHA+metadata integrity checks; frozen selected DTOs survive later evidence replacement/mutation. SameIDidenticalretry succeeds, conflictingbody denied. Read-only noncreation/missingcatalogue errors preserved. No status/authorship/live-state/captureassociation claim. Caller sanitizedDTOprecondition unchanged. Native review-95c3fe5d27b2bb2f approvedddee67bf…; exactackburnc9f8ab15…; ASSESSmedium/consumedtrue/closedderived, no separate verifier. Commit2cabd581763a0d2abf84c9790827ccf246521e76.

## H2B2a mapped scope
- Read-only scout mv33zv4o-r-o5qr: reuse CLI history handler/parser/dispatch pattern, direct SQLiteStorageAdapter(read_only=True), tupleget/list already validate frozen record/capturefacts; no schema/domain/validator change.
- Exactsource/test paths cli.py + new tests/test_cli_proposals.py; small README/requirements/validation updates to correct old H2-not-touched ledger. List explicit IDs/capture/field/status metadata and supporting_evidence_count; show allowlisted proposal fields including current/proposed values, rationale/impact/evidence_ids, never supporting EvidenceItemDTO.
- Not-found and foreign-project show indistinguishable; static errors never DB/model data. No Settings/providers/orchestrator/artifacts or migrations. No submit/import/transition/actor/executor; source/testtarget<=300lines, >400stop/split.
- Test RED/GREEN temporary persisted fixture, exact JSON/values/types, project isolation/errors/tamper/missingcatalogue/no evidence leakage, DB/schema/files unchanged, oldcommands/hybrid retained. Native review only exact completed unit, no accumulatedbranch.

## H2B2a verification
- Writer mv34ai5t-s-clrg completed5allowedpaths; source/test217addedlines,5newtests/subcases. RED62tests10missing-command errors included6duplicatedimportedTestCases; removedfixtureclassimport without removingoriginaltests; finalGREEN56focused/full552run550pass2skip/pruebas7pass. Parent5pass; gitdiffcheckclean.
- Explicit list/show JSON allowlist excludes supportingDTOpayload/summary/snippet/URL; statuses fixed, unknown/foreignshow indistinguishable, static errors. DBbytes/schema/dirs/artifacts unchanged in isolatedfixtures, oldcommands/hybrid preserved. Projection not a free-textproposal sanitizer or authorship/capture-provenance proof. SQLiteResourceWarning persists; no real DB/T9calls. Native review-4274773cf9fd69e3 approvedd822c7ae…; exactackburnb20d8cde…; ASSESSmedium/consumedtrue/closedderived, no separate verifier. Commit4d0d0868185386f702e6e28493ba8cf10d56203f.

## Next step
H2A+B1+B2aclosed. Read-only scout mv34i2nx-t-3jrd maps minimal structured-draft/technical-binding role integration without inventing review/auth/date policy; no next writer until bounded scope or one indispensable human decision is resolved. Freeze exact sanitized submission evidence: SQLite(id,run_id) evidence rows INSERTORREPLACE are not immutable references; provenance.id alone is insufficient. SHA consistency is not authorship or capture association. Resolve indispensable product/security policy with one focused question before crossing its boundary.

Locator: odd/tasks/action-proposals.md
