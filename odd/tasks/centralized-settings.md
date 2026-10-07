# Centralized environment settings

## Objective
Add pydantic-settings .env support to existing prototype without altering architecture or provider routing. User authorizes code/docs/tests; no commits or remote publishing.

## Scope and decisions
Single Settings implementation in src/agente_gestion/application/config.py; SystemConfig compatibility alias if needed. Keep YAML and explicit CLI overrides. Priority: CLI overrides > OS environment > .env > YAML > defaults. Read GEMINI_API_KEY, ALLOW_REMOTE_AI, OLLAMA_BASE_URL, GEMINI_MODEL and preserve QUALITY_TIMEOUT_SECONDS. Existing .gitignore already ignores .env/.env.* and permits .env.example. Never write real credentials; tests use temporary dotenv files and synthetic values with isolated env. Credentials excluded from logging, serialization and persistent fingerprints; reveal only to Gemini adapter composition if SecretStr used. No new infrastructure or real model calls.

## Tasks
- [x] S1 (implemented; writer checks pass; no commit authorized): Implement centralized Settings, compatibility, dependency, .env.example and README plus test-first config regressions.
- [x] S2 (independent checks passed; template inspection blocked): Independent tests, default fake CLI regression, secret/ignore/precedence checks and diff/whitespace check.
- [x] S3 (closed with limitations; no commits authorized): Close with verified results and explicit native review limitations; no commits without authorization.

## Acceptance
OS variables beat .env; overrides remain intentional; YAML defaults preserved; boolean false parsed as false; missing/empty key remains unavailable; no secrets in example/source/output; all deterministic tests and preserved benchmark tests pass, no benchmark reruns.

## Evidence
Parent inspected complete existing config.py, pyproject and .gitignore; uses BaseModel/manual os.getenv and no ALLOW_REMOTE_AI parsing. Config consumers CLI and orchestrator identified. Previous prototype has no HEAD, all files untracked; native review complete candidate previously exceeded context; no native authority created. Package documentation tools discovery returned unavailable in parent; writer should consult official documentation through available read-only doc tools or report limitation.

## Next
Writer reports .venv installed pydantic-settings2.15.0; centralized Settings/SystemConfig alias, YAML source precedence, SecretStr excluded repr/dump, env-file CLI and safe template/docs. Observed RED ImportError then GREEN11 config tests/8 CLI tests; full112 tests (110 pass/2 skips),7 existing pass; fake smoke success. First independent verifier timed out stalled before results; no evidence accepted. One bounded retry launched with direct tools/no harness modifications. Parent safety blocked .env.example read; do not bypass via other tools, template content remains unverified. Native inspect requires intended-untracked selection; no authority/start/approval. Retry verifier observed19 config/CLI pass,112 full tests (110 pass/2 opt-in skips),7 existing pass and fake CLI smoke pass. Confirmed source priority, compatibility alias, SecretStr repr/dump exclusion. git check-ignore .env/.env.local confirms ignored; .env.example not ignored. diff --check clean but untracked limitation remains. .venv ignored internally; root ignore optional future hygiene not blocker. Parent spot-read full Settings code and reran11 config tests PASS. Template direct read remains blocked, no bypass. No native authority/approval or commits/staging/publishing. Commit evidence unavailable by explicit user constraint.
