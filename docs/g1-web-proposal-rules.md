# Reglas de propuestas en el flujo de análisis iniciado desde la web

Este documento describe el cierre del hueco **G1**: el análisis lanzado desde la web ya carga las reglas de propuestas del propietario usando el mismo contrato validado que el CLI, publica las propuestas resultantes y las deja disponibles en la vista de Propuestas.

**Estado:** implementado y verificado con pruebas deterministas y un E2E aislado. **No** se activó ninguna regla real sobre `HORMIGESTION`: eso requiere autorización explícita del gestor.

## El problema que se corrigió

`AnalysisJobs` llamaba al orquestador sin reglas:

```python
# antes
orchestrator.execute(project_id=project_id)
```

El CLI sí las pasaba (`cli.py`), de modo que la web nunca podía producir propuestas: `monitoring_choices` devolvía `missing_rules`, la etapa `proposals` quedaba `skipped` y se persistían 0 propuestas. La vista de Propuestas quedaba vacía y el contador de pendientes del gestor marcaba 0 o desconocido, sin explicación.

## Cómo se cargan ahora las reglas desde la web

1. `load_analysis_plan` resuelve la ruta de reglas **por proyecto**:
   - `ProjectConfig.monitoring_rules_path` (explícito, por proyecto), y
   - `EngineSettings.monitoring_rules_path` (clave YAML de nivel superior, la misma que lee el CLI).
   El valor explícito del proyecto tiene prioridad.
2. La ruta se carga con el cargador validado ya existente, `load_monitoring_rules`, que:
   - acota el tamaño del archivo a 1 MiB,
   - valida el JSON contra el contrato estricto `DomainProposalRuleSet`,
   - exige que `project_id` del archivo coincida con el proyecto del análisis.
3. Las reglas validadas viajan en el `AnalysisRuntimePlan` y se pasan a `orchestrator.execute(proposal_rules=...)`.
4. El orquestador sigue siendo el dueño de la decisión: recalcula los candidatos con `monitoring_choices` sobre la captura y la evidencia reales, y solo persiste lo que la evidencia respalda.

No se duplicó ninguna validación del CLI: se reutilizan `load_monitoring_rules`, `DomainProposalRuleSet`, `monitoring_choices` y `validate_action_proposal`.

### Nada se deriva de los riesgos narrativos

El camino de propuestas **no lee** `MonitoringDraft.risks` ni `MonitoringDraft.suggested_actions`. Las reglas solo provienen de un archivo explícito del propietario. El agente de Monitoring no puede inventar una acción correctiva: únicamente **selecciona** entre los candidatos que el backend ya calculó, como máximo uno por tarea y campo.

### Aislamiento

- Un archivo de reglas de otro proyecto se rechaza (`project_mismatch`): ese proyecto queda sin reglas.
- Una regla que nombra una tarea que no existe en la captura del proyecto se descarta (`unknown_task`).
- El diagnóstico distingue tres estados: `configured`, `not_configured` e `invalid`.

### Comportamiento seguro sin reglas

Sin reglas, el análisis **se ejecuta igual**: la elegibilidad no cambia, no se persiste ninguna propuesta, la etapa `proposals` queda `skipped` con la limitación `missing_rules` y el diagnóstico muestra *"Reglas de propuestas no configuradas"*. La ausencia de reglas nunca es un bloqueo del análisis, solo la ausencia de propuestas.

## Qué configuración necesita HORMIGESTION

`config/hormigestion.yaml` no declara reglas hoy. Eso es intencional: hasta que el gestor autorice un archivo de reglas, el proyecto se comporta como antes (análisis sin propuestas) y el diagnóstico lo dice explícitamente.

Para activarlas hacen falta dos piezas:

1. **El archivo de reglas** (JSON), fuera de `config/` mientras no esté aprobado. Ruta sugerida: `config/monitoring-rules-hormigestion.json`.
2. **La ruta declarada**, en `config/hormigestion.yaml`. La clave de nivel superior es la que ya lee el CLI:

```yaml
monitoring_rules_path: config/monitoring-rules-hormigestion.json
```

O, de forma explícita y por proyecto, dentro de la sección `project:`:

```yaml
project:
  id: HORMIGESTION
  timezone: America/Guayaquil
  allow_remote_ai: true
  monitoring_rules_path: config/monitoring-rules-hormigestion.json
```

Ambas formas son equivalentes para la web; la de `project:` es la que queda atada al proyecto y es la recomendada cuando un mismo despliegue sirve a más de un proyecto.

## Qué reglas reales habría que autorizar

El archivo tiene esta forma exacta (`DomainProposalRuleSet`):

```json
{
  "project_id": "HORMIGESTION",
  "rules": [
    {
      "rule_id": "regla-prorroga-vencida",
      "task_ids": ["<ID EXACTO de la tarjeta de Trello>"],
      "field": "due_date",
      "allowed_values": ["2026-10-24T17:00:00-05:00"],
      "rationale": "Motivo legible para la persona que decide.",
      "expected_impact": "Efecto esperado de aplicar el cambio."
    }
  ]
}
```

Restricciones que el validador y el motor aplican, y que conviene respetar al redactarlas:

| Campo | Regla |
| --- | --- |
| `project_id` | Debe ser exactamente `HORMIGESTION`; si no, el archivo se rechaza. |
| `rule_id` | Identificador único y no vacío. |
| `task_ids` | IDs **exactos** de tarjetas de Trello. Una tarea inexistente descarta la regla. |
| `field` | Solo `start_date`, `due_date`, `priority`, `assignee_ids` o `state`. |
| `allowed_values` | Lista no vacía; ningún valor puede ser `unknown`. El valor propuesto debe diferir del actual. |
| Valores de fecha | Cadena ISO con hora y **offset**: `"2026-10-24T17:00:00-05:00"`. Sin offset, la comparación con la fecha observada de Trello no coincide. |
| `assignee_ids` | Lista de identificadores de miembro, sin duplicados. |
| `priority` | Debe existir una etiqueta configurada para ese valor; si no, la ejecución se rechaza. |
| `rationale`, `expected_impact` | Texto no vacío; se copian literalmente a la propuesta y los ve el gestor. |

Restricción adicional del collector: el campo debe estar entre los observados de la tarjeta. Con `state_strategy: date_fields` el adaptador de Trello observa `due_date`, `start_date`, `assignee_ids`, `priority`, `due_complete`, `is_archived`, `last_activity_at`, `state` y `list_name`.

**Ninguna de estas reglas se creó en el repositorio.** El bloque anterior es la plantilla; los `task_ids` y las fechas concretas los define el gestor a partir del tablero real, y el archivo solo se crea después de la autorización.

Propuesta de primera regla, sujeta a autorización: una sola regla de prórroga sobre `due_date` para las tarjetas cuya fecha ya venció, con un plazo máximo y un motivo explícito. Empezar por una sola tarea y un solo campo permite verificar el circuito completo antes de ampliar el alcance.

## Pruebas ejecutadas

Backend (`tests/test_g1_proposal_rules_api.py`, 12 pruebas, aplicación FastAPI real con SQLite temporal):

| Prueba | Qué demuestra |
| --- | --- |
| `test_web_analysis_with_owner_rules_publishes_and_lists_proposals` | Un análisis lanzado por HTTP carga las reglas, genera candidatos válidos, persiste la propuesta y la vista de Propuestas la devuelve con tarea y campo. |
| `test_shared_yaml_rules_path_is_accepted_like_the_cli` | La clave YAML de nivel superior funciona igual que en el CLI. |
| `test_analysis_without_rules_keeps_running_and_publishes_nothing` | Sin reglas el análisis corre, no publica propuestas y el diagnóstico dice `not_configured`. |
| `test_invalid_rules_file_reports_invalid_without_publishing` | Un archivo inválido se reporta como `invalid` y no publica nada. |
| `test_rules_of_another_project_never_reach_this_project` | Aislamiento: un archivo de otro proyecto no se aplica. |
| `test_rule_naming_a_task_outside_the_project_is_discarded` | Una regla sobre una tarea ajena al proyecto se descarta. |
| `test_published_proposal_starts_unauthorized_and_unexecuted` | Una propuesta publicada es recomendación: `review_status=pending`, `authorization_status=not_authorized`, `execution_status=not_executed`, `execution_authorized=false`. |
| `test_proposal_without_manager_session_cannot_be_executed` | Sin sesión de gestor la ejecución se rechaza con 401 y no deja estado. |
| `test_proposal_without_csrf_token_cannot_be_executed` | Sin prueba CSRF la ejecución se rechaza con 403 y no deja estado. |
| `test_stale_proposal_is_blocked_and_cannot_be_executed` | Una propuesta con captura obsoleta queda `blocked` y la ejecución se deniega (`technical_review_blocked`) sin tocar Trello. |
| `test_catalogue_refuses_a_proposal_whose_evidence_is_insufficient` | El propio catálogo rechaza guardar una propuesta cuya evidencia no verifica. |
| `test_rule_without_supporting_evidence_publishes_nothing` | Una regla que la evidencia observada no respalda no publica nada. |

E2E web (`frontend/e2e-backend/g1-web-proposal-rules.spec.ts`): React real + FastAPI real + SQLite temporal + proveedor y fuente simulados. El análisis se prepara y confirma **desde el navegador**, se espera a su finalización y la propuesta aparece en la bandeja de revisión con su tarea. El guardia de red rechaza cualquier petición fuera de `127.0.0.1:4173`.

Suite backend completa: `Ran 1105 tests ... FAILED (errors=1, skipped=1)` — 0 fallos. El único error es preexistente y ajeno al cambio: `tests/test_monitoring_gemini_runner.py` importa `test_actions` de forma absoluta y no carga bajo `unittest discover -s tests -t .`; ejecutado desde `tests/` pasa (14 pruebas, OK).

Frontend: `npm run typecheck` sin errores, `npm run build` correcto.

## Validación controlada sobre datos reales de HORMIGESTION

El E2E sintético (`g1-web-proposal-rules.spec.ts`) cubre el circuito hasta que la propuesta aparece. Para exercising decisión, sandbox, recibo y auditoría sobre **datos reales del proyecto**, existe una demostración controlada adicional.

### Qué hace y qué no hace

- **No** activa ninguna regla permanente: `config/hormigestion.yaml` queda intacto y el archivo de reglas vive solo en un directorio temporal.
- **No** modifica la base del proyecto: `.runtime/agente.db` se abre con `file:...?mode=ro` (SQLite rechaza cualquier escritura en ese manejador) y la demostración comprueba además que su SHA-256 no cambia entre el inicio y el final.
- **No** toca Trello: la ejecución usa `SandboxTaskActions`, un doble en memoria, y `EXTERNAL_WRITES_ENABLED=false` fuerza el modo `sandbox`.
- **No** usa LLM real: el proveedor simulado determinista sustituye al modelo solo dentro del entorno de prueba.
- **No** afirma que la prórroga sea una corrección operativa validada. El archivo de reglas lleva un marcador explícito (`DEMOSTRACION G1 - CASO DE PRUEBA, NO ES UNA CORRECCION OPERATIVA`) en el `rule_id`, la `rationale` y el `expected_impact`.

### Archivos

| Archivo | Papel |
| --- | --- |
| `tests/g1_hormigestion_demo_server.py` | Sirve la aplicación real sobre una base temporal, copiando la última captura y la evidencia de tareas reales de HORMIGESTION |
| `frontend/e2e-backend/g1-hormigestion-demo.spec.ts` | Recorrido completo desde React |
| `frontend/e2e-backend/playwright.g1demo.config.ts` | Configuración aislada del recorrido |

### Recorrido verificado

1. `/analysis` → *Preparar análisis* → el diagnóstico dice **"Reglas de propuestas cargadas"** → *Confirmar análisis* → el trabajo llega a `completed`.
2. `/proposals` → **1 propuesta** de campo "Fecha de vencimiento".
3. Detalle: revisión técnica **lista**, "Valor actual" y "Cambio recomendado", el enlace *Abrir tarea* apunta al identificador exacto `6a9582c16a2c16f6106609e2`, y bajo demanda la evidencia `trello-task-6a9582c16a2c16f6106609e2` con `category=task`.
4. Antes de decidir, la API confirma `review_status=pending`, `authorization_status=not_authorized`, `execution_status=not_executed` y `execution_authorized=false`.
5. Decisión con motivo → diálogo de confirmación → la interfaz muestra **"Resultado sandbox registrado · no acredita cambios live"**.
6. Recibo: `execution_mode=sandbox`, `status=SIMULATED`, `result_status=EXECUTED`, `verification_status=verified`, `live_state_changed=false`, con `value_before=2026-10-19T17:00:00-05:00` y `value_observed_after=2026-10-20T17:00:00-05:00` **en el doble en memoria**.
7. Historial: `decision_history[0].decision=approve_and_execute` con actor `gestora-g1-demo` y el motivo escrito; `latest_receipt.status=EXECUTED`. El recibo y el historial sobreviven a una recarga.
8. Auditoría: evento `proposal_execution` con resultado `EXECUTED` y evento `security` con resultado `granted`, actor verificado y permiso `web:proposal_execute`.
9. El SHA-256 de `.runtime/agente.db` es idéntico al inicial y el guardia de red del navegador no registra ninguna petición fuera de `127.0.0.1:4173`.

Resultado observado: `1 passed (3.9s)`.

## Qué falta para conectar riesgos, tareas y propuestas

Diagnóstico de la fase siguiente, con la evidencia que lo sostiene.

### G2 — el texto de riesgo no está atado a su tarea

Hoy conviven dos cosas distintas llamadas "riesgo":

