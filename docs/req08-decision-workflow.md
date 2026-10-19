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

## T5: conteo del Dashboard

El Dashboard y la insignia de Propuestas muestran `pending_actionable_count`, el agregado de todo el proyecto que informa el API; no usan `total` ni cuentan los elementos de la página consultada. Un cero conocido se muestra como `0`. Si el agregado es nulo, falta o la colección no está disponible, la interfaz muestra `—` y explica que no pudo confirmar cuántas propuestas esperan la decisión del gestor.

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

## T4: proyección de decisiones en la UI

La interfaz separa revisión técnica, decisión humana, autorización y ejecución persistida. Los eventos `authorize_dry_run` y `approve_offline` por sí solos no prueban una ejecución terminal; la UI sigue los estados actuales del backend y bloquea ejecución si el estado o el historial requerido es desconocido. Quality no permite aprobar GitHub sin una revisión independiente del parche exacto; rechazo y solicitud de cambios siguen disponibles. Los payloads e idempotencia de las mutaciones no cambian.

## Verificación aislada

El runner `/tmp/req08-decision-workflow-t1-isolated/t4/run-spotcheck.sh` copia los 15 archivos de UI/pruebas acotados, verifica sus hashes contra el origen y usa salidas únicas. Las seis selecciones enfocadas de Playwright terminaron con **59 aprobadas, 0 fallidas y 0 omitidas**: propuestas (33), Quality (8), acciones de fase 2 (10), casos T4 de `web-platform` (4), caso de Quality en `multirepo` (1) y candidatos de Quality (3). `npm run typecheck` y `npm run build` pasaron. Vite informó que el `outDir` único está fuera de la raíz y no se vaciará; no fue un fallo.

Las pruebas usaron respuestas API mock, `EXTERNAL_WRITES_ENABLED=false`, sin credenciales, backend ni proveedores reales. El preloader Node se cargó en 68 procesos; el guard registró cero intentos externos/no autorizados. Esto verifica la proyección de la UI aislada, no una integración React/backend real ni la escritura en servicios remotos. No representa el cierre completo de REQ-08 ni T4/T6.

### Corrección independiente: `approved_offline`

La UI ahora permite la confirmación explícita `approve_and_execute` desde `approved_offline` únicamente si el historial protegido disponible contiene `approve_offline` vinculado al ID de esa propuesta. Se mantienen todas las comprobaciones existentes de elegibilidad técnica, actualidad, autorización, ejecución no iniciada, relaciones, decisiones terminales, historial desconocido y recibo; la aprobación offline no se presenta como ejecución. El payload y la idempotencia no cambian.

RED: la prueba pública `una aprobación offline conocida permite ejecutar solo tras una nueva confirmación del gestor`, contra el helper previo, terminó con 1 fallo (botón de ejecución ausente), exit 1. GREEN: la regresión positiva y el caso sin evento coincidente pasaron (2/2); la suite `manager-proposals` pasó (35/35). El runner fresco es `/tmp/req08-decision-workflow-t1-isolated/t4/offline-approval-correction/run-offline-approval-spotcheck.sh`. Logs RED: `runs/offline-approval-red-20261019T045522Z-2441433/results/`; GREEN: `runs/offline-approval-spotcheck-20261019T045608Z-2442812/results/`. La copia verificada incluyó 67 archivos de frontend, el guard Node cargó en 16 procesos y no hubo intentos de red inesperados. No se repitieron typecheck/build ni la suite amplia tras esta corrección.

La clasificación independiente de las seis suites completas reportó 100 aprobadas y 6 fallidas, con exactamente las mismas seis fallas en `basefeeffa0`: `multirepo` líneas 159, 186, 325, 382 y 403; `web-platform` caso `unknown-stage` línea 117. Se dejan intactas; los otros diez fallos exploratorios fueron resueltos según esa clasificación. El recheck de este único bloqueo no constituye un resultado full-green.

## T6: validación final observada

**El ciclo de decisión Trello en sandbox y el enlace contextual de Auditoría pasaron contra React, FastAPI y SQLite temporales reales, sin mocks HTTP.** La suite frontend general no está verde: conserva ocho fallos reproducidos antes de T1–T5. No se declara cierre global de REQ-08.

| Comprobación | Resultado observado |
|---|---|
| Backend completo | 1106 pruebas en 23.424 s: 1104 aprobadas, 2 omisiones preexistentes, 0 fallos/errores; exit 0 |
| Demo de bloqueo GitHub | RED antiguo 200→409; GREEN de demo y rechazo/solicitud de cambios: 2 pruebas; denegar no consume la clave ni modifica informe/finding/auditoría |
| Playwright default completo | 134 pruebas en 42.9 s: 126 aprobadas, 8 fallidas, 0 omitidas; exit 1 |
| Typecheck / build | Ambos exit 0 sobre la UI final |
| E2E real React/API | 1 aprobada en 9.3 s; test 5.8 s; exit 0 |
| Baseline anterior a T1–T5 | Las ocho aserciones fallan también en `d7d37a6`, en selecciones de seis y dos; ninguna se cambió ni omitió |

