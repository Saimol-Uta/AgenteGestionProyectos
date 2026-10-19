# REQ-08: evidencia del flujo de decisiones

T2 demuestra el circuito existente de propuestas de tareas sin cambiar el pipeline técnico ni añadir agentes: `ProjectOrchestrator` ejecuta Monitoring con proveedor y fuente deterministas de prueba; la misma SQLite temporal se consulta y modifica mediante la API FastAPI real.

## Recorrido comprobable

1. `tests/test_req08_decision_workflow.py` ejecuta el orquestador real con salida controlada de `FakeProvider` y un adaptador de fuente de prueba. La propuesta válida llega a `GET /api/v1/projects/{project_id}/proposals` en estado pendiente, técnicamente elegible y sin autorización.
2. Si la fuente no entrega evidencia, Monitoring no persiste propuestas ejecutables: la lista pública queda vacía y no existe recibo de ejecución.
3. Una decisión humana autenticada se envía por `POST .../proposals/{proposal_id}/execute`. La aprobación en fixture informa `execution_mode: sandbox`, `SIMULATED`, resultado verificado y no cambio de estado remoto. Una repetición con la misma clave devuelve el recibo existente.
4. Al modificar la fecha, la propuesta original conserva su proyección pública; la derivada tiene otro ID y digest. El recibo y el detalle público de la propuesta ligan el nuevo digest con la revisión técnica correspondiente.
5. Una captura con fecha posterior hace obsoleta una propuesta. Se puede rechazar aun bloqueada; queda un recibo `DENIED` y no se ejecuta ninguna acción.
6. `GET .../audit` y `GET .../audit/{event_id}` verifican actor de sesión, motivo humano, fecha y resultado. Una mutación sin sesión responde 401 y deja la propuesta pendiente.

La prueba de sandbox comprueba además que la ruta de propuestas no crea el adaptador live de Trello ni usa el transporte HTTP. El endpoint GitHub sigue siendo el flujo distinto de hallazgos/calidad; T1, commit `6f31f61`, verifica que aprobar código devuelve `409 revision_independiente_requerida` antes de cualquier acción o replay, mientras rechazar y solicitar cambios continúan disponibles.

## Evidencia relacionada y límites

| Escenario | Evidencia existente o T2 | Qué demuestra |
|---|---|---|
| Detección y propuesta reales de aplicación | `Req08DecisionWorkflowTests.test_monitoring_pipeline_publishes_proposal_and_missing_evidence_publishes_none` | Orquestación y Monitoring reales; únicamente proveedor/fuente son deterministas. |
| Aprobación, auditoría, modificación y rechazo bloqueado | `tests/test_req08_decision_workflow.py` | HTTP público contra la aplicación y almacenamiento temporales; no consulta SQL privado ni simula recibos. |
| Fallo live sin presentar éxito sandbox | `ProposalExecutionApiTests.test_live_put_failure_is_not_reported_as_sandbox_success` en `tests/test_phase2_trello.py` | Adaptador Trello real con transporte HTTP falso; no contacta Trello. |
| Revisiones/decisiones GitHub | T1, commit `6f31f61` y verificación independiente reportada para ese commit | Separación GitHub/Trello y bloqueo de aprobación sin revisión independiente exacta. T2 no repite esa demostración. |

## T3: lectura segura de decisiones y recibos

`GET /api/v1/projects/{project_id}/proposals` agrega `pending_actionable_count`, calculado para todo el proyecto y no solo para la página solicitada. Cuenta únicamente propuestas actuales, elegibles, con revisión técnica `ready`, revisión humana `pending` y ejecución `not_executed`; excluye las propuestas originales consumidas por una modificación derivada. Si algún ledger requerido no está disponible o el dato histórico no permite concluirlo, el valor es `null`.

`GET /api/v1/projects/{project_id}/proposals/{proposal_id}` conserva el resumen público. Solo una sesión con alcance al proyecto y permiso existente `audit:read` recibe los campos opcionales de ledger; usuarios anónimos o sin alcance no reciben historial, actor, motivo, relaciones ni recibos.

| Campo de detalle | Significado |
|---|---|
| `decision_history` | Decisiones humanas y ejecuciones conocidas, con actor, verificación, motivo y fecha disponibles. |
| `decision_history_availability` | `available` o `unknown`; los eventos internos de aprobación/rechazo que corresponden a la misma acción web no se duplican como una segunda decisión. |
| `original_proposal_id`, `derived_proposal_ids` | Enlace entre propuesta original y las propuestas derivadas; no altera el cuerpo, digest, estado ni revisión de la original. |
| `relationship_availability` | `unknown` cuando el ledger histórico no permite reconstruir el vínculo; los identificadores no se infieren. |
| `latest_receipt` | `execution_id`, `proposal_id`, `original_proposal_id`, `decision`, `execution_mode`, `status`, `verification_status`, `reasons`, `actor_id`, `actor_verification`, `reason`, `recorded_at` y `audit_event_id`, según disponibilidad. |
| `receipt_availability` | `available` o `unknown`; si no hay recibo conocido, `latest_receipt` es `null`. |

`status` es el resultado tipado de la ejecución y siempre se interpreta junto con `execution_mode`: `EXECUTED` en modo `sandbox` no acredita una escritura live. `reasons` contiene códigos estables allowlisted, no texto de excepción ni payload del proveedor. Datos legacy o no verificables permanecen `null`/`unknown`; el API solo proyecta ledgers existentes con conexiones de solo lectura y no añade migraciones ni decisiones persistidas.

Ejemplo resumido de detalle autorizado:

```json
{
  "proposal_id": "proposal-mod-…",
  "original_proposal_id": "proposal-…",
  "derived_proposal_ids": [],
  "relationship_availability": "available",
  "decision_history_availability": "available",
  "decision_history": [{
    "event_id": "exec-web-…",
    "decision": "modify_and_execute",
    "proposal_id": "proposal-mod-…",
    "related_proposal_id": "proposal-…",
    "actor_id": "gestora-e2e-fixture",
    "actor_verification": "verified_local_session",
    "reason": "Fecha corregida y revisada por la gestora.",
    "recorded_at": "2026-10-18T21:28:22-05:00",
    "execution_mode": "sandbox",
    "execution_status": "EXECUTED",
    "verification_status": "verified",
    "reasons": [],
    "audit_event_id": "web-exec-…"
  }],
  "receipt_availability": "available",
  "latest_receipt": {
    "execution_id": "exec-web-…",
    "proposal_id": "proposal-mod-…",
    "original_proposal_id": "proposal-…",
    "decision": "modify_and_execute",
    "execution_mode": "sandbox",
    "status": "EXECUTED",
    "verification_status": "verified",
    "reasons": [],
    "actor_id": "gestora-e2e-fixture",
    "actor_verification": "verified_local_session",
    "reason": "Fecha corregida y revisada por la gestora.",
    "recorded_at": "2026-10-18T21:28:22-05:00",
    "audit_event_id": "web-exec-…"
  }
}
```

## Verificación aislada

El conjunto enfocado ejecutó 53 pruebas en 4.480 s y terminó con código 0. El preflight confirmó `EXTERNAL_WRITES_ENABLED=false`, ausencia de credenciales/configuración real y cero intentos de red; el guard de procesos hijos quedó activo.

Estas pruebas no son E2E de React ni de navegador, no prueban escrituras en servicios remotos y no afirman que un mock de Playwright cubra el backend. El tramo React/backend E2E sigue pendiente para T4/T6. No se modificó la colección, Monitoring, Quality, Verification, Report ni otro tramo del pipeline.