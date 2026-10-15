# Plataforma web: flujo unificado

La web consulta el motor existente y aplica un único flujo operativo: **Ejecutor → Revisor técnico automático → Aprobador humano → ejecución protegida**. Las métricas, capturas, políticas y autorizaciones siguen siendo responsabilidad del backend.

La retirada del flujo anterior y la regeneración de semillas se registran en `odd/tasks/backend-fullstack-phase2.md`. Las cifras de pruebas de H5/Fase 1 no acreditan los cambios actuales; consultá ese log para los resultados vigentes.

## Recorrido recomendado

1. Iniciá la demostración con una base nueva del esquema actual, al menos dos capturas comparables y archivos fuente completos.
2. En Dashboard, comprobá gráficos históricos y el comparador REQ-03. Los datos vienen de capturas persistidas, no de constantes del navegador.
3. Iniciá una sesión de operador de la fixture. La consulta anónima no otorga permisos ni decisiones locales simuladas.
4. En Propuestas, inspeccioná el dictamen técnico automático. Aprobá, modificá el campo correspondiente o rechazá con motivo; la UI llama exclusivamente al endpoint unificado `/execute`.
5. En Calidad, inspeccioná código y explicación del backend. La aprobación exige fuente completa y validación del archivo modificado completo; sin esa prueba se bloquea. El backend devuelve `approved` y diferencia rama remota de artefacto sandbox.
6. En Auditoría, comprobá autor, motivo humano, estado de ejecución y resultado de verificación. En correo, confirmá destinatario y distinguí aceptación SMTP, error y HTML local.

La [guía de Fase 2](phase2-fullstack.md) contiene endpoints, configuración GitHub/Trello/SMTP y pasos de presentación. El [contrato REST](h5-api-contract.md) describe los DTOs compartidos.

## Límites de seguridad

- Escuchá en `127.0.0.1`; no publiques el servidor ni la base de demostración.
- GET no ejecuta IA, fuentes remotas ni mutaciones. Los contadores y el progreso se calculan en Python.
- Sesión, alcance de proyecto, origen exacto, CSRF e idempotencia son obligatorios para decidir o ejecutar.
- GitHub/Trello en vivo requieren configuración explícita del backend, credenciales acotadas y aprobación humana; ninguna preferencia del navegador lo habilita.
- Una tarjeta Trello debe acreditar el tablero configurado. Sin `idBoard` válido se rechaza antes de escribir.
- No hay aprobación local, fallback por 404 ni secuencia pública de revisión/autorización/dry-run manual. Los registros internos de revisión automática no conceden por sí solos autorización humana.
- No se guardan tokens de operador ni de proveedores en `localStorage`. Las credenciales permanecen en el backend.
- Sandbox y HTML local son pruebas controladas, nunca evidencia de mutación remota ni entrega en una bandeja de correo.

## Entorno y configuración

El backend usa `.venv/bin/python`. El frontend necesita una versión de Node compatible con Vite (20.19+ o 22.12+). La aplicación lee variables del proceso; no carga `.env` automáticamente.

| Variable | Propósito | Predeterminado |
| --- | --- | --- |
| `AGENTE_CONFIG_PATH` | Configuración del proyecto; debe coincidir con el proyecto de la base | `config/hormigestion.yaml` |
| `AGENTE_DB_PATH` | SQLite de capturas y resultados | `.runtime/agente.db` |
| `AGENTE_RUNS_DIR`, `H5_OUTBOX_DIR` | Artefactos de corridas y entregas | Configuración del backend |
| `H5_OPERATOR_TOKEN` | Credencial local independiente de al menos 32 caracteres | Sin privilegios si falta |
| `H5_OPERATOR_ACTOR` | Autor de auditoría fijado por el servidor | `operador-local` |
| `H5_ANALYSIS_ENABLED` | Preparar/confirmar análisis autenticados | `false` |
| `H5_GOVERNANCE_ENABLED` | Decisiones unificadas sobre propuestas y código | `false` |
| `H5_REPORT_DECISIONS_ENABLED` | Decisión humana sobre informes | `false` |
| `H5_WEEKLY_CYCLE_ENABLED` | Ciclo semanal manual local SQLite | `false` |
| `H5_WEB_JOBS_DB_PATH` | Ledger de trabajos; usar uno nuevo para la demo regenerada | `.runtime/h5-web-jobs.sqlite` |
| `EXTERNAL_WRITES_ENABLED` | Habilitación explícita de GitHub/Trello en vivo | `false` |
| `H5_ALLOWED_ORIGINS`, `H5_ALLOWED_HOSTS` | Listas exactas de orígenes/hosts | Loopback local |
| `H5_SESSION_TTL_SECONDS` | Vigencia de sesión | `3600` |
| `H5_SESSION_COOKIE_SECURE` | Cookie solo HTTPS | `false` solo para desarrollo HTTP local |

Las variables de SMTP, tablero y etiquetas están en la guía de Fase 2. Una credencial configurada no prueba disponibilidad del proveedor.

## Arranque

Desde la raíz, para una demostración desechable sin proveedores reales:

```sh
.venv/bin/python scripts/serve_web_demo.py --dataset fixture --port 8000
```

La credencial pública de pruebas `h5-fixture-operator-only-never-use-in-production` es exclusiva de esa base controlada. No la uses en una base privada. El helper debe crear capturas/corridas nuevas con fuente completa y un análisis confirmado debe generar una nueva corrida persistida, no devolver un identificador de semilla fijo.

En otra terminal:

```sh
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Abrí `http://127.0.0.1:5173`. OpenAPI está en `http://127.0.0.1:8000/docs`. El cliente usa `/api/v1` y el proxy de Vite; `H5_API_PROXY_TARGET` cambia el destino de API sin ampliar los orígenes permitidos.

Para iniciar una API sobre una base/configuración ya preparadas, aprovisioná sus variables de forma segura y ejecutá un solo worker:

```sh
PYTHONPATH=src .venv/bin/python -m uvicorn agente_gestion.api.app:app --host 127.0.0.1 --port 8000 --workers 1
```

## Verificación

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s pruebas -p 'test_*.py' -v
cd frontend
npm run typecheck
npm run build
npx playwright test
```

Se usa `unittest` por decisión explícita del operador; no se instala `pytest`. Las pruebas Playwright con respuestas interceptadas prueban la UI, no sustituyen integración con Python. Las pruebas de semillas/API deben crear estado aislado y comprobar las capturas comparables, fuentes completas, rechazo sin efectos y motivos recuperables por la interfaz pública.

El ciclo semanal web conserva preparación y confirmación local SQLite sin llamadas remotas ni entregas externas; no es la secuencia burocrática retirada de propuestas. El envío web de correo requiere su confirmación separada. La ejecución desatendida de cron/systemd y los proveedores en vivo no se consideran probados por una fixture.

Los resultados históricos de H5 permanecen en su log como evidencia anterior, no como una guía para reactivar rutas o datos incompletos. La base anterior a la regeneración se conserva únicamente como respaldo recuperable dentro de `.runtime/backups/`, no se carga como datos activos de demostración.
