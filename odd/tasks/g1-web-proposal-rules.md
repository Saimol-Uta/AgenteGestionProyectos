# Conectar reglas de propuestas al análisis iniciado desde la web

Objetivo: que `AnalysisJobs` use el mismo mecanismo validado de `DomainProposalRuleSet` y `monitoring_choices` que ya usa el CLI, sin modificar el pipeline multiagente. Cierra el hueco G1 detectado en la validación manual: la web nunca podía producir propuestas.
Rama: `feature/multirepo` (base `0a9bd50`). Entrega: commits locales por unidad, sin push ni merge.
Runner de pruebas: `python -m pytest` (backend) y Playwright `frontend/e2e-backend` (E2E web).

## Specs

S1. "Objetivo: que AnalysisJobs utilice el mismo mecanismo validado de DomainProposalRuleSet y monitoring_choices que ya utiliza el CLI, sin modificar el pipeline multiagente."

S2. "Reutiliza el cargador, validador y contrato de reglas existente. Evita duplicar la lógica del CLI."
Alcance: `load_monitoring_rules`, `DomainProposalRuleSet`, `DomainProposalRule`, `monitoring_choices` y `validate_action_proposal` se usan sin reimplementar. La única lógica añadida es resolución de ruta y estado del diagnóstico.

S3. "Implementa una configuración explícita y segura por proyecto, validando las reglas y el aislamiento MultiRepo."
Alcance: ruta de reglas explícita por proyecto en `ProjectConfig.monitoring_rules_path`, con respaldo en la clave YAML de nivel superior `monitoring_rules_path` que ya lee `EngineSettings` (mismo mecanismo que el CLI). Aislamiento: el archivo de reglas se valida contra el `project_id` del análisis; un archivo de otro proyecto se rechaza; una tarea de otro ámbito no existe en la captura del proyecto y se descarta.

S4. "No generes reglas automáticamente a partir de los riesgos narrativos del LLM."
Alcance: las reglas se cargan solo desde un archivo explícito del propietario. `MonitoringDraft.risks` y `MonitoringDraft.suggested_actions` no se leen para derivar reglas, candidatos ni propuestas.

S5. "No inventes propuestas ni modificaciones autorizadas para HORMIGESTION. Si requiere reglas nuevas del propietario, presenta primero las reglas concretas para mi aprobación."
Alcance: no se crea ningún archivo de reglas real para `HORMIGESTION` en `config/`. Las reglas de prueba viven en `tests/`. Las reglas reales propuestas se presentan en el informe final y quedan pendientes de autorización.

S6. "Mantén el comportamiento seguro cuando no existan reglas: no generar propuestas y mostrar claramente esa condición en el diagnóstico."
Alcance: sin reglas, `proposal_rules` es `None`, la etapa `proposals` queda `skipped` con limitación `missing_rules` y no se persiste ninguna propuesta. El diagnóstico expone el estado (`configured` / `not_configured` / `invalid`).

S7. "Comprueba con pruebas deterministas que un análisis iniciado mediante la API HTTP puede cargar reglas de prueba, generar candidatos válidos, persistir propuestas y permitir recuperarlas desde Propuestas."
Alcance: prueba a nivel de API HTTP con SQLite temporal, proveedor LLM simulado y fuente de prueba.

S8. "Verifica que las propuestas bloqueadas, no autorizadas o sin evidencia suficiente no puedan ejecutarse."
Alcance: cobertura de denegación por revisión técnica bloqueada, por decisión humana ausente y por evidencia insuficiente o no única.

S9. "Realiza un E2E aislado del flujo web/API con SQLite temporal y proveedores simulados, sin llamadas LLM reales."
Alcance: React real + FastAPI real + SQLite temporal + proveedor LLM simulado + fuente de prueba. El análisis se dispara desde el navegador y la propuesta aparece en la vista de Propuestas.

S10. "Restricciones: EXTERNAL_WRITES_ENABLED=false, sin escrituras externas, sin modificar HORMIGESTION ni datos históricos, sin nuevos agentes, sin push y sin merge."

