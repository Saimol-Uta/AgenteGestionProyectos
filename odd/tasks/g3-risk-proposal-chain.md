# G3 — Cadena Riesgo → Tarea → Propuesta → Revisión → Decisión → Resultado

Objetivo: vincular los riesgos deterministas con las propuestas correctivas y las decisiones del gestor, con navegación trazable y coherente.
Rama: `feature/multirepo`. Entrega: commits locales separados (dominio, API, web, E2E), sin push ni merge.
Runner: `.venv/bin/python -m unittest discover -s tests`; Playwright con API simulada y E2E aislado real. G3 no habilita reglas permanentes de HORMIGESTION.

## Specs

- S1. Un único modelo de riesgo compartido. `RiskKind`, `TaskRisk` y la referencia de riesgo viven en `domain/risks.py`; la proyección determinista se calcula una sola vez desde `DeterministicTaskMetrics` y la reutilizan la API, el orquestador y la validación. No se duplican cálculos ni se crea un subsistema nuevo de riesgos.
- S2. Las reglas del proprietor declaran opcionalmente el tipo de riesgo admitido mediante un selector validado (`risk_kind`) asociado a los `task_ids` ya autorizados. Una regla sin selector conserva su serialización exacta: `config_hash`, `proposal_id` y el cuerpo persistido no cambian.
- S3. Durante Monitoring el riesgo concreto se resuelve contra la misma captura y la misma evidencia usadas para construir la propuesta. Solo se vinculan riesgos cuya tarea y cuyo tipo coincidan con la regla; sin coincidencia no hay candidato y queda el motivo `risk_not_present`.
- S4. `ActionProposal` conserva la referencia al riesgo concreto y a la captura de origen (`risk_refs`, opcional y excluido cuando está vacío). Solo las propuestas nuevas pueden llevarla: a las históricas no se les inventa ninguna asociación.
- S5. Compatibilidad de identificadores. El `proposal_id` de una propuesta sin riesgo no cambia respecto de hoy; una propuesta con riesgo es una propuesta nueva. Derivaciones, decisiones, recibos y revisiones técnicas históricos conservan sus identificadores y su vínculo al cuerpo exacto.
- S6. La coherencia de la referencia se valida: la captura, la tarea, el tipo y la versión del esquema deben concordar. En lectura, el backend resuelve el riesgo contra la captura y no expone asociaciones que no pueda verificar.
- S7. El modelo de lectura expone, por riesgo, las propuestas realmente vinculadas con su estado de revisión y decisión, y expone en cada propuesta qué riesgo motivó la acción.
- S8. Aislamiento por proyecto: un enlace riesgo→propuesta nunca cruza proyectos; proyectos, capturas y tareas ajenos siguen respondiendo 404.
- S9. Un riesgo sin propuesta muestra que no hay una propuesta vinculada y, cuando el diagnóstico persistido lo explica, el motivo; nunca una recomendación inventada.
- S10. Un riesgo no se marca como resuelto por aprobar o ejecutar una propuesta. La interfaz lo declara y exige evidencia posterior de la captura.
- S11. La relación es por captura. Si una captura posterior cambia el riesgo, la relación antigua se conserva como histórica y no se presenta como situación actual.
- S12. La narrativa del LLM sigue sin autoridad: `MonitoringDraft.risks` y `suggested_actions` no crean propuestas ni referencias de riesgo.
- S13. Una propuesta sin riesgo asociado se muestra como tal, sin inventar motivo.
- S14. Pruebas obligatorias: asociación válida riesgo-tarea-propuesta; regla sin coincidencia de riesgo; regla de otro proyecto; riesgos múltiples sobre la misma tarea; propuestas sin riesgo; compatibilidad de propuestas y decisiones históricas; vigencia y bloqueo; idempotencia y recibos; navegación Dashboard↔Tareas↔Propuestas; y un E2E aislado React–FastAPI–SQLite sobre los paneles reales de G2/G3.

Restricciones vigentes: no modificar el pipeline multiagente, no crear agentes, no activar reglas permanentes de HORMIGESTION, no alterar `.runtime/agente.db` ni los artefactos históricos, `EXTERNAL_WRITES_ENABLED=false`, sin proveedores LLM reales, sin escrituras externas, sin push y sin merge.

## Tasks

