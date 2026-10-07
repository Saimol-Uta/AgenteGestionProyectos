# Pruebas locales para el asistente de gestión de proyectos

La [arquitectura propuesta del agente](ARCHITECTURA.md) define el flujo de Trello,
GitHub y calidad, los roles y la compatibilidad con Ollama y APIs externas de IA.
Es un diseño para implementar; los conectores y el backend aún no existen.

Ya se ejecutaron las pruebas en esta laptop. Consulta el
[informe de resultados](INFORME_PRUEBAS.md) para ver velocidades, memoria,
errores observados y diferencias entre configuraciones.

Preparado a partir de la conversación «Revisa El Audio». El objetivo es comparar
modelos en la Lenovo IdeaPad Gaming 3, Ryzen 5 6600H, 16 GB de RAM y RTX 3050 de
4 GB. Las pruebas usan datos simulados; no requieren credenciales de Trello ni GitHub.

## Modelos

| Orden | Modelo exacto | Descarga publicada aproximada |
|---|---|---:|
| 1 | `qwen3.5:2b-q4_K_M` | 1,9 GB |
| 2 | `qwen3:4b-instruct-2507-q4_K_M` | 2,5 GB |
| 3 | `qwen3.5:4b-q4_K_M` | 3,4 GB |

Etiquetas verificadas en la biblioteca oficial el 2 de octubre de 2026.
El tamaño descargado no equivale a la VRAM necesaria. Los modelos 3.5 incluyen
componentes de visión, pero estos casos solo envían texto. La elección del mejor
modelo se hará con las mediciones de esta laptop y la revisión de las respuestas.

## Preparación

Desde esta carpeta, usando tu shell fish o bash:

```sh
python3 pruebas/instalar_ollama.py
bash pruebas/ollama-local.sh serve
```

Mantén esa terminal abierta. Ollama 0.35.1 se descarga desde su release oficial,
se verifica con SHA-256 y se extrae en `.runtime/ollama`. El instalador reanuda
descargas parciales. Necesita `curl`, `tar` y `zstd`; el comparador usa Python 3.10+
sin paquetes adicionales. El archivo del runtime ocupa 1,4 GB comprimido, además
del espacio al extraerlo y el de los modelos.

El servidor escucha solo en `127.0.0.1:11435`, guarda los modelos en
`.runtime/models`, permite una petición y un modelo cargado a la vez, y desactiva
las funciones cloud. Ollama crea sus propias claves locales en `~/.ollama` al
arrancar por primera vez. No se instala un servicio del sistema. Para detenerlo,
pulsa Ctrl+C en su terminal. No necesitas `prime-run` para el backend CUDA de Ollama.

En una segunda terminal:

```sh
python3 pruebas/benchmark.py doctor
python3 pruebas/benchmark.py pull
python3 pruebas/benchmark.py run
```

Para empezar solo con el modelo más pequeño:

```sh
python3 pruebas/benchmark.py pull --models qwen3.5:2b-q4_K_M
python3 pruebas/benchmark.py run --models qwen3.5:2b-q4_K_M
```

Con un Ollama existente en el puerto habitual, usa `--host http://127.0.0.1:11434`
en el comparador. Sus ajustes de concurrencia dependen de cómo iniciaste ese servidor.
El comparador se detiene si encuentra un modelo ya cargado para no alterar otro trabajo.

## Qué se compara

1. **Seguimiento:** calcula avance, retrasos y bloqueos a partir de Trello y CI;
   incluye una descripción maliciosa que intenta cambiar el reporte.
2. **Revisión de código:** detecta inyección SQL, una conexión sin cierre y división
   por cero usando criterios explícitos, con líneas de evidencia.
3. **Aprobación:** contrasta un reporte incorrecto con pruebas fallidas, baja cobertura,
   un hallazgo crítico y una revisión documental sin evidencia. Conserva al gestor
   como responsable de la decisión final. Un cuarto caso de control cumple todos
   los requisitos y debe recibir una recomendación favorable.

La comparación inicial se realizó con 4096 tokens de contexto, hasta 768 tokens
de salida, temperatura 0, semilla 42, seis hilos de CPU, razonamiento desactivado cuando el
modelo lo soporta, y tres repeticiones. Un calentamiento inicial separado permite
medir la carga del modelo sin mezclarla con las respuestas habituales. Los roles
se ejecutan por turnos. Son casos independientes: no prueban todavía el flujo
completo entre agentes ni llamadas a herramientas.