S11. "Por ahora no implementes G2 ni G3. Después de verificar G1, revisaremos cómo vincular riesgos, tareas y recomendaciones."
Alcance: fuera de alcance. No se modifica `MonitoringDraft.risks` (G2) ni el enlace riesgo↔propuesta (G3).

S12. "Detente antes de activar reglas correctivas reales sobre HORMIGESTION y espera mi autorización."
Alcance: el trabajo termina en un commit local con reglas de prueba. La activación real sobre `HORMIGESTION` requiere autorización posterior.

Criterios de aceptación:
- Un análisis lanzado por HTTP con un archivo de reglas de prueba persiste ≥1 propuesta y la vista de Propuestas la devuelve con su tarea y campo.
- Sin archivo de reglas, el análisis corre, persiste 0 propuestas y el diagnóstico dice `not_configured`.
- Un archivo de reglas inválido o de otro proyecto produce 0 propuestas y estado `invalid`, sin_crashes ni efectos colaterales.
- Una propuesta con revisión técnica bloqueada, sin decisión humana o con evidencia insuficiente no se ejecuta y deja recibo de denegación.

## Tasks

T1 [S2,S3] done; route=parent; `MonitoringRulesError` gana `reason` (`invalid`/`project_mismatch`) sin cambiar el mensaje del CLI; la excepción se re-lanza para no perder la razón. `ProjectConfig.monitoring_rules_path` explícito por proyecto + `project_rules_path` que lee `project.monitoring_rules_path` del YAML. Commit=pending.
T2 [S1,S4,S6] done; route=parent; `_resolve_proposal_rules` carga con el cargador validado existente; `AnalysisRuntimePlan` transporta las reglas y su estado; el fingerprint las incluye; `_execute_orchestrator` pasa `proposal_rules` a `orchestrator.execute`. Commit=pending.
T3 [S6] done; route=parent; `AnalysisPreflight.proposal_rules_status` (`configured`/`not_configured`/`invalid`) como campo dedicado, no como `PreflightCheck`, para no degradar `configuration_status` ni la elegibilidad. Commit=pending.
T4 [S6] done; route=parent; la vista de Análisis muestra el estado de las reglas y su consecuencia. Commit=pending.
T5 [S2,S9] superseded; el cambio directo sobre `FakeProvider` rompió el contrato existente de `tests/test_monitoring_proposals.py:64` (una respuesta genérica no puede seleccionar cambios). Se revirtió y se creó `SelectingFakeProvider` en el módulo de pruebas, que solo sustituye al proveedor en el ámbito de la prueba. `FakeProvider` compartido queda intacto. Commit=pending.
T6 [S7,S8] done; route=parent; 12 pruebas deterministas de API HTTP. Commit=pending.
T7 [S9] done; route=parent; E2E web aislado con `tests/g1_browser_server.py` y `frontend/e2e-backend/g1-web-proposal-rules.spec.ts`. Commit=pending.
T8 [S5,S12] done; route=parent; `docs/g1-web-proposal-rules.md` con la configuración que necesita HORMIGESTION y la plantilla de reglas pendiente de autorización. Commit=pending.

## Log

L1. Petición original del usuario, verbatim:
"Continuamos el desarrollo de AgenteGestionProyectos en la rama `feature/multirepo`. Esta es una nueva sesión. Recupera primero el contexto persistido del proyecto, revisa las instrucciones del repositorio y consulta el estado de Git. Los trabajos P0/P1 de REQ-08 y la corrección de Playwright ya se implementaron mediante commits locales. El último commit de referencia es `0a9bd50`. No repitas esas implementaciones ni reviertas los cambios existentes. Estamos realizando una validación manual de la experiencia del gestor. El siguiente objetivo es investigar la relación entre las tareas de Trello, los riesgos detectados por Monitoring y las propuestas de acciones correctivas. Antes de intervenir, confirma que has recuperado el contexto necesario y que reconoces los cambios locales preexistentes. Por ahora no modifiques archivos, no ejecutes LLM reales y no realices escrituras externas. Espera mis siguientes instrucciones."