La clasificación de los 15 fallos y 30 errores backend originales, sus 45 IDs conservados y los ocho fallos frontend está en [la evidencia T6](req08-t6-validation.md). Los 44 problemas de montaje se resolvieron copiando los tres YAML y cuatro fixtures JSON rastreados sin alteración y creando un `.runtime/` vacío únicamente en scratch. La expectativa obsoleta de demo se actualizó a la política 409 aprobada; no se cambió producción para hacer pasar pruebas.

### Recorrido real comprobado

1. El servidor de prueba usa el orquestador y Monitoring reales con fuente y proveedor deterministas, proyecto sintético `P` y SQLite nueva.
2. El navegador inicia sesión mediante la API real, abre Dashboard con un pendiente y navega a Propuestas.
3. Revisa evidencia/condiciones, exige motivo y confirmación y envía exactamente un POST protegido con CSRF e idempotencia.
4. Recibe `sandbox/SIMULATED`, resultado verificado y `live_state_changed=false`.
5. Recarga: conserva el mismo recibo y una sola decisión; Dashboard pasa de 1 a 0.
6. El historial conserva actor de sesión, motivo, fecha y resultado. `Ver evento en Auditoría` usa exclusivamente el `audit_event_id` persistido, codificado en `/audit?event_id=…`.
7. El enlace abre el evento público protegido correcto. Un cliente anónimo recibe 401; la navegación no concede permisos.

El componente solo muestra el enlace cuando el evento tiene referencia; no inventa IDs, identidad externa ni éxito live. GitHub continúa bloqueado para aprobación sin revisión independiente del parche; Quality, Verification, recomendaciones, rechazo y solicitud de cambios permanecen disponibles.

### Aislamiento y reproducción local

Los runners y evidencia de esta sesión están en el directorio temporal expresamente autorizado, no son datos históricos ni forman parte de la base de la aplicación:

```bash
bash /tmp/req08-decision-workflow-t1-isolated/t6/renewed-backend/scripts/run-backend.sh final-full
bash /tmp/req08-decision-workflow-t1-isolated/t6/renewed-real/run-real-browser.sh real-green
bash /tmp/req08-decision-workflow-t1-isolated/t6/renewed-real/run-frontend-final.sh
```

Requieren que se conserven esos helpers temporales y las dependencias existentes; no instalan ni descargan nada. Los archivos de prueba permanentes son `tests/req08_browser_server.py` y `frontend/e2e-backend/`; se mantienen fuera de la suite mock default.

Cada runner usa `env -i`, `EXTERNAL_WRITES_ENABLED=false`, dotenv deshabilitado, sin credenciales reales y con HOME/TMP/cachés/DB/runs/outbox/jobs/branches aislados. Solo permite servidores propios en `127.0.0.1:4173/4174`, sin reutilización. Las guardas de Python, Node y navegador verifican integridad, herencia y cero intentos externos inesperados; se comprueban hashes de fuentes y liberación de puertos. Las aserciones originales de Playwright permanecen como prefijos exactos; los hooks de seguridad solo se añaden en scratch.

La excepción autorizada para el módulo no vendorizado permite exclusivamente el sondeo de capacidad `urllib3.util.connection._has_ipv6`: IPv6 stream `bind(::1, 0)`, identificado por stack y registrado por separado. No permite escuchar, conectar ni otros llamadores/destinos/puertos. El preflight conservó 13 denegaciones Python y 5 Node deliberadas en logs separados. El GREEN real registró un solo sondeo bare y ninguna otra excepción.

### Evidencia y límites

- GREEN real: `renewed-real/runs/renewed-real-green-20261019T120046Z-2700543/results/`.
- Backend completo: `renewed-backend/runs/t6-backend-final-full-20261019T065915-1792393155766659624-2555151/results/`.
- Frontend completo y tipos/build: `renewed-real/runs/renewed-frontend-final-20261019T073040Z-2580553/results/`.
- Las rutas anteriores cuelgan de `/tmp/req08-decision-workflow-t1-isolated/t6/`.

El RED de producto llegó a la última aserción del enlace inexistente (Playwright exit 1); su wrapper exit 87 por evidencia de guardas incompleta no se presenta como corrida aceptada. Los errores previos de arranque, cookie y guardas se conservaron en los logs y en `odd/tasks/req08-decision-workflow.md`. El GREEN posterior validó esas guardas además del recorrido funcional.

Las dos omisiones backend son las preexistentes: smoke LLM remoto deshabilitado y snapshot histórico no copiado. No se agregó ningún skip ni se alteraron `.runtime/agente.db`, `./runs/` o HORMIGESTION. No se realizaron escrituras externas, push o merge.

**Pendiente de revisión humana:** los ocho fallos generales de Análisis/MultiRepo/encabezado son anteriores a T1–T5 y corregirlos queda fuera de T6. El flujo mínimo se demuestra en Trello sandbox; eso no acredita ejecución live, aprobación correctiva GitHub ni suite frontend global verde.
