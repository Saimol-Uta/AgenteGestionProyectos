# Weekly automation

## Objective
Advance H3/REQ-04/06/09/10 safely after H2 closed offline (945c15e, c6d5277, ef898c1; documentation efc8976). Preserve the existing orchestrator, agent roles, sources, reports and SQLite architecture. This feature never authorizes live Trello execution.

## Evidence and constraints
- Read-only scout mv4eomyk-1f-wv9t: H3 requirement docs/requisitos-y-plan-de-implementacion.md:129+ requires configured weekly scheduling, queryable notifications and failure/duplicate controls.
- Friday is only a soft architecture preference, not a binding configured schedule. Current settings/config have no weekly weekday/time/period rule. Do not choose or activate defaults.
- Owner input must explicitly supply project, weekday, local time, America/Guayaquil and period rule. First pure component can support an explicit previous_scheduled_week window (previous local scheduled boundary to most recent elapsed scheduled boundary); it must not configure that policy for any project.
- Inject evaluation time, require timezone-aware datetime. No wall-clock calls, cron dependency, host service activation, pipeline/report generation, providers, network or database in H3A.
- A requested cutoff/window never proves historical/live capture state. Actual captured_at remains separate; no backdating snapshots, synthetic reports presented as real, or inferred source/actor identity.
- Single writer, <=350 changed source/test lines per unit, hard400; test-first applicable deterministic checks plus regressions/native exact-unit review/local commits. No push/PR/merge/dependency/credential changes.
- H2 final independent 623run621passed2optinskips/7offline evaluator checks; SQLiteResourceWarning remains nonblocking. Real T9/Ollama/Gemini validation and trusted execution remain unperformed.

## Tasks
- [ ] H3A — In progress: pure explicit weekly schedule/window planner. Exactly domain/weekly_schedule.py and tests/test_weekly_schedule.py. Required owner fields, strict validation, stable project/schedule/window identity, elapsed/next boundary in Guayaquil and UTC; planning only, no scheduling/execution. No default schedule.
- [ ] H3B — Pending exploration: unique project/period job records with immutable attempts, version/CAS, controlled retries and failure evidence. Preserve existing ExecutionRun/report records; no implicit pipeline calls.
- [ ] H3C — Pending exploration: idempotent offline notification outbox and local delivery/failure states, queryable history; no email/Slack/Trello credentials/network.
- [ ] H3D — Pending exploration: explicit offline plan/tick/inspection CLI linking actual stored capture/report facts, not generic synthetic activities or private-code inference. Activation requires owner-selected schedule and data scope.

## H3A acceptance
Strict nonblank project; weekday integer Monday0–Sunday6 (not bool/string), exact HH:MM time, required timezone and explicit supported window rule. Reject missing/unknown/extra/live/unsafe-constructed fields and naive evaluation times. Before/exact/after schedule boundary, weekend/year/leap rollover, UTC-equivalent evaluations, prior/next boundary and previous seven local days window checked deterministically. Return planning-only metadata, never executed/notified. Inputs unchanged, deterministic identity; different project/schedule changes identity. No runtime configuration edits, default Friday/time/cutoff or historical capture inference.

## H3A verified handoff
- Writer mv4fstkm-1o-ktr4 completed exactly domain/weekly_schedule.py + tests/test_weekly_schedule.py,255addedlines115source140tests,seven deterministic tests.
- REDmissingmodule observed; GREEN7planner17domain11dueSoon12history630run628passed2optinskips7evaluator exactPYTHONPATH=src; whitespaceclean. Existing SQLiteResourceWarning remains nonblocking.
- WeeklySchedule strict/frozen/extra-forbid requires project/weekday0–6/exactHH:MM/Guayaquil/previous_scheduled_week; no defaults or configuration activation. Unsafeconstructed/mutated rawfields and extra claims revalidated before use.
- Injected aware datetime only, local latest elapsed boundary <=evaluation, next+7localdays, [elapsed-7localdays,elapsed) cutoffelapsed; every boundary local+UTC, deterministic SHA fullschedule+UTCendpoints, not capture/event proof. planning_only constant even unsafe construction, no execution/notification/DB flags.
- Parent read115line source; native exact-unit review/localcommit pending. H3B scout mv4fx48c-1p-hail read-only lifecycle/storage map running, no activewriter.

## Next step
Implement and verify H3A only; continue storage/outbox/CLI through fresh bounded mapping. A real schedule remains unconfigured until its owner explicitly chooses the fields.