- **Las deterministas**: `build_task_plan_summary` (`application/tasks_query.py:160-176`) arma cadenas de conteo ("2 tarea(s) próxima(s) a vencer…"). No llevan identificador de tarea, así que el Dashboard las muestra como una lista plana y el gestor no puede saltar a la tarea.
- **Las narrativas del LLM**: `MonitoringDraft.risks` y `suggested_actions` (`domain/report.py:92-93`) son texto libre. El agente solo garantiza que mencione tareas en `referenced_task_ids`, pero el vínculo es paralelo, no estructural: una lista de riesgos y otra de tareas, sin correspondencia.

La única estructura atada que existe hoy es `risk_task_ids` del informe (`api/control_plane.py:240-243`), y se deriva de los **cajones deterministas**, no de la narración. Por eso "riesgos de Monitoring" y "riesgos del informe" son conjuntos distintos.

Falta, en concreto:

1. Una entidad de riesgo tipada (`risk_id`, `kind`, `task_ids`, `severity`, `origen` determinista o narrativo) en lugar de cadenas sueltas. Ya existe el precedente que seguir: `MonitoringDraft.task_state_claims` ata un `task_id` a un estado y se valida.
2. Que la interfaz renderice cada riesgo con sus tareas enlazadas, en lugar de una lista de frases.
3. Una decisión explícita: si el riesgo narrativo del LLM se tipa, pasa a validarse; si no, la estructura se construye solo sobre los riesgos deterministas y la narración queda como prosa.

### G3 — el riesgo y su propuesta correctiva no se reconocen entre sí

`ActionProposal` (`domain/action_proposal.py:45-62`) transporta `task_id`, `field`, valores, `evidence_ids`, `rationale` y `expected_impact`, pero **ninguna referencia al riesgo** que la motivó. Su `rationale` y su `expected_impact` se copian literalmente de la regla del propietario, no del riesgo detectado. Y `suggested_actions` (texto libre) y las propuestas tipadas viven en lugares sin conexión: el Dashboard muestra unas, Propuestas muestra las otras.

Falta, en concreto:

1. Un identificador de riesgo que ambos extremos puedan llevar.
2. `DomainProposalRule` con una referencia opcional al riesgo que atiende, de modo que la regla se declare "contra un riesgo" y no solo "contra una tarea".
3. Que `monitoring_choices` pueda calcular candidatos por riesgo, no solo por tarea y campo.
4. En la interfaz, la cadena riesgo → tarea → propuesta → decisión, con la propuesta mostrando qué riesgo responde.

### Hallazgo transversal: el motivo del silencio no se muestra

Cuando una regla no produce ninguna propuesta, la causa **se calcula y se persiste** en el resumen de la etapa `proposals` (por ejemplo `{"selected": 0, "saved": 0, "limitations": ["unknown_task"]}`), pero **no llega a ninguna superficie de la API**:

- `api/repository.py:935` define `_RUN_STAGES = ("source_collection", "monitoring", "quality", "verifier", "report")`: la etapa `proposals` no está en la lista, así que ni siquiera aparece en `GET /runs/{run_id}/stages`.
- Y aunque estuviera, `api/repository.py:1124-1125` descarta cualquier resumen que empiece por `{` o `[`, que es exactamente el caso del resumen JSON de esa etapa.

Verificado en la demostración: con una regla que nombra una tarea no visible, la base de datos guarda `limitations: ["unknown_task"]` y el gestor ve una bandeja vacía sin ninguna explicación. Es la misma clase de vacío que G1 vino a resolver, pero del lado de la configuración de reglas: sin reglas el diagnóstico ya dice "Reglas de propuestas no configuradas"; con reglas que no encajan, el sistema calla.

Este punto debería resolverse junto con G2/G3, porque es la pieza que permite entender por qué una cadena de riesgo, tarea y propuesta no se cierra.

## Qué sigue pendiente para validar el flujo desde el navegador

1. **Autorizar el archivo de reglas** para `HORMIGESTION` (sin autorización no hay nada que activar).
2. **Cargar las reglas en la configuración** del proyecto y reiniciar la API.
3. En el navegador: *Análisis de IA* → *Preparar análisis* → confirmar que el diagnóstico dice *"Reglas de propuestas cargadas"* → *Confirmar análisis*.
4. Verificar en *Propuestas* que la propuesta aparece con la tarea, el campo, el valor actual y el propuesto, y que el contador de pendientes del menú refleja el valor del backend.
5. Decidir la propuesta con un motivo y comprobar el recibo: en sandbox no cambia nada en Trello; con `EXTERNAL_WRITES_ENABLED=true` y credenciales, el cambio se aplica y queda auditado.
6. Pendiente de la siguiente fase, fuera de este alcance: **G2** (vincular el texto de riesgo con su tarea) y **G3** (enlazar el riesgo con su propuesta y su recomendación). El diagnóstico actual expone el estado de las reglas, no la relación riesgo ↔ tarea ↔ propuesta.