- T1 [S1] `domain/risks.py` con `RiskKind`, `TaskRisk`, `ProposalRiskRef` y la proyección; `application/tasks_query.py` la reutiliza. Route: inline. Estado: hecho.
- T2 [S2] `DomainProposalRule.risk_kind` como selector opcional y validado, excluido del volcado cuando está vacío. Route: inline. Estado: hecho.
- T3 [S3] `monitoring_choices` resuelve el riesgo por regla, tarea y tipo; el orquestador calcula los riesgos una vez y los entrega al agente y al persistido. Route: inline. Estado: hecho.
- T4 [S4,S5] `ActionProposal.risk_refs` con esquema v2 solo cuando hay referencias; el `proposal_id` de las propuestas sin riesgo no cambia. Route: inline. Estado: hecho.
- T5 [S6] Validación de coherencia de la referencia en `validate_action_proposal`. Route: inline. Estado: hecho.
- T6 [S7] `RiskView`, `RiskProposalLink`, `ProjectSummaryView` y `risk_refs` en el modelo de propuestas. Route: inline. Estado: hecho.
- T7 [S8,S9] Enlace riesgo→propuesta aislado por proyecto y contexto del diagnóstico persistido. Route: inline. Estado: hecho.
- T8 [S9,S10,S11,S13] Panel de riesgo con propuestas vinculadas, ausencia declarada y el riesgo nunca marcado como resuelto. Route: delegated (superficie `frontend/` disjunto de T9). Estado: hecho.
- T9 [S10,S12] Propuestas: qué riesgo motivó la acción, navegación a su tarea y evidencia; detalle ejecutivo de REQ-08 intacto. Route: delegated (misma unidad que T8). Estado: hecho.
- T10 [S14] Pruebas backend de los casos obligatorios y specs Playwright de navegación. Route: inline (backend) + parte de T8 (frontend). Estado: hecho.
- T11 [S14] E2E aislado React–FastAPI–SQLite sobre los paneles reales de G2/G3. Route: inline. Estado: hecho.
- T12 [S14] Suites completas, typecheck y build. Route: inline. Estado: hecho.
- T12 [S14] Suites completas, typecheck y build. Route: inline. Estado: pendiente.

Commits: `feat(domain)` (T1–T5), `feat(api)` (T6–T7), `feat(web)` (T8–T10), `test(web)` (T11).

## Log

