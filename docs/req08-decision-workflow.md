# REQ-08: evidencia del flujo de decisiones T2

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

## Verificación aislada

El conjunto enfocado ejecutó 53 pruebas en 4.480 s y terminó con código 0. El preflight confirmó `EXTERNAL_WRITES_ENABLED=false`, ausencia de credenciales/configuración real y cero intentos de red; el guard de procesos hijos quedó activo.

Estas pruebas no son E2E de React ni de navegador, no prueban escrituras en servicios remotos y no afirman que un mock de Playwright cubra el backend. El tramo React/backend E2E sigue pendiente para T4/T6. No se modificó la colección, Monitoring, Quality, Verification, Report ni otro tramo del pipeline.