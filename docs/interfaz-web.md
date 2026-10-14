# Plataforma web H5

La interfaz web es un adaptador del motor existente: consulta sus capturas y resultados; no reemplaza agentes, métricas, políticas ni reglas de autorización. La aplicación está en español y se orienta a una demostración local de HORMIGESTION.

> Módulos T1–T5 implementados y verificados con HTTP real sobre fixtures desechables. El cierre T6 sigue pendiente: la demostración histórica completa en navegador agotó su espera y la revisión nativa todavía no se inició. La evidencia y sus límites están en `odd/tasks/h5-web-platform.md`; no se declara H5 terminado.

## Límites de seguridad

- El servidor se inicia en `127.0.0.1`, no en una interfaz pública. La consulta histórica local no constituye autenticación de un usuario privilegiado.
- Ningún endpoint debe invocar IA, GitHub ni Trello durante una consulta. Las métricas de tareas proceden del servicio determinista Python.
- No hay capacidad de escritura real en Trello desde la web. La brecha de identidad/tokens de H2 continúa siendo una restricción explícita; una sesión web no la subsana ni habilita el adaptador de mutaciones.
- La revisión de una propuesta, la decisión sobre un informe, el permiso de simulación y la ejecución externa son decisiones diferentes.
- Los proveedores y sus claves permanecen en el backend. No se almacenan credenciales de proveedor ni de operador en `localStorage`.
- No se deben publicar el servidor, las capturas, los informes o la base del piloto sin revisar antes los controles de acceso y la información del proyecto.

## Entorno y configuración local

El backend usa `.venv/bin/python`; el frontend necesita Node compatible con Vite (20.19+ o 22.12+). El entorno de implementación dispone de Node 26.9.0 y Python 3.14.8.

La configuración de consulta web no carga archivos dotenv ni llama proveedores. Variables aceptadas por el servidor:

| Variable | Propósito | Predeterminado |
| --- | --- | --- |
| `AGENTE_CONFIG_PATH` | Archivo de configuración del proyecto | `config/hormigestion.yaml` |
| `AGENTE_DB_PATH` | Base existente de capturas/resultados | `.runtime/agente.db` |
| `AGENTE_RUNS_DIR` | Directorio de artefactos generados | `runs` |
| `H5_OPERATOR_TOKEN` | Credencial local independiente para iniciar sesión | Sin configurar; no hay privilegios |
| `H5_OPERATOR_ACTOR` | Identidad del operador definida por el servidor | `operador-local` |
| `H5_ANALYSIS_ENABLED` | Habilitar preparación/confirmación autenticadas de análisis | `false` |
| `H5_GOVERNANCE_ENABLED` | Revisión local, permiso separado y simulación de propuestas; nunca Trello real | `false` |
| `H5_REPORT_DECISIONS_ENABLED` | Primera decisión humana versionada de un informe | `false` |
| `H5_WEEKLY_CYCLE_ENABLED` | Preparación/confirmación explícita del ciclo semanal local SQLite | `false` |
| `H5_WEB_JOBS_DB_PATH` | Ledger separado de trabajos asíncronos | `.runtime/h5-web-jobs.sqlite` |
| `H5_SESSION_TTL_SECONDS` | Vigencia de sesión en segundos | `3600` |
| `H5_SESSION_COOKIE_SECURE` | Restringir cookie a HTTPS | `false`, exclusivamente desarrollo local HTTP |
| `H5_ALLOWED_ORIGINS` | Orígenes HTTP(S) exactos, separados por coma | localhost/127.0.0.1 en puertos 5173 y 8000 |
| `H5_ALLOWED_HOSTS` | Hosts explícitos, sin comodines | localhost, 127.0.0.1, ::1 |

La credencial local debe ser de al menos 32 caracteres y generarse de forma aleatoria. No es una clave de Gemini, Groq, GitHub o Trello. Los valores de actor y alcance no se reciben del navegador. Una sesión usa cookie HttpOnly con SameSite=Strict y ruta `/api/v1`, protección CSRF y validación de origen; cerrar sesión la revoca. La configuración no equivale a un sistema multiusuario ni a SSO.

## Servidor de demostración aislada

Desde la raíz del repositorio, con las dependencias web instaladas:

```fish
# Historial auténtico, sin privilegios ni llamadas a fuentes o IA
.venv/bin/python scripts/serve_web_demo.py --dataset historical --port 8000

# Base desechable controlada para pruebas (no son datos auténticos)
.venv/bin/python scripts/serve_web_demo.py --dataset fixture --port 8000
```

El modo `historical` ignora cualquier privilegio de operador heredado. El modo `fixture` crea una base nueva bajo `.runtime/`, la elimina al detener el proceso y usa una credencial de pruebas pública, exclusiva de esa base: `h5-fixture-operator-only-never-use-in-production`. No debe usarse para desplegar una instancia ni cargar datos privados. El helper no acepta un host público. En modo `fixture`, el control de trabajos usa una configuración `is_fake` temporal y un executor inyectado que devuelve la ejecución simulada ya sembrada: prueba preparación, confirmación, persistencia y polling HTTP, **no ejecuta el pipeline ni llama proveedores**. El modo `historical` fuerza también análisis, gobernanza, decisiones y ciclos manuales deshabilitados. La fixture habilita únicamente esas operaciones locales sobre su base desechable. Para hacer reproducible el ciclo semanal usa un reloj de pruebas fijo (`2026-01-08T18:00:00Z`) y jueves 13:00 en `America/Guayaquil`; **no representa la fecha actual ni una programación de producción**. El ciclo manual usa el servicio real existente con outbox SQLite, sin fuentes remotas, IA ni entrega externa.

