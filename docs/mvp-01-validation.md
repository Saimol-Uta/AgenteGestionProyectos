# MVP-01 Real Sources Offline Validation Report

> **Nota de alcance:** las pruebas de T8/T9 documentadas aquí evalúan
> integración y fiabilidad del MVP; **no certifican el cumplimiento final
> de REQ-01…REQ-11**. La correspondencia conceptual Ejecutor/Revisor/Aprobador
> no sustituye la implementación futura de acciones aprobadas.
> [Matriz de requisitos y hoja de ruta](requisitos-y-plan-de-implementacion.md).

## Actualización de validación — 10 de octubre de 2026

> **Este apartado es posterior al informe offline original.** El texto
> histórico que sigue refleja T0–T7 antes de configurar las credenciales,
> no el estado presente. No se reescriben mediciones antiguas como actuales.

### T8 — fuentes reales completadas

Entorno piloto: tablero Trello `PROYECTO HORMIGESTION`
(`6a957e9173d4d13d0c5db608`) y repositorio público
`Josu-F1/hormigestion-backend` (`main`).

| Provider | Estado | Count | SHA |
|---|---|---:|---|
| Trello | success | 31 | N/A |
| GitHub | success | 6 | `5412744023c4b3961be9cd783609423d12280f64` |
| GitHub Actions | success | 0 | `5412744023c4b3961be9cd783609423d12280f64` |

Comando: `PYTHONPATH=src .venv/bin/python -m agente_gestion
source-check all --sources real`. El cero en Actions no es un fallo de
conectividad: faltan runs del SHA. Por eso la política de cobertura
o pruebas puede quedar `unknown`.

### T9 — ejecuciones reales observadas, cierre pendiente

| Run ID | Estado | Hallazgo / lección |
|---|---|---|
| `run-20261007-160935-557437` | failed | Ollama inaccesible en localhost:11435; etapa monitoring |
| `run-20261007-162232-675554` | failed | Timeout de lectura 120 s en quality; Ollama ya respondía |
| `run-20261007-163608-df22fc` | needs_attention | Reporte y 8 artefactos; 18/30 tareas completas, 60 %, finding C-001 falso positivo por snippet parcial; escalación 1 y verificación 0 por Gemini HTTP 503 |
| `run-20261007-173724-2b49a3` | failed | ContextBuilder impidió truncamiento: Monitoring estimó 4161 tokens de evidencia con budget 2750 |

La corrida que generó reporte distinguió correctamente ausencia de CI
mediante `evidencia_insuficiente`, pero Monitoring describió una actividad
completada como en curso y Quality asoció un finding no sustentado con Q2.
Por tanto **T9 no se declara validado**.

### Cambios posteriores probados offline

- `140177f`: ContextBuilder integrado, metadata de alcance y límites.
- `14845ce`: grounding determinista y asociación semántica de findings.
- `e13c3e0`: selección role-specific: 3 tareas relevantes de 31,
  prompt estimado Monitoring ~1655 tokens y reserva para salida.
- `393ceb3`: adaptadores remotos GroqProvider/MistralProvider.
- `7a44ee1`: presupuesto seguro de Quality según proveedor.
- `38d4f8a`: variables y Settings para Quality híbrido.
- `3c4885f`: CLI `--quality-provider` y wiring de proveedores.

El último trabajo con Antigravity reportó **472 tests y 7 benchmarks
offline aprobados**, sin corrida real Groq/Mistral ni revalidación T9.
Son resultados reportados, no tests ejecutados al editar este documento.
Los tests opt-in LLM/Gemini pueden estar fuera de los checks offline.

### Próximo criterio de aceptación

Preparar `.env` local sin divulgar credenciales, seleccionar modelo remoto
vigente, levantar Ollama para Monitoring y Report, ejecutar smoke fake,
y después `run hormigestion --sources real --allow-remote-ai
--quality-provider groq`. Inspeccionar la evidencia y el informe, evitando
aprobar claims de código basados únicamente en fragmentos truncados.
Documentar run_id/SHA, duración, hallazgos, escalaciones, verificaciones y
políticas unknown. No hacer cambios externos sin autorización.

