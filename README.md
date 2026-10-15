# Asistente de gestión y análisis de proyectos

Aplicación local para que un gestor consulte el avance de un proyecto, analice riesgos y calidad de código, revise propuestas y autorice acciones protegidas. El backend conserva métricas, permisos y recibos; los agentes recomiendan, pero la autoridad final siempre es una persona.

## Roles y responsabilidad humana

| Rol de trabajo | Componente | Responsabilidad y límite |
|---|---|---|
| Ejecutor | `ProjectMonitoringAgent` y fuentes del proyecto | Resume tareas, riesgos y propuestas a partir de datos observados. No autoriza sus propias propuestas. |
| Revisor | `CodeQualityAgent`; `VerifierAgent` como apoyo independiente | Produce hallazgos candidatos; el verificador interviene cuando corresponde. Un hallazgo candidato no equivale a defecto confirmado. |
| Aprobador humano | Gestor del Proyecto (`PROJECT_MANAGER`) | Decide aprobar, modificar o rechazar con motivo. Las escrituras se ejecutan solo tras autorización y controles del servidor. |
| Coordinación y síntesis | `ProjectOrchestrator` y `ReportAgent` | El orquestador coordina; el agente de informes explica resultados y recomienda. Ninguno reemplaza la decisión humana. |

El sistema calcula porcentajes, fechas y estados en Python. Los datos ausentes se muestran como desconocidos; una recomendación no convierte una medición no observada en un resultado favorable.

## Componentes

- **API:** FastAPI, sesiones de gestor, contratos REST y trabajos locales de análisis.
- **Persistencia:** SQLite para capturas, análisis, decisiones y auditoría; artefactos locales para informes y recibos.
- **Web:** React 19, TypeScript y Vite. Incluye tablero, tareas, análisis, calidad, propuestas, informes, programación, auditoría y ajustes.
- **Fuentes y proveedores:** adaptadores para Trello, GitHub, GitHub Actions, Gemini y Groq. La disponibilidad real depende de la configuración y permisos efectivos.

## Instalación rápida

Requisitos: Python 3.10 o posterior y Node.js compatible con Vite (20.19+ o 22.12+).

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[web]'
cd frontend && npm install
```

La API importa el paquete instalado en modo editable; no hace falta definir `PYTHONPATH`.

## Configuración

La configuración combina argumentos/ajustes explícitos, variables del proceso, el archivo `.env` local si existe, YAML del proyecto y valores predeterminados. El proceso tiene prioridad sobre `.env`; los secretos no se escriben en YAML, reportes ni en el navegador. API y CLI combinan `config/config.yaml` como base con `config/hormigestion.yaml` como proyecto predeterminado y con la configuración explícita (`AGENTE_CONFIG_PATH` o `--config`). Una configuración explícita de otro proyecto no hereda las vinculaciones ni el SHA del proyecto predeterminado. Las variables del proceso y `.env` tienen prioridad sobre YAML. Los alias `GITHUB_REPO`/`GITHUB_REPOSITORY` y `GITHUB_BRANCH`/`GITHUB_REF` se reconcilian; un valor contradictorio se rechaza. `TRELLO_BOARD_ID` o `board_id` en YAML permite asociar el tablero al proyecto sin construir manualmente un mapa JSON.

Este bloque deja las claves externas vacías para poder iniciar el flujo local sin contactar proveedores. Completalas solo cuando quieras usar servicios reales; no pegues marcadores como si fueran credenciales. La contraseña mostrada es solo la credencial de demostración del login; cámbiala antes de cualquier despliegue. No publiques secretos ni el archivo local que los contiene.

```dotenv
# Inicio de sesión de demostración; reemplazar antes de producción
MANAGER_LOGIN_EMAIL=gestor@hormigestion.ec
MANAGER_LOGIN_PASSWORD=HormiGestion2026!
MANAGER_EMAIL=gestor@hormigestion.ec

# Proyecto y capacidades web
AGENTE_CONFIG_PATH=config/hormigestion.yaml
H5_ANALYSIS_ENABLED=true
H5_GOVERNANCE_ENABLED=true
H5_REPORT_DECISIONS_ENABLED=true
H5_WEEKLY_CYCLE_ENABLED=true

