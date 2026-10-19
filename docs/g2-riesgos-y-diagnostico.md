# Riesgos deterministas por tarea y diagnóstico de propuestas

Este documento describe **G2**: cada riesgo determinista queda identificado, se conoce su tarea afectada y el gestor puede entender por qué una ejecución no produjo propuestas. **G3** (asociar un riesgo a una propuesta) queda fuera de esta fase.

## 1. Riesgos deterministas estructurados

Antes, `TaskPlanSummary.identified_risks` era una lista de cadenas de conteo sin identificador de tarea: el gestor veía "2 tarea(s) vencida(s) no completada(s)" sin poder llegar a esas tareas.

Ahora el resumen expone además `risks`, una lista de objetos `TaskRisk`:

| Campo | Contenido |
| --- | --- |
| `risk_id` | Identificador estable, derivado de proyecto, captura, tipo y conjunto ordenado de tareas. |
| `project_id` / `capture_id` | Trazabilidad: de qué proyecto y de qué captura proviene el riesgo. |
| `kind` | `blocked`, `overdue`, `due_soon`, `contradictory_state`, `unknown_state`. |
| `task_ids` / `task_count` | Tareas afectadas por ese riesgo. |
| `summary` | La cadena histórica equivalente (sin cambios de texto). |
| `basis` | Regla determinista que produjo la clasificación. |
| `source` | Siempre `deterministic`. |

`identified_risks` y todos los conteos (`overdue_count`, `due_soon_count`, `blocked_count`, `unknown_count`, `contradictory_count`) se conservan con el mismo valor y el mismo orden: `risks` es un campo aditivo.

`GET /projects/{project_id}/tasks/{task_id}` añade `risks`, limitado a los riesgos que afectan a esa tarea, con su `capture_id` para trazabilidad.

### Determinista y narrativo no se mezclan

Solo las métricas de tareas (`calculate_task_metrics`) crean riesgos. El texto libre del agente (`MonitoringDraft.risks`, `MonitoringDraft.suggested_actions`) nunca se convierte en riesgo ni genera asociaciones riesgo-tarea: no hay ningún camino de código que lo haga. Los campos `source` y `kind` son conjuntos cerrados y auditables.

## 2. La etapa `proposals` dejó de ser invisible

Dos defectos independientes ocultaban el resultado de la etapa:

1. `_RUN_STAGES` no incluía `proposals`, así que la consulta de etapas nunca la devolvía.
2. `run_stages` descartaba cualquier resumen que empezara por `{` o `[`, y el resumen de `proposals` es JSON.

Ahora la etapa aparece en `GET /runs/{run_id}/stages` y su resumen se expone como `proposals`, un diagnóstico estructurado y acotado:

| Campo | Contenido |
| --- | --- |
| `outcome` | `saved`, `not_configured`, `rules_invalid`, `no_candidates`, `candidates_discarded`, `not_selected`, `unknown`. |
| `offered` / `selected` / `saved` | Candidatos ofrecidos, seleccionados y guardados. |
| `technical_ready` / `technical_blocked` | Resultado de la revisión técnica automática. |
| `limitation_codes` / `failure_codes` | Códigos técnicos conocidos, para auditoría. |
| `discarded` | Candidatos descartados. |

El JSON interno crudo nunca se devuelve: `result_summary` sigue vacío para esta etapa y el diagnóstico solo expone conteos y códigos de una lista permitida. Un código desconocido se descarta en lugar de exponerse.

Para distinguir "no hubo candidatos" de "el agente no seleccionó", el resumen persistido ahora registra `offered`, el número de candidatos que el backend ofreció. Es un dato adicional, no cambia el pipeline.

### Casos de cero propuestas

| `outcome` | Qué se puede demostrar |
| --- | --- |
| `not_configured` | No hay reglas de propuestas configuradas. |
| `rules_invalid` | Hay reglas, pero fueron rechazadas (`invalid_rules`, `project_mismatch`). |
| `no_candidates` | Las reglas se aplicaron y no produjeron ningún candidato válido. |
| `candidates_discarded` | Hubo candidatos seleccionados y todos fueron descartados; los motivos están en `failure_codes`. |
| `not_selected` | Hubo candidatos offered y el agente no seleccionó ninguno. |
| `saved` | Se guardaron propuestas. |
| `unknown` | Los datos persistidos no permiten probar la causa. |

La clasificación es conservadora: cuando el resumen no alcanza para probar una causa, el resultado es `unknown`. No se atribuye un motivo que los datos no sostienen.

## 3. Aislamiento

- Los riesgos se calculan dentro del proyecto y la captura que consulta el cliente; `project_id` y `capture_id` viajan en cada riesgo.
- Un proyecto, una captura o una tarea ajenos responden 404 y no devuelven datos de riesgo.
- La consulta de etapas sigue resolviendo el run en la base local y devuelve 404 para un run inexistente.

## 4. G3 (pendiente, no implementado)

G3 asociará un riesgo a las propuestas que lo originan. Requiere un `risk_id` compartido, una referencia opcional de riesgo en `DomainProposalRule`, candidatos por riesgo en `monitoring_choices` y la cadena riesgo → tarea → propuesta → decisión en la interfaz. Nada de eso se implementó aquí: esta fase no toca `ActionProposal` ni el contrato de reglas.