**Persisten como trabajo futuro:** CI con artefactos verificables de
pruebas/cobertura, soporte multi-repositorio por proyecto, scheduler semanal,
notificaciones e interfaz de gestión. T8 exitoso no equivale a CI aprobado.

---


- Scope: Offline verification complete for branch `mvp/real-sources` at code commit `fed1ac7d338cf4c4ba2c3e0ac5f20da0c68df1b6` (baseline `d08bff4`). Live validation is blocked: T8 configuration preflight failed; T9 real-source E2E was not executed (see final handoff below).
- Isolation: Executed in isolated environment (`RUN_REAL_LLM_TESTS` unset, no network, no live credentials, zero live API/LLM calls).

## Verification Suites (Foreground Only)

| Suite / Command | Total | Passed | Skipped | Runner Time | Wall Time |
|---|---:|---:|---:|---:|---:|
| `.venv/bin/python -m unittest tests.test_source_collection tests.test_real_source_pipeline tests.test_orchestrator tests.test_storage tests.test_cli_sources` | 79 | 79 | 0 | 0.214s | 0.487s |
| `.venv/bin/python -m unittest tests.test_actions tests.test_github tests.test_trello` | 115 | 115 | 0 | 0.031s | 0.270s |
| `.venv/bin/python -m unittest discover -s tests` | 356 | 354 | 2 (opt-in LLM) | 0.607s | 0.886s |
| `.venv/bin/python -m unittest discover -s pruebas -p 'test_*.py'` | 7 | 7 | 0 | 0.001s | 0.074s |
| `git diff --check` | - | Clean | 0 | - | 0.004s |
| `git status --short` (at verified code commit) | - | Only untracked ODD task document | 0 | - | 0.003s |

*Note: Commands represent distinct test slices; test totals are not summed across overlapping suites.*

## Actual CLI `main()` Fixture Smokes

Both invocations exited 0 with human decision `pending`, using `--env-file /dev/null`, cleared environment, strict zero-hit HTTP sentinels and `--db-path {tmp}/smoke.db --output-dir {tmp}/runs`. The temporary run durations were 0.0053s and 0.0024s respectively; these are not live performance measurements. Note: `--fake` simulates LLM providers only; `--sources real` targets external source APIs.

| Scenario / Fixture | Recommendation | Status | LLM Calls | Stored `llm_calls` | Stages | Artifacts |
|---|---|---|---:|---:|---:|---:|
| `run fixture_1_standard --fixture fixture_1_standard --fake` | `solicitar_cambios` | `awaiting_human` | 4 (simulated verifier on Q3 finding) | 4 | 4 | 8 |
| `run fixture_3_empty_and_unknown --fixture fixture_3_empty_and_unknown --sources fixtures --fake` | `evidencia_insuficiente` | `awaiting_human` | 2 (no findings; verifier skipped) | 2 | 4 | 8 |

Generated artifacts per run: `snapshot.json`, `metrics.json`, `code_findings.json`, `verification.json`, `monitoring.json`, `report.json`, `report.md`, `execution_metrics.json`.

## Core Acceptance Invariants Verified