- L1. Pedido autorizado: G3, vincular riesgos deterministas con propuestas correctivas y decisiones, con las doce condiciones del gestor y la lista de pruebas obligatorias. Restricciones: sin pipeline multiagente, sin agentes nuevos, sin reglas permanentes de HORMIGESTION, sin tocar `.runtime/agente.db` ni artefactos históricos, `EXTERNAL_WRITES_ENABLED=false`, sin LLM real, sin escrituras externas, sin push ni merge.
- L2. Decisión: el vínculo no se declara con `risk_id` en las reglas (ese identificador depende de la captura). Las reglas declaran el TIPO de riesgo admitido (`risk_kind`); el identificador concreto se resuelve durante Monitoring contra la captura real.
- L3. Decisión: `exclude_if` en el campo opcional mantiene el volcado de las reglas sin selector idéntico al actual; por eso `config_hash`, `proposal_id` y el cuerpo persistido de las propuestas existentes no cambian. Verificado que pydantic 2.14.1 soporta `exclude_if`.
- L4. Decisión: la referencia de riesgo viaja en el cuerpo de la propuesta y el `proposal_id` la incluye a través del volcado de la opción canónica, de modo que el identificador ata la asociación sin recalcular nada. Las propuestas con riesgo son nuevas; las históricas siguen siendo v1 y no se les añade nada.
- L5. Decisión: el agente solo elige índices de opción; las referencias viajan en los objetos canónicos, así que el LLM no puede inventarlas ni alterarlas.
- L6. Decisión: la validación comprueba la coherencia interna de la referencia (captura, tarea, tipo, versión) y la lectura resuelve el riesgo contra la captura; una referencia que no se puede resolver se declara no verificable en lugar de mostrarse como válida.
- L7. Restricción de la relación: la vista de una propuesta conserva el riesgo que la motivó; la UI declara que una propuesta aprobada o ejecutada no prueba que el riesgo haya desaparecido.
- L8. RED→GREEN del dominio: `tests/test_g3_risk_proposal_chain.py` con 13 pruebas. RED comprobado ejecutando el archivo contra el código previo a G3 en un worktree temporal (`ModuleNotFoundError: agente_gestion.domain.risks`), luego 13/13 en verde.
- L9. Compatibilidad medida, no supuesta: con el mismo escenario, el código previo a G3 y el actual producen el MISMO `proposal_id` (`proposal-6022f2c8…747d4f`), las MISMAS claves de cuerpo (sin `risk_refs`) y el mismo resumen de etapa. El almacén acepta la omisión documentada de `risk_refs` mediante `OPTIONAL_SUBMISSION_FIELDS`, de modo que la invariante "todo campo del modelo está en el cuerpo" se mantiene para el resto.
- L10. Decisión: los riesgos se calculan una sola vez en el orquestador (`risks_from_metrics` sobre las métricas de la captura) y ese mismo objeto viaja al agente y al persistido; así el candidato y la propuesta histórica coinciden por construcción.
- L11. Decisión: la resolución ocurre después de confirmar la tarea y la visibilidad del campo, y antes de evaluar el valor y la evidencia; así `unknown_task`, `conflicting_task` y `field_not_visible` conservan su prioridad y `risk_not_present` solo aparece cuando la tarea existe y no tiene ese riesgo.
- L12. Decisión de lectura: el resumen y el detalle de tarea exponen, por riesgo, las propuestas del MISMO proyecto y captura; el enlace se hace por `risk_id`, de modo que dos riesgos que afectan a la misma tarea no se confunden. `proposals_context` expone el diagnóstico persistido de la última ejecución con su `run_id` y su `capture_id` para que la UI no lo presente como situación actual.
- L13. Decisión: una referencia que no se puede resolver en la clasificación vigente de su captura se expone con `resolved: false`; no se oculta ni se presenta como válida. Una propuesta sin referencias se publica con `risk_refs: []`.
- L14. Verificación: 21 pruebas de G3 en verde y suite completa del backend en 1159 pruebas (1 omitida). Los enlaces nunca cruzan proyectos: el resumen de `STORED-ONLY` no lista propuestas de `H5-TEST` y su bandeja de propuestas está vacía.
- L15. E2E real: `tests/g3_hormigestion_demo_server.py` reutiliza el arnés G1 (copia de solo lectura de la captura real a un SQLite temporal) y el único cambio es que la regla de demostración declara `risk_kind: "due_soon"`. Verificado sobre los datos reales de HORMIGESTION: la propuesta reproduce `risk_refs` con `resolved: true` y el `risk_id` calculado por el orquestador coincide con el que recalcula la API en lectura; el resumen muestra el riesgo `due_soon` con su propuesta vinculada (revisión `pending`, ejecución `not_executed`, revisión técnica `ready`) y el contexto del diagnóstico persistido con `outcome: "saved"`.
- L16. Presentación (delegada a un worker con superficie `frontend/`): panel de riesgo con las propuestas vinculadas y su estado, ausencia declarada con el diagnóstico persistido, deep link `?task=&capture_id=`, marca "Histórica" cuando la propuesta pertenece a otra captura, sección "Riesgo que motivó la acción" en la propuesta con enlace a su tarea y a su evidencia, y la nota explícita de que una propuesta aprobada o ejecutada no demuestra que el riesgo haya desaparecido. El detalle ejecutivo de REQ-08 quedó intacto.
- L17. Verificación de la presentación: 10 specs nuevas del worker (`frontend/e2e/g3-risk-proposal-chain.spec.ts`), suite completa 151/151 en verde y `tsc --noEmit` sin errores. Se comprobó la sensibilidad mutando el render del panel de riesgo: fallaron exactamente las 2 pruebas del dashboard afectadas; tras revertir, todo en verde.
- L18. Lección de infraestructura: la suite Playwright es frágil si dos launas corren a la vez (comparten CPU y el servidor de desarrollo en el puerto 4173). Los fallos intermitentes observados en la primera pasada no provenían del código: con el worker inactivo la misma suite da 151/151. En el futuro, no ejecutar dos launas de Playwright en paralelo sobre este repositorio.
- L19. E2E aislado de G3 en verde sobre datos reales: el recorrido completo riesgo → tarea → propuesta → revisión → decisión → recibo sandbox, con el SHA-256 de la base de origen sin cambios y sin peticiones fuera del origen permitido.
- L20. Cierre: backend 1160 pruebas en verde (1 omitida), frontend 151/151, `tsc --noEmit` sin errores, `vite build` sin errores, E2E real de G1 en verde y E2E real de G3 en verde. `config/` y `.runtime/` intactos, la base de origen conserva su mtime (08:42) y su SHA-256. 21 commits locales adelantados, sin push ni merge. Los cuatro commits de G3: `3cc52a3` (dominio), `58a3d6c` (API), `cc3398e` (web), `ecc2199` (E2E y documentación).
- L21. Pendiente para la siguiente fase: el vínculo riesgo→propuesta es de una sola dirección en el modelo (la propuesta conoce su riesgo; el riesgo conoce sus propuestas mediante el modelo de lectura). Una propuesta que responde a un riesgo que ya no existe conserva la referencia pero se declara no verificable; no se re-resuelve ni se reescribe.
