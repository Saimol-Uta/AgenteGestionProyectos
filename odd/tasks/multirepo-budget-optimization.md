# Conservative MultiRepo budget optimization
Branch: `feature/multirepo`; delivery: local work-unit commit only, no publication.
Runner: root `.venv/bin/python`, `PYTHONPATH=<worktree>/src`, `EXTERNAL_WRITES_ENABLED=false`, `PYTHONDONTWRITEBYTECODE=1`.

## Specs
S1. Reduce duplication between `diff` and `full_file` snippets in `github.py` while preserving verifiable evidence, necessary content, and provenance.
S2. Introduce bounded, deterministic full-file retrieval prioritizing missing patches and core source files, without inventing content or silently dropping pending files.
S3. Reserve project budget for both repositories and for GitHub Actions before performing expensive collections.
S4. Prevent excessive consumption of one repository from preventing the next from resolving its SHA and collecting data within its allocation.
S5. Maintain real accounting of requests, bytes, files, time, and estimated tokens, without double charging.
S6. Preserve `unknown`, `incomplete`, and `needs_attention` states when appropriate.
S7. RDD remains disabled; `EXTERNAL_WRITES_ENABLED=false`; no modifications to HORMIGESTION; no modifications to `monitoring`; no push or merge; no Gemini/Groq calls; do not increase limits.

## Tasks
T1 [S1,S2,S5,S6] route=inline; status=completed; Implement de-duplication and bounded deterministic full_file capture in `github.py`.
T2 [S3,S4,S5,S6] route=inline; status=completed; Implement budget reservation for CI and repository isolation in `multirepo.py` and `collection_budget.py`.
T3 [S1–S7] route=inline; status=completed; Regression tests for large commits, duplicate files, exhausted budgets, CI reservation, and repo isolation. Verify partial evidence not complete.
T4 [S1,S2,S6] route=inline; status=completed; Support Git LFS pointers in `github.py` with size validation and prioritize code over media.

## Verification
- Unit tests: `tests/test_multirepo_collection.py` (29 passed), `tests/test_github.py` (54 passed).
- Suite tests: 223 tests passed across 15 modules without errors or external calls.
- Real read-only collection: `hormigestion-frontend` resolved SHA `adc9794`, collected 81 diffs and 85 evidence items without `malformed_payload`.
- Preserved states: `incomplete`, `unknown`, `needs_attention` properly retained when limits exceeded.