## Instalación y arranque en CachyOS / Fish

Desde la raíz, sin instalar paquetes globales:

```fish
.venv/bin/python -m pip install -e '.[web]'
cd frontend
npm ci
npx playwright install chromium
cd ..
```

Terminal del backend (un solo worker; no inicia análisis al arrancar):

```fish
PYTHONPATH=src .venv/bin/python -m uvicorn agente_gestion.api.app:app --host 127.0.0.1 --port 8000 --workers 1
```

Terminal del frontend:

```fish
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Abrí `http://127.0.0.1:5173`. OpenAPI está en `http://127.0.0.1:8000/docs`. El cliente usa rutas relativas; Vite reenvía `/api/v1` al backend. Para un puerto de API distinto, configurá `H5_API_PROXY_TARGET` antes de iniciar Vite. No cambies los orígenes permitidos a `*`.

## Pruebas de fundamentos

```fish
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py'
cd frontend
npm run typecheck
npm run build
npm run test:e2e
npm audit
cd ..
```

La suite Playwright de `frontend/e2e/web-platform.spec.ts` usa respuestas interceptadas controladas: verifica la UI, no la integración con Python. Para verificar fundamentos con HTTP real, iniciá el helper `--dataset fixture`, el frontend, y comprobá que `/api/v1/system/health` responda antes de ejecutar:

```fish
node scripts/check_web_foundation.mjs
```

Ese script no intercepta API, rechaza proyectos no-fixture, verifica detalle/diff/sesión/temas/móvil y guarda capturas bajo `.runtime/h5-browser/`. No modifica el historial auténtico. Los resultados observados y las comprobaciones aún pendientes se registran en el documento de tareas; una captura no sustituye una prueba funcional.

## Evidencia histórica auténtica

```fish
.venv/bin/python scripts/check_web_history.py
```

Este verificador consulta la API en proceso con la base histórica real, bloquea el transporte HTTP de proveedores y compara huellas de la base y los artefactos antes/después. No sustituye datos ausentes por fixtures. La evidencia queda en `.runtime/h5-authentic-readonly.json`.

Resultado observado en el piloto: 31 tareas registradas, 30 incluidas en avance, 18 completadas y 60 % informado por el motor; 5 capturas de HORMIGESTION y 2 tareas críticas. Estos números son evidencia de esa consulta, no constantes de React. La comparación entre las capturas más antigua y más reciente fue rechazada con `409 comparacion_no_disponible`: la evidencia histórica disponible no permite compararlas. La UI debe mostrar esa limitación, no fabricar un diff. La comparación con datos controlados compatibles sí pasó contra la API real.

## Análisis y calidad

La API consulta ejecuciones, etapas, candidatos, verificaciones, fragmentos compatibles, CI y políticas persistidas. Las etapas ausentes se muestran desconocidas; una etapa explícitamente omitida no se presenta como ejecutada. La cobertura ausente no equivale a cero y los candidatos no se convierten en defectos confirmados.

La ejecución normal está deshabilitada por defecto. Requiere habilitar `H5_ANALYSIS_ENABLED`, aprovisionar una credencial local y claves de proveedores exclusivamente en el entorno del backend, iniciar sesión y confirmar en la web. La preparación muestra configuración y presencia de credenciales, **no verifica disponibilidad remota**. La confirmación queda vinculada a esa configuración y sesión; exige un consentimiento remoto adicional cuando corresponde. No debe activarse para una presentación solo de consulta.

Los trabajos usan una cola finita y un ledger separado. El backend persiste estados reales, no porcentajes estimados. Preparaciones y sesiones son locales al proceso: usá un solo worker. Tras reiniciar no se reanudan trabajos activos automáticamente; se muestran interrumpidos. No se habilitan mutaciones de Trello.

Con API fixture y frontend ya disponibles, la integración de IA/calidad se comprueba sin interceptar HTTP:

```fish
node scripts/check_web_intelligence.mjs
```

Este recorrido confirma únicamente un job del executor inyectado de pruebas, no un análisis real del motor. Los resultados observados se registran en el documento de tareas.

## Gobernanza, informes y ciclo semanal

El backend y las vistas T4/T5 están implementados y conectados; el contrato está en [h5-api-contract.md](h5-api-contract.md). La integración con API real pasó en una base desechable: revisión y simulación separadas, informes y descargas, decisión humana, ciclo semanal SQLite, auditoría y configuración. Esa evidencia no equivale a una demostración histórica completa ni a ejecución con proveedores IA auténticos.

