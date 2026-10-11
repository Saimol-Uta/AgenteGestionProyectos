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
- [ ] H2A — In progress; implementation/checks green, native review/commit pending. New pure proposal schema and validate_action_proposal(proposal,capture,evidence_items); field-specific values, immutable capture/project/task binding, explicitly observed current fact and exact supporting evidence. No review transitions or executor. Route: delegated writer (new source/test files).
- [ ] H2B — Pending exploration after H2A. Integrate proposals with existing roles and versioned consultative review/audit, subdividing storage/CLI work into small units as needed. No claimed authorization.
- [ ] H2C — Pending product/security authorization. Define authenticated actor/role and specific-action authorization boundary before any executor; explicit human decisions required for real Trello actions.

## Acceptance and checks
Observe test-first RED/GREEN; tests reject wrong field/value, stale capture/hash, missing/unobserved target fact, wrong/duplicate evidence ID, wrong project/task/category and fabricated authorization/execution. Cover all exact proposal fields, explicit null versus unknown/empty, deterministic serialization and nonmutation. Run focused and full tests plus offline evaluator checks; git diff --check. Native review stays on, per exact current candidate consent and provider bindings. Each closed unit has a local Conventional Commit, identity recorded here.

## Evidence
- H1 closed offline:534tests532pass2opt-in skips +7 evaluator checks (not live performance benchmarks). Real T9PENDING. Existing SQLiteResourceWarning nonblocking.
- Scout mv31ql95-l-i5bn mapped minimal domain/action_proposal.py + tests/test_action_proposals.py; evidence IDs are provenance.id and captures bind exact storedJSON hash. HumanDecisionState has no established transition/authentication policy, so H2A does not invent it.

## H2A verification
- Writer mv333rkp-n-96x8: RED42tests1expected importerror; GREEN47focused/full540run538pass2skip/pruebas7pass; gitdiffcheckclean. New source/test308lines,6tests withsubcases; parent6pass. Pure schema and validator enforce observed current fact plus matching field evidence, wrongcapture/project/task/IDs and fabricatedpermissions rejected; original models unchanged. Review/commitpending.
- Supporting evidence is explicit metadata, not proof of membership in a capture; storage-verification assumed for SnapshotCapture. Review/authentication/executionpolicy remains unimplemented. No API/network/provider calls or role changes.

## Next step
Complete H2A only, review/check/commit, then map next safe H2B slice. Resolve indispensable product/security policy with one focused question before crossing its boundary.

Locator: odd/tasks/action-proposals.md
