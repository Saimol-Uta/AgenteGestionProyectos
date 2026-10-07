# Grounded Agent Evidence

Restore evidence reliability in monitoring, code quality, and selective verification without rebuilding the MVP or adding features.

## Scope and constraints

- Preserve snippet/diff boundaries and exact commit provenance; mark completeness and budget limitations explicitly.
- Integrate the existing role-specific ContextBuilder; never silently omit critical evidence.
- Reject unsupported code-absence conclusions, incompatible policy associations, contradictory task states, and uncited task mentions.
- Use offline deterministic test doubles only. No live APIs, Gemini, Ollama, T8/T9, secret/config changes, push, PR, merge, other repositories, or MVP-02.
- Preserve unrelated untracked docs/arquitectura-implementada.md and the local .codegraph/ index.

## Authorized work and acceptance

| ID | Outcome | Route and trigger | Required proof |
| --- | --- | --- | --- |
| T1 | Wire explicit evidence scope and bounded ContextBuilder into the three runtime agents | delegated: preparation for writing and multiple non-trivial files | Observed RED then GREEN for partial function opener, snippet/full-file distinction, budget failure and runtime contexts |
| T2 | Enforce policy applicability and deterministic task-state/reference grounding | delegated: new validation logic and multiple non-trivial files | Observed RED then GREEN for invalid Q2 mapping, non-in-progress tasks, and uncited task mentions |
| T3 | Verify complete offline acceptance and close local commits | delegated: full suite and independent verification | Focused/context/agents/GitHub, orchestrator, full suite, offline benchmark tests, diff check |

- [x] T1: Complete evidence and bounded runtime integration (local commit recorded below at closure).
- [x] T2: Complete deterministic semantic grounding (local commit recorded at closure).
- [x] T3: Complete independent offline acceptance and local commit evidence; candidate left unreviewed by explicit user approval.

## Design

Reuse existing ContextBuilder rather than introduce parallel context assembly. Metadata defaults conservatively to partial diff/snippet; complete-file claims require explicit full-file evidence. Budget overflow must remain explicit and fail closed before provider invocation. Findings must not misuse unknown coverage policies as generic code-review containers. Keep contract changes proportional and backward-compatible where possible.

## Delivery

Existing feature branch: mvp/real-sources. Initial boundary: b69f11f0f2146d5936bc6e78b5c56ef2aaa412cd. Local Conventional Commits only, no AI attribution. Delivery strategy: exception-ok for this local-only repair, because the user explicitly forbids PRs; no chain or remote delivery will be created. Forecast: approximately 700-1000 authored changed lines across two coherent behavior units with regressions (advisory, never code-golf). Native RDD is globally on: assess each committed unit and follow only provider-returned transitions/consent; review never authorizes remote delivery.

## Verification

- PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_evidence_grounding.py' -v
- PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_context.py' -v
- PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_agents.py' -v
- PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_github.py' -v
- PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_orchestrator.py' -v
- PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
- .venv/bin/python -m unittest discover -s pruebas -p 'test_*.py' -v
- git diff --check

Never run pruebas/benchmark.py run: it invokes forbidden Ollama. Optional real-provider tests remain explicitly skipped, not claimed green. No meaningful RED applies to passive tracking documentation; use structural readback.

## Progress and recovery

Exploration complete. Python context/validation gaps confirmed; the reported external TypeScript commit and exact T9 artifacts are not present in this checkout, so the specific incident remains unverified. T1 complete: 9 approved paths, 222 additions + 27 deletions. Actual RED: context 17 tests / 3 missing-metadata errors; agent tests / 2 missing-builder errors (accidental inherited test duplication subsequently removed). GREEN with existing .venv/bin/python: context 20, agents 17, GitHub 42, orchestrator 10, full suite 384 (382 passed, 2 opt-in skips), offline benchmarks 7, diff check exit 0. Parent spot check: context 20 passed. System python3 full discovery failed with 6 pydantic_settings import errors; existing virtualenv resolves this without installs. Strict bounds reserve instructions/schema/output tokens and fail closed before provider. T1 prompt/metadata checks do not alone guarantee arbitrary model compliance; deterministic partial-absence guard belongs to T2. T1 local commit: 140177f1c61e2b9c011f910a32618f193278c0a2. Native assessment: medium / under_budget, 10 paths / 296 authored lines including tracker, base b69f11f; unrelated untracked files explicitly excluded with canonical inventory. No transaction started or receipt claimed. Initial assessment/status sandbox failures required authorized internal Git write escalation; resolved without source changes. T2 complete after user-approved fake.py compatibility fix and one independent requirement correction. Initial partial checks and subsequent test-first failures are preserved in the recovery history; no partial outcome was claimed complete. T2 local commit: 14845ce78d0430abceb6ee81ee11767f693c2487, 11 paths / 437 additions + 25 deletions including tracker. Final committed slice assessment: medium / slice_budget_reached, 16 paths / 750 authored lines against initial b69f11f. All requested offline acceptance and independent checks passed. The user explicitly approved leaving only this candidate unreviewed after the unverified selection-schema block; no START, lineage, native approval or receipt exists. The global RDD switch remains on and unchanged. This passive closure revision finishes T3; report and stop without additional implementation or remote work. Engram mirror locator: odd/evidence-grounding/tasks.

