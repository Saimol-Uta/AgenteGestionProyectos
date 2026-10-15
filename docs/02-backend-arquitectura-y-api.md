# Backend: arquitectura, seguridad y API REST

La API de FastAPI presenta un contrato versionado bajo `/api/v1`. Lee el estado local, prepara análisis y registra decisiones protegidas. Las rutas de lectura no conceden autoridad; las mutaciones verifican sesión, origen, CSRF, alcance del proyecto, capability e idempotencia según la operación.

## Capas y persistencia

- `api/`: routers, esquemas Pydantic, sesión, configuración, preflight y errores.
- `application/`: casos de uso, planificación de análisis, gobernanza, tareas, notificaciones y ciclo semanal.
- `domain/`: estados, propuestas, métricas, políticas, evidencia y reglas de fechas.
- `ports/`: interfaces para fuentes, acciones, proveedores y almacenamiento.
- `adapters/`: Trello, GitHub, GitHub Actions, Gemini, Groq, SQLite, SMTP y sandbox local.
- SQLite conserva capturas, ejecuciones, informes, propuestas, decisiones, recibos y eventos de auditoría. Los artefactos permanecen bajo los directorios configurados del servidor.

La API de análisis utiliza una cola local acotada con un worker. La preparación no crea una ejecución ni prueba conectividad remota; confirmar vuelve a verificar la huella de configuración y crea un trabajo idempotente.

## Configuración, defaults y modos

La precedencia de configuración es: argumentos/overrides → variables del proceso → `.env` local → YAML → defaults. API y CLI combinan YAML base (`config/config.yaml`), proyecto predeterminado (`config/hormigestion.yaml`) y configuración explícita (`AGENTE_CONFIG_PATH` o `--config`). Si la configuración explícita pertenece a otro proyecto, no hereda los bindings ni el SHA del proyecto predeterminado. Las claves `GITHUB_REPO`/`GITHUB_REPOSITORY` y `GITHUB_BRANCH`/`GITHUB_REF` son alias y se validan para evitar contradicciones. `TRELLO_BOARD_ID` o `board_id` en YAML asocia el tablero al proyecto `HORMIGESTION`; también existe el mapa explícito `TRELLO_BOARD_IDS`.

| Control / componente | Valor predeterminado del producto | Comportamiento |
|---|---|---|
| Monitoring / Verifier / Report | Gemini | Si falta la clave de un componente, se usa el fallback local determinista y se registra el motivo. |
| Quality | Groq | Si falta la clave, se usa un fallback local acotado; el resultado conserva el estado de evidencia real. |
| `ALLOW_REMOTE_AI` | `true` | Habilita el uso remoto en servidor; cada transferencia de análisis requiere consentimiento explícito. `false` explícito se respeta. |
| `EXTERNAL_WRITES_ENABLED` | `true` | Participa en la autorización de escrituras; no basta para habilitar una mutación live. `false` explícito se respeta. |
| Capacidades `H5_*_ENABLED` | `true` | El servidor puede deshabilitar explícitamente análisis, gobernanza, decisión de informes o ciclo semanal. |

`AnalysisPreparation` informa `ready`/`eligible`, comprobaciones, proveedores, estado de credenciales, `execution_mode` previsto y `fallback_reasons`. La ausencia de una clave remota opcional no deshabilita por sí sola la acción cuando el fallback local puede cubrir el componente. `AnalysisJob.execution_mode` identifica el modo realmente usado (`live`, `sandbox` o `mixed`); cada recibo de acción identifica el modo y resultado de esa operación.

Antes de una escritura externa, el modo live exige proyecto real, recurso vinculado, credenciales, permiso efectivo del proveedor y aprobación humana. Si una condición falta en preflight, se elige sandbox y se informa; no se intenta la escritura. Si ya se intentó una operación live y falla, se conserva el fallo/estado incierto y no se rebautiza como éxito sandbox. La disponibilidad de claves no se presenta como prueba de conectividad.

## Seguridad y consentimiento

