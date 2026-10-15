# Fase 2: revisión y ejecución gobernada de Trello

## Flujo

Las propuestas que genera `ProjectOrchestrator` reciben una revisión técnica automática y una previsualización dry-run. El dictamen verifica evidencia, captura vigente y elegibilidad del cambio; el dry-run únicamente compara el valor observado con el propuesto. Ambos quedan en `proposal_technical_reviews` (append-only). No crean una decisión humana, no autorizan ejecución y no modifican Trello.

El router `agente_gestion.api.proposal_execution.router` expone:

```text
POST /api/v1/projects/{project_id}/proposals/{proposal_id}/execute
```

El servidor anfitrión debe registrar este router. La operación requiere sesión, alcance sobre el proyecto, capacidad `governance:proposal_review`, origen permitido, CSRF y una cabecera `Idempotency-Key`. El actor procede de la sesión verificada; `actor_id`, grants y tokens en el cuerpo se rechazan.

Cuerpo admitido:

```json
{
  "decision": "approve_and_execute | modify_and_execute | reject",
  "modified_due_date": null,
  "modified_priority": null,
  "modified_assignee_ids": null,
  "reason": "Motivo humano"
}
```

Todas las decisiones requieren un motivo humano no vacío. Para `modify_and_execute` también se requiere exactamente un campo modificado, correspondiente al campo de la propuesta. La propuesta original permanece intacta; el cambio se guarda como una propuesta derivada inmutable y vuelve a recibir revisión técnica y dry-run vinculados a su propio hash y captura. `reject` registra la decisión humana sin llamar al proveedor.

La confirmación de una persona se registra como aprobación real solo cuando la persona elige `approve_and_execute` o `modify_and_execute`. Un rechazo técnico impide mutar el proveedor. La llamada de ejecución reutiliza `execute_proposal_action`: lectura previa, comparación de concurrencia, mutación y lectura de verificación posterior. Los intentos se guardan en `proposal_executions` y `security_audit_log`.

## Modo seguro y configuración

Las escrituras reales requieren simultáneamente un proyecto `real`, `EXTERNAL_WRITES_ENABLED=true`, `TRELLO_API_KEY` y `TRELLO_TOKEN`. En proyectos fixture o cuando las escrituras no están habilitadas se usa el adaptador en memoria; la respuesta informa `execution_mode: "sandbox"` y `execution.status: "SIMULATED"`. Un fallo de Trello en modo real queda como fallo/resultado incierto y nunca se presenta como simulación exitosa.

El adaptador usa el identificador configurado del proyecto como `board_id` y valida `idBoard` durante la lectura previa. El proyecto debe corresponder al tablero Trello esperado; no se hace descubrimiento remoto de tableros.

`TRELLO_PRIORITY_LABELS` contiene un mapa JSON por proyecto con las tres prioridades, por ejemplo:

```json
{"proyecto-demo":{"alta":"id-etiqueta-alta","media":"id-etiqueta-media","baja":"id-etiqueta-baja"}}
```

También se aceptan `high`, `medium` y `low` como nombres de dominio. Para cambiar prioridad, el mapa debe estar completo, válido y sin IDs repetidos. El adaptador reemplaza solo la etiqueta de prioridad configurada, conserva los demás `idLabels` y verifica el resultado después del `PUT`. Si falta el mapa, la solicitud se rechaza antes de una mutación del proveedor.

## Idempotencia y recuperación

Las claves se vinculan de forma durable a proyecto, propuesta, actor, sesión y cuerpo. Repetir exactamente la solicitud devuelve el recibo guardado; reutilizar la clave con otra solicitud produce conflicto. Una reserva sin resultado final se considera incierta: no se vuelve a intentar una escritura externa automáticamente. Una propuesta admite un único intento; para volver a proponer un cambio se debe generar una propuesta nueva.

Las pruebas de integración usan `unittest` y transporte HTTP simulado; no requieren credenciales reales ni realizan solicitudes a Trello.