L2. Hallazgo de la investigación previa (read-only, sin escrituras): la web nunca produce propuestas. `api/analysis_jobs.py:862-864` llama `orchestrator.execute(project_id=project_id)` sin `proposal_rules`; solo el CLI los pasa (`cli.py:284-319`); no existe `monitoring_rules_path` en `config/hormigestion.yaml` ni `config/config.yaml` y no hay JSON de reglas en el repo. `monitoring_choices` devuelve `missing_rules` y la etapa `proposals` queda `skipped` con 0 guardados. El E2E de T6 (`0b78a98`) no cubría este camino: `tests/test_req08_decision_workflow.py:80` invoca el orquestador directamente con `proposal_rules`.

L3. Mapa de la relación (read-only): ancestro común = captura `WeeklySnapshot` + evidencias. Trello → `TaskCard` + métricas deterministas. "Riesgos" son dos cosas distintas: (a) strings de conteo deterministas en `build_task_plan_summary` (`application/tasks_query.py:160-176`) → `identified_risks` → `GET /projects/{id}/summary` → Dashboard; (b) texto libre del LLM en `MonitoringDraft.risks`/`suggested_actions` (`domain/report.py:92-93`), consumido solo por validación, narrativa y ReportAgent. Las propuestas (`ActionProposal`, `domain/action_proposal.py:45-62`) nunca leen (b): las intermedia el archivo de reglas del dueño (`DomainProposalRuleSet`). Secuencia real: reglas → `monitoring_choices` (`agents/context.py:55-133`) calcula candidatos deterministas → el LLM solo selecciona → `_persist_proposals` revalida y persiste (`application/orchestrator.py:934-1013`).

L4. Decisión del usuario: "Selecciono la segunda opción: corregir G1 antes de continuar con la validación manual." Con las ocho condiciones, las restricciones y la orden de detenerse antes de activar reglas reales sobre HORMIGESTION.

L5. Diseño acordado antes de escribir: (a) `ProjectConfig.monitoring_rules_path` explícito por proyecto, con respaldo de la clave YAML de nivel superior `monitoring_rules_path` que ya lee `EngineSettings` (`application/config.py:363`) — misma ruta que el CLI; el YAML se aplana, por lo que la clave va en el nivel superior (`application/config.py:196-199`). (b) Resolución y carga en `load_analysis_plan` mediante el cargador validado existente; el estado del diagnóstico es un campo dedicado `proposal_rules_status` en `AnalysisPreflight`, NO una `PreflightCheck`, porque `configuration_status` solo es `ready` si todos los checks lo son y un check `incomplete` haría el análisis no elegible (`api/analysis_jobs.py:354`, `plan.eligible`). (c) `MonitoringRulesError` gana un atributo `reason` opcional (`invalid` / `project_mismatch`) para distinguir sin duplicar validación; el mensaje del CLI no cambia.

L6. Detalle confirmado: el proveedor LLM simulado (`adapters/llm/fake.py`) no devuelve `selected_choice_ids`, por lo que con reglas cargadas pero proveedor simulado no habría selección y 0 propuestas. `MonitoringDraftSchema` ignora campos extra, y `runtime_payload` copia `context.data`, donde `agents/monitoring.py:118-124` inyecta `proposal_choices`. Ampliar el doble de prueba (no el pipeline) es necesario y está autorizado por S9.

L7. Rutas de configuración confirmadas: la capa YAML aplana claves de nivel superior (`application/config.py:196-199`); `sources.policies_path` ya sigue ese patrón (`application/config.py:118-120`), así que `monitoring_rules_path` de nivel superior es coherente con el estilo existente. `ProjectConfig` forbids extras (`api/settings.py:44`), por lo que el campo nuevo se declara explícitamente.

