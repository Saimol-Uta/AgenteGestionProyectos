# Capas y contratos del sistema

Este documento compara las responsabilidades, contratos y garantías de los componentes del producto.

## Comparación de capas

| Capa | Decide o calcula | Recibe | Produce | No debe hacer |
|---|---|---|---|---|
| Web React | Presentación, filtros, navegación y confirmación explícita | Respuestas REST y sesión | Solicitudes tipadas, decisiones del gestor y estados visibles | Calcular métricas oficiales, inventar evidencias o persistir autorizaciones por cuenta propia. |
| API FastAPI | Validación de DTOs, sesión, proyecto, origen, CSRF y capacidades | Cookies, headers y cuerpo validado | Respuestas seguras, preflight, trabajos y recibos | Confiar en roles/actores enviados por el cliente o filtrar secretos. |
| Servicios de aplicación | Orden de operaciones, precondiciones e idempotencia | Contratos de dominio y puertos | Resultado del caso de uso y auditoría | Convertir un fallo live en sandbox exitoso. |
| Dominio | Estados, métricas, ventanas temporales y reglas | Capturas normalizadas y políticas | Hechos reproducibles y estados `pass/fail/unknown` | Delegar aritmética, permisos o autoridad a un modelo. |
| Agentes | Interpretación y redacción acotadas por rol | Métricas y evidencia permitida | Borradores, hallazgos candidatos, verificaciones y síntesis | Cambiar datos objetivos o aprobar acciones. |
| Puertos y adaptadores | Conectar contratos con transporte/almacenamiento concreto | Parámetros ya autorizados | Lecturas, persistencia, fallback y resultado externo/local | Expandir alcance, reutilizar credenciales en el navegador o ocultar resultado. |

## Comparación de contratos operativos

| Contrato | Fuente de verdad | Decisión protegida | Evidencia de resultado |
|---|---|---|---|
| Métricas | Dominio y captura persistida | No aplica | Valores calculados con desconocidos conservados. |
| Interpretación | Agente y contexto validado | Revisión humana del contenido | Salida estructurada y estado de verificación. |
| Elegibilidad de análisis | Preflight del servidor | Consentimiento remoto por separado al confirmar | `ready`/`eligible`, comprobaciones y `fallback_reasons`. |
| Selección de proveedor | Configuración efectiva por componente | `ALLOW_REMOTE_AI` más consentimiento del gestor cuando se transfiere información | Proveedor/transporte usados y `execution_mode`. |
| Decisión sobre propuesta | Gestor autenticado | Sesión, proyecto, origen, CSRF, capability, motivo e idempotencia | Decisión, verificación, modo y recibo persistidos. |
| Escritura externa | Adaptador de acción y permiso real del proveedor | Proyecto real, credenciales, `EXTERNAL_WRITES_ENABLED` y aprobación humana | Lectura previa/posterior y resultado live, o sandbox previo declarado. |
| Notificación | Correo de sesión y servicio de outbox | Confirmación del gestor y configuración SMTP | Recibo local, aceptación del relay o fallo SMTP. |
| Auditoría | Registros persistidos del servidor | Capability `audit:read` | Actor verificado cuando lo acredita la sesión; los demás permanecen desconocidos. |

## Modos de ejecución

Los modos nombran lo ocurrido, no lo que se esperaba que ocurriera:

| Modo | Interpretación |
|---|---|
| `live` | El componente/acción utilizó servicio externo con permisos y el resultado remoto quedó confirmado por el contrato. |
| `mixed` | Algunos componentes usaron servicio externo y otros fallback local o sandbox; se enumeran causas por componente. |
| `sandbox` | La operación se mantuvo local y no afirmó una mutación remota. |

Cuando una credencial o permiso falta antes de iniciar una escritura, el servidor elige sandbox y lo anuncia. Si una operación live empezó y devolvió fallo o estado incierto, se conserva ese resultado. No existe equivalencia entre `sandbox` y "live completado".

Para análisis sin una clave opcional, el fallback determinista permite conservar un flujo accionable, pero sus límites deben ser visibles. La verificación de un hallazgo no se sustituye por un veredicto favorable inventado: si no puede corroborarse, queda pendiente o insuficiente.

## Configuración y seguridad entre capas

La configuración efectiva combina overrides explícitos, entorno del proceso, `.env`, YAML y defaults. Las rutas YAML de API y CLI se pueden seleccionar, y alias compatibles de repositorio/rama se concilian antes de construir adaptadores. Ningún valor secreto llega a la UI; la disponibilidad de una credencial no se interpreta como conectividad.

`ALLOW_REMOTE_AI` y `EXTERNAL_WRITES_ENABLED` tienen default `true` en el contrato del producto; una configuración explícita `false` prevalece. El permiso remoto del servidor no sustituye el consentimiento del gestor. El permiso de escritura no sustituye modo de proyecto, credenciales, permiso del proveedor, verificación del objetivo ni autorización humana. Estos controles protegen operaciones diferentes.

El email de la sesión verificada del gestor es el destinatario inicial. `MANAGER_EMAIL` queda como alternativa del servidor cuando no se cuenta con una dirección de sesión; el usuario puede elegir una copia al enviar. El recibo distingue outbox local, relay aceptado y fallo, sin afirmar llegada al buzón.

## Comparación de límites de almacenamiento

| Datos | Persistencia | Límite de exposición |
|---|---|---|
| Capturas, tareas, hallazgos, decisiones y auditoría | SQLite | API devuelve proyecciones tipadas y acotadas al proyecto. |
| Trabajos web | Ledger SQLite local separado | Estados y códigos seguros; no se exponen excepciones internas. |
| Informes y artefactos | Archivos locales vinculados a registros | La descarga valida relación y ruta; la UI no envía rutas arbitrarias. |
| Entregas de correo | Outbox local/SQLite y, si está habilitado, SMTP | Receipt identifica el canal real, no una entrega no observada. |

## Referencias de implementación

- [Arquitectura y límites generales](../ARCHITECTURA.md)
- [API, rutas y contratos](../src/agente_gestion/api/app.py) · [schemas](../src/agente_gestion/api/schemas.py) · [trabajos](../src/agente_gestion/api/analysis_jobs.py)
- [Configuración compartida](../src/agente_gestion/application/config.py) · [configuración API](../src/agente_gestion/api/settings.py)
- [Métricas y dominio](../src/agente_gestion/domain/tasks.py) · [orquestador](../src/agente_gestion/application/orchestrator.py)
- [Matriz REQ-01 a REQ-11](03-motor-de-agentes-y-requisitos.md) · [verificaciones](../INFORME_PRUEBAS.md)
