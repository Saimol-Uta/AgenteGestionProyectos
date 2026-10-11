# Asistente multiagente para seguimiento y calidad de proyectos de software

**Estado al 10 de octubre de 2026:** MVP modular en Python implementado, con
adaptadores de solo lectura para Trello, GitHub y GitHub Actions, métricas
deterministas, ContextBuilder con control de evidencia, agentes especializados,
SQLite, CLI y proveedor de revisión de código configurable entre Ollama, Groq y
Mistral. La decisión final sigue siendo humana.

- **T8 (fuentes reales):** validado con HORMIGESTION (Trello 31 tarjetas,
  GitHub 6 elementos, GitHub Actions 0 ejecuciones).
- **T9 (E2E real):** se generó un informe en una corrida, pero aparecieron
  un falso positivo por evidencia parcial y un 503 de Gemini. El grounding,
  la selección por rol y Quality híbrido se corrigieron offline; **falta
  repetir y validar T9 con Groq/Mistral real**.
- **Pruebas automáticas (H2B2a, offline):** 552 ejecutadas, 550 aprobadas,
  2 omitidas opt-in y 7 comprobaciones del evaluador aprobadas (no benchmarks
  de rendimiento). No certifican funcionamiento de las APIs en vivo.

[Diseño de referencia](ARCHITECTURA.md) ·
[arquitectura implementada (línea base + actualización)](docs/arquitectura-implementada.md) ·
[registro de validación inicial y evolución](docs/mvp-01-validation.md) ·
[matriz REQ-01…REQ-11 y plan de implementación](docs/requisitos-y-plan-de-implementacion.md).

## Historial de planificación (H1D2, offline)

Consulta una base existente sin Settings, proveedores, artefactos ni migraciones:

```sh
.venv/bin/python -m agente_gestion history list PROJECT --db-path DB
.venv/bin/python -m agente_gestion history compare PROJECT PREVIOUS_CAPTURE CURRENT_CAPTURE --db-path DB
```

`list` devuelve JSON de metadatos y conteo de tareas, no código ni evidencia
completa. Copia sus `capture_id` para `compare`: un `snapshot_id` puede
reutilizarse para varias versiones; el `capture_id` identifica el JSON exacto
por SHA-256. Comparar una captura consigo misma produce cero cambios.
Los deltas conservan before/after, fechas y advertencias; no prueban operaciones,
eliminación física ni actores. La evidencia por run no se asocia arbitrariamente
a capturas. Base ausente/legada, corrupción o comparación incompatible fallan
con stderr y código no cero, sin crear ni migrar datos.

Ejemplo sintético reproducible: `planning_example()` en
`tests/test_cli_history.py` persiste A/B en bases temporales y comprueba los
cuatro cambios (responsable, fecha límite, prioridad, estado). Sin base real ni
APIs; T9 sigue **PENDING**. Scheduler, UI y acciones externas siguen pendientes.

## Consulta local de propuestas (H2B2a, solo lectura)

H2A aporta el contrato validado; H2B1 persiste propuestas y evidencia de apoyo
congelada. H2B2a consulta ese catálogo existente sin crear/migrar datos ni cargar
Settings, proveedores, orquestador o artefactos:

```sh
.venv/bin/python -m agente_gestion proposals list PROJECT --db-path DB
.venv/bin/python -m agente_gestion proposals show PROJECT PROPOSAL_ID --db-path DB
```

`list` devuelve IDs, versión, proyecto/tarea/captura, campo, estados y
`supporting_evidence_count`. `show` añade valores actual/propuesto, motivo,
impacto e IDs de evidencia. Las fechas conservan su representación JSON;
null, listas y estado desconocido no se sustituyen por hechos inventados.
Nunca se exportan DTOs de apoyo, payloads, snippets, resúmenes, URLs de fuentes
ni snapshots completos. Los campos propios de la propuesta sí se muestran:
la proyección no es un sanitizador de texto libre.

Base ausente, catálogo legado o registros inválidos fallan con error estático
por stderr y código 1, sin crear archivos ni migrar; propuesta ausente o de otro
proyecto devuelve el mismo error. Los estados siguen `pending`,
`not_authorized`, `not_executed`: no hay envío/importación, revisión, transición,
actor, autorización ni ejecución. La integridad comprobada no autentica autoría
ni prueba asociación de evidencia a captura. Pruebas con SQLite temporal en
`tests/test_cli_proposals.py`; no se abrió la base real y T9 sigue **PENDING**.

## Cumplimiento académico y continuidad

La materia exige tres roles: **Ejecutor → Revisor → Aprobador**. En la
presentación se explicará su **equivalencia funcional con la arquitectura
existente**, sin modificar código únicamente para cambiar nombres o dividir
componentes:

- **Agente padre / coordinador:** `ProjectOrchestrator` coordina las etapas
  de software; no es por sí mismo un agente LLM autónomo.
- **Ejecutor de análisis:** `ProjectMonitoringAgent` y los módulos que recogen
  y calculan información objetiva del proyecto.
- **Revisor:** `CodeQualityAgent` y validación determinista de evidencia.
  `VerifierAgent`, llamado condicionalmente por `EscalationGate`, actúa como
  **verificación auxiliar independiente**.
- **Aprobador consultivo:** `ReportAgent` sintetiza una recomendación
  restringida por políticas. **No otorga autorización de escritura**.
- **Autoridad final:** el gestor humano registra su decisión con la CLI y
  SQLite. El **ejecutor de acciones externas autorizadas aún no existe**.

