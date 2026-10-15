# Notificaciones ejecutivas por correo

El backend conserva los recibos inmutables de `notification_outbox` en SQLite y los artefactos locales bajo `.runtime/outbox/`. El adaptador mantiene el comportamiento existente de los canales `file`, `sqlite`, `console` y `all`; los envíos de correo se identifican mediante una clave durable, no solamente por la ventana semanal.

## Configuración

La configuración del servidor puede recibir estas variables de entorno. El código no carga archivos `.env`; los consumidores de CLI deben optar explícitamente por `SMTPConfig.from_env()` o `WeeklySchedulerService.from_env(...)`.

| Variable | Uso | Valor predeterminado |
| --- | --- | --- |
| `MANAGER_EMAIL` | Destinatario predeterminado del gestor | Sin configurar |
| `SMTP_HOST` | Host del relay SMTP | Sin configurar |
| `SMTP_PORT` | Puerto SMTP | `587` |
| `SMTP_USER` | Usuario remitente/autenticación SMTP | Sin configurar |
| `SMTP_PASSWORD` | Contraseña o contraseña de aplicación | Sin configurar |
| `SMTP_USE_TLS` | Activar STARTTLS | `true` |

Para Gmail, configura el host `smtp.gmail.com`, el puerto `587`, STARTTLS y una contraseña de aplicación autorizada para la cuenta remitente. No uses la contraseña normal de la cuenta ni guardes credenciales en Git. El envío real solo se intenta cuando host, usuario y contraseña están configurados; de otro modo se conserva un HTML local con `delivery_channel=local_outbox_simulated_email`. La aceptación SMTP se registra como `smtp_email_sent`; significa aceptación del relay, no confirmación de entrega al buzón. Los fallos quedan como `FAILED`/`smtp_email_failed` y nunca se convierten en una simulación exitosa.

## API protegida

El router exportado por `agente_gestion.api.email_notifications` agrega:

- `POST /api/v1/projects/{project_id}/notifications/email`
- `GET /api/v1/projects/{project_id}/notifications/email/{delivery_id}/preview`

El servidor principal debe registrar el router. El POST requiere sesión local, alcance al proyecto, capacidad `governance:proposal_review`, `Origin` permitido, `X-CSRF-Token` válido e `Idempotency-Key`. El cuerpo permite `recipient_email` y `outbox_delivery_id`; sin recibo se compone el reporte con la última captura, alertas reales, propuestas pendientes y sugerencias persistidas. Un recibo indicado se reenvía solo si pertenece al proyecto solicitado.

La vista previa requiere una sesión con alcance al mismo proyecto. Solo lee el archivo derivado de un `delivery_id` validado y enlazado a su recibo; no acepta rutas de archivo. La respuesta aplica `nosniff`, CSP y `no-store`.

Una repetición de la misma clave y solicitud devuelve el recibo durable; reutilizarla con otro destinatario/recibo responde `409`. Una clave nueva expresa un envío manual nuevo incluso dentro de la misma ventana semanal. Si el proceso cae durante SMTP, el intento queda ambiguo y no se reenvía automáticamente; para intentar otro envío se requiere una nueva clave explícita.

## Integración de la corrida

Cuando una corrida termine y ya se hayan persistido sus datos, la aplicación orquestadora puede invocar exactamente este helper con una clave estable derivada de la corrida:

```python
from agente_gestion.adapters.outbox.smtp import SMTPConfig
from agente_gestion.application.manager_notifications import ManagerNotificationService

notifications = ManagerNotificationService(storage, smtp_config=SMTPConfig.from_env())
receipt = notifications.send_project_email(
    project_id,
    recipient_email=manager_email,
    idempotency_key=f"run:{run_id}",
)
```

`WeeklySchedulerService.from_env(storage)` es la opción explícita para habilitar la configuración de SMTP desde entorno en una ejecución CLI/scheduler. Si la configuración SMTP no incluye `MANAGER_EMAIL`, se conserva el destinatario legado `gestor_proyecto` y el ciclo semanal no añade el envío de correo.

## Verificación local

Las pruebas usan un transporte SMTP falso; no requieren credenciales, instalaciones, conexión a Internet ni acciones contra proveedores. Para probar el modo sin SMTP, omite las variables SMTP y revisa el HTML protegido desde el endpoint de vista previa.
