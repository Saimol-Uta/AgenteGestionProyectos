# REQ-08 T6: backend harness y demostración de aprobación

**Estado backend observado:** `final-full` terminó con salida 0: **1106 pruebas en 23.424 s, 1104 aprobadas, 2 omitidas preexistentes, 0 fallos y 0 errores**. Los 45 IDs originales siguen presentes en la ejecución final. Las entradas exactas del montaje resuelven 44 resultados y la expectativa de demo corregida resuelve el restante; no se modificó producción ni se eliminaron pruebas. El cierre E2E/frontend de T6 se informa por separado.

Este fragmento cubre únicamente S14–S18 y S20–S23 en lo relativo al backend harness y la expectativa de demo. Conserva T1–T5 hasta `e0d9170`. No modifica producción, pipeline, flags ni presupuestos, y no toca el E2E real/enlace de Auditoría ni `docs/req08-decision-workflow.md`, que pertenecen a otra persona.

## Ejecución reproducible

Runner desechable: `/tmp/req08-decision-workflow-t1-isolated/t6/renewed-backend/scripts/run-backend.sh`. Cada modo crea un checkout, runtime, cachés y resultados nuevos bajo `renewed-backend/`; rehúsa reutilizar una salida existente.

| Modo | Comando | Estado/evidencia |
|---|---|---|
| `baseline-old-demo` | `bash /tmp/req08-decision-workflow-t1-isolated/t6/renewed-backend/scripts/run-backend.sh baseline-old-demo` | Ejecutado: 1 fallo intencional en la expectativa antigua (RED); el exit 1 real queda en `unittest.exit`. |
| `focused-demo` | `bash /tmp/req08-decision-workflow-t1-isolated/t6/renewed-backend/scripts/run-backend.sh focused-demo` | Ejecutado: la demo y la prueba HTTP existente de `reject`/`request_changes` pasan (2 tests). |
| `final-full` | `bash /tmp/req08-decision-workflow-t1-isolated/t6/renewed-backend/scripts/run-backend.sh final-full` | Ejecutado por el padre: exit 0; `python -m unittest discover -s tests -p 'test_*.py' -v`, 1106 pruebas, `OK (skipped=2)`. |
| `base-full` | `bash /tmp/req08-decision-workflow-t1-isolated/t6/renewed-backend/scripts/run-backend.sh base-full` | Opcional si el resultado completo requiere clasificación diferencial; usa el test de demo de `HEAD` y no se ha ejecutado. |

Los logs separados conservan el stdout, stderr y exit real de cada comando. El runner no agrega skips: conserva los skips preexistentes para smoke LLM sin credenciales y snapshots históricos no presentes en el checkout aislado.

### Aislamiento y entradas

Antes de cada comando de unittest, el runner verifica rutas, manifiesto/hash, entorno y guard:

- Usa `env -i`; fija `EXTERNAL_WRITES_ENABLED=false`, `PYTHON_DOTENV_DISABLED=true` y `PYTHONDONTWRITEBYTECODE=1`. No pasa variables de credenciales. No lee ni copia `.env`.
- Copia sin alteración estos tres YAML rastreados: `config/config.yaml`, `config/hormigestion.yaml` y `config/policies.yaml`; también copia exactamente los cuatro JSON de `fixtures/fixture_{1_standard,2_ci_mismatch,3_empty_and_unknown,4_mandatory_unknown_policies}.json`. Preflight verificó hash igual al archivo rastreado actual para los siete archivos. No inventa YAML de reemplazo.
- Crea `.runtime/` vacío en el checkout aislado. No copia `.runtime/agente.db`, `./runs/`, bases de datos, historial, credenciales ni el origen real HORMIGESTION. Las rutas mutables de DB, runs, jobs, outbox y branches se redirigen al runtime único del run.
- El guard Python bloquea DNS, connect/connect_ex, sendto, listen y binds no autorizados. Únicamente registra aparte un bind efímero `::1:0` cuando el stack demuestra `urllib3.util.connection._has_ipv6`, y el bind efímero `127.0.0.1:0` del test `test_unified_smoke_commands`; ninguno escucha ni conecta. Toda dirección IPv6 u otra dirección sin propietario sigue bloqueada para listen/connect. Los rechazos deliberados del autochequeo se registran en otro archivo. Se verifican herencia del guard e identidad intacta de sus funciones.
- No hace falta guard Node: estos modos solo ejecutan Python.

## RED → GREEN y triangulación