**La decisión de no refactorizar aplica solo a la presentación/mapeo de
roles.** No es un recorte del alcance: se implementarán todos los requisitos
REQ-01 a REQ-11 del documento del docente **y** las funcionalidades
adicionales propuestas que resulten justificadas. REQ-08 permanece **parcial**
hasta completar revisión de propuestas, autorización humana y ejecución
controlada de cambios. Consultar la
[matriz de cumplimiento y roadmap](docs/requisitos-y-plan-de-implementacion.md)
para evidencias, brechas y prioridades.

La sección siguiente conserva el **banco histórico de benchmarks locales**
que precedió al MVP. Consulta [INFORME_PRUEBAS.md](INFORME_PRUEBAS.md)
para las mediciones originales de Ollama; no se deben interpretar como
mediciones actuales de Groq/Mistral.

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

El pipeline implementado mantiene una arquitectura monolítica modular. La
[revisión 2](ARCHITECTURA.md) es el **diseño de referencia**, mientras que
[docs/arquitectura-implementada.md](docs/arquitectura-implementada.md)
distingue el as-built inicial de los cambios posteriores. La
[revisión v1](ARCHITECTURA.v1.md) se conserva solo como antecedente histórico.

### Ruta rápida (Quick Path)

1. **Ejecutar demo simulada determinista (sin consumo de LLM real):**
   ```bash
   PYTHONPATH=src .venv/bin/python -m agente_gestion run fixture_1_standard --fake --fixture fixture_1_standard
   ```
2. **Registrar decisión del gestor humano sobre el reporte:**
   ```bash
   PYTHONPATH=src .venv/bin/python -m agente_gestion decide <RUN_ID> --decision approved --reason "Aprobado en revisión técnica"
   ```
3. **Ejecutar todas las pruebas deterministas:**
   ```bash
   PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
   .venv/bin/python -m unittest discover -s pruebas -p 'test_*.py' -v
   ```

### Historial SQLite desde Python (H1D1, validado offline)

```python
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter

# Solo lectura: exige un archivo existente; no crea ni migra la base.
history = SQLiteStorageAdapter("ruta/a/base-existente.db", read_only=True)
captures = history.list_snapshot_captures("mi-proyecto")
if captures:
    capture = history.get_snapshot_capture(captures[0].capture_id)
    snapshot = capture.snapshot  # WeeklySnapshot, reutilizable por el comparador
```

El escritor predeterminado conserva `save_snapshot(snapshot, content_hash=None)`
y `get_snapshot(id)` (última versión), y añade capturas inmutables: SHA-256 del
JSON exacto UTF-8, independiente del hash aportado por el llamador. Un replay
idéntico no duplica filas; otro JSON con el mismo ID sí crea otra captura.
Las consultas devuelven valores nuevos, validan hash/metadatos y ordenan por
`captured_at` UTC (fechas sin zona: Guayaquil), después por ID de captura.

La apertura escritora migra únicamente snapshots retenidos, conservando su JSON
original y runs, con DDL/índice/backfill transaccionales. **Versiones sobrescritas
antes de esta migración son irrecuperables**. Una base antigua abierta en solo
lectura permite `get_snapshot`, pero las consultas de historial lanzan
`NotImplementedError`; una base ausente falla sin crear archivos. Corrupción
lanza `ValueError`; SHA-256 detecta inconsistencias, no autentica al autor.
H1D2 añadirá la CLI list/compare; este backend no ejecuta acciones externas.

### Comparación de tareas entre capturas (H1C, offline)

```python
from agente_gestion.domain.task_comparison import compare_task_snapshots

# Dos WeeklySnapshot del mismo proyecto, ordenados por captured_at.
comparison = compare_task_snapshots(
    previous, current,
    previous_evidence=previous_task_evidence, current_evidence=current_task_evidence,
)
for change in comparison.changes:
    print(change.task_id, change.kind, change.field, change.before, change.after)
```

API pura: conserva tarjetas excluidas, compara campos observados en ambas
capturas y devuelve IDs estables y referencias de evidencia por lado. Nulos y
listas vacías explícitos representan limpieza; campos ausentes no son cambios.
La evidencia usa claves normalizadas y se asocia por ID, proyecto y categoría
`task`, nunca por título. En tarjetas antiguas sin `observed_fields`, solo el
título es seguro sin evidencia adicional; no se comparan defaults opcionales.
`task_source_identity` identifica fuente **y versión de mapeo**;
`tasks_complete` describe solo la enumeración de tareas, no el estado de CI.
Ambos son desconocidos (`None`) en capturas antiguas y generan advertencias.
H1E conecta esta API con Trello/factory/composite mediante tests mock offline:
`state` y `list_name` son observaciones derivadas con IDs de evidencia.
La identidad es SHA-256 de JSON canónico del tablero, estrategia y mapeo/alcance
incluido; no contiene credenciales, nombres de listas ni fechas de captura.
La colección de tareas exitosa, incluso vacía, marca `tasks_complete=True` aunque
falte CI; su fallo marca `False`. Fuentes desconocidas mantienen identidad `None`.
En `date_fields`, el estado puede cambiar solo por el tiempo de captura: no prueba
una operación API ni quién actuó. Cambiar tablero, estrategia o mapeo conocido
impide comparar esas capturas; la prioridad nunca se deduce de etiquetas.
`first_observed`/`not_observed` no significan creación/eliminación física;
`actor` siempre es `None`. H1D añadirá historial persistido y consulta CLI:
esta API no almacena, autoriza ni ejecuta acciones externas.

