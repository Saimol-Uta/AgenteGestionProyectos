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
- [ ] T2: Complete deterministic semantic grounding.
- [ ] T3: Complete independent offline acceptance and local commit evidence.

## Design

Reuse existing ContextBuilder rather than introduce parallel context assembly. Metadata defaults conservatively to partial diff/snippet; complete-file claims require explicit full-file evidence. Budget overflow must remain explicit and fail closed before provider invocation. Findings must not misuse unknown coverage policies as generic code-review containers. Keep contract changes proportional and backward-compatible where possible.

## Delivery

Existing feature branch: mvp/real-sources. Initial boundary: b69f11f0f2146d5936bc6e78b5c56ef2aaa412cd. Local Conventional Commits only, no AI attribution. Delivery strategy: exception-ok for this local-only repair, because the user explicitly forbids PRs; no chain or remote delivery will be created. Forecast: approximately 700-1000 authored changed lines across two coherent behavior units with regressions (advisory, never code-golf). Native RDD is globally on: assess each committed unit and follow only provider-returned transitions/consent; review never authorizes remote delivery.

## Verification

- PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_context.py' -v
- PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_agents.py' -v
- PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_github.py' -v
- PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_orchestrator.py' -v
- PYTHONPATH=src python3 -m unittest discover -s tests -v
- python3 -m unittest discover -s pruebas -p 'test_*.py' -v
- git diff --check

Never run pruebas/benchmark.py run: it invokes forbidden Ollama. Optional real-provider tests remain explicitly skipped, not claimed green. No meaningful RED applies to passive tracking documentation; use structural readback.

## Progress and recovery

Exploration complete. Python context/validation gaps confirmed; the reported external TypeScript commit and exact T9 artifacts are not present in this checkout, so the specific incident remains unverified. T1 complete: 9 approved paths, 222 additions + 27 deletions. Actual RED: context 17 tests / 3 missing-metadata errors; agent tests / 2 missing-builder errors (accidental inherited test duplication subsequently removed). GREEN with existing .venv/bin/python: context 20, agents 17, GitHub 42, orchestrator 10, full suite 384 (382 passed, 2 opt-in skips), offline benchmarks 7, diff check exit 0. Parent spot check: context 20 passed. System python3 full discovery failed with 6 pydantic_settings import errors; existing virtualenv resolves this without installs. Strict bounds reserve instructions/schema/output tokens and fail closed before provider. T1 prompt/metadata checks do not alone guarantee arbitrary model compliance; deterministic partial-absence guard belongs to T2. Next: parent local T1 commit and native risk assessment, then T2. Engram mirror locator: odd/evidence-grounding/tasks.