L8. Verificación de contratos que NO se tocan: la ejecución de propuestas conserva sus puertas (`application/proposal_execution.py:560-563` modo live/sandbox, `:657` revisión técnica, `:682` decisión humana, `:690` destino no encontrado). `assess_proposal` (`domain/proposal_review.py:97-105`) recalcula los errores técnicos. El cambio solo habilita la generación; no altera ninguna puerta de autorización.

L9. Hallazgo que condiciona el diseño de la prueba: la ruta de propuestas exige que la evidencia de tareas esté asociada a la captura. `orchestrator.py:244-247` solo puebla `task_evidence` cuando la colección implementa `SourceCollectionPort`; en modo fixtures la colección heredada no produce evidencia y la etapa queda `collection_unassociated`. La colección real (`CompositeSourcesAdapter` y `MultiRepoSourcesAdapter`) sí produce evidencia de tareas, así que `HORMIGESTION` no queda afectado. Para la prueba y el E2E se usa un adaptador de colección simulado propio que emite evidencia de tarea como el adaptador real, sin tocar `FixtureSourcesAdapter`.

L10. RED observado antes de implementar: con el código de producción revertido, las 12 pruebas de `tests/test_g1_proposal_rules_api.py` fallan (3 con `KeyError: 'proposal_rules_status'` por el diagnóstico ausente y 3 con `ValidationError` por el campo de configuración por proyecto ausente). GREEN: 12/12 tras la implementación.

L11. Corrección de T5: el primer intento amplió `FakeProvider` para seleccionar candidatos y rompió `tests/test_monitoring_proposals.py:64`, que exige que una respuesta genérica no produzca `requested_changes`. Ese contrato es correcto y se preservó: el doble compartido no inventa selecciones. Se revirtió el cambio en `src/agente_gestion/adapters/llm/fake.py` y se creó `SelectingFakeProvider` en el módulo de pruebas, que se inyecta solo dentro del ámbito de la prueba y del E2E. `src/agente_gestion/adapters/llm/fake.py` queda sin cambios respecto a `0a9bd50`.

L12. Corrección del E2E: la primera ejecución del navegador falló con un `ReferenceError` por un typo en `AnalysisPage.tsx` (`preposalRulesHint`); luego el botón *Confirmar análisis* apareció deshabilitado porque el preflight de la configuración marcaba *Requiere atención*: en el directorio de trabajo del servidor no existían `config/policies.yaml` ni `fixtures/`, así que el cargador YAML los resolvía como rutas relativas. Se corrigió con rutas absolutas en la configuración aislada. Una tercera causa: la tarjeta simulada usaba el ID de tarea del módulo de pruebas y no el del E2E, por lo que la regla caía en `unknown_task`. Tras parametrizar el helper de tarjeta, el E2E pasa: 1 passed (4.0s).

L13. Verificación observada: `tests/test_g1_proposal_rules_api.py` 12/12 OK; E2E `g1-web-proposal-rules.spec.ts` 1 passed (4.0s) con React real, FastAPI real, SQLite temporal, fuente y proveedor simulados y guardia de red que rechaza cualquier destino fuera de `127.0.0.1:4173`; `npm run typecheck` sin errores; `npm run build` correcto; suite backend `Ran 1105 tests` con 0 fallos, 1 skip y 1 error preexistente de descubrimiento (`tests/test_monitoring_gemini_runner.py` importa `test_actions` de forma absoluta; ejecutado desde `tests/` pasa con 14 pruebas OK). Ese archivo no fue modificado por este trabajo.

L14. Riesgo de la entrega: el cambio habilita la generación de propuestas desde la web, es decir que un archivo de reglas configurado puede producir propuestas que antes no existían. Ninguna puerta de autorización se relajó: sin sesión de gestor la ejecución es 401, sin CSRF es 403, con revisión técnica bloqueada la ejecución se deniega y sin evidencia suficiente el catálogo rechaza guardar la propuesta. El proyecto `HORMIGESTION` no tiene reglas configuradas, así que su comportamiento real no cambia hasta que el gestor autorice un archivo. Riesgo: item 3 (contrato de diagnóstico) y item 2 (guardas de autorización) verificados por pruebas.