El comparador ahora usa por defecto el perfil `--sampling qwen`: temperatura
0,7, top_p 0,8, top_k 20, min_p 0 y repeat_penalty 1. En Qwen3.5 aplica
presence_penalty 1,5. Si activas razonamiento, el perfil general de Qwen3.5 usa
temperatura 1 y top_p 0,95. Las repeticiones usan semillas 42, 43 y 44. Estos
ajustes siguen las fichas de los modelos; no todas las configuraciones equivalen
a los valores recomendados para todas las tareas, especialmente código preciso.
La comparación inicial con temperatura 0 se conserva como referencia. Para
repetir esa variante, añade `--sampling greedy`.

No se fijan velocidades esperadas antes de medir. Revisa `bash pruebas/ollama-local.sh ps`
y `nvidia-smi` para comprobar la carga efectiva en GPU. Usa el equipo conectado
a corriente y con una carga de otras aplicaciones parecida entre comparaciones.

## Pruebas de contexto

Primero compara los casos cortos en 4096. Después prueba más datos con 8192:

```sh
python3 pruebas/benchmark.py run --context 8192 --extra-cards 60 --cases seguimiento
```

`--context` fija la ventana; `--extra-cards` aumenta la entrada real. Cambiar solo
la ventana no demuestra que el modelo maneje una entrada larga. El CSV registra
los tokens de entrada efectivos. Las tarjetas añadidas son pendientes futuras,
por lo que el total y el porcentaje esperado se recalculan.

Si el prompt más la reserva de salida alcanza la ventana, el resultado se marca
como no comparable y obtiene cero puntos. Evita subir la cantidad de tarjetas
sin revisar ese aviso: Ollama puede truncar entradas que no caben.

## Comparar el razonamiento

Para comprobar si el razonamiento mejora la precisión de Qwen3.5 4B:

```sh
python3 pruebas/benchmark.py run --models qwen3.5:4b-q4_K_M --thinking on --context 8192 --max-tokens 2048 --repeats 1 --cases seguimiento revision_codigo
```

El límite de salida incluye los tokens de razonamiento. El CSV mide también el
tiempo hasta la primera respuesta visible; `tokens_s` incluye todo lo generado.
Si se agota el límite antes de completar el JSON, se conserva el resultado con
su advertencia. Esta prueba es independiente de la comparación principal con
razonamiento desactivado; no mezcles sus latencias o puntos en una misma tabla.

## Resultados e interpretación

Cada ejecución crea una carpeta en `resultados/` con fecha y hora de Guayaquil:

- `resumen.md`: comparación de latencia, velocidad, formato y aciertos.
- `resultados.csv`: mediciones por caso y repetición.
- `respuestas.jsonl`: respuestas completas, puntuación, muestras de memoria y temperatura,
  métricas de la API y asignación de memoria del modelo en `/api/ps`.
- `entorno.json`, `configuracion.json`, `casos.json`: entorno y condiciones de la ejecución.
- Archivos por modelo: detalles, cuantización y calentamiento separado.

`tokens_s` se calcula como tokens generados / duración de generación de la API.
También se mide el tiempo al primer texto de respuesta y la latencia completa.
La VRAM de `nvidia-smi` y la RAM usada corresponden a toda la GPU y el sistema,
no solo al proceso. Las muestras de un segundo pueden omitir picos breves.
Una respuesta válida en JSON todavía puede ser incorrecta: se valida el esquema
y se puntúan campos concretos contra resultados conocidos.

La puntuación no evalúa por sí sola la calidad de recomendaciones y correcciones.
Lee las respuestas y revisa claridad, evidencia, información inventada y falsos
positivos. Cuatro casos y repeticiones con temperatura cero sirven como primera
comparación local; no son una certificación ni una evaluación estadística amplia.

## Prototipo funcional del agente (`agente_gestion`)

Implementación del pipeline de análisis semanal modular y determinista según [ARCHITECTURA.v1.md](ARCHITECTURA.v1.md).

### Ruta rápida (Quick Path)

1. **Ejecutar demo simulada determinista (sin consumo de LLM real):**
   ```bash
   PYTHONPATH=src python3 -m agente_gestion run fixture_1_standard --fake --fixture fixture_1_standard
   ```
2. **Registrar decisión del gestor humano sobre el reporte:**
   ```bash
   PYTHONPATH=src python3 -m agente_gestion decide <RUN_ID> --decision approved --reason "Aprobado en revisión técnica"
   ```