- Propuestas: consultar la evidencia y captura persistidas, revisar localmente, autorizar por separado una simulación versionada y ejecutar el dry-run del núcleo. No modifica tareas ni llama Trello. Una aprobación offline no demuestra identidad histórica ni permite ejecución externa.
- Informes: consultar métricas, recomendaciones, políticas y Markdown saneado; descargar Markdown/JSON ligados al informe. Una decisión humana necesita sesión verificada, versión pendiente actual, CSRF, origen e idempotencia. La decisión/historial y su auditoría web se confirman en la misma transacción; un fallo de auditoría revierte la operación. Las decisiones antiguas sin identidad verificable conservan actor desconocido.
- Auditoría: lectura protegida con filtros y detalle allowlisted; distingue un operador local verificado de eventos del núcleo sin atribución. No expone fingerprints de credenciales ni payloads crudos.
- Configuración: metadatos saneados, de solo lectura (`editable_fields: []`); presencia de una credencial no implica disponibilidad del proveedor. Cambiar el tema no modifica integraciones.

La planificación semanal solo se configura al proporcionar las cinco variables `H5_WEEKLY_SCHEDULE_PROJECT_ID`, `H5_WEEKLY_SCHEDULE_WEEKDAY` (0=lunes, 6=domingo), `H5_WEEKLY_SCHEDULE_LOCAL_TIME` (`HH:MM`), `H5_WEEKLY_SCHEDULE_TIMEZONE` (`America/Guayaquil`) y `H5_WEEKLY_SCHEDULE_WINDOW_RULE` (`previous_scheduled_week`). No se elige un horario de producción implícito. La ausencia o invalidez aparece como desconocida/incompleta, no como una agenda ficticia.

La API no instala ni comprueba cron/systemd: `automatic_trigger_status: not_verified` **no acredita ejecución desatendida**. El ciclo manual requiere preparación/confirmación, sesión, CSRF, origen exacto e idempotencia; usa una cola finita y no ejecuta `force`, no crea capturas ni nuevos informes IA. Un job `completed` solo ejecutó el ciclo si su `outcome` es `EXECUTED`; también puede omitirlo por no estar vencido o por entrega previa. Outbox, descarga y recibos proceden de datos persistidos; archivos sin vínculo o fuera de la raíz permitida se rechazan.

## Comprobación offline del motor real

```fish
PYTHONPATH=src .venv/bin/python scripts/check_web_engine.py
```

Esta prueba usa el **executor predeterminado y el orquestador real**, fuentes/LLM fake, base y artefactos temporales; bloquea el transporte HTTP y rechaza cualquier preflight no simulado antes de confirmar. No usa el historial auténtico ni confirma análisis pagados. La evidencia se guarda en `.runtime/h5-offline-engine.json`; la ejecución observada pasó con una nueva ejecución/informe persistidos y cero llamadas HTTP. El resultado completo está registrado en el documento de tareas. Es una prueba diferente del job de transporte inyectado de los recorridos de navegador.

## Evidencia integrada y pendientes

Con una instancia **nueva** de `scripts/serve_web_demo.py --dataset fixture` y el frontend disponibles, los recorridos se ejecutan sin interceptar API:

```fish
node scripts/check_web_foundation.mjs
node scripts/check_web_intelligence.mjs
node scripts/check_web_governance.mjs
```

El último script requiere la propuesta aún pendiente: revisa y autoriza una simulación, decide un informe y confirma un ciclo local únicamente en la base desechable. Comprueba además que tareas/capturas no cambian y que no aparecen llamadas externas. No lo apuntes al historial auténtico ni lo repitas sobre una fixture ya consumida.

Resultados observados: 771 pruebas Python (2 omitidas), 37 pruebas Playwright de UI interceptada, typecheck/build correctos y npm audit sin vulnerabilidades. Los ocho recorridos esenciales pasaron contra HTTP real. Un verificador independiente ejecutó otras 19 pruebas de seguridad/gobernanza sobre una copia temporal y no observó bloqueos de código; no reemplaza la revisión nativa pendiente. Persisten advertencias no fatales de Starlette/httpx y conexiones SQLite.

Evidencia local: `.runtime/h5-final-python.log`, `.runtime/h5-offline-engine.json`, `.runtime/h5-governance-proof.json` y capturas bajo `.runtime/h5-browser/`. Estos archivos generados se excluyen de Git; no contienen una autorización de publicación.

**Demostración histórica en navegador: pendiente.** `scripts/check_web_historical_browser.mjs` se detuvo por timeout al esperar la respuesta de comparación; el intento corregido autorizado también terminó en timeout. No se atribuye todavía una causa concluyente al segundo fallo ni se lo oculta con datos simulados. La comprobación final de integridad no falló: las 90 huellas históricas permanecieron intactas. Hay capturas auténticas de dashboard y tarea crítica, pero no se acredita el recorrido completo. `.runtime/h5-authentic-browser-proof.json` tiene `completed: false`. No reiniciar automáticamente ese chequeo: primero se requiere una decisión explícita sobre el diagnóstico y la evidencia faltante. Las escrituras Trello reales siguen bloqueadas y el disparador automático semanal continúa sin verificar.