- **RED observado** — `baseline-old-demo`, HEAD `e0d9170`: `test_public_history_technical_review_and_source_approval_are_real_and_local` falla en la aserción antigua de `tests/test_phase2_demo_seed.py:195`: esperaba HTTP 200 y observó HTTP 409 con `revision_independiente_requerida`. Fue un único fallo (`Ran 1 test`, `FAILED (failures=1)`); coincide con la política T1 ya aprobada, no con una regresión de producción. Evidencia: `/tmp/req08-decision-workflow-t1-isolated/t6/renewed-backend/runs/t6-backend-baseline-old-demo-20261019T064918-1792392558283574925-2534283/results/` (`unittest.exit=1`).
- **GREEN observado** — `focused-demo`: `Ran 2 tests`, `OK`. Incluye la demo corregida y `MultiRepoFindingActionTests.test_reject_and_request_changes_are_scoped_replayable_and_audited`. Evidencia actual: `/tmp/req08-decision-workflow-t1-isolated/t6/renewed-backend/runs/t6-backend-focused-demo-20261019T065808-1792393088314636492-2551696/results/` (`unittest.exit=0`). El guard Python se cargó en todos los procesos observados; cero intentos de red inesperados.
- La demo comprueba 409/código exacto y que la lista pública protegida de Auditoría, finding pendiente e informe no cambien por la denegación. Un action port de registro no recibe llamadas, ni aparecen branch/artefacto. Luego reusa la misma clave de idempotencia con un `reject` válido: obtiene 200, la decisión pública pasa a `rejected`, el informe sigue inmutable y la Auditoría pública contiene el evento de rechazo. Esto demuestra que la aprobación denegada no reservó la clave; no añade aserciones SQL.
- En ambos modos, las importaciones producen dos probes efímeros IPv6 identificados por stack; están separados del log de intentos de red. El autochequeo registró seis denegaciones deliberadas en su propio log. Ningún intento externo/no asignado quedó registrado.

## Clasificación de los 45 resultados originales (diagnóstico previo)

Fuente primaria: `/tmp/req08-decision-workflow-t1-isolated/t6/runs/t6-backend-suite-20261019T055023Z-2497003/results/backend-unittest.stderr`, resumen final en líneas 2031–2033 (`Ran 1106 tests in 22.112s`, `FAILED (failures=15, errors=30, skipped=2)`). El runner de esa corrida omitió las entradas estáticas completas y fabricó YAML incompleto; por eso su resultado no es una línea base válida del producto. La clasificación siguiente conserva los 45 IDs observados. «Montaje incompleto» describe la evidencia inicial; su resolución por la suite completa sigue sin observarse.

### Montaje incompleto del checkout: 11 fallos y 22 errores

Las trazas de error de fixtures apuntan a rutas ausentes bajo `fixtures/` (incluidos `fixtures/proyecto-demo` y `fixtures/fixture_1_standard`). El runner nuevo incorpora los cuatro JSON rastreados y YAML de producto exactos; no sustituye contenido. La ejecución `final-full` confirmó que estos 33 tests pasan sin modificar sus aserciones.

**Fallos (11):**

- `test_cli_source_check_fixtures_mode`
- `test_fixtures_adapter_loads_four_marked_fixtures`
- `test_legacy_owner_with_full_repository_alias_preserves_singleton_api_and_cli`
- `test_executor_failure_is_terminal_and_does_not_expose_exception_text`
- `test_missing_credentials_execute_deterministic_pipeline_and_persist_run_and_report`
- `test_partial_credentials_select_mixed_mode_and_remote_components_require_consent`
- `test_preflight_is_protected_and_does_not_create_database_or_job_ledger`
- `test_queue_capacity_rejects_a_second_project_while_worker_is_active`
- `test_real_mode_missing_credentials_is_eligible_for_sandbox_prepare`
- `test_remote_consent_idempotency_and_polling`
- `test_server_remote_permission_cannot_be_overridden_by_client_consent`

**Errores (22):**

- `test_cli_default_run_without_fixture_uses_default_fixture_and_preserves_project_id`
- `test_cli_env_file_option_loads_and_cli_overrides_beat_it`
- `test_cli_explicit_fixture_flag_preserves_custom_project_id`
- `test_cli_fake_run_generates_report_and_summary`
- `test_cli_human_decision_registration`
- `test_cli_fixture_override_takes_precedence_over_config_real`
- `test_fixture_ci_mismatch_behavior`
- `test_fixture_empty_and_unknown_metrics`
- `test_fixture_mandatory_unknown_policy_evaluation`
- `test_e2e_1_positive_flow`
- `test_e2e_2_no_code_routing`
- `test_e2e_3_no_escalation_routing`
- `test_e2e_4_wrong_sha_ci_mismatch`
- `test_e2e_5_cloud_unavailable_needs_attention`
- `test_e2e_6_invalid_citations_semantic_validation_recovery`
- `test_e2e_7_bounded_budget`
- `test_e2e_8_idempotence_and_bounded_recovery`
- `test_e2e_9_human_decision_flow`
- `test_real_fake_pipeline_keeps_sparse_fixture_inactive_and_legacy_cache`
- `test_missing_coverage_percentage_results_in_unknown`
- `test_existing_fixture_snapshot_behavior_unchanged`
- `test_fixture_adapter_implements_policy_source_port`

