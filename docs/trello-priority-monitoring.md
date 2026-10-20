# Read official Trello priorities without changing the board

The reader accepts the same explicit project-scoped priority-to-label-ID map
as the existing corrective-action adapter. Label names alone are not priority
evidence. This configuration neither creates proposal rules nor grants writes.

## HORMIGESTION configuration

For an explicitly scoped local process, prepare this environment value:

```sh
export EXTERNAL_WRITES_ENABLED=false
export TRELLO_PRIORITY_LABELS='{"HORMIGESTION":{"alta":"6a957e9173d4d13d0c5db616","media":"6a957e9173d4d13d0c5db615","baja":"6a957e9173d4d13d0c5db614"}}'
```

Alternatively use the same object under the top-level YAML key
`trello_priority_labels`. The engine and API use the same project scope; an
explicit API project map is propagated to analysis. No permanent configuration
or dotenv file was changed by this implementation. Restart an existing process
after intentionally changing its environment; do not launch a real model analysis
without separate authorization.

The map must define unique high, medium and low labels (aliases `alta`, `media`,
`baja` are supported). The reader preserves the configured names, matching action
preconditions. Two priority labels on one card fail collection rather than select
a convenient value. Missing label IDs remain unobserved; an observed empty label
set yields `priority=null`. The `bloqueada` label is not a priority. Existing
unconfigured readers still accept explicit priority and do not infer it from names.

## Monitoring scope

- Selected active cards carry state, priority, owner IDs, available owner names
  and the observation mask. Existing evidence validation and budget checks remain.
- Descriptions of at most 600 characters are transmitted whole. Longer ones are
  absent with `detail_availability=omitted_length_limit` and their character count;
  structured descriptions can instead supply whole `Objetivo`/`Alcance` (or
  `Objective`/`Scope`) sections within 600 characters, explicitly marked
  `selected_sections`. Request more evidence rather than infer omitted content.
- Only the existing explicit-dependency parser supplies `explicit_dependencies`.
  Proposed predecessors are not automatically activated or persisted as relations.
- Archived/excluded cards remain in snapshots and raw metrics, but not in active
  Monitoring task rows or corrective choices. Raw unknown-state counts are data
  observations, not active deadline risks. No historical data is rewritten.

## Before a first real correction

Trello needs an owner-backed exact measure and rule, fresh evidence, technical
review/dry-run, human approval, then separately authorized card/field credentials
and writes. Passing sandbox tests does not approve a change to the official board.

GitHub's branch/commit/PR sandbox circuit already exists. It still needs a genuine
reproducible defect, independently favorable exact-patch review, fresh source and
human approval before separately authorized publication against `develop`. The
Zod 4 `z.int()` candidate was rejected by the real verifier and is not a defect.

No Gemini calls, dependency activation, live actions, UI redesign or REQ-06 are
part of these changes. Focused regression commands are recorded in
`odd/tasks/operational-circuits.md`.
