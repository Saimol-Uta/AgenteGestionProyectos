# Fase 2: gobernanza del frontend

La interfaz conserva el diseño, la navegación, las consultas y las funciones no relacionadas de Fase 1. Las decisiones locales de demostración y la secuencia manual de aprobación/revisión/simulación fueron retiradas: la API es la única autoridad para registrar una decisión o informar una ejecución.

## Contratos de acciones

| Acción | Endpoint | Capability | Contrato de interfaz |
|---|---|---|---|
| Decidir un hallazgo de código | `POST /api/v1/runs/{run_id}/findings/{finding_id}/decision` | `quality:decide` | Muestra `suggested_code` e `improvement_summary` del backend. `decision_status` es `pending`, `approved`, `rejected` o `changes_requested`. La respuesta determina rama, commit y modo; un rechazo de validación se presenta como error del backend. |
| Decidir una propuesta Trello | `POST /api/v1/projects/{project_id}/proposals/{proposal_id}/execute` | `governance:proposal_execute` | El Revisor técnico prepara automáticamente `technical_review`; el gestor envía `approve_and_execute`, `modify_and_execute` o `reject` junto con un motivo. Una modificación solo incluye el campo de la propuesta. El resultado y el modo proceden de la respuesta del backend. |
| Enviar correo al gestor | `POST /api/v1/projects/{project_id}/notifications/email` | `notifications:email_send` | Se confirma el destinatario. La UI presenta el canal y estado reales; `local_outbox_simulated_email` no acredita entrega SMTP. La vista HTML usa el enlace protegido del mismo proyecto. |

Las escrituras protegidas usan la sesión same-origin, `X-CSRF-Token` y `Idempotency-Key`. La API aplica los permisos y la vigencia del proyecto. Si falta la capability o la solicitud es rechazada, la interfaz mantiene visible el error real y no crea una aprobación, un rechazo, una rama ni una ejecución local. No hay fallback al recibir `404` en `/execute`.

La sugerencia de código solo se presenta cuando el backend entrega código y explicación. El navegador no fabrica ejemplos ni valida el archivo completo; la validación de fuente corresponde al servidor y cualquier rechazo se muestra sin sustituirlo por una aprobación local. Las decisiones de calidad usan `quality:decide`; las propuestas usan `governance:proposal_execute`.

## Refresco de datos

Tras una ejecución de propuesta, se invalidan las consultas de propuestas, detalle, tareas, resumen y auditoría. La decisión de calidad invalida el hallazgo y la lista asociada. Los recibos y registros de auditoría se consultan desde el backend; la UI no reemplaza ni reescribe su historial.

## Verificación frontend

Las pruebas Playwright interceptan la API localmente y no contactan GitHub, Trello, SMTP ni otros proveedores. Desde `frontend/`:

```sh
npm run typecheck
npm run build
npx playwright test e2e/phase2-actions.spec.ts
```

La verificación no requiere credenciales ni habilitar escrituras externas. Las instrucciones de configuración de proveedores y la demostración integrada pertenecen a la documentación del backend.