### Otros fallos de montaje observados

| Causa inicial | Resultado original | IDs | Resolución/preparación observada |
|---|---:|---|---|
| YAML sintético de policies no conforme al contrato | 1 F, 2 E | F: `test_default_local_policy_equivalence`; E: `test_actions_metrics_none_baseline_quality_unknown`, `test_load_default_config_policies_file` | El runner nuevo copia `config/policies.yaml` exacto y comprueba su hash; `final-full` aún debe confirmar los tres resultados. |
| Defaults/configuración de proyecto incompletos | 2 F | `test_project_defaults_enable_remote_and_select_gemini`, `test_project_yaml_defaults_remote_gate_and_current_providers` | Copia `config/config.yaml` y `config/hormigestion.yaml` rastreados sin edición. El entorno de prueba fuerza `EXTERNAL_WRITES_ENABLED=false`; no se reescriben defaults YAML para lograr tests verdes. Suite pendiente. |
| Directorio `.runtime` ausente en el checkout | 6 E | `test_capabilities_and_external_writes_default_enabled`; `test_default_capabilities_do_not_bypass_session_csrf_or_origin`; `test_explicit_false_environment_preserves_disabled_capabilities`; `test_missing_dotenv_uses_defaults_and_invalid_flags_are_rejected`; `test_process_environment_wins_and_dotenv_can_be_disabled`; `test_root_dotenv_loads_automatically_without_mutating_process_environment` | El runner crea `.runtime/` vacío antes del import y mantiene DB/runs en scratch. Resultado de esos tests pendiente. |
| Expectativa de demo obsoleta tras T1 | 1 F | `test_public_history_technical_review_and_source_approval_are_real_and_local` | RED/GREEN observado mediante APIs públicas: 409 sin cambios de estado/auditoría, seguido de `reject` 200 con la misma clave y sin acción GitHub. |

La suma es exactamente **15 fallos + 30 errores = 45**. La clase completa incluye además dos skips deliberados, que el runner nuevo no agrega ni altera.

## Resultado completo observado

El padre ejecutó `bash /tmp/req08-decision-workflow-t1-isolated/t6/renewed-backend/scripts/run-backend.sh final-full` (job-8). La suite terminó en **23.424 s, 1106 pruebas, `OK (skipped=2)`**, unittest y wrapper exit 0. El guard se cargó en seis procesos y verificó sus funciones intactas; hubo **cero intentos de red inesperados**, tres probes efímeros identificados y seis denegaciones deliberadas en logs separados. Siete hashes de entradas estáticas coincidieron; `.runtime` empezó vacía, no se copió `.env`, base real ni historial.

Evidencia: `/tmp/req08-decision-workflow-t1-isolated/t6/renewed-backend/runs/t6-backend-final-full-20261019T065915-1792393155766659624-2555151/results/`. El contraste de los encabezados `FAIL`/`ERROR` antiguos contra el log final confirmó **45/45 IDs presentes, ninguno faltante**. Las dos omisiones son las ya existentes: smoke LLM remoto deshabilitado y prueba dependiente de snapshot histórico no copiado. No se agregó ningún skip ni se recuperó un artefacto histórico para obtener verde.

Las filas de clasificación anteriores registran qué preparación estaba pendiente antes de esta ejecución; los 44 casos de montaje más la demo ya tienen resolución observada en esta suite completa. No hay evidencia de una regresión backend de T1–T5 entre aquellos 45 resultados.

## Regresión frontend final y contraste anterior a T1–T5

El padre ejecutó la suite Playwright default completa sin filtrar ni omitir pruebas: **134 pruebas, 126 aprobadas, 8 fallidas, 0 omitidas, 42.9 s; exit 1**. `npm run typecheck` y `npm run build` terminaron con exit 0. Node y navegador verificaron métodos intactos y pares de guarda activa/limpia por test, con cero intentos inesperados. Los hooks añadidos únicamente al scratch conservaron como prefijo exacto los bytes originales de las pruebas; sus hashes y los del overlay se comprobaron. No se tocaron las aserciones para conseguir verde.

Las ocho aserciones se reprodujeron en `d7d37a6`, antes de todos los commits T1–T5, mediante dos selecciones diagnósticas (seis originales y dos adicionales de encabezado). Sus comandos Playwright devolvieron exit 1; el wrapper diagnóstico devolvió 0 únicamente porque verificó exactamente los fallos esperados, las guardas y los manifiestos. **Esto no significa suite verde.**

