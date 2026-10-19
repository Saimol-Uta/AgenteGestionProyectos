# G2 — Riesgos deterministas por tarea y diagnóstico seguro de propuestas

Objetivo: que el gestor identifique cada riesgo determinista, llegue a las tareas afectadas y entienda por qué una ejecución no produjo propuestas.
Rama: `feature/multirepo`. Entrega: dos commits locales (contratos/diagnóstico y presentación), sin push ni merge.
Runner: `python -m pytest` en backend; Playwright con API simulada en frontend. G3 queda fuera de esta fase.

## Specs

- S1. Modelo de riesgo determinista reutilizando las métricas existentes. Cada riesgo expone `risk_id` estable, `project_id`, `capture_id`, `kind`, `task_ids`, `task_count`, `summary`, `basis` y `source="deterministic"`. El `risk_id` se deriva de (proyecto, captura, tipo, conjunto ordenado de tareas) y no cambia entre lecturas de la misma captura.
- S2. Compatibilidad: `identified_risks` y los conteos existentes (`overdue_count`, `due_soon_count`, `blocked_count`, `unknown_count`, `contradictory_count`) se conservan sin cambios de valor ni de orden. `TaskPlanSummary.risks` es aditivo.
- S3. Separación determinista/narrativo: solo las métricas deterministas crean riesgos. El texto libre del LLM (`MonitoringDraft.risks`, `suggested_actions`) nunca se convierte en riesgo ni crea asociaciones riesgo-tarea. La UI rotula el panel como clasificación determinista de la captura.
- S4. Dashboard: cada riesgo muestra su tipo, resumen y las tareas afectadas como enlaces que abren el detalle de la tarea. No se agregan contadores nuevos ni se duplican los existentes.
- S5. Detalle de tarea: muestra la clasificación de riesgo (tipo y fundamento) y la trazabilidad a la captura que lo originó. La tarjeta de Trello aparece como fuente/evidencia, no como diagnóstico repetido. Sin riesgos se declara la ausencia explícitamente.
- S6. `_RUN_STAGES` incluye `proposals`; `GET /runs/{run_id}/stages` devuelve la etapa. El orden visual es `source_collection`, `monitoring`, `proposals`, `quality`, `verifier`, `report`.
- S7. `StageView.proposals` expone un diagnóstico estructurado y seguro derivado del resumen persistido: `outcome`, `offered`, `selected`, `saved`, `technical_ready`, `technical_blocked`, `limitation_codes`, `failure_codes`, `discarded`. Nunca se devuelve el JSON interno crudo como texto.
- S8. Casos de cero propuestas distinguibles y conservadores: `not_configured` (reglas ausentes), `rules_invalid` (reglas presentes pero rechazadas), `no_candidates` (sin candidatos válidos), `candidates_discarded` (candidatos descartados con motivos), `not_selected` (hubo candidatos y el agente no seleccionó), `saved` y `unknown`. `unknown` cuando los datos no permiten probar la causa; nunca se inventa una.
- S9. Bandeja de propuestas: distingue "no hay propuestas registradas" de "no hay pendientes de decisión pero existen en otros estados", con enlace al diagnóstico de la última ejecución cuando corresponda.
- S10. Aislamiento y permisos: los riesgos llevan proyecto y captura; los endpoints solo sirven proyectos del conjunto permitido; la consulta de etapas verifica que el run pertenezca a un proyecto conocido. Un proyecto o run ajeno responde 404 sin filtrar datos.
- S11. Códigos técnicos de limitación disponibles para auditoría; las explicaciones comprensibles viven en React. Se descartan códigos desconocidos y se conserva el conjunto seguro.
- S12. Pruebas backend y frontend: ausencia de datos, resultados parciales, reglas inválidas, selección nula y consulta de tareas afectadas.

Restricciones vigentes (verbatim del pedido): no modificar el pipeline multiagente, no crear agentes nuevos, no generar propuestas automáticas desde texto narrativo, no asociar riesgo-propuesta todavía (G3), no cambiar las reglas reales de HORMIGESTION, no modificar bases históricas, `EXTERNAL_WRITES_ENABLED=false`, sin llamadas reales al LLM, sin escrituras externas, sin push y sin merge.

## Tasks