### Requisitos e instalación

1. **Entorno virtual e instalación del paquete:**
   ```bash
   python3 -m venv .venv
   # En Fish: source .venv/bin/activate.fish
   .venv/bin/python -m pip install -e .
   ```

2. **Inicio del runtime local de Ollama (opcional para modo real):**
   ```bash
   bash pruebas/ollama-local.sh serve
   ```
   El script inicia Ollama local escuchando en `http://127.0.0.1:11435`.

- **Python:** 3.10+ (con `pydantic`, `pydantic-settings`, `pyyaml`, `requests`).
- **Ollama local (modo híbrido real):** requerido para MonitoringAgent y ReportAgent; escucha en `http://127.0.0.1:11435` con el script de este repositorio.
- **Modelos locales:** `qwen3.5:2b-q4_K_M` para monitoreo y reporte; `qwen3.5:4b-q4_K_M` disponible para Quality si se selecciona Ollama.
- **Quality remoto:** se puede elegir `groq` o `mistral` con su clave/modelo y permiso explícito para datos remotos. El adapter está implementado y probado offline; **la primera corrida real híbrida continúa pendiente**.
- **Gemini remoto:** verificación independiente condicional. Requiere `GEMINI_API_KEY` y autorización remota. Si no está disponible, los findings que escalan permanecen pendientes y la ejecución puede marcar `needs_attention`, sin fabricar veredictos.

### Opciones de ejecución CLI

| Comando / Opción | Descripción |
|---|---|
| `python -m agente_gestion run <PROYECTO>` | Ejecución del pipeline (por defecto utiliza `proyecto-demo` y fixtures locales). |
| `--sources <fixtures\|real>` | Selecciona el modo de fuentes (`fixtures` o `real`). Requiere configuración explícita en modo `real`. |
| `--commit <FULL_SHA>` | Fija el commit SHA objetivo exacto (40 o 64 hex) para recolección de código y verificación de CI. |
| `--branch <BRANCH>` | Rama de GitHub para resolver el commit objetivo si `--commit` no se suministra. |
| `--config <PATH>` | Ruta al archivo de configuración YAML (ej. `config/config.yaml`). |
| `--fake` | Modo simulado determinista con `FakeProvider` para LLM. No requiere servicios de IA; simula el flujo E2E completo. En modo `--sources real`, las fuentes externas siguen activas a menos que se inyecte transporte simulado. |
| `--fixture <NOMBRE>` | Carga datos desde `fixtures/<NOMBRE>.json` (modo fixtures). Incompatible con `--sources real`. |
| `--allow-remote-ai` | Autoriza el uso de proveedores remotos configurados (Quality Groq/Mistral y/o Verifier Gemini); revisar política de datos antes de usar repositorios privados. |
| `--quality-provider <ollama\|groq\|mistral>` | Selecciona explícitamente el proveedor de CodeQualityAgent. |
| `--ollama-base-url <URL>` | URL personalizada para el runtime de Ollama. |
| `--db-path <PATH>` | Ruta del archivo SQLite (por defecto `.runtime/agente.db`). |
| `--output-dir <PATH>` | Carpeta de artefactos (por defecto `runs/`). |
| `--env-file <PATH>` | Ruta a archivo `.env` personalizado (por defecto lee `.env`). |
| `python -m agente_gestion source-check <FUENTE>` | Diagnóstico de solo lectura (`trello`, `github`, `github-actions`, `local-policy`, `all`). |
| `python -m agente_gestion decide <RUN_ID>` | Registra la decisión humana (`approved`, `rejected`, `changes_requested`, `postponed`). |

### Vencidas y próximas a vencer (H1B, validado offline)

```bash
PYTHONPATH=src .venv/bin/python -m agente_gestion run --fake --fixture fixture_1_standard --due-soon-days 7
```

`due_soon_days` acepta enteros no negativos (por defecto **7**): CLI
`--due-soon-days` > entorno `DUE_SOON_DAYS` > `.env` > YAML
`execution.due_soon_days` > valor por defecto. `0` incluye solo el instante del corte.
El backend usa el corte capturado del snapshot, normalizado en `America/Guayaquil`
por defecto (fechas sin zona se interpretan en la zona configurada):

- **Vencida:** `due < cutoff`.
- **Próxima a vencer:** `cutoff <= due <= cutoff + timedelta(days=N)` (ambos extremos incluidos).

Ambas señales requieren tarjeta incluida, no archivada, estado pendiente/en curso/bloqueada,
fecha de vencimiento y `due_complete` distinto de `True`. No cambian el estado base,
el denominador del avance ni las contradicciones. El reporte muestra IDs calculados
por backend; Monitoring debe conservar su evidencia o fallar por presupuesto, no omitirla.
REQ-02/04 mejoran offline, pero siguen parciales; historial, dependencias e impacto quedan pendientes.

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
- `QUALITY_PROVIDER`: `ollama` (valor por defecto), `groq` o `mistral`; también configurable mediante `--quality-provider`.
- `GROQ_API_KEY`, `GROQ_MODEL`: credencial y modelo para Quality con Groq.
- `MISTRAL_API_KEY`, `MISTRAL_MODEL`: credencial y modelo para Quality con Mistral.
- `QUALITY_CONTEXT_WINDOW`, `QUALITY_MAX_OUTPUT_TOKENS`, `QUALITY_CONTEXT_MARGIN`: presupuesto configurable de Quality, con reserva de salida y margen. Debe respetar las capacidades del modelo remoto seleccionado.
- `QUALITY_TIMEOUT_SECONDS`: timeout del Revisor (por defecto `120.0` s); el piloto local necesitó un valor superior para Qwen 4B.
- `VERIFIER_TIMEOUT_SECONDS`: Timeout para verificación independiente en el agente verificador (por defecto `120.0` s; configurable en `220.0` s si se desea conservar el presupuesto previo). Presupuesto independiente de `QUALITY_TIMEOUT_SECONDS`.
- `TRELLO_STATE_STRATEGY`: Estrategia de determinación de estado para Trello (`list_mapping` por defecto, o `date_fields`).
- `RUN_REAL_LLM_TESTS`: Establecer en `1` para ejecutar pruebas con modelos reales (opt-in).