3. **Ejecutar todas las pruebas deterministas:**
   ```bash
   PYTHONPATH=src python3 -m unittest discover -s tests -v
   python3 -m unittest discover -s pruebas -v
   ```

### Requisitos e instalación

1. **Entorno virtual e instalación del paquete:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```

2. **Inicio del runtime local de Ollama (opcional para modo real):**
   ```bash
   bash pruebas/ollama-local.sh serve
   ```
   El script inicia Ollama local escuchando en `http://127.0.0.1:11435`.

- **Python:** 3.10+ (con `pydantic`, `pydantic-settings`, `pyyaml`, `requests`).
- **Ollama local (Opcional para modo real):** escuchando en `http://127.0.0.1:11435`.
- **Modelos recomendados:** `qwen3.5:2b-q4_K_M` (monitoreo y reporte) y `qwen3.5:4b-q4_K_M` (calidad de código).
- **Gemini remoto (Verificación independiente):** requiere variable `GEMINI_API_KEY` y flag explícito `--allow-remote-ai`. No existe fallback automático a Ollama para verificación: si hay hallazgos que escalan y el verificador remoto no está habilitado o no cuenta con credenciales, la etapa pasa a `needs_attention` y los hallazgos permanecen pendientes (nunca se inventan veredictos ni se confirma automáticamente).

### Opciones de ejecución CLI

| Comando / Opción | Descripción |
|---|---|
| `python -m agente_gestion run <PROYECTO>` | Ejecución del pipeline (por defecto utiliza `proyecto-demo` y fixture configurable `fixture_1_standard`). |
| `--fake` | Modo simulado determinista con `FakeProvider`. No requiere servicios ni claves; simula el flujo E2E completo listo para decisión humana. |
| `--fixture <NOMBRE>` | Carga datos desde `fixtures/<NOMBRE>.json` preservando la identidad del proyecto solicitado. |
| `--allow-remote-ai` | Autoriza llamadas al verificador remoto (Gemini). |
| `--ollama-base-url <URL>` | URL personalizada para el runtime de Ollama. |
| `--db-path <PATH>` | Ruta del archivo SQLite (por defecto `.runtime/agente.db`). |
| `--output-dir <PATH>` | Carpeta de artefactos (por defecto `runs/`). |
| `--env-file <PATH>` | Ruta a archivo `.env` personalizado (por defecto lee `.env`). |
| `python -m agente_gestion decide <RUN_ID>` | Registra la decisión humana (`approved`, `rejected`, `changes_requested`, `postponed`). |

### Configuración de entorno y precedencia de fuentes

El proyecto utiliza un módulo centralizado de configuración (`Settings`) basado en `pydantic-settings`.

1. **Copiar plantilla de variables de entorno:**
   ```bash
   cp .env.example .env
   ```
2. **Seguridad de credenciales:**
   - **Nunca commitee credenciales reales** ni archivos `.env` (el archivo `.gitignore` ya excluye `.env` y `.env.*`).
   - `GEMINI_API_KEY` se procesa como `SecretStr`, excluyéndose de representaciones (`repr`), dumps JSON y metadatos persistentes de corrida. Solo se revela al instanciar el adaptador de Gemini. Si la clave está ausente o vacía, se evalúa como `None` impidiendo bypass de seguridad remota.

#### Jerarquía de precedencia (de mayor a menor prioridad)

| Prioridad | Fuente | Descripción |
|---|---|---|
| **1 (Mayor)** | **Parámetros explícitos (CLI / overrides)** | Argumentos pasados al comando o diccionario `overrides`. |
| **2** | **Variables de entorno del SO (OS)** | Variables exportadas en el shell; sobrescriben `.env` (incluso `ALLOW_REMOTE_AI=false`). |
| **3** | **Archivo `.env`** | Valores leídos de `.env` (o archivo indicado vía `--env-file`). |
| **4** | **Archivo YAML (`config/config.yaml`)** | Configuración base estructurada del proyecto. |
| **5 (Menor)** | **Valores por defecto de `Settings`** | Valores predeterminados seguros definidos en el código. |

### Variables de entorno soportadas