# Permisos del servidor: true por defecto; false explícito se conserva
ALLOW_REMOTE_AI=true
EXTERNAL_WRITES_ENABLED=true

# Proveedores remotos: vacíos seleccionan alternativas deterministas locales
GEMINI_API_KEY=""
GROQ_API_KEY=""

# Fuentes y acciones de proyecto
TRELLO_API_KEY=""
TRELLO_TOKEN=""
TRELLO_BOARD_ID=""
# Mapa opcional para varios tableros/proyectos:
# TRELLO_BOARD_IDS={"HORMIGESTION":"<ID_TABLERO_HORMIGESTION>"}
GITHUB_TOKEN=""
GITHUB_REPO=Josu-F1/hormigestion-backend
# Alias alternativo (usa uno de los dos): GITHUB_REPOSITORY=Josu-F1/hormigestion-backend

# Correo; SMTP solo se usa cuando está configurado y el usuario confirma el envío
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=""
SMTP_PASSWORD=""
SMTP_USE_TLS=true
```

**Defaults y controles:** Monitoring, Verifier y Report usan Gemini; Quality usa Groq. Si una clave remota no está disponible, cada componente puede usar su alternativa determinista local y el resultado identifica `execution_mode` (`live`, `sandbox` o `mixed`) y `fallback_reasons`. La preparación informa `ready`/`eligible` y la acción disponible; no deshabilita el análisis solo por faltar una credencial opcional. Si faltan credenciales o permisos para una escritura externa, se elige sandbox antes de intentar escribir. Un intento live que falla permanece fallido: nunca se presenta como éxito sandbox.

`ALLOW_REMOTE_AI=true` es el permiso predeterminado del servidor, no el consentimiento del gestor. La transferencia remota requiere la confirmación explícita del análisis y sigue sujeta a autenticación y alcance del proyecto. `EXTERNAL_WRITES_ENABLED=true` participa en la autorización de escrituras, pero no sustituye modo de proyecto, credenciales, permisos del proveedor ni aprobación humana. Un `false` explícito en cualquiera de estos flags se respeta.

El destinatario inicial de alertas es el correo verificado de la sesión del gestor; `MANAGER_EMAIL` sirve como configuración del servidor si no hay correo de sesión. El botón para cambiar destinatario permite enviar una copia a otra dirección. La aceptación de SMTP no demuestra que el mensaje haya llegado al buzón; sin SMTP, el recibo queda en el outbox local, identificado como tal.

## Iniciar la aplicación

Desde la raíz, inicia el backend en una terminal:

```sh
.venv/bin/python -m uvicorn agente_gestion.api.app:app --host 127.0.0.1 --port 8000
```

En otra terminal, inicia la web:

```sh
cd frontend && npm run dev -- --host 127.0.0.1 --port 5173
```

Abre <http://127.0.0.1:5173>. La API publica su esquema en <http://127.0.0.1:8000/docs>. Usa un worker de Uvicorn para conservar las sesiones locales en memoria. Para la demostración, las credenciales iniciales son `gestor@hormigestion.ec` / `HormiGestion2026!`; reemplázalas en un entorno propio.

## Recorrido web

1. **Inicia sesión** con el correo y contraseña del gestor. La API emite cookie `HttpOnly` y token CSRF; la sesión informa el correo verificado y las capacidades habilitadas. El modo de consulta no concede permisos de gestor.
2. **Prepara el análisis** en el Centro de Análisis. Revisa fuentes, componentes, modo de ejecución y razones de fallback. Cuando se vaya a transferir información a un proveedor o fuente remota, confirma ese uso por separado. La aplicación vuelve a comprobar configuración y permisos antes de encolar el trabajo.
3. **Sigue el resultado** en el estado del trabajo, el dashboard y el informe. Un modelo o una fuente sin credenciales se sustituye por el fallback local configurado y queda identificado; no se inventan datos de CI, hallazgos confirmados ni entregas.
4. **Revisa una corrección de Trello** en Propuestas. Comprueba tarea, evidencia y cambio propuesto. Elige aprobar y ejecutar, modificar el campo de esa propuesta o rechazar, siempre con motivo. Si faltan credenciales o permisos live, la respuesta declara sandbox y no llama a Trello. Un fallo de una escritura live se conserva como fallo.
5. **Compara una corrección de código** en Calidad. Revisa el código observado, la sugerencia y el dictamen independiente disponible. Aprueba solo tras revisar la propuesta; el backend valida la fuente completa antes de crear una rama. Comprueba si el resultado es sandbox local o rama GitHub live; la creación de una rama no abre automáticamente un Pull Request.
6. **Envía un informe por correo** desde el panel de notificaciones. El correo verificado de sesión es el destinatario inicial; puedes cambiarlo para enviar una copia. Con SMTP no configurado se genera un recibo local. `smtp_email_sent` significa que el relay aceptó el mensaje, no que llegó a la bandeja.
7. **Consulta Auditoría** para verificar actor de sesión, motivo, proyecto, modo y recibos. Las preferencias del navegador no sustituyen las decisiones del backend.

## Recorrido CLI

Una corrida local reproducible usa fixtures y proveedores simulados; no contacta servicios externos:

```sh
.venv/bin/python -m agente_gestion run HORMIGESTION --sources fixtures --fixture fixture_1_standard --fake
```

La salida muestra el ID, estado y ubicación del informe. Para consultar capturas ya guardadas sin escribir una nueva captura:

```sh
.venv/bin/python -m agente_gestion tasks summary HORMIGESTION --db-path .runtime/agente.db --json
.venv/bin/python -m agente_gestion history list HORMIGESTION --db-path .runtime/agente.db
.venv/bin/python -m agente_gestion proposals list HORMIGESTION --db-path .runtime/agente.db
```

Una comparación requiere dos `capture_id` existentes:

```sh
.venv/bin/python -m agente_gestion history compare HORMIGESTION <CAPTURA_ANTERIOR> <CAPTURA_ACTUAL> --db-path .runtime/agente.db
```

Una corrida con fuentes reales puede hacer lecturas HTTP a Trello y GitHub; comprueba primero el proyecto, la configuración y la política de datos. Para ejecutar el análisis configurado:

```sh
.venv/bin/python -m agente_gestion run HORMIGESTION --sources real --allow-remote-ai
```

`source-check all --sources real` es un diagnóstico de solo lectura que también contacta las APIs configuradas. No lo uses como prueba de escritura. En la CLI se puede registrar la decisión humana sobre un informe, pero las correcciones de Trello, ramas GitHub y envíos de correo requieren los flujos protegidos de la web:

```sh
.venv/bin/python -m agente_gestion decide <RUN_ID> --decision approved --reason "Revisado por el gestor"
.venv/bin/python -m agente_gestion security audit HORMIGESTION --json
.venv/bin/python -m agente_gestion schedule check HORMIGESTION --json
.venv/bin/python -m agente_gestion schedule outbox HORMIGESTION --json
```

La auditoría y el outbox muestran lo persistido. No son evidencia de una escritura remota o de entrega SMTP si el recibo no lo declara.

## Verificaciones

Comandos para ejecutar en la integración actual:

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
cd frontend && npm run typecheck
cd frontend && npm run build
cd frontend && npx playwright test
```

La suite Python terminó sin fallos: 844 pruebas aprobadas y 1 prueba optativa contra Gemini real omitida. El frontend pasó chequeo de tipos, compilación y 92 pruebas Playwright. Las pruebas HTTP de Python comprueban el motor determinista sin claves y su corrida/informe persistidos; Playwright usa respuestas interceptadas para comprobar la interfaz. Se observaron advertencias de Starlette y SQLite que no hicieron fallar la suite. Consulta [INFORME_PRUEBAS.md](INFORME_PRUEBAS.md) para el alcance y los límites de la evidencia.

## Documentación

- [Guía de la web](docs/01-frontend-guia-y-estado.md)
- [Backend, seguridad y catálogo REST](docs/02-backend-arquitectura-y-api.md)
- [Agentes y matriz REQ-01 a REQ-11](docs/03-motor-de-agentes-y-requisitos.md)
- [Capas y contratos actuales](docs/04-analisis-comparativo-arquitectura.md)
- [Arquitectura del sistema](ARCHITECTURA.md)
- [Verificación actual](INFORME_PRUEBAS.md)