### Limitaciones conocidas y comportamiento por defecto

- **Verificación sin credenciales remotas:** Por defecto (`allow_remote_ai: false` o sin `GEMINI_API_KEY`), cuando existen hallazgos de código que escalan (por ejemplo, categorías severas como inyección SQL o seguridad), la corrida finaliza con estado esperado `needs_attention`. No se sustituye localmente ni se asume veredicto favorable. Para una demostración determinista completa sin servicios externos, utilice la bandera `--fake`.
- **Contexto y proveedores:** ContextBuilder selecciona evidencia relevante por rol, conserva alcance y procedencia de snippets/diffs y rechaza el truncamiento silencioso. Groq/Mistral tienen un presupuesto de Quality configurable, distinto del límite local de Qwen.
- **Límites operativos:** los timeouts de Quality y Verifier son independientes (por defecto 120 s cada uno). El piloto local requirió `QUALITY_TIMEOUT_SECONDS=300`; no extrapolar esa medición como requisito para Groq/Mistral.
- **Fuentes incompletas:** una API que devuelve 0 runs de CI puede haber respondido correctamente, pero la ausencia de métricas de pruebas/cobertura mantiene reglas obligatorias en `unknown`.

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
  - **Estrategias de determinación de estado (`state_strategy`):** El constructor de `TrelloSourceAdapter` admite el parámetro `state_strategy` (por defecto `'list_mapping'`, alternativa `'date_fields'`; cualquier valor no reconocido eleva `SourceError` de configuración). En `'list_mapping'` se mantiene la semántica histórica de listas y etiquetas. En `'date_fields'`, el estado se deriva de los campos de fecha y completitud de la tarjeta: `dueComplete == True` determina `completed` (con `has_contradiction=True` si coexiste con etiqueta de bloqueo o con fecha `start` futura frente al instante de recolección UTC); `dueComplete == False` con etiqueta de bloqueo determina `blocked`; `dueComplete == False` con fecha de inicio consciente (`start`) posterior a la recolección determina `pending`, o menor o igual determina `in_progress`; ante ausencia de `dueComplete` o inicio no especificado se asigna `unknown` sin inferir estados. Los identificadores de lista actúan como fase de proceso y el filtro de pertenencia `included_list_ids` permanece activo sin requerir mapeo de tres grupos de estado. Los valores originales y normalizados `due_complete`, `start_date` y `start_raw` se preservan en la carga útil de `EvidenceItem`. No se introducen estados adicionales de vencimiento (`overdue`) en el dominio de tareas.
  - **Segregación de métricas y preservación de evidencia:** Las tarjetas archivadas (`closed=True`), las tarjetas en listas archivadas (`closed=True`) o aquellas fuera de `included_list_ids` se marcan con `is_included=False`, excluyéndose del denominador de avance de las métricas deterministas pero conservándose íntegramente en `get_tasks` y `get_evidence` con URLs canónicas seguras del recurso oficial de API (`https://api.trello.com/1/cards/{id}`).
  - **Ciclo de vida y caché acotada:** Realiza exactamente dos peticiones GET (`/1/boards/{board_id}/lists/all` y `/1/boards/{board_id}/cards/all`) por ciclo. La colección normalizada se almacena en una caché acotada por instancia (máximo 20 entradas) y publica tareas y evidencias de forma atómica solo tras validar completamente ambas respuestas; los fallos no dejan estado parcial en memoria. Las llamadas retornan copias profundas aislando la caché contra mutaciones externas. Admite invalidación explícita (`invalidate_cache(project_id)`) y recarga (`refresh(project_id)`). Respuestas con 1000 o más tarjetas o que superen los límites configurados fallan conservadoramente con `limit_exceeded`.
