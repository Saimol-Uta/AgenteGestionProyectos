# Índice del contrato REST

El catálogo base de rutas, accesos, preparación de análisis, trabajos, propuestas, informes, programación, correo y auditoría está en [docs/02-backend-arquitectura-y-api.md](02-backend-arquitectura-y-api.md). Esta página registra las extensiones REST MultiRepo T7/T8; el detalle de compatibilidad y los ejemplos están en [docs/multirepo.md](multirepo.md).

## Historial y filtros MultiRepo (T7)

| Método y ruta | Acceso | Contrato |
|---|---|---|
| `GET /api/v1/projects/{project_id}/repository-results` | Sesión de operador, capability `audit:read` y alcance del proyecto | Lista la colección configurada y el historial persistido paginado por proyecto. |
| `GET /api/v1/runs/{run_id}/findings` | Conserva el acceso público de lectura existente | Conserva la página de hallazgos y admite filtros opcionales de repositorio, commit y CI; devuelve IDs de hallazgo heredados o con namespace. |
| `GET /api/v1/runs/{run_id}/findings/{finding_id}` | Conserva el acceso público de lectura existente | Devuelve el detalle del hallazgo con el mismo identificador heredado o con namespace que la lista. |
| `POST /api/v1/runs/{run_id}/findings/{finding_id}/decision` | Sesión de operador, `quality:decide`, alcance del proyecto, origen permitido, CSRF y `Idempotency-Key` | Registra aprobar, rechazar o pedir cambios; una aprobación exige repositorio y rama configurados que coincidan con el origen persistido del hallazgo. |

Ambas rutas aceptan filtros AND: `repository_id`; `repository_owner` junto con `repository_name`; `commit_sha` hexadecimal completo de 40 o 64 caracteres; `ci_run_id`; e `attempt` positivo. La paginación usa `page` (predeterminado 1, mínimo 1) y `page_size` (predeterminado 25, rango 1–100). El intento es el intento real de `CIRunResult` y los filtros de CI/commit deben coincidir dentro del mismo registro. Omitir uno de los dos campos owner/name o enviar una combinación de filtros inválida produce HTTP 422; el error de forma de filtro del historial es `filtros_multirepo_invalidos`.

El historial usa la capacidad T6 en modo de solo lectura. Si esa capacidad no está disponible, responde HTTP 200 con `availability: "unknown"`, un `message` explicativo y una página vacía; un `GET` no crea ni migra la base. La ruta requiere sesión (401 sin sesión); un proyecto fuera del alcance de la sesión devuelve 403 y un proyecto no configurado devuelve 404. La lista de hallazgos mantiene su autorización pública anterior. Procedencia histórica desconocida permanece desconocida y métricas de informe no certificadas permanecen `null`.

La preparación existente (`POST /api/v1/projects/{project_id}/analysis/prepare`) informa el preflight por repositorio sin consultar GitHub: identidad, rama, SHA configurado, workflow, presencia de credencial y estados iniciales pendiente/desconocido. El fingerprint cubre la colección ordenada completa y sus selectores; cambiar un repositorio hermano invalida una preparación anterior (`409 configuracion_cambiada`). Se conservan sesión, expiración, autorización, consentimiento remoto, idempotencia y auditoría existentes. No se publican credenciales ni rutas privadas.

Las rutas de lista, detalle y decisión aceptan IDs de hallazgo heredados o con namespace (`repository-id:finding-id`); esta gramática específica no relaja la validación de proyecto, corrida, ruta ni autorización. La decisión no acepta repositorio ni SHA en el cuerpo. El servidor obtiene proyecto, corrida, hallazgo, repositorio, commit completo, archivo, hash de fuente y excerpt desde los registros persistidos. Una aprobación requiere origen inequívoco, configuración actual coincidente, archivo completo con hash íntegro y una única coincidencia exacta del excerpt; conflictos, origen desconocido, sintaxis no soportada o una rama base que ya no apunte al commit fijado fallan sin redirección a otro repositorio. Rechazar o pedir cambios no crea una rama. Respuestas y detalle de auditoría incluyen el repositorio y SHA cuando el origen histórico está disponible; los datos antiguos desconocidos no se reconstruyen a partir de la configuración actual.

Para las reglas de compatibilidad con almacenamiento histórico, estados, métricas, evidencia y acciones, consulta [docs/multirepo.md](multirepo.md#api-history-filters-and-preparation-t7) y [el contrato de decisiones T8](multirepo.md#finding-decisions-and-origin-bound-code-actions-t8).

Para la sesión y experiencia del Gestor, consulta la [guía web](01-frontend-guia-y-estado.md). Para la verificación actual, consulta [INFORME_PRUEBAS.md](../INFORME_PRUEBAS.md).