| Caso en la suite final | Causa observada también en `d7d37a6` | Clasificación |
|---|---|---|
| `multirepo.spec.ts:159` | No existe el encabezado `Repositorios del proyecto` en la pantalla consultada | Expectativa de navegación/panel anterior a T1–T5 |
| `multirepo.spec.ts:186` | El artículo de repositorio esperado no aparece en esa ruta | Expectativa de navegación/panel anterior a T1–T5 |
| `multirepo.spec.ts:325` | No se encuentra el artículo/encabezado de workflows esperado | Expectativa de navegación/panel anterior a T1–T5 |
| `multirepo.spec.ts:382` | El primer texto coincide con una opción oculta del selector | Locator ambiguo preexistente |
| `multirepo.spec.ts:403` | No aparece el panel de repositorios esperado para datos legacy | Expectativa de navegación/panel preexistente |
| `web-platform.spec.ts:117` | Falta la etiqueta explícita de etapa desconocida; el detalle usa otra presentación | Discrepancia funcional/contractual preexistente; no se corrige fuera de T6 |
| `header-actions.spec.ts:16` | El análisis no contiene el antiguo encabezado de repositorios | Expectativa de panel preexistente |
| `header-actions.spec.ts:246` | El locator espera `Preparar análisis` durante el estado en curso | Expectativa de etiqueta preexistente |

Evidencia final: `renewed-real/runs/renewed-frontend-final-20261019T073040Z-2580553/results/`. Baseline de las seis: `renewed-frontend-baseline-20261019T073029Z-2579200/results/`; baseline de los dos encabezados: `renewed-frontend-baseline-20261019T100124Z-2661506/results/`. Todos están bajo `/tmp/req08-decision-workflow-t1-isolated/t6/renewed-real/runs/`. No se identificó una regresión introducida por T1–T5 entre estas ocho fallas. Resolverlas exige un trabajo separado; no se modificaron tests ni producción de Análisis/encabezado/MultiRepo para ocultarlas. El E2E React/API real del enlace se registra separadamente cuando tenga un GREEN aceptado.

## E2E real y enlace de Auditoría: GREEN observado

El padre ejecutó `bash /tmp/req08-decision-workflow-t1-isolated/t6/renewed-real/run-real-browser.sh real-green` (job-12): **1 prueba aprobada en 9.3 s, test 5.8 s, Playwright y wrapper exit 0**. React y FastAPI sirvieron el proyecto sintético sobre SQLite temporal real; no se mockearon endpoints HTTP. El recorrido confirmó sesión/CSRF, una ejecución sandbox, persistencia del mismo recibo tras reload, historial actor/motivo/fecha/resultado, pendientes 1→0 y navegación al evento real mediante el nuevo enlace. Auditoría anónima mantuvo 401.

Guardas Python/Node activas e intactas en todos los procesos instrumentados, hashes de fuentes/helper verificados y puertos liberados. El único sondeo bare fue el `bind(::1,0)` exacto autorizado; quedó separado de los intentos inesperados (cero). El preflight negó las 13 operaciones Python y 5 Node previstas, en logs separados. Evidencia: `/tmp/req08-decision-workflow-t1-isolated/t6/renewed-real/runs/renewed-real-green-20261019T120046Z-2700543/results/`.

El cambio de producción es exclusivamente un enlace condicional de tres líneas en `ProposalDecisionTrail.tsx`, basado en el ID persistido y URL-encoded. No cambia autorizaciones, pipeline ni acciones. La suite frontend general sigue con ocho fallos preexistentes documentados; este GREEN no los convierte en aprobados ni autoriza corregirlos fuera de alcance.

## Key learnings

- Un checkout aislado no es completo por copiar solo `src/`, `tests/`, `scripts/` y `pyproject.toml`: el backend depende de los cuatro fixtures raíz y los tres YAML estáticos rastreados.
- Los YAML sintéticos del intento anterior alteraron contratos y defaults; deben copiarse bytes rastreados, no recrearse para la prueba.
- Las pruebas de startup crean temporales con `dir=".runtime"`; un directorio vacío es suficiente y evita importar DB/artifacts existentes.
- `EXTERNAL_WRITES_ENABLED=false` tiene precedencia sobre defaults YAML. Se preserva el YAML de producto y se comprueba el efecto del entorno aislado.
- La denegación HTTP 409 de aprobación GitHub sin revisión independiente es el resultado correcto; no crea branch, decisión, solicitud ni auditoría de decisión.
- **Pendiente:** el padre debe ejecutar `final-full` y registrar el resultado y la clasificación/resolución final de los 45 IDs antes de declarar GREEN completo.