- **Adaptador de lectura de GitHub (`GitHubCodeSourceAdapter`):**
  - **Firma y contrato:** Implementa `CodeSourcePort` con el método `get_diffs(project_id: str, commit_sha: Optional[str] = None) -> list[CodeDiffSnippet]` y el accesor aditivo compatible de evidencias `get_evidence(project_id: str, commit_sha: Optional[str] = None) -> list[EvidenceItem]`. Expone propiedades de estado de recolección: `last_resolved_commit_sha`, `last_collection_complete` y `unavailable_file_paths`.
  - **Alcance acotado a un commit fijado (pinned commit):** Si se suministra `commit_sha`, se exige un hash hexadecimal estricto de 40 o 64 caracteres (rechazando SHAs abreviados o nombres de rama). Si `commit_sha` se omite, resuelve la punta de la rama configurada (`branch`) o consulta `GET /repos/{owner}/{repo}` para obtener `default_branch` y resolver su commit una sola vez. El SHA devuelto por la primera página queda fijado (`pinned_sha`) y se reutiliza de forma determinista en todas las páginas subsiguientes; cualquier discordancia de SHA entre páginas se rechaza como `malformed_payload`.
  - **Paginación determinista y presupuestos de red:** Utiliza `per_page=100` y páginas secuenciales con URLs sintetizadas a partir del `pinned_sha`. Las cabeceras `Link` se validan estrictamente (origen HTTPS `api.github.com`, rutas bajo `/repos/` o `/repositories/`, parámetros `page` y `per_page`, sin fragmentos, credenciales ni consultas externas). Se aplican presupuestos máximos configurables de archivos (máximo 3000 por techo del proveedor), páginas (máximo 30), peticiones HTTP (máximo 50) y bytes acumulados (por defecto 10 MiB); superar cualquier límite eleva `limit_exceeded`. Páginas intermedias vacías o archivos repetidos elevan `malformed_payload`.
  - **Seguridad e higiene de credenciales:** El token opcional se envía exclusivamente en la cabecera `Authorization: Bearer <token>` (nunca en URLs ni parámetros). Se validan rutas seguras (sin transversales `..`, absolutas o caracteres de control). Si el token configurado aparece en `owner`, `repo`, `branch` o `project_id`, se rechaza con error de configuración; si aparece en rutas, metadatos, o en el texto del diff o código fuente remoto, se rechaza deterministamente como `malformed_payload` (nunca se redacta código fuente en texto falso para evitar inventar sintaxis). `repr(adapter)` no expone el token.
  - **Parser estricto de parches unificados y anclaje de líneas:** Procesa parches unificados (`patch`) validando encabezados de hunk (`@@ -old,len +new,len @@`), contadores de líneas y marcadores `\ No newline at end of file`. Omite líneas eliminadas (`-`) de los snippets de lado `NEW`, mapeando líneas agregadas (`+`) y de contexto estrictamente a los números de línea reales (base 1) del archivo nuevo, sin generar números contiguos falsos a través de saltos entre hunks. Rechaza diffs combinados (`@@@`) y parches malformados o no crecientes.
  - **Semántica de archivos eliminados y parches no disponibles:** Archivos con `status == "removed"` no generan snippets `NEW` pero conservan evidencia normalizada con `reason="file_removed"` y `patch_available=True`. Archivos sin parche (`patch=None`, por ser binarios o diffs que exceden el tamaño de GitHub) no inventan snippets sintéticos; se registran en la evidencia con `patch_available=False` y `reason="patch_unavailable"`, marcando `last_collection_complete=False` y registrando su ruta en `unavailable_file_paths`. Commits vacíos legítimos retornan lista vacía de snippets y preservan evidencia del commit y SHA fijado.
  - **Ciclo de vida y caché local atómica:** Publica la colección en una caché acotada en memoria (máximo 20 entradas) únicamente tras validar con éxito todos los archivos, parches y páginas; ante cualquier fallo no se publica estado parcial ni se actualiza `last_resolved_commit_sha`. Las consultas retornan copias profundas inmutables y sincronizan el estado público de metadatos (SHA resuelto, completitud y rutas no disponibles) en cada acierto de caché. Admite invalidación (`invalidate_cache`) y refresco (`refresh`).
- **Adaptador de lectura de GitHub Actions (`GitHubActionsSourceAdapter`):**
  - **Firma y contrato:** Implementa `CISourcePort` con el método `get_ci_results(project_id: str, commit_sha: Optional[str] = None) -> list[CIRunResult]` y el accesor aditivo compatible de evidencias `get_evidence(project_id: str, commit_sha: Optional[str] = None) -> list[EvidenceItem]`. Expone propiedades de estado de recolección: `last_target_commit_sha`, `last_resolved_commit_sha` y `last_collection_complete`.
  - **Exigencia de commit fijado (target commit SHA):** Requiere un commit SHA completo (hash hexadecimal estricto de 40 o 64 caracteres) mediante `commit_sha` en la llamada o fijado vía constructor (`commit_sha`/`target_commit_sha`, validando independientemente ambos alias y rechazando contradicciones). Si se omite o es inválido, eleva deterministamente `SourceError(configuration)` antes de tocar la red; nunca infiere ramas flotantes, `HEAD` ni el último commit de CI.
  - **Normalización honesta de métricas y semántica de calidad:** La API de metadatos de Actions no expone recuentos detallados de pruebas ni cobertura. Por contrato, `total_tests`, `failed_tests` y `coverage_percentage` se asignan siempre a `None` (incluso ante workflow exitoso `conclusion="success"`); el timestamp de completitud del dominio se asigna a `None` (las fechas de actualización no constituyen prueba de conclusión). Al evaluarse contra políticas obligatorias (`Q1` pruebas fallidas <= 0 y `Q2` cobertura >= 80), ambas reglas resultan en estado `unknown` y recomendación determinista `evidencia_insuficiente`.
  - **Preservación de discordancias de SHA y orden determinista:** Conserva íntegramente el `head_sha` real devuelto por la API en `CIRunResult.commit_sha`, sin sobreescribir ni descartar ejecuciones discordantes como si coincidieran; la función de dominio `match_ci_for_commit` garantiza que solo ejecuciones con coincidencia exacta certifiquen el commit bajo análisis. Las ejecuciones se ordenan de más reciente a más antigua por `updated_at` (utilizando el ID numérico positivo de corrida como desempate determinista).
  - **Normalización de estados y procedencia de evidencias:** `CIRunResult.status` normaliza la conclusión (`conclusion` si está presente) o el estado (`status`). La procedencia de evidencias bajo `EvidenceSource.github_actions` preserva tanto `status` como `conclusion`, `head_branch`, `event`, marcas temporales ISO en UTC (`updated_at`, y las opcionales `created_at`, `run_started_at` donde valores nulos son permitidos pero tipos falsey no válidos como `False` o `0` elevan `malformed_payload`), el SHA objetivo solicitado frente al devuelto, la bandera `is_target_match` y la URL canónica documentada de API (`https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}`).
  - **Paginación determinista, techos y validación de cabeceras:** Consulta `GET /repos/{owner}/{repo}/actions/runs` con parámetros `head_sha=<pinned_sha>`, `per_page=100` y páginas secuenciales. Si `total_count` es >= 1000 (techo documentado de GitHub para consultas filtradas) o supera presupuestos configurados de corridas (por defecto 1000), páginas (máximo 10), peticiones HTTP (máximo 50), bytes acumulados (por defecto 10 MiB) o longitud de textos variables (`max_text_length`), eleva `limit_exceeded`. Las cabeceras `Link` se validan estrictamente; páginas terminales que alcancen el recuento o con total 0 que incluyan `rel="next"` contradictorio, cambios de `total_count`, páginas intermedias vacías o IDs duplicados elevan `malformed_payload`.
  - **Seguridad e higiene de credenciales:** El token opcional se transmite únicamente mediante cabecera `Authorization: Bearer <token>`. Si el token aparece en identificadores (incluyendo `workflow_id` o cadenas numéricas), nombres, textos o marcas temporales antes y después de su normalización ISO, la carga se rechaza de forma conservadora como `malformed_payload`, sin redactar identificadores para evitar colisiones. `repr(adapter)` no expone el token.
  - **Ciclo de vida y caché local atómica:** Almacena colecciones en memoria por tupla `(project_id, target_sha)` (máximo 20 entradas), publicándolas de forma atómica solo tras validar completamente todas las corridas y páginas. Las consultas retornan copias profundas inmutables y sincronizan los metadatos de la última recolección en cada acierto de caché. Admite invalidación (`invalidate_cache`) y refresco (`refresh`).