- Login humano mediante correo y contraseña; sesión respaldada por cookie `HttpOnly`, `SameSite=Strict` y CSRF del servidor.
- Los endpoints de mutación validan el origen HTTP exacto, CSRF y una clave de idempotencia cuando corresponda.
- El alcance de proyecto se deriva de configuración/sesión. El servidor fija actor y permisos; el cliente no los puede elevar.
- El consentimiento del gestor para transferir datos a fuentes/proveedores remotos es separado del permiso `ALLOW_REMOTE_AI` del servidor.
- `EXTERNAL_WRITES_ENABLED` no reemplaza autenticación, permiso del proveedor, alcance, consentimiento aplicable ni decisión humana.
- Secretos se mantienen en backend y no se incluyen en esquemas de respuesta, logs de uso ni artefactos de análisis.
- El destinatario inicial del correo es el `email` de la sesión verificada `PROJECT_MANAGER`; `MANAGER_EMAIL` es el fallback del servidor.
- `smtp_email_sent` indica aceptación del relay. Outbox local y `smtp_email_failed` son resultados distintos; ninguno se presenta como correo recibido en bandeja.

## Convenciones del contrato

- Todas las rutas listadas son relativas al prefijo `/api/v1`.
- Las listas paginadas usan `{items, total, page, page_size}`; `page_size` máximo 100.
- Los campos no observados son `null`, `unknown` o una disponibilidad explícita, no cero sintético.
- Los errores usan `{error: {code, message}}`; credenciales de login inválidas añaden RFC 7807 (`application/problem+json`).
- Los análisis y mutaciones no se inician con GET.

## Catálogo completo de endpoints

### Sistema, sesión y proyectos

| Método | Ruta | Uso | Acceso |
|---|---|---|---|
| `GET` | `/system/health` | Estado y versión de servicio. | Público |
| `GET` | `/auth/session` | Estado de sesión, correo, rol, CSRF y capabilities. | Público / sesión actual |
| `POST` | `/auth/login` | Iniciar sesión del gestor; emite cookie de sesión. | Público; requiere origen permitido |
| `POST` | `/auth/logout` | Revocar sesión y limpiar cookie. | Sesión + origen + CSRF |
| `GET` | `/projects` | Listar proyectos paginados y su último estado conocido. | Público |
| `GET` | `/projects/{project_id}` | Consultar metadatos de un proyecto. | Público |
| `GET` | `/projects/{project_id}/summary` | Resumen de tareas y señales; acepta `capture_id`. | Público |
| `GET` | `/projects/{project_id}/executive-summary` | Resumen ejecutivo con series persistidas de capturas/corridas. | Público |
| `GET` | `/projects/{project_id}/configuration` | Configuración segura y capabilities visibles; no expone secretos. | Sesión + `configuration:read` + alcance |

### Tareas, capturas e historial

| Método | Ruta | Uso | Acceso |
|---|---|---|---|
| `GET` | `/projects/{project_id}/tasks` | Lista paginada; filtros `capture_id`, `q`, `state`, `assignee_id`, `list_name`, `critical_only`, `overdue_only`, `due_soon_only`, `sort`, `direction`. | Público |
| `GET` | `/projects/{project_id}/tasks/{task_id}` | Detalle de tarea, evidencia e historial disponible; acepta `capture_id`. | Público |
| `GET` | `/projects/{project_id}/history` | Capturas paginadas con fecha y resumen. | Público |
| `GET` | `/projects/{project_id}/compare` | Compara capturas mediante `before` y `after`. | Público |

### Corridas, etapas, hallazgos y evidencia