- **Transport & Security:** Fixed-host HTTPS GETs (`api.trello.com`, `api.github.com`), with authentication confined to adapters; read-only scope and no automated retry loops. Tested credential-bearing SHAs, patches and metadata are rejected at boundaries with static errors rather than fabricated identifiers.
- **Failures & Availability:** Zero hybrid fixture fallbacks on source retrieval failure. Missing sources trigger honest `WeeklySnapshot.is_complete = False` and `Q_SOURCE_AVAILABILITY_INCOMPLETE` (`unknown`), yielding `evidencia_insuficiente`. Known mandatory policy failures retain `solicitar_cambios` precedence.
- **SHA & CI Pinning:** Target SHA resolved and pinned strictly across diffs and Actions; empty commits preserve target SHA; Actions API unknown metrics (`None`) prevent artificial passes.
- **Audit & Metrics Integrity:** Failed code units omit `target_commit_sha` (`null`). `attempt_count=1` denotes provider unit attempts, not low-level HTTP requests. Reported stage durations sum measured unit intervals (`perf_counter`) rather than wall-clock time.
- **Governance:** Final reports require explicit human review (`human_decision: pending`, `awaiting_human`). LLM contracts preserve Gemini `generationConfig.responseFormat.text.schema` with MIME `APPLICATION_JSON` and unchanged Ollama behavior.

## Work Units (mvp/real-sources)

| Task | Commit | Purpose / Delivery Unit | Parent Review |
|---|---|---|---|
| T0 | `275ad13` | Verifier timeout and dotenv-isolated tests | Verified / Acked |
| T1 | `b8da2e9` | Source, policy, collection and audit contracts; local YAML policies | Verified / Acked |
| T2 | `d4501c4`, `add5476` | Bounded HTTP transport and Trello source adapter | Verified / Acked |
| T3 | `5637856` | Bounded GitHub commit diff source adapter | Verified / Acked |
| T4 | `1faf246` | Bounded GitHub Actions CI source adapter | Verified / Acked |
| T5a-c | `39bf8f1`, `5d2f42f`, `9e281b2` | Composite sources, CLI diagnostics, pipeline integration | Verified / Acked |
| T6 | `fed1ac7` | Direct boundary secret guard on pinned SHA & audit | Verified / Acked |

## Boundaries and Limitations

1. **Test Exclusions:** 2 live LLM tests remain skipped (require explicit opt-in `RUN_REAL_LLM_TESTS=1`).
2. **Pending Live Validation:** T8 (live external source verification) and T9 (controlled real-source E2E with applicable Ollama/conditional Gemini calls) remain blocked; no live source or LLM authentication was verified.
3. **Local Scope:** Benchmark timings reflect local deterministic runs, not production cloud SLAs.
4. **Current vs Historical Data:** External source collection captures current state at retrieval (`captured_at` UTC); no retroactive historical state reconstruction.
5. **CI Quality Data:** GitHub Actions API does not provide test counts or coverage; fields remain `None` (`unknown`), not synthetic 0% or 80%.
6. **Sanitization Proof:** Sanitization verified for tested configured secret patterns; does not constitute a mathematical proof against arbitrary untracked token formats.
7. **Test-First Evidence:** Initial T5c production behavioral RED was not established (mock-payload failures are not behavioral proof). T6 observed 2 genuine failures among 37 tests before the direct SHA guard, then GREEN; already-safe cleanup received coverage-only locks.

## T8/T9 Final Handoff: Blocked, Not Live-Verified

At documentation commit `3647518`, the secure runtime loader and one actual `PYTHONPATH=src .venv/bin/python -m agente_gestion source-check all --sources real` preflight returned exit **1** in approximately **0.30s**. A non-mutating transport wrapper observed **0 Trello, 0 GitHub and 0 Actions GETs**. Trello/GitHub diagnostics reported `configuration`; Actions reported `dependency_blocked`. Counts, target SHA and actual CI runs are unknown, not zero-result successful fetches.

Missing configuration: `trello_api_key`, `trello_token`, `trello_board_id`, all three Trello status-list mapping groups, `github_owner`, and `github_repo` (alias `github_repository`). GitHub token/branch/full SHA are optional, not missing mandatory prerequisites. Gemini key, remote-AI permission and Ollama base URL were configured, but no connectivity, credentials, model availability or LLM calls were verified. T9 was **not executed** because its source prerequisites failed; no fixtures substituted for live evidence. Configuration and secrets were not modified.