- **Adaptador de composición normalizada de fuentes (`CompositeSourcesAdapter`):**
  - **Firma y contrato:** Implementa `SourceCollectionPort` con el método `collect_snapshot(project_id: str, cutoff_datetime: datetime, commit_sha: Optional[str] = None) -> SourceCollectionResult` y el accesor compatible `get_evidence(project_id: str, commit_sha: Optional[str] = None) -> list[EvidenceItem]`. Expone propiedades de estado `last_resolved_commit_sha` y `last_collection_complete`.
  - **Orquestación acotada y guardia de dependencias de CI:** Ejecuta una secuencia acotada: políticas locales y tareas/evidencias de Trello de forma independiente; GitHub resuelve y fija un único commit SHA (`pinned_sha`); finalmente GitHub Actions recibe exactamente dicho SHA fijado sin consultas flotantes ni ramas no fijadas. Si la recolección de código en GitHub falla o no valida completamente su unidad (por parches no disponibles, detección de secretos o fallos en su evidencia), la consulta de CI se omite de forma estricta (0 llamadas a la API de CI) sin registrar intentos de CI ni duraciones ficticias, y `last_resolved_commit_sha` no se publica.
  - **Semántica honesta de disponibilidad y errores:** Colecciones legítimamente vacías (tablero vacío, commit sin cambios) se preservan con estado exitoso. Ante fallas de recuperación se dejan contenedores vacíos con significado de datos NO DISPONIBLES (no un cero conocido), marcando `WeeklySnapshot.is_complete = False` con `incomplete_reason` explicativo estático. Si no existen ejecuciones de CI que coincidan exactamente con el SHA objetivo fijado (lista vacía o discordancias legítimas de head SHA), el registro de ejecución de CI refleja éxito de lectura pero el snapshot se marca con `is_complete = False` por falta de evidencia de CI para el objetivo fijado.
  - **Registro de auditoría por proveedor (`SourceExecutionRecord`):** Cada proveedor efectivamente invocado genera exactamente un registro con `provider`, `status`, `duration_seconds` medido con `time.perf_counter()` y `attempt_count=1`. Errores sanitizados se normalizan canónicamente mediante `SourceErrorInfo`, sin incluir diagnósticos de red crudos, credenciales ni rutas de archivos.
  - **Semántica temporal (recolección actual vs corte solicitado):** Exige `cutoff_datetime` con zona horaria estricta (rechazando fechas "naive" antes de invocar cualquier fuente). El snapshot retiene el `cutoff_datetime` solicitado y registra el instante real de captura en `captured_at` (UTC). No realiza reconstrucción histórica ni retrofechado de entidades de Trello.
  - **Consistencia y desduplicación sin pérdida de evidencias:** Desduplica identificadores de evidencia preservando deterministamente la totalidad de ítems (nunca descarta evidencias legítimas). Ante colisiones entre proveedores o con identificadores que ya poseen prefijos naturales, reasigna espacios de nombres y ordinales estables sin inventar entidades sintéticas. La evidencia de cada corrida de CI debe concordar con el SHA real devuelto por dicha corrida; discordancias o evidencias huérfanas elevan `malformed_payload` con reversión atómica de la unidad de CI.
  - **Ciclo de vida e inmutabilidad (diseño por corrida):** Diseñado para una ejecución por corrida. Consultas repetidas con parámetros idénticos devuelven copias profundas del resultado congelado sin re-consultar adaptadores; peticiones con parámetros diferentes sobre una misma instancia se rechazan con `ValueError` para evitar re-envolver cachés obsoletas. Todo resultado retorna copias profundas (`model_copy(deep=True)`).
  - **Higiene de credenciales cruzadas:** Admite `known_secret_values` opcional para prevenir que secretos conocidos aparezcan en la configuración del proyecto o en entidades normalizadas antes de su publicación. `repr(adapter)` oculta credenciales.
