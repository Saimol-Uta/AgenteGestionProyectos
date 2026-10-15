# Sugerencias de calidad y decisiones de rama

## Flujo

Cada hallazgo de calidad puede incluir `suggested_code` e `improvement_summary`. El agente Verificador conserva su dictamen independiente y, cuando recibe el archivo completo, comprueba que el reemplazo exacto sugerido mantenga la sintaxis del archivo. Los registros antiguos siguen siendo válidos: las columnas se agregan con migraciones `ALTER TABLE` solo cuando faltan y las consultas usan el JSON histórico del informe como respaldo.

El gestor inicia sesión local y envía una decisión explícita a:

```http
POST /api/v1/runs/{run_id}/findings/{finding_id}/decision
Origin: http://localhost:5173
X-CSRF-Token: <token de la sesión>
Idempotency-Key: <clave única de 8 a 128 caracteres>
Content-Type: application/json
```

Cuerpo de ejemplo:

```json
{
  "decision": "approve",
  "suggested_code_override": null,
  "target_branch": "quality/hallazgo-123",
  "reason": "Revisado por el gestor"
}
```

`decision` acepta `approve`, `reject` o `request_changes`. La respuesta incluye `decision_status`, `branch_name`, `branch_url`, `commit_sha_applied`, `suggested_code`, `improvement_summary` y `execution_mode` (`live` o `sandbox`). Rechazar o solicitar cambios registra la decisión sin crear rama. Rechazar el mismo identificador de idempotencia con otro cuerpo, aprobar de nuevo un hallazgo ya aprobado o concurrir con otra decisión devuelve conflicto. Los recibos de solicitudes y la auditoría se persisten; el ledger de decisiones es append-only. El endpoint no reescribe `report.json` ni el JSON inmutable del informe.

## Modo local seguro y GitHub

Por defecto se usa el sandbox local bajo `BRANCHES_DIR` (por defecto `.runtime/branches`). El resultado local usa una URL `sandbox://` y un SHA-256 determinista; no representa una rama remota. El parche requiere una coincidencia exacta y única del fragmento original, rechaza fragmentos obsoletos/ambiguos, conserva el contexto de archivo capturado y no añade código al final como fallback. Los destinos existentes con contenido distinto y las rutas simbólicas o inseguras se rechazan.

Las escrituras remotas solo se habilitan cuando concurren estas condiciones del servidor: `EXTERNAL_WRITES_ENABLED=true`, `GITHUB_TOKEN`, `GITHUB_REPOSITORY=owner/repository`, y un proyecto en modo `real`. `GITHUB_BASE_BRANCH` selecciona la rama base (por defecto `main`). El adaptador primero comprueba que GitHub reporte permiso `push`; si no se confirma, utiliza el sandbox explícito. Fallos de red o una respuesta de commit no confirmada nunca se presentan como éxito remoto. La ruta remota crea una rama nueva y aplica el archivo solamente en esa rama.

No se necesita leer `.env` desde el flujo de consulta. Configura las variables en el entorno protegido del servidor; nunca las envíes en el cuerpo HTTP ni las registres en logs. Las pruebas no usan GitHub real ni hacen solicitudes de red: simulan las respuestas HTTP localmente.