## T2 verified evidence

- Explicit claim_basis binds file-absence conclusions to complete, cited, matching SHA/file/line evidence; partial snippets still support valid visible defects.
- CI policy association needs an exact-SHA cited reported metric; unknown coverage cannot demonstrate absent tests/code. Unassociated findings use explicit null with the generated key and all evidence/claim/severity requirements retained.
- TaskState serialized values remain authoritative; structured task-state claims and concrete title/ID mentions require both citation lists. Adjacent task/pronoun sentences retain subject attribution; ambiguous multi-task status claims request context instead of guessing.
- User approved only the additional fake.py surface. Simulated findings no longer choose an arbitrary CI rule or invent Q3. SQLite nullable storage/report serialization were already compatible and remain unchanged.
- Actual RED: initial 11 tests (8 failures / 5 errors, including 3 corrected fixture errors); resumed controls 16 (5 failures / 3 errors), binding controls 19 (2 failures / 1 corrected fixture error), policy controls 21 (2 failures). Independent verifier found accepted completed-task summary 'Build migration is listed. It is in progress.'; correction RED 24 (3 failures), then GREEN.
- Writer final and independent final: grounding 24, context 20, agents 17, GitHub 42, orchestrator 10, complete suite 408 (406 passed / 2 opt-in Gemini and Ollama skips), offline benchmarks 7, git diff --check exit 0. Independent exact English/Spanish pronoun repros rejected; matching completed/in-progress controls accepted, ambiguity failed closed. Parent grounding spot check: 24 passed.
- T2 worker diff: 10 paths, 424 additions + 23 deletions, including new 231-line tests/test_evidence_grounding.py. All changes normalized before final checks; no live provider or remote operation.
- Residual risk: legacy prose guards are conservative lexical checks, not proof for arbitrary paraphrases. Ambiguous state-bearing prose after task mentions may be rejected; explicit IDs/subjects resolve attribution. Token budgets use an approximation and oversized contexts fail closed.

## Native review exception

Exact provider-issued committed preflight STATUS was executed without token changes. It returned collect / intended_untracked_selection_required (forecast horizon partial; step 1 kind collect). The only eligible untracked paths are .codegraph/.gitignore and docs/arquitectura-implementada.md, both outside the authorized repair. The returned external selection submission requires gentle-ai.review-intended-untracked-selection/v1 JSON, but a bounded installed-documentation lookup could not verify that schema (the installed schema command exposes reviewer/refuter/validator/dry-run only). No fields, tokens, selection payload, binding or approval were guessed, and no START ran. Functional acceptance is complete. The user answered Aprobar to closing only this candidate without native review; this is an explicit candidate-only unreviewed exception, not a native consent/decline receipt and not a mode change. Global and clone switches were not modified. No further lifecycle operation is needed for this explicitly unreviewed candidate.

## Closure

| Work unit | Local commit | Outcome |
| --- | --- | --- |
| T1: bounded role contexts | 140177f1c61e2b9c011f910a32618f193278c0a2 | Verified offline; initial medium / under_budget assessment |
| T2: deterministic grounding | 14845ce78d0430abceb6ee81ee11767f693c2487 | Verified offline; pending slice medium / slice_budget_reached, then explicit candidate-only unreviewed exception |
| T3: offline acceptance record | This documentation revision (`docs(quality): record offline evidence-grounding acceptance`) | Passive structural readback; exact closure identity recorded in Engram mirror |

All 32 added regression tests observed their applicable RED/GREEN cycle. Final functional commands above passed under the existing virtualenv; the 2 real-provider opt-in tests were deliberately skipped. No live benchmark execution, T8/T9, API/LLM calls, push, PR or merge was performed. Only this tracker changed after final functional verification, so the verified source/test bytes remain the same. Unrelated untracked architecture documentation and local CodeGraph index remain untouched by delivery. No remaining implementation task is authorized; STOP.