- **Configuración y fábrica de fuentes de datos (`Settings` & `factory.py` - T5b):**
  - **Fábrica fresca por invocación:** `create_task_source`, `create_code_source`, `create_ci_source`, `create_policy_source`, y `create_composite_source` generan instancias nuevas e independientes con transporte acotado inyectable. Las credenciales conocidas (`gemini_api_key`, `trello_api_key`, `trello_token`, `github_token`) se resguardan como `SecretStr` y se transfieren privadamente a la frontera de composición sin registro en logs ni cadenas `repr`.
  - **Estrategia de estado de Trello (`trello_state_strategy`):** Cableada desde la configuración (`TRELLO_STATE_STRATEGY` en variables de entorno/dotenv o en YAML bajo `sources.trello.state_strategy` o plano `trello_state_strategy`) hacia `create_task_source`. Admite `'list_mapping'` (por defecto, mapea listas a grupos de estado) o `'date_fields'` (para tableros por fases donde el estado se determina por `dueComplete`, fecha `start` consciente frente al instante UTC de recolección y etiquetas de bloqueo, sin requerir grupos de IDs de listas; `included_list_ids` actúa como filtro opcional de fases). Valores inválidos, vacíos o no-strings se rechazan de forma segura al cargar la configuración con código de salida `2` sin exponer datos sin sanitizar.
  - **Validación estricta y previa a HTTP:** Los alias de repositorio (`github_repo` / `github_repository`) y rama (`github_branch` / `github_ref`) son reconciliados; si se especifican valores contradictorios, fallan con error de configuración estático antes de cualquier llamada HTTP. El commit SHA exige exactamente 40 o 64 caracteres hexadecimales completos.
  - **Requisitos por proveedor:** La verificación de un proveedor individual requiere únicamente la configuración de dicho proveedor (p. ej., chequear Trello no exige variables de GitHub).
- **Subcomando de diagnóstico de fuentes (`source-check` - T5b):**
  - **Sintaxis:** `python -m agente_gestion source-check <trello|github|github-actions|all|local-policy> [--sources fixtures|real] [--commit <FULL_SHA>] [--branch <BRANCH>] [--project <ID>] [--config <PATH>]`
  - **Metadatos permitidos exclusivamente:** Ejecuta operaciones estrictamente de solo lectura y formatea una tabla resumen con: `Provider`, `Status` (`success`, `failed`, `incomplete`, `blocked`), `Count` (o `unknown`), `Target/Actual SHA`, `Duration` (s) y `Error` canónico (`none`, `configuration`, `network`, `timeout`, `malformed_payload`, `limit_exceeded`, `dependency_blocked`).
  - **Seguridad e integridad:** Nunca imprime tarjetas, descripciones, parches, URLs con parámetros, cabeceras ni credenciales. No instancia clientes de LLM ni bases de datos SQLite.
  - **Comportamiento en modo `all`:** Si la recolección de código falla o no resuelve un SHA, la comprobación de GitHub Actions se marca honestamente como `dependency_blocked` con duración `0.000` s en lugar de inventar peticiones flotantes; los proveedores independientes continúan su diagnóstico. El código de salida es `0` solo si todos los proveedores seleccionados completan exitosamente sin fallos ni estados incompletos.