- `ALLOW_REMOTE_AI`: Habilitación estricta de proveedores remotos (`false` o `true`, por defecto `false`).
- `OLLAMA_BASE_URL`: URL base de Ollama (por defecto `http://127.0.0.1:11435`).
- `GEMINI_API_KEY`: Clave de API de Google Gemini (requerida únicamente para verificación remota en modo real).
- `GEMINI_MODEL`: Modelo de Gemini (por defecto `gemini-2.5-flash`).
- `QUALITY_TIMEOUT_SECONDS`: Timeout para revisión de calidad en el agente revisor (por defecto `120.0` s).
- `VERIFIER_TIMEOUT_SECONDS`: Timeout para verificación independiente en el agente verificador (por defecto `120.0` s; configurable en `220.0` s si se desea conservar el presupuesto previo). Presupuesto independiente de `QUALITY_TIMEOUT_SECONDS`.
- `RUN_REAL_LLM_TESTS`: Establecer en `1` para ejecutar pruebas con modelos reales (opt-in).

### Limitaciones conocidas y comportamiento por defecto

- **Verificación sin credenciales remotas:** Por defecto (`allow_remote_ai: false` o sin `GEMINI_API_KEY`), cuando existen hallazgos de código que escalan (por ejemplo, categorías severas como inyección SQL o seguridad), la corrida finaliza con estado esperado `needs_attention`. No se sustituye localmente ni se asume veredicto favorable. Para una demostración determinista completa sin servicios externos, utilice la bandera `--fake`.
- **Tiempo de respuesta en calidad y verificación:** La revisión exhaustiva de diffs en modelos locales de 4B y la verificación en modelos remotos disponen de presupuestos de tiempo independientes y acotados por defecto a 120 segundos (`quality_timeout_seconds: 120.0` y `verifier_timeout_seconds: 120.0`). Si se desea conservar el presupuesto previo de verificación, se puede sobrescribir vía `VERIFIER_TIMEOUT_SECONDS=220`.

### Políticas de calidad locales (`config/policies.yaml`) y frontera de fuentes

- **Formato del archivo de políticas:** Archivo YAML estructurado bajo la clave raíz `policies: [...]`. Cada regla requiere de forma estricta los campos `id`, `description`, `metric_key`, `operator` (`==`, `!=`, `<=`, `>=`, `<`, `>`, `is_true`, `is_false`), `threshold`, `source` (`ci`, `review`, etc.) y `mandatory` (booleano).
- **Semántica de cobertura faltante:** Si la ejecución de CI no suministra métricas requeridas por una regla obligatoria (por ejemplo, `coverage_percentage` en la regla `Q2`), la evaluación de dicha regla queda en estado `unknown`, resultando deterministamente en la recomendación `evidencia_insuficiente`. No se deduce cobertura favorable a partir de la finalización de un workflow sin datos.
- **Frontera de recolección y errores sanitizados:** El contrato de puertos (`PolicySourcePort`, `SourceCollectionPort`, `SourceExecutionRecord`) audita la recolección por fuente con consistencia estricta de completitud (un snapshot completo no puede contener registros fallidos). Toda falla en adaptadores se normaliza mediante `SourceError` suprimiendo excepciones previas (`raise ... from None`). Para enums válidos, sus mensajes y representaciones derivan canónicamente de `provider` y `category`, impidiendo la propagación de diagnósticos de transporte, URLs o secretos en los mensajes de excepción o en `SourceErrorInfo`, mientras que los marcos de ejecución estándar de Python permanecen en el traceback.
- **Transporte HTTP acotado para fuentes externas (`BoundedGetTransport`):**
  - **Protocolo inyectable (`HttpGetTransportPort`):** Define el método único de lectura `get(url: str, params: Optional[dict[str, Any]] = None, headers: Optional[dict[str, str]] = None, timeout_seconds: Optional[float] = None) -> HttpResponse`. Retorna `HttpResponse` inmutable con `status_code`, `body` (JSON decodificado como `dict` o `list`), `headers` filtrados por allowlist seguro (`link`, `retry-after`, `x-ratelimit-*`, `ratelimit-*`, `content-type`, `etag`), `bytes_received` y `attempt_count=1`. Los valores de cabeceras como `link` se consideran metadatos externos de transporte; los adaptadores descendientes deben validar de forma estricta las URLs de paginación y evitar persistir cabeceras ciegamente. No almacena payloads binarios redundantes ni objetos de request con credenciales.
  - **Orígenes y preflight de seguridad:** Requiere estrictamente esquema HTTPS y orígenes fijos (`api.trello.com` para Trello; `api.github.com` para GitHub y GitHub Actions). `local_policy` no admite transporte HTTP. Se rechazan userinfo en URL, puertos distintos a 443 y fragmentos antes de cualquier llamada de red suprimiendo contexto de excepciones previas (`from None`). Las redirecciones se desactivan (`allow_redirects=False`) y cualquier respuesta 3xx se rechaza como error de red para evitar propagación inadvertida de credenciales. La sesión se aísla con `trust_env=False`.
  - **Ciclo de vida y limpieza de recursos (best-effort):** La creación de la sesión y la configuración de `trust_env` están contenidas dentro de bloques protegidos. Tanto `response.close()` como `session.close()` se intentan en todas las rutas de salida; los fallos secundarios durante la limpieza son suprimidos para nunca sobreescribir el resultado exitoso ni enmascarar la categoría canónica primaria de `SourceError`.
  - **Intento único, límite de bytes y decodificación JSON segura:** Ejecuta exactamente 1 intento por llamada (sin reintentos automáticos ni sleep en el transporte). Transmite la respuesta por fragmentos (`stream=True`) verificando el tamaño acumulado contra `max_response_bytes` (por defecto 2 MiB), sin confiar ciegamente en `Content-Length`. La decodificación JSON captura `ValueError` (incluyendo límites de dígitos de enteros grandes) y `RecursionError` clasificándolos deterministamente como `malformed_payload`.
  - **Limitaciones conocidas de timeout:** El timeout de `requests` aplica a la inactividad de socket (conexión y lectura entre bytes), no a un plazo de reloj estricto (wall-clock total). Se incluye una verificación de presupuesto transcurrido entre fragmentos de streaming para mitigar transferencias lentas ("slow trickle"), y se desempaqueta la excepción `ReadTimeoutError` de `urllib3` encapsulada en `requests.ConnectionError` para clasificarla como `timeout`. No obstante, no se utiliza infraestructura de hilos o procesos para abortar lecturas de socket bloqueadas a nivel de sistema operativo, por lo que el timeout residual de inactividad de socket actúa como límite por lectura. Los adaptadores posteriores (T2b/T3/T4) acotarán la paginación y el presupuesto agregado de peticiones.