| Método | Ruta | Uso | Acceso |
|---|---|---|---|
| `GET` | `/runs` | Lista paginada por `project_id`. | Público |
| `GET` | `/runs/{run_id}` | Detalle persistido de una corrida. | Público |
| `GET` | `/runs/{run_id}/stages` | Estados, duración y proveedores registrados por etapa. | Público |
| `GET` | `/runs/{run_id}/findings` | Lista paginada de hallazgos candidatos y verificación registrada. | Público |
| `GET` | `/runs/{run_id}/findings/{finding_id}` | Detalle de hallazgo, evidencia y excerpt disponible. | Público |
| `GET` | `/runs/{run_id}/evidence` | Evidencia paginada en proyección segura. | Público |
| `POST` | `/runs/{run_id}/findings/{finding_id}/decision` | Aprobar, rechazar o pedir cambios sobre un hallazgo; puede crear rama sandbox/live. | Sesión + `quality:decide` + origen + CSRF + idempotencia |

### Análisis asíncrono

| Método | Ruta | Uso | Acceso |
|---|---|---|---|
| `POST` | `/analysis/prepare` | Preparar con `project_id` explícito en el cuerpo y los mismos controles de la ruta con proyecto. | Sesión + `analysis:prepare` + origen + CSRF |
| `POST` | `/analysis/confirm` | Confirmar preparación ligada al proyecto/sesión; exige `project_id` en el cuerpo. | Sesión + `analysis:confirm` + origen + CSRF + idempotencia |
| `POST` | `/projects/{project_id}/analysis/prepare` | Revisar configuración, elegibilidad, fuentes, modo y fallbacks sin ejecutar. | Sesión + `analysis:prepare` + origen + CSRF |
| `POST` | `/projects/{project_id}/analysis/confirm` | Confirmar el análisis y crear un trabajo con `Idempotency-Key`; el cuerpo lleva consentimiento remoto separado. | Sesión + `analysis:confirm` + origen + CSRF + idempotencia |
| `GET` | `/analysis/jobs` | Listar trabajos por `project_id`, paginados. | Sesión + alcance + `analysis:confirm` |
| `GET` | `/analysis/jobs/{job_id}` | Consultar estado del trabajo y corrida asociada. | Sesión + alcance del proyecto |

La preparación enlaza `preparation_id`, `configuration_fingerprint`, proyecto y sesión. La confirmación rechaza expiración, cambios de configuración, falta de consentimiento remoto cuando sea necesario o una capacidad deshabilitada. La respuesta de trabajo informa `queued`, `running`, `completed`, `failed` o `interrupted`; un estado completado no demuestra que cada componente usara un proveedor remoto.

### Calidad, propuestas y decisiones humanas

| Método | Ruta | Uso | Acceso |
|---|---|---|---|
| `GET` | `/projects/{project_id}/quality` | Estado de revisión, conteos de hallazgos y CI conocido. | Público |
| `GET` | `/projects/{project_id}/policies` | Estados `pass`, `fail` o `unknown` de políticas persistidas. | Público |
| `GET` | `/projects/{project_id}/proposals` | Lista paginada de propuestas y estado técnico/ejecución. | Público |
| `GET` | `/projects/{project_id}/proposals/{proposal_id}` | Detalle de propuesta y decisiones registradas. | Público |
| `POST` | `/projects/{project_id}/proposals/{proposal_id}/execute` | Decisión humana y ejecución protegida de propuesta de tarea. | Sesión + `governance:proposal_execute` + origen + CSRF + idempotencia |
| `GET` | `/projects/{project_id}/reports` | Lista paginada de informes; filtro `decision` opcional. | Público |
| `GET` | `/projects/{project_id}/reports/{report_id}` | Informe, recomendación y decisión registrada. | Público |
| `GET` | `/projects/{project_id}/reports/{report_id}/download` | Descargar `markdown` o `json`. | Público |
| `POST` | `/projects/{project_id}/reports/{report_id}/decision` | Registrar aprobación, rechazo, solicitud de cambios o aplazamiento humano. | Sesión + `reports:decide` + origen + CSRF + idempotencia |

La recomendación de `ReportAgent` es consultiva. Una decisión de informe no concede por sí misma permiso para modificar Trello o GitHub.

### Programación y outbox