- **Integración de fuentes reales en pipeline de ejecución (`run` - T5c):**
  - **Ejecución en modo real:** Se activa explícitamente mediante `--sources real` o configurando `sources.mode: real` en el archivo YAML de configuración. En este modo, el orquestador consulta las fuentes externas reales (Trello, GitHub, GitHub Actions) a través de la fábrica de adaptadores acotados (`CompositeSourcesAdapter`). Si falta configuración obligatoria para algún proveedor (por ejemplo, credenciales o tablero de Trello), la ejecución falla de forma temprana con código de salida `2` y error estático canónico, sin intentar llamadas de red ni inicializar el pipeline.
  - **Distinción entre `--fake` y `--sources`:** La bandera `--fake` sustituye únicamente los modelos de lenguaje (LLM) por `FakeProvider` determinista en memoria. En cambio, `--sources real` continuará llamando a las APIs reales externas a menos que se inyecte un transporte simulado en pruebas unitarias; no debe presentarse como un modo offline. Para una ejecución completamente offline y desacoplada de la red, utilice el modo por defecto de fixtures (`--sources fixtures` o `--fixture <NOMBRE>`).
  - **Fijación de commit (target commit SHA) y propagación:** En modo real, se puede fijar el commit objetivo mediante `--commit <FULL_SHA>` (hash hexadecimal de 40 o 64 caracteres) o `github_commit_sha` en la configuración. Si no se especifica, el adaptador de GitHub consulta la rama configurada (`--branch` o `default_branch`) una sola vez y fija el SHA devuelto (`pinned_sha`). Dicho SHA objetivo fijado se transfiere rigurosamente a la consulta y cotejo de CI (`match_ci_for_commit`) incluso si el commit de código contiene 0 archivos modificados (diffs vacíos).
  - **Semántica temporal y recolección actual:** La recolección en modo real captura el estado actual disponible de las entidades externas en el instante de ejecución (`captured_at` en UTC); no constituye una reconstrucción histórica retroactiva ni retrofechado de estados previos al corte (`cutoff_datetime`).
  - **Manejo de colecciones incompletas y ausencia de fallback:** Si alguna fuente externa falla o no devuelve datos completos (por ejemplo, error en Trello, diff sin parche o discordancia de SHA en CI), el pipeline **no recurre jamás a fixtures de respaldo**. El snapshot se marca honestamente como incompleto (`is_complete = False`) con su motivo estático (`incomplete_reason`). Los conteos de elementos no disponibles no deben interpretarse como un "cero saludable", sino como información no recuperada.
  - **Compuerta determinista de disponibilidad (`QualityEvaluator`):** Cuando el snapshot es incompleto, el evaluador de calidad registra la regla interna `Q_SOURCE_AVAILABILITY_INCOMPLETE` en estado desconocido (`unknown`), impidiendo cualquier recomendación de aprobación (`aprobado`) y determinando `evidencia_insuficiente`. Si existen fallos de políticas obligatorias (por ejemplo, pruebas fallidas conocidas), la precedencia de `solicitar_cambios` se preserva estrictamente y nunca queda oculta por la indisponibilidad de fuentes.
  - **Semántica de CI y cobertura desconocida:** Dado que la API de GitHub Actions no provee recuentos de pruebas unitarias ni porcentaje de cobertura, los valores se normalizan como `None`. Al evaluarse contra políticas obligatorias de pruebas y cobertura, estas reglas resultan en estado `unknown`, conduciendo a `evidencia_insuficiente` aun cuando el workflow figure como exitoso (`conclusion: "success"`).
  - **Auditoría y persistencia de fuentes:** La recolección de fuentes reales persiste la totalidad de evidencias normalizadas en la tabla SQLite `evidence` y registra la etapa `source_collection` en `stage_results` con su duración real (`perf_counter`), estado, registros individuales por proveedor (`SourceExecutionRecord`) y SHA objetivo fijado. Esta misma estructura de auditoría se plasma en `execution_metrics.json` bajo la clave `source_collection`.
  - **Métricas de auditoría y precisión de mediciones (T6):** Si la unidad de recolección de código falla (por transporte, formato o detección de secretos en parches o SHA), el SHA objetivo fijado se omite honestamente (`target_commit_sha: null / None`) en la etapa y en `execution_metrics.json`. El campo `attempt_count` en cada `SourceExecutionRecord` representa 1 intento para la unidad de fuente del proveedor en su conjunto (no peticiones HTTP individuales ni páginas paginadas). La duración total de la etapa reporta la suma de las duraciones medidas de cada unidad individual, no el tiempo exacto de reloj de pared (*wall-clock*, del cual se excluye la sobrecarga interna del orquestador); estas cifras reflejan mediciones locales del arnés y no constituyen afirmaciones de rendimiento en producción ni latencia en la nube.
  - **Decisión humana y estado final:** El reporte final generado siempre queda con decisión humana pendiente (`human_decision: pending`, estado de corrida `awaiting_human`), requiriendo la acción explícita del gestor mediante el subcomando `decide`.
  - **Compatibilidad hacia atrás y conflictos CLI:** La bandera explícita `--fixture <NOMBRE>` tiene precedencia sobre configuraciones de modo real para permitir pruebas locales continuas. Sin embargo, especificar simultáneamente `--sources real` y `--fixture <NOMBRE>` en la línea de comandos se rechaza inmediatamente por contradicción explícita.
  - **Estado de verificación al 10/10/2026:** T8 se validó contra Trello/GitHub/Actions reales; T9 produjo al menos un reporte real con `needs_attention` y reveló problemas de grounding/contexto corregidos después mediante tests offline. Falta una ejecución real completa con Quality remoto; ver [registro de validación](docs/mvp-01-validation.md).


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
| **Fuentes de datos** | Fixtures y adaptadores de solo lectura Trello, GitHub y GitHub Actions; T8 real exitoso | Comparación histórica de snapshots y ampliación multi-repositorio |
| **Persistencia** | SQLite con transacciones atómicas y archivos locales en `runs/` | PostgreSQL / Blob Storage |
| **Modelos de IA** | FakeProvider, Ollama, Gemini, GroqProvider y MistralProvider. Quality es seleccionable | Fallback sofisticado entre proveedores y evaluación real multi-modelo |
| **Aprobación** | Decisión humana registrada por CLI | UI Web interactiva con panel para el gestor |
| **Límites** | Presupuesto de llamadas, reparación acotada, selección de contexto por rol y límites fail-closed | Cuotas dinámicas y políticas por organización |

### Próxima validación controlada (T9 híbrido)

Tras configurar `QUALITY_PROVIDER=groq` (o `--quality-provider groq`),
un `GROQ_MODEL` vigente y su clave **solo en el entorno local**, mantener
Ollama activo y comprobar primero el modo fake:

```fish
PYTHONPATH=src .venv/bin/python -m agente_gestion run fixture_1_standard --fake --fixture fixture_1_standard
PYTHONPATH=src .venv/bin/python -m agente_gestion source-check all --sources real
# Solamente cuando las fuentes y las políticas de envío remoto estén verificadas:
PYTHONPATH=src .venv/bin/python -m agente_gestion run hormigestion --sources real --allow-remote-ai --quality-provider groq
```

Esta secuencia es **instrucción pendiente**, no evidencia de una prueba real
híbrida ya completada. Evitar imprimir claves, subir `.env` o enviar
repositorios privados a un proveedor remoto sin autorización.

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
