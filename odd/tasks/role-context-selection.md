# Role-Specific Context Selection

Restore bounded monitoring after the full TaskCard projection overflow, without increasing the model window or effective input budget.

## Scope and constraints

- Keep fail-closed behavior, explicit scope/completeness and exact selected evidence provenance.
- Select and prioritize role evidence before the final budget estimate. Never clip code, cards or critical facts silently.
- Monitoring: authoritative metrics/global progress, relevant task states and temporal/problem signals, compact CI availability/status, source completeness and necessary commit metadata; no code.
- Quality: retain relevant code evidence and its snippet boundaries.
- Verifier: only snippets, rules and evidence relevant to escalated findings; validate against the same selection.
- Budget from the configured model window minus actual output reservation and serialized instruction/schema/request overhead; align Ollama request num_ctx with its existing configured capability rather than increasing it.
- No live APIs/Ollama/Gemini/T8/T9, dependencies/config/secret changes, new features, multi-repo, push, PR or merge.
- Preserve unrelated untracked docs/arquitectura-implementada.md and local .codegraph/.

## Tasks and acceptance

| ID | Outcome | Route and evidence | Required proof |
| --- | --- | --- | --- |
| T1 | Compact/prioritize monitoring and scope verifier; preserve quality code and safe effective model budgeting | delegated: preparation for writes and multiple non-trivial files | Observed RED then GREEN for A-G; fake-transport proof for configured Ollama window/output; no budget increase |
| T2 | Independently verify offline acceptance and close local commits | delegated: full-suite checks and writer low-effort profile | Context/agents/grounding/providers/orchestrator/full/benchmark/diff; final source readback and parent spot check |

- [x] T1: Complete role-specific evidence selection and safe model budgeting.
- [ ] T2: Complete independent offline proof and local commit closure.

## Confirmed root cause and baseline

The recorded local snapshot has 31 tasks, six diffs and zero CI runs. Monitoring receives metrics, tasks, matched CI and task/CI IDs, NOT diff contents. The previous integration changed compact task rows to full TaskCard.model_dump() (10,130 bytes); repeated list/external IDs and list names inflate context. Existing estimator reproduces 4,090 data tokens / 4,161 runtime payload tokens, versus 2,750 evidence-input budget (4,096 window - 512 output - 834 instructions/schema). A synthetic priority projection is approximately 691 tokens; this is a design estimate, not an implemented result. The snapshot completeness flag is false despite source success and must remain explicit.

Ollama advertises context_tokens=4096/max_output_tokens=768 by default, but currently sends only num_predict. The normal monitoring request reserves 512, quality 768 and verifier 1024 (normal verifier is Gemini); bind configured window and validate output capability rather than assuming every n_ctx token is input. No live server capacity or tokenizer measurement is claimed.

## Verification

Use existing .venv/bin/python (system Python lacks declared pydantic_settings); no install. Tests are deterministic fake-provider/transport only.

- RUN_REAL_LLM_TESTS=0 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_role_context_selection.py' -v
- RUN_REAL_LLM_TESTS=0 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_context.py' -v
- RUN_REAL_LLM_TESTS=0 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_agents.py' -v
- RUN_REAL_LLM_TESTS=0 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_evidence_grounding.py' -v
- RUN_REAL_LLM_TESTS=0 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_providers.py' -v
- RUN_REAL_LLM_TESTS=0 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_orchestrator.py' -v
- RUN_REAL_LLM_TESTS=0 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
- PYTHONPATH=src .venv/bin/python -m unittest discover -s pruebas -p 'test_*.py' -v
- git diff --check

Never run pruebas/benchmark.py run or opt-in real LLM tests. Baseline full suite: 408 tests, 406 passed / 2 opt-in skips. Passive tracker edits have no meaningful RED; structural readback is sufficient.

## Delivery and recovery

Existing branch mvp/real-sources, starting boundary 7e49d58b8b5d9fd632d787b32f56a802635a8cea. Local Conventional Commits only, no AI attribution. Delivery strategy exception-ok for the explicitly local-only fix; forecast 400-650 authored changed lines with bounded code/tests, advisory only and never code-golf. No PR or chain will be created.

RDD remains globally on. The prior candidate-only unreviewed exception applied only to the prior fix, not this candidate. Assess work-unit commits and follow native provider transitions/consent when due; do not guess the unavailable untracked-selection schema or change review switches automatically.

T1 implemented and independently verified offline. T2 functional proof complete; local commit and native assessment pending. Engram mirror: odd/role-context-selection/tasks; locator odd/tasks/role-context-selection.md.

## Observed implementation and proof

- Role selection is semantic necessity, not budget filling: all active/in-progress and overdue/blocked/unknown/contradictory metric cards; otherwise at most one explicitly scoped progress representative. Global deterministic metrics stay intact. Selected IDs/provenance survive; omission count and source completeness are explicit.
- Monitoring includes no code. Quality keeps supplied relevant code whole and fails closed if irreducible. Verifier filters to escalated finding SHA/file/line and cited policy/evidence; invalid or ambiguous scope fails before the provider.
- Final serialized envelope includes escaped JSON, instructions/schema, request fields, exact output and 64-token framing reserve. Ollama receives unchanged configured num_ctx and exact num_predict. Character estimates are not tokenizer-exact proof.
- Same recorded snapshot: 31 tasks, 6 diffs, 0 CI; selected 3/31 with all 3 required signals, omitted 28. Evidence estimate 4161 before / 632 after; allowance 2750 before / 2497 after. Full prompt 4995 before / 1655 after. Window4096 and monitoring output512 unchanged; final estimated total2231 including64reserve. Quality on this snapshot still fails closed before provider because six whole diffs do not fit.
- Test-first: initial role regressions failed; semantic relevance regression18tests/2failures RED then18GREEN. Authorized fixture changes cite selected active T2 rather than omitted completed T1; invented-ID/ambiguous-attribution rejection unchanged. User explicitly approved only tests/test_e2e.py recovery fixture as additional surface.
- Writer and independent verifier: role18, context20, agents17, grounding24, providers15, orchestrator11, full427 (425passed/2opt-inreal-provider skips), offlinebenchmarks7; all exit0. git diff --check exit0. Parent spot: role18 exit0; structural readback confirms relevance selection and scoped E2E fixture.
- Final source/test candidate: 432 additions +40 deletions across11paths (including new232-line role test). No live provider/network/T8/T9, dependency/config/secrets/multi-repo changes or remote delivery.
- RDD effective on, global source, clone-local unset. Prior candidate exception is not reused. Current assessment/closure pending. No native receipt claimed.