Configure values locally—never paste credentials into chat: `TRELLO_API_KEY`, `TRELLO_TOKEN`, `TRELLO_BOARD_ID`, the relevant JSON-list mappings `TRELLO_PENDING_LIST_IDS`, `TRELLO_IN_PROGRESS_LIST_IDS`, `TRELLO_COMPLETED_LIST_IDS`, and `GITHUB_OWNER` / `GITHUB_REPO` (alias `GITHUB_REPOSITORY`). Then resume:

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m unittest discover -s pruebas -p 'test_*.py'
PYTHONPATH=src .venv/bin/python -m agente_gestion source-check all --sources real
# Only after source checks succeed; confirm IS_FAKE=false in local configuration.
PYTHONPATH=src .venv/bin/python -m agente_gestion run proyecto-demo --sources real
```

The final command is a **future** real E2E step, not executed evidence. Review its generated report and audit records; human decision must remain pending. Missing CI test counts/coverage may still produce `evidencia_insuficiente`. No source writes, push, PR, merge, deployment or MVP-02 work was performed.

## H0 — evidencia offline actual en `6f60007`

**Baseline e invariantes híbridos verificados offline; T9 real sigue PENDING.**
Este apartado agrega evidencia ejecutada para
[`planning-tracking-history`](../odd/tasks/planning-tracking-history.md), sin
reemplazar cifras ni observaciones históricas T8/T9 y sin declarar cumplido
ningún REQ-01…REQ-11.

### Comandos ejecutados y resultados

| Comando exacto | Ejecutados | Aprobados | Omitidos | Resultado |
|---|---:|---:|---:|---|
| `.venv/bin/python -m unittest tests.test_hybrid_config tests.test_hybrid_pipeline tests.test_quality_context_budgets tests.test_evidence_grounding tests.test_role_context_selection tests.test_remote_providers` | 87 | 87 | 0 | OK |
| `.venv/bin/python -m unittest discover -s tests` | 472 | 470 | 2 | OK (skipped=2) |
| `.venv/bin/python -m unittest discover -s pruebas -p 'test_*.py'` | 7 | 7 | 0 | OK |
| `git diff --check` | — | — | — | Sin errores de whitespace |

Los slices se solapan: no sumar sus totales. Las 7 pruebas de `pruebas`
son checks deterministas del evaluador, no un benchmark de rendimiento real.
Discovery emitió el rechazo esperado de argparse a `invalid_status` y un
`ResourceWarning` por conexión SQLite sin cerrar; no hubo fallos de tests.

### Contratos comprobados con tests existentes

- **Hybrid/fake y CLI:** `test_fake_mode_preserves_fake_provider_for_all_agents`
  verifica FakeProvider para todos los roles con Quality ollama/groq/mistral.
  Los tests de wiring mantienen Monitoring/Report en Ollama y Verifier en
  Gemini cuando está habilitado; `test_cli_quality_provider_flag_override`
  comprueba `--quality-provider mistral` sobre el entorno usando orchestrator
  mock, no una ejecución híbrida real.
- **Settings:** `tests.test_config` (incluido en discovery) comprueba
  overrides > entorno OS > dotenv sintético > YAML sintético > defaults,
  incluyendo `ALLOW_REMOTE_AI=false`. `tests.test_hybrid_config` comprueba
  precedencia de Quality, selección inválida y protección de secretos.
- **Presupuestos fail-closed:** `tests.test_quality_context_budgets` y
  `tests.test_role_context_selection` comprueban reserva de salida/margen,
  ventana local y remota configurada, rechazo antes de provider/transport
  cuando no cabe la evidencia y conservación de código completo sin recorte
  silencioso. Las respuestas Groq/Mistral usan transport mocks.
- **Anchors/completitud:** `tests.test_evidence_grounding` comprueba SHA,
  archivo, línea y citas; rechaza claims de ausencia sobre snippets parciales
  y permite el control positivo de archivo completo coincidente. También
  rechaza estados de tareas falsos y métricas CI sin procedencia coincidente.
- **CI ausente/UNKNOWN:**
  `test_pipeline_ci_mismatched_sha_evaluates_to_insufficient` comprueba CI
  ausente para el SHA objetivo y `evidencia_insuficiente`;
  `test_policy_evaluator_demonstration_mandatory_q1_q2_yield_evidencia_insuficiente`
  comprueba que tests/cobertura no reportados dejan Q1/Q2 `unknown`, incluso
  con workflow exitoso. Ambos están incluidos en discovery; no se inventó
  un test denominado H0.

### Límites y siguiente validación real

`RUN_REAL_LLM_TESTS` no estaba habilitado. Quedaron omitidos
`test_real_ollama_local_smoke` y `test_real_gemini_remote_smoke`.
No se ejecutaron source-check real, inferencia local/remota ni T9; tampoco
se inspeccionaron valores de credenciales ni contenido de dotenv. Un control
posterior mediante Settings comprobó únicamente presencia: Groq y Mistral
sin clave; Gemini con clave; Trello con clave/token/board; GitHub sin token,
con owner/repo. Son booleanos de configuración, no validación de secretos ni
conectividad. La evidencia procede de fixtures, fake providers y transport
mocks, no de autenticación o conectividad.

Los tokens son estimaciones por caracteres JSON (3 chars/token), no conteos
exactos del tokenizer ni garantía de ajuste al template del proveedor. Los
modelos/ventanas configurados y el parsing mock no prueban disponibilidad o
compatibilidad actual de modelos Groq/Mistral/Gemini remotos.

**Pendiente con autorización explícita:** comprobar fuentes reales del SHA
objetivo, disponibilidad de Ollama y modelo remoto elegido, y repetir T9
híbrido registrando run_id/SHA, métricas, grounding, verificaciones y políticas
unknown; mantener decisión humana pendiente. `--fake` no vuelve offline
`--sources real`. No se modificaron dependencias, entorno ni configuración,
ni se hicieron commits/staging. Por ser documentación pasiva sin cambio de
comportamiento, no aplica RED; la verificación usa regresiones existentes.

## H1 — actualización offline H1D2 sobre `ea29dda`

**T9 real permanece PENDING; REQ siguen parciales.** Evidencia nueva, sin
reescribir el registro histórico ni afirmar validación externa/model performance.

| Unidad previa | Commit | Disposición nativa disponible en el tracker |
|---|---|---|
| H1A | `748379c` | Declined para ese candidato; verificación independiente verde |
| H1B | `be369c5` | Declined para ese candidato; verificación independiente verde |
| H1C | `939876f` | Approved + acknowledged; verificación independiente verde |
| H1E | `3b6f269` | Approved + acknowledged; ASSESS cerrado |
| H1D1 | `0617ef1` | Approved + acknowledged; ASSESS cerrado |

H1D2: implementación/verificación offline; disposición nativa y cierre son
responsabilidad del padre, no se invocó revisión desde el writer.

| Comando exacto | Resultado observado H1D2 |
|---|---|
| `.venv/bin/python -m unittest tests.test_cli_history tests.test_snapshot_history tests.test_task_comparison tests.test_cli tests.test_cli_sources tests.test_hybrid_pipeline` | 70/70 aprobados |
| `.venv/bin/python -m unittest discover -s tests` | 534 ejecutados, 532 aprobados, 2 omitidos opt-in |
| `.venv/bin/python -m unittest discover -s pruebas -p 'test_*.py'` | 7/7 aprobados: tests del evaluador, no benchmarks de rendimiento de modelos |
| `git diff --check` | Sin errores de whitespace |

Baseline suministrado: 527 ejecutados/525 aprobados/2 omitidos + 7 evaluadores.
Los siete nuevos tests de `tests/test_cli_history.py` usan `planning_example()`
y SQLite temporal: A/B con el mismo `snap-p`, responsables A→B, fecha límite
15→19 de octubre de 2026, prioridad normal→high y estado pending→en_curso.
La CLI devuelve exactamente esos cuatro campos con todos los before/after,
IDs explícitos y fechas originales. RED antes de modificar fuente: 70 tests,
10 errores (incluidos subtests), todos por `history` ausente de argparse.
GREEN conserva también run/source-check/decide en las regresiones.

Casos alternos: captura idéntica sin cambios; proyecto vacío; DB ausente/legada;
captura faltante/corrupta (JSON/metadatos/modelo), proyecto ajeno en cualquiera
de las capturas, tiempo invertido y fuente incompatible. Warnings y ausencia
`not_observed` se conservan sin inferir eliminación física/actor. Se verifican
bytes, esquema y entradas/bytes de artefactos sin cambios en éxito y error,
bloqueando inicialización escritora, Settings, orquestador, artefactos y
composición de proveedores. El slice enfocado emitió el ResourceWarning SQLite
ya conocido y el rechazo argparse esperado; no hubo fallos.

Límites: hechos persistidos y hash prueban consistencia/reproducibilidad, no
operaciones ni autoría. La evidencia por run no se une arbitrariamente a
capturas. En H1D2 no se abrió la base real ni se hicieron APIs, cambios de
credenciales, H2, scheduler, UI, acciones externas, commits o staging.
El avance posterior H2A/H2B1/H2B2a se registra a continuación.

### Avance H2A + H2B1 + H2B2a (offline)

H2A implementó el contrato puro y su validación; H2B1 añadió el catálogo
inmutable con evidencia de apoyo congelada (cierre suministrado: 547 ejecutados,
545 aprobados, 2 omitidos y 7 comprobaciones del evaluador). H2B2a añade solamente
`proposals list/show --db-path`: consulta SQLite con `read_only=True` y JSON por
lista permitida de campos de propuesta, nunca DTOs de apoyo ni captura completa.
REQ-08 sigue **PARCIAL**; revisión integrada, identidad, autorización y ejecución
siguen pendientes. No se atribuyen actores ni asociaciones evidencia/captura.

| Comando exacto | Resultado observado H2B2a |
|---|---|
| `.venv/bin/python -m unittest tests.test_cli_proposals tests.test_action_proposal_storage tests.test_cli_history tests.test_cli tests.test_cli_sources tests.test_hybrid_pipeline` | GREEN: 56/56 aprobados |
| `.venv/bin/python -m unittest discover -s tests` | 552 ejecutados, 550 aprobados, 2 omitidos opt-in |
| `.venv/bin/python -m unittest discover -s pruebas -p 'test_*.py'` | 7/7 aprobados: comprobaciones del evaluador, no rendimiento |
| `git diff --check` | Sin errores de whitespace |

RED observado antes de editar CLI: 62 tests, 10 errores esperados (con subcasos)
por `proposals` ausente de argparse. La importación directa del TestCase de
fixture duplicaba 6 tests preexistentes; se cambió a importar su módulo, dejando
56 tests enfocados y cinco nuevos tests de CLI con subcasos.
GREEN conserva run/source-check/decide/history y pipeline híbrido.

Se verificaron claves/tipos/valores JSON exactos, estados fijos, fechas/null/listas,
catálogo vacío y filtro de proyecto; show ausente/ajeno indistinguible;
DB ausente/legada, registro malformado/modelo inválido y excepciones privadas
con errores estáticos. Sentinelas PRIVATE en payload/resumen/snippet/URL de
apoyo y detalle de snapshot no aparecen en stdout/stderr. Huellas de bytes,
esquema, directorios y artefactos se conservan en éxito y error, denegando
inicialización escritora, Settings, composición, proveedores y artefactos.
Persisten ResourceWarning SQLite conocido y rechazo argparse esperado,
sin fallos. No se abrió la base real ni se hicieron APIs/inferencia/red,
lecturas de credenciales, migraciones CLI, staging o commits; T9 **PENDING**.
La proyección no sanitiza los textos propios de la propuesta ni autentica autoría.