- **Adaptador de lectura de Trello (`TrelloSourceAdapter`):**
  - **Firma y contrato:** Implementa `TaskSourcePort` con el método `get_tasks(project_id: str) -> list[TaskCard]` y el accesor aditivo compatible de evidencias `get_evidence(project_id: str) -> list[EvidenceItem]`.
  - **Seguridad e higiene de credenciales:** Las credenciales (`api_key`, `token`) y el `board_id` se validan estrictamente contra caracteres de control, saltos de línea (`\r`, `\n`), comillas y caracteres no permitidos (`^[a-zA-Z0-9_\-]+$`), previniendo inyección de rutas o cabeceras HTTP. La autenticación se envía únicamente mediante cabecera `Authorization: OAuth oauth_consumer_key="...", oauth_token="..."` (nunca en query params ni URLs). Cualquier secreto configurado que aparezca en identificadores entrantes (`id`, `idBoard`, `idList`, `idMembers`) es rechazado deterministamente como `malformed_payload` sin redactar identificadores para evitar colisiones; secretos en `board_id` o `project_id` se rechazan como error de configuración antes de emitir llamadas HTTP o poblar la caché. Textos libres (títulos, descripciones) redactan automáticamente secretos configurados (`[REDACTED]`), y `repr(adapter)` oculta las credenciales.
  - **Validación estricta de esquemas:** Exige tipos booleanos estrictos (`bool` no `int`), rechaza duplicados de listas o tarjetas, verifica la correspondencia del `idBoard`, y rechaza tarjetas que apunten a un `idList` inexistente en el tablero (`malformed_payload`) sin inferir nombres arbitrarios. Las fechas (`due`, `dateLastActivity`) requieren formato ISO 8601 estricto con zona horaria o Z, convirtiéndose a `America/Guayaquil`; las fechas "naive" sin zona horaria son rechazadas deterministamente.
  - **Mapeo de estados y contradicciones:** Aplica `TaskMappingConfig` a las listas del tablero (`pending_list_ids`, `in_progress_list_ids`, `completed_list_ids`), asignando `TaskState.unknown` a listas no mapeadas. Si una tarjeta está en una lista completada pero contiene etiquetas de bloqueo (`blocked_labels`), se marca con `has_contradiction=True`. En listas pendientes o en curso, la etiqueta de bloqueo asigna `TaskState.blocked` sin contradicción.
  - **Segregación de métricas y preservación de evidencia:** Las tarjetas archivadas (`closed=True`), las tarjetas en listas archivadas (`closed=True`) o aquellas fuera de `included_list_ids` se marcan con `is_included=False`, excluyéndose del denominador de avance de las métricas deterministas pero conservándose íntegramente en `get_tasks` y `get_evidence` con URLs canónicas seguras del recurso oficial de API (`https://api.trello.com/1/cards/{id}`).
  - **Ciclo de vida y caché acotada:** Realiza exactamente dos peticiones GET (`/1/boards/{board_id}/lists/all` y `/1/boards/{board_id}/cards/all`) por ciclo. La colección normalizada se almacena en una caché acotada por instancia (máximo 20 entradas) y publica tareas y evidencias de forma atómica solo tras validar completamente ambas respuestas; los fallos no dejan estado parcial en memoria. Las llamadas retornan copias profundas aislando la caché contra mutaciones externas. Admite invalidación explícita (`invalidate_cache(project_id)`) y recarga (`refresh(project_id)`). Respuestas con 1000 o más tarjetas o que superen los límites configurados fallan conservadoramente con `limit_exceeded`.