| Método | Ruta | Uso | Acceso |
|---|---|---|---|
| `GET` | `/projects/{project_id}/schedule` | Ver programación configurada y recibo conocido. | Público |
| `GET` | `/projects/{project_id}/schedule/outbox` | Listar recibos paginados. | Público |
| `GET` | `/projects/{project_id}/schedule/outbox/{delivery_id}` | Consultar recibo y mensaje permitido. | Público |
| `GET` | `/projects/{project_id}/schedule/outbox/{delivery_id}/download` | Descargar `markdown` o `json` del recibo. | Público |
| `POST` | `/projects/{project_id}/schedule/manual-cycle/prepare` | Preflight del ciclo manual local. | Sesión + `weekly_cycle:prepare` + origen + CSRF |
| `POST` | `/projects/{project_id}/schedule/manual-cycle/confirm` | Confirmar ciclo; recibe `Idempotency-Key`. | Sesión + `weekly_cycle:confirm` + origen + CSRF + idempotencia |
| `GET` | `/projects/{project_id}/schedule/manual-cycle/jobs` | Listar trabajos del ciclo manual. | Sesión + `weekly_cycle:confirm` + alcance |
| `GET` | `/projects/{project_id}/schedule/manual-cycle/jobs/{job_id}` | Consultar un trabajo de ciclo manual. | Sesión + `weekly_cycle:confirm` + alcance |

El ciclo manual de la web es un trabajo local con resultado y recibo; no debe describirse como scheduler desatendido. Un recibo de outbox identifica su canal y estado reales.

### Auditoría y notificaciones

| Método | Ruta | Uso | Acceso |
|---|---|---|---|
| `GET` | `/projects/{project_id}/audit` | Consultar eventos filtrados por tipo y fecha. | Sesión + `audit:read` + alcance |
| `GET` | `/projects/{project_id}/audit/{event_id}` | Detalle permitido de evento auditado. | Sesión + `audit:read` + alcance |
| `POST` | `/projects/{project_id}/notifications/email` | Enviar informe al destinatario confirmado o reenviar recibo. | Sesión + `notifications:email_send` + origen + CSRF + idempotencia |
| `GET` | `/projects/{project_id}/notifications/email/{delivery_id}/preview` | Ver HTML vinculado a un recibo del mismo proyecto. | Sesión + `audit:read` + alcance |

## Contratos de mutación

- **Análisis:** `POST .../prepare` recibe `request_analysis: true`. `POST .../confirm` recibe preparación, huella, confirmación y `remote_consent` booleano; la clave de idempotencia va en el header.
- **Propuestas:** `POST .../execute` exige decisión, motivo y, para modificación, únicamente el campo asociado a la propuesta. La respuesta conserva ejecución, modo, verificación y recibo.
- **Hallazgos:** `POST .../decision` recibe `approve`, `reject` o `request_changes`; para aprobar, el servidor exige fuente verificable y valida el archivo completo afectado.
- **Correo:** el destinatario puede omitirse para usar el correo de sesión; el envío se confirma por acción del usuario. La API retorna `local_outbox_simulated_email`, `smtp_email_sent` o `smtp_email_failed` según el resultado real.
- **Idempotencia:** una repetición idéntica devuelve el recibo existente; una clave reutilizada con otro cuerpo se rechaza. Un resultado live incierto no se convierte en una nueva acción automática.

## Fuentes de implementación

- [Composición y routers montados](../src/agente_gestion/api/app.py)
- [Rutas principales](../src/agente_gestion/api/routes.py)
- [Esquemas públicos](../src/agente_gestion/api/schemas.py)
- [Preflight y trabajos de análisis](../src/agente_gestion/api/analysis_jobs.py)
- [Configuración API](../src/agente_gestion/api/settings.py)
- [Seguridad de sesión](../src/agente_gestion/api/security.py)
- [Acciones de código](../src/agente_gestion/api/finding_actions.py) · [propuestas](../src/agente_gestion/api/proposal_execution.py) · [correo](../src/agente_gestion/api/email_notifications.py)

La estructura completa de capas está en [ARCHITECTURA.md](../ARCHITECTURA.md); el mapa de responsabilidades académicas está en [docs03](03-motor-de-agentes-y-requisitos.md).