- T1 [S1,S2] Modelo `TaskRisk` + proyección en `build_task_plan_summary` y `query_tasks_view`; tests unitarios. Route: inline. Estado: hecho.
- T2 [S3] Test de separación con el `FakeProvider` narrativo; sin derivación de texto libre. Route: inline. Estado: hecho.
- T3 [S4] Panel de riesgos del Dashboard con enlaces a tareas. Route: inline. Estado: hecho.
- T4 [S5] Sección de riesgo en el detalle de tarea + deep link `?task=`. Route: inline. Estado: hecho.
- T5 [S6,S7] `_RUN_STAGES`, `ProposalStageDiagnostic`, parser seguro y `offered` en el resumen persistido. Route: inline. Estado: hecho.
- T6 [S8,S11] Clasificación conservadora de outcomes y allowlist de códigos; tests de cada caso. Route: inline. Estado: hecho.
- T7 [S9] Diagnóstico en RunDetailPage y estados vacíos de propuestas en React. Route: inline. Estado: hecho.
- T8 [S10] Aislamiento de proyecto/run; tests HTTP. Route: inline. Estado: hecho.
- T9 [S12] Fixtures y specs Playwright (dashboard, detalle, run, bandeja). Route: inline. Estado: hecho.
- T10 [S12] Suites enfocadas, typecheck y build. Route: inline. Estado: hecho.

Commits: `feat(api)` contratos y diagnóstico (T1,T2,T5,T6,T8 con sus tests) y `feat(web)` presentación (T3,T4,T7,T9).

## Log

- L1. Pedido autorizado: implementar G2 (identificar cada riesgo determinista, alcanzar las tareas afectadas y explicar por qué una ejecución no produjo propuestas) más el arreglo de visibilidad de la etapa `proposals`. G3 se difiere. Restricciones: no modificar el pipeline multiagente, no crear agentes, no convertir narrativa en hechos, no asociar riesgo-propuesta aún, no tocar reglas reales de HORMIGESTION ni bases históricas, `EXTERNAL_WRITES_ENABLED=false`, sin LLM real, sin escrituras externas, sin push ni merge.
- L2. Diagnóstico previo verificado: `_RUN_STAGES` omite `proposals` y cualquier resumen que empiece con `{`/`[` se descarta; por eso un run con `limitations: ["unknown_task"]` no muestra nada. Los riesgos deterministas son cadenas de conteo sin tarea; los riesgos narrativos son texto libre. El reporte deriva `risk_task_ids` de los buckets deterministas. Precedente a seguir: `MonitoringDraft.task_state_claims` ya vincula `task_id` a estado y se valida.
- L3. Decisión: `TaskRisk` vive en `application/tasks_query.py` junto a la proyección que lo origina. `risk_id` es un hash canónico de (project, capture, kind, task_ids ordenados). `basis` describe la regla determinista; `summary` conserva la cadena existente.
- L4. Decisión: el diagnóstico de propuestas es una función pura de la capa API (`api/proposal_diagnostics.py`) que solo acepta códigos de una allowlist y clasifica con evidencia; los códigos desconocidos se descartan. `offered` se agrega al resumen persistido para distinguir "sin candidatos" de "no seleccionó", sin cambiar el pipeline.
- L5. Decisión: `TaskDetailResponse.risks` es aditivo y se calcula en el repositorio; la lista de tareas no carga riesgos para no inflar el payload.
- L6. RED→GREEN backend: `tests/test_g2_risk_diagnosis.py` con 20 pruebas (modelo de riesgo, contrato HTTP, aislamiento y diagnóstico de la etapa). RED observado (27 errores + 1 fallo por contrato ausente) y luego 20/20 en verde. Suite completa: 1138 pruebas en verde, 1 omitida.
- L7. Verificación: `identified_risks` conserva texto y orden; `risk_task_ids` del informe coincide con la unión de tareas de los riesgos deterministas; el texto narrativo del `FakeProvider` no aparece en ningún riesgo.
- L8. Decisión: el Dashboard muestra tipo, fundamento y enlaces a las tareas afectadas, sin repetir los conteos que ya viven en la grilla de métricas. El detalle de tarea muestra tipo, resumen, fundamento y trazabilidad a la captura; la tarjeta de Trello queda como fuente en la sección de evidencia, no como diagnóstico repetido.
- L9. Decisión: deep link `?task=<id>` en la página de tareas para que el enlace del Dashboard abra directamente el detalle; al cerrar se limpia el parámetro. La etapa `proposals` entra en el pipeline visual entre `monitoring` y `quality`.
- L10. RED→GREEN frontend: `frontend/e2e/g2-risks-diagnostics.spec.ts` con 6 pruebas. Se comprobó la sensibilidad mutando el código (panel del dashboard y orden de etapas): fallaron exactamente las 2 pruebas afectadas; tras revertir, 6/6 en verde. Suite completa: 141 pruebas en verde; `tsc --noEmit` y `vite build` sin errores.
- L11. Ajuste de pruebas existentes: `web-platform.spec.ts` fijaba el pipeline de 5 etapas; se actualizó a 6 manteniendo la misma fuerza de aserciones (cada etiqueta de estado se sigue comprobando en su tarjeta).