- **Selección de fuentes en CLI:** La selección y composición de fuentes externas reales (Trello, GitHub, GitHub Actions) desde el CLI aún no está implementada (diferida a etapas posteriores del MVP). Actualmente el CLI utiliza fixtures simuladas (`--fake` / `--fixture`).

### Ubicación de artefactos y persistencia

Cada ejecución persiste su trazabilidad completa en SQLite (`.runtime/agente.db`) y genera los siguientes archivos en `runs/<RUN_ID>/`:
- `snapshot.json`: Snapshot de datos de entrada (tareas, diffs, CI y políticas).
- `metrics.json`: Métricas deterministas calculadas por el motor del backend.
- `monitoring.json`: Borrador redactado por el Elaborador (`ProjectMonitoringAgent`).
- `code_findings.json`: Hallazgos candidatos del Revisor (`CodeQualityAgent`).
- `verification.json`: Verificaciones independientes del Verificador (`VerifierAgent`).
- `report.json`: Reporte estructurado final.
- `report.md`: Reporte legible en Markdown (en español).
- `execution_metrics.json`: Métricas de ejecución, modelos, errores y latencias.

En caso de fallo, se guardan artefactos parciales de diagnóstico sin presentar el reporte como completo.

### Alcance: Implementado vs Futuro

| Componente | Estado Prototipo | Futuro / Producción |
|---|---|---|
| **Orquestador** | Secuencial síncrono modular con reanudación acotada e idempotencia por hash | Worker distribuido / Celery / Temporal |
| **Fuentes de datos** | Adaptador de fixtures normalizadas JSON (`fixtures/`) | Conectores REST en vivo a Trello y GitHub |
| **Persistencia** | SQLite con transacciones atómicas y archivos locales en `runs/` | PostgreSQL / Blob Storage |
| **Modelos de IA** | FakeProvider determinista + Ollama local + Gemini opt-in | Adaptadores adicionales (OpenAI Responses, Anthropic) |
| **Aprobación** | Decisión humana registrada por CLI | UI Web interactiva con panel para el gestor |
| **Límites** | Presupuesto compartido de 8 llamadas y 1 intento de reparación | Cuotas dinámicas configurables por organización |

## Comprobación del comparador

```sh
python3 -m unittest discover -s pruebas -p 'test_*.py' -v
```

## Fuentes oficiales

- [Qwen3.5 2B Q4_K_M](https://ollama.com/library/qwen3.5:2b-q4_K_M)
- [Qwen3 4B Instruct 2507 Q4_K_M](https://ollama.com/library/qwen3:4b-instruct-2507-q4_K_M)
- [Qwen3.5 4B Q4_K_M](https://ollama.com/library/qwen3.5:4b-q4_K_M)
- [Instalación de Ollama en Linux](https://docs.ollama.com/linux)
- [Release Ollama 0.35.1](https://github.com/ollama/ollama/releases/tag/v0.35.1)
- [API de chat y formato estructurado](https://docs.ollama.com/api/chat)
- [Métricas de uso](https://docs.ollama.com/api/usage)
- [Modelos cargados y memoria asignada](https://docs.ollama.com/api/ps)
- [Concurrencia y contexto](https://docs.ollama.com/faq)
- [Soporte de GPU](https://docs.ollama.com/gpu)
- [Ajustes de generación Qwen3.5](https://huggingface.co/Qwen/Qwen3.5-4B#best-practices)
- [Ajustes de generación Qwen3 Instruct](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507#best-practices)
