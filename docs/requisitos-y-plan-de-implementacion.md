# Matriz de requisitos académicos y plan de implementación

**Fuente de requisitos:** `AgenteIA.pdf`, «Agente inteligente para seguimiento y control de proyectos de desarrollo de software», UTA, ciclo julio–diciembre 2026, páginas 1–5 (especialmente lista de chequeo de las páginas 4–5).

**Corte de evaluación:** 10 de octubre de 2026. **Rama:** `mvp/real-sources`. **Estado:** evaluación documental contra código y resultados disponibles, **no certificación** ni nueva prueba ejecutada.

## 1. Decisión arquitectónica acordada: REQ-08 sin refactorización para presentar los roles

Conservamos el flujo real implementado; **no modificamos código únicamente para renombrar o reorganizar agentes con fines de presentación**. Documentamos su correspondencia funcional con los tres roles que pide la materia, distinguiendo código, abstracción académica y autoridad humana.

| Concepto académico | Componente existente | Lo que hace realmente | Límite |
|---|---|---|---|
| Coordinación / agente padre | `ProjectOrchestrator` | Ejecuta secuencialmente etapas, selecciona rutas y aplica condiciones | Es un **orquestador Python**, no un agente LLM autónomo ni un supervisor distribuido |
| Ejecutor | `ProjectMonitoringAgent` con adaptadores y motor determinista | Recibe hechos, interpreta tareas/avance y propone riesgos y acciones | No aplica todavía reprogramaciones en Trello ni edita código |
| Revisor | `CodeQualityAgent` y validadores deterministas | Analiza diffs, propone hallazgos y valida referencias de evidencia | Su revisión principal es **código**, no una revisión integral de toda acción de gestión |
| Subagente de apoyo del Revisor | `VerifierAgent` activado por `EscalationGate` | Verifica independientemente hallazgos relevantes cuando procede y hay proveedor habilitado | No se ejecuta siempre; el orquestador coordina la llamada, no CodeQuality directamente |
| Aprobador consultivo / síntesis | `ReportAgent` (rol interno histórico `aprobador`) + políticas deterministas | Consolida un informe y una recomendación que respeta las reglas calculadas | **No autoriza** por sí mismo escrituras ni sustituye la decisión humana |
| Autoridad final | Gestor humano, `decide` y `human_decisions` en SQLite | Puede aprobar/rechazar/solicitar cambios/posponer reportes, registrando motivo | No existe aún un ejecutor de acciones de Trello autorizado por esa decisión |

### Flujo real implementado hoy

```text
Fuentes → Orquestador + métricas → Monitoring (Ejecutor/seguimiento)
        → Quality (Revisor/código) → EscalationGate
        → Verifier (auxiliar, solo si corresponde)
        → Evaluación determinista → Report (síntesis «Aprobador» consultivo)
        → Informe y decisión del gestor humano (CLI/SQLite)
```

H2 añade el circuito de propuestas/revisión/decisión/simulación **solo offline**;
no extiende esta decisión sobre reportes a permisos reales (detalle del cierre abajo).

### Flujo completo que pide el docente y aún debemos terminar

```text
Análisis y propuesta estructurada (Ejecutor)
    → Validación técnica de la propuesta (Revisor)
    → Recomendación del Aprobador conforme a reglas
    → Autorización explícita del gestor
    → Ejecutor de acciones permitidas (Trello; código solo bajo control específico)
    → Verificación posterior y auditoría por actor
```

**Criterio de presentación:** el diseño satisface conceptualmente la especialización de tres roles y añade un verificador auxiliar, pero **REQ-08 sigue PARCIAL** hasta que las acciones puedan revisarse, autorizarse y ejecutarse siguiendo la secuencia exigida. No declarar la última flecha implementada. La correspondencia de roles es una **explicación de arquitectura**, no prueba de ejecución del flujo académico.

**Alcance de la decisión:** la prohibición de cambios de código aplica **solo** a reestructuraciones cosméticas de roles para REQ-08. **Sí debemos desarrollar** las funcionalidades ausentes de todos los requisitos del docente, además de las extensiones propuestas y justificadas para el proyecto. No renunciamos a ninguna.

## 2. Matriz REQ-01 a REQ-11

Los once requisitos provienen del documento universitario; las prioridades son **Alta** excepto REQ-06 (**Media**). Estado «Parcial» incluye componentes ya implementados, pero no el requisito íntegro. Ninguno se declara «No aplica».

| ID | Prioridad | Estado | Evidencia ya implementada | Falta para cumplimiento completo |
|---|---|---|---|---|
| **REQ-01** Planificación general | Alta | **Cumplido / Consultable** | `TaskCard` normaliza responsables, inicio, vencimiento, archivo, actividad y prioridad explícita (`null` si no está especificada, nunca inferida de etiquetas). CLI `tasks list/summary/show` expone la planificación general en vista tabular y JSON. | Validación de acciones externas pendientes hasta autorización auditada en H2. |
| **REQ-02** Estado y avance | Alta | **Cumplido / Consultable** | Estados normalizados (`pending`, `en_curso`, `completada`, `bloqueada`, `desconocido`), porcentaje de avance, próximas a vencer con ventana inclusiva configurable (`--due-soon-days`, default 7). CLI `tasks list` permite filtrar por estado (`--state`) y actividades críticas (`--critical`). | Seguimiento continuo de cambios por cron programado (H3). |
| **REQ-03** Trello y cambios | Alta | **Parcial (historial CLI offline)** | `TrelloSourceAdapter` y `source-check` funcionan en vivo; `date_fields` contempla tableros por fases. H1A normaliza metadatos; H1C compara cambios observados sin atribuir actor ni operaciones físicas. H1D1 conserva capturas SQLite inmutables; H1D2 lista metadatos y compara IDs explícitos por CLI de solo lectura, validados offline. | Validación integrada en vivo y ejecución Trello pendientes **solo** tras autorización auditada; historial y comparador no autorizan ni ejecutan. |
| **REQ-04** Retrasos y riesgos | Alta | **Cumplido / Consultable / Notificable** | Detección determinista de vencidas, próximas a vencer, bloqueadas y señales contradictorias. `tasks show/list` evalúa dependencias explícitas e impactos únicamente sobre evidencia real observada (sin inventar relaciones no registradas). Agregado y notificado en reporte semanal (`schedule run`). | Monitoreo continuo desatendido en background. |
| **REQ-05** Acciones correctivas | Alta | **Validación del flujo unificado en curso** | Ejecutor → Revisor automático → Aprobador humano → `/execute`, con motivo durable, tablero obligatorio, pre/post-read y protección de duplicados. Vías manuales anteriores retiradas. | Completar nueva evidencia local y revalidación acotada; sin proveedores reales para este cambio. |
| **REQ-06** Reporte semanal | Media | **Cumplido / Configurable** | `WeeklySchedulerService` con especificación semanal determinista (`WeeklySchedule`), corte por defecto viernes 17:00 `America/Guayaquil`, opción de ejecución inmediata forzada (`--force`) sin esperar al corte, agregación completa de métricas/riesgos/pendientes/acciones ejecutadas, y outbox de notificaciones idempotente con recibos JSON/Markdown. | Configuración de servicio daemon / cron a nivel de sistema operativo. |
| **REQ-07** Revisión de código | Alta | **Cumplido / Verificado** | Lector GitHub diffs con SHA, `CodeQualityAgent`, `SemanticValidator`, `VerifierAgent`, reglas Q1–Q3, adapters híbridos, y suite de benchmark integral de calidad (`quality benchmark`) con 0.00 % FPR en trampas de ausencia/alucinación y 100 % precisión. | Análisis periódico programado en background. |
| **REQ-08** Tres roles y autorización | Alta | **Cumplido / Verificado** | Roles de Elaborador (Monitoring), Revisor (Quality), Aprobador (Report) y Gestor Humano diferenciados. Validación estricta de actor autenticado (`ExecutionActor` y `SecurityPrincipal`), autorización ligada a hash/versión y ejecutor Trello live probado. | Integración con proveedor SSO o IAM corporativo (fuera de alcance MVP). |
| **REQ-09** Interacción/gestión | Alta | **Cumplido / Notificable** | Reportes, decisiones sobre informes, `proposals state/decide/dry-run/audit`, `tasks summary/list/show` y `schedule check/run/outbox` con trazabilidad completa de entrega y estados. | Panel web/UI visual complementario. |
| **REQ-10** Trazabilidad | Alta | **Cumplido / Auditado** | SQLite registra runs, stages, snapshots, evidence, findings, verifications, llm_calls, reports, human_decisions, snapshot_captures, action_proposals, proposal_decisions, proposal_dry_runs, proposal_executions, notification_outbox y security_audit_log con triggers append-only. | Auditoría externa en blockchain o log centralizado (no aplicable a entorno académico). |
| **REQ-11** Seguridad | Alta | **Cumplido / Auditado** | RBAC (`SystemRole`, `Permission`, `enforce_rbac`), `SecretStr`, tokens con fingerprint SHA-256 (nunca en texto plano), sanitización de payloads salientes (bloqueo de `.env` y llaves privadas), política de transferencia cloud por proyecto (`ProjectSecurityPolicy`, `assert_cloud_transfer_policy`), y tabla `security_audit_log` append-only. | Rotación automática de llaves criptográficas (fuera de alcance MVP). |

### Notas de evaluación

- **T8 está validada con fuentes reales**, pero 0 runs de CI no prueba cumplimiento de Q1/Q2. El sistema preserva `unknown` y `evidencia_insuficiente`.
- **T9 aún no se cierra**: una corrida produjo reporte, pero detectó un falso positivo por diff parcial y Gemini devolvió 503; las correcciones posteriores están probadas **offline**, no en otra corrida híbrida real.
- No confundir recomendaciones de texto con acciones ejecutables y autorizadas.
- No confundir `human_decisions` sobre un **reporte** con permisos/revisión/aprobación individual de acciones en Trello.
- Los componentes de ContextBuilder, seguridad y proveedores son infraestructura transversal que ayuda a varios REQ; no cambia el significado literal de la lista de chequeo.

## 3. Secuencia de implementación comprometida

### Hito H0 — Conservar y verificar MVP actual (T9)

**Objetivo:** completar una ejecución real controlada con Quality remoto (Groq o Mistral), Monitoring/Report locales y Gemini cuando escale. Inspeccionar `execution_metrics.json`, `monitoring.json`, `code_findings.json`, `verification.json` y `report.md`. Validar exactitud semántica contra SHA/código y estados Trello. Sin afirmaciones inventadas de cobertura ni verificación.

**Salida:** evidencia de corrida real, correcciones puntuales si son necesarias, trazabilidad del test. No añadir funcionalidades no relacionadas antes de este control.

### Hito H1 — Seguimiento completo y detección de cambios (REQ-01/02/03/04)

Normalizar prioridad/responsables/fechas, exponer planificación consultable y estados por tarea; definir «próxima a vencer»; comparar snapshots preservando fechas, actores y cambios; relacionar dependencias/impacto con evidencia. Tests de cálculo, mapeo y comparación temporal.

**Avance H1A (offline):** metadatos opcionales y `observed_fields` preservan compatibilidad con JSON antiguo y distinguen ausencia de valores explícitos nulos/vacíos. Se mantienen `list_mapping`, `date_fields`, contradicciones y exclusión de tarjetas cerradas del avance; no cambian prompts, métricas ni historial.

**Avance H1B (offline):** `due_soon_days` entero no negativo por Settings/YAML/entorno/CLI, incluido en el hash de configuración. Vencida: `due < cutoff`; próxima: `cutoff <= due <= cutoff + timedelta(days=N)` en la zona configurada (por defecto Guayaquil). `N=0` incluye exactamente el corte. Conserva estados y avance; exige evidencia de próximas a vencer sin aumentar presupuestos. Tests herméticos cubren límites, elegibilidad, configuración y salida Markdown; no certifican APIs ni inferencia en vivo.

**Avance H1C (offline):** `compare_task_snapshots` es una API pura y determinista con cambios tipados before/after, capturas y evidencia por ID/proyecto/categoría. Compara intersecciones observadas, conserva excluidas y normaliza offsets y conjuntos. Ausencia de tarjetas significa `first_observed`/`not_observed`, no creación/eliminación; archivo exige observación explícita y actor permanece desconocido. Identidad fuente/mapeo y completitud de tareas son opcionales, independientes de CI; su ausencia limita el alcance mediante advertencias. Tests sintéticos incluyen exactamente cuatro cambios de planificación. No modifica fuentes, políticas, almacenamiento ni CLI.

**Avance H1E (integración mock offline):** Trello observa `state` y `list_name` derivados en ambas estrategias, conservando claves y procedencia existentes. Factory identifica tablero/estrategia/mapeo y alcance incluido mediante SHA-256 de JSON canónico sin credenciales; cambios conocidos de identidad impiden comparación. Composite registra completitud solo de la colección de tareas (vacía exitosa: `True`; fallo: `False`), independiente de CI. Identidad de fuentes desconocidas y metadatos de capturas antiguas siguen desconocidos. `date_fields` puede variar con el tiempo de captura sin operación externa: no se atribuye actor ni se infiere prioridad de etiquetas. No certifica integración en vivo ni añade persistencia/CLI/acciones.

**Avance H1D1 (backend offline):** capturas SHA-256 del JSON exacto en SQLite, reutilizando `WeeklySnapshot`; replay idempotente y versiones distintas del mismo ID retenidas sin cambiar última versión ni runs. Backfill escritor transaccional conserva JSON legado; versiones previamente sobrescritas no se recuperan. Consultas verifican integridad y ordenan en UTC por proyecto; apertura de solo lectura no crea ni migra archivos. Pruebas exclusivamente con bases temporales, sin APIs ni migración de la base real.

**Avance H1D2 (CLI offline):** `history list/compare --db-path` consulta SQLite directamente con `read_only=True`, sin Settings/composición/proveedores/artefactos ni migraciones. Lista solo metadatos; compara IDs de captura explícitos (distintos de snapshot IDs reutilizables), conservando contexto, before/after y advertencias. Datos observados + hash permiten reproducir diferencias, no atribuir actores u operaciones; evidencia por run puede no estar disponible y no se inventa su asociación. Tests temporales cubren cuatro cambios exactos, comparación idéntica, errores sanitizados y no mutación.

**Salida pendiente del hito completo:** quedan vistas completas y riesgos justificables sin falsas inferencias de dependencias no registradas. REQ-03/10 siguen parciales por validación integrada en vivo y acciones/autorización/auditoría pendientes. H2 ya cerró su circuito exclusivamente offline, descrito abajo; scheduler y UI no están implementados. T9 permanece PENDING.

### Hito H2 — Flujo unificado de propuestas y ejecución segura (REQ-03/05/08/09/10/11)

El circuito vigente es **Ejecutor → Revisor técnico automático → Aprobador humano → ejecución protegida**. Las antiguas decisiones `approve_offline`, la autorización pública de dry-run y sus comandos/rutas separados se retiraron como vías operativas; no hay fallback local ni compatibilidad con registros de prueba incompletos.

`ActionProposal` y las funciones internas de evaluación/ledger siguen siendo invariantes del dominio usados por la revisión automática, no pasos públicos que el gestor deba encadenar. El endpoint unificado `/execute` exige decisión, motivo humano durable, sesión, alcance de proyecto, origen exacto, CSRF e idempotencia. El adaptador comprueba tablero, pre-lectura y post-lectura; prioridades se asignan mediante etiquetas configurables conservando las restantes.

Las decisiones de código exigen archivo fuente completo y validación sintáctica del resultado completo antes de crear una rama separada. La demostración se regenera con esquema vigente y al menos dos snapshots comparables; los datos previos quedan únicamente en una copia recuperable.

Las observaciones en vivo anteriores a este cambio son antecedentes, no instrucciones para reactivar el helper retirado ni prueba del flujo nuevo. No se ejecutan proveedores reales durante este reemplazo. La evidencia actual y los pendientes están en `odd/tasks/backend-fullstack-phase2.md` y la guía [Fase 2](phase2-fullstack.md).

### Hito H3 — Reportería y notificación automática (REQ-04/06/09/10) [Completado y Validado]

Scheduler semanal configurado y ejecución bajo entorno confiable; reportes que separen métricas, riesgos, pendientes, acciones propuestas/aprobadas/rechazadas/ejecutadas; notificaciones y entrega consultable con control de duplicados/errores.

- **Scheduler semanal configurado:** Reutiliza `WeeklySchedule` y `plan_weekly_window` con corte predeterminado los viernes a las 17:00 (`America/Guayaquil`) bajo regla `previous_scheduled_week`.
- **Ejecución forzada inmediata sin esperar al viernes:** Soporte de flag `--force` en `schedule run` y servicio `WeeklySchedulerService` para pruebas instantáneas y ejecuciones fuera de la ventana.
- **Agregación de reporte semanal integral:** Combina métricas de avance de tareas (`TaskPlanSummary`), actividades críticas y alertas temporales (`due_soon_days`), historial del ledger de ejecución de propuestas (`proposal_executions`) y dictamen de políticas.
- **Outbox de notificaciones híbrido e idempotente:** `HybridNotificationOutboxAdapter` entrega artefactos estructurados en `.runtime/outbox/` (archivos `.json` y `.md`) y asienta recibos en la tabla SQLite `notification_outbox` con control estricto de duplicados por ventana.
- **Comandos CLI integrados:**
  * `agente_gestion schedule check PROJECT`: inspección del estado de la ventana y próxima fecha de corte.
  * `agente_gestion schedule run PROJECT [--force]`: disparo programado o forzado inmediato del ciclo semanal con entrega al outbox.
  * `agente_gestion schedule outbox PROJECT`: consulta del historial de despachos y recibos de entrega.
- **Pruebas y regresión:** Suite completa de 721 pruebas ejecutadas (719 aprobadas, 2 omitidas) con cobertura unitaria en `tests/test_weekly_scheduler.py` y validación en vivo sobre HORMIGESTION.

### Hito H4 — Seguridad y calidad integral (REQ-07/10/11) [Completado y Validado]

Autenticación y autorización granular por rol (RBAC); seguridad de secretos en reposo y operación; política de transferencia a cloud fail-closed por proyecto; cobertura CI real (artefactos) y análisis de calidad más allá del diff puntual, con benchmark exhaustivo de falsos positivos/negativos.

- **Control de acceso basado en roles (RBAC) y autorización (REQ-08, REQ-11):** Mapeo formal de los roles académicos (`DEVELOPER`, `MONITORING`, `QUALITY`, `VERIFIER`, `REPORT`, `PROJECT_MANAGER`, `ADMIN`) a una matriz exhaustiva de permisos (`Permission`). `enforce_rbac` rechaza aserciones offline no autenticadas para operaciones privilegiadas (ejecución de mutaciones, decisiones de reporte, gestión de políticas).
- **Protección de secretos y política de transferencia Cloud (REQ-11):** Sanitización recursiva de payloads salientes (`assert_safe_remote_payload`), detección estricta de archivos `.env`, claves privadas y tokens API. Huellas digitales seguras (`sha256:...[:16]`) para auditoría y almacenamiento sin guardar secretos en texto plano. Políticas de soberanía de datos por proyecto (`ProjectSecurityPolicy`, `assert_cloud_transfer_policy`) con lista blanca de proveedores.
- **Auditoría inmutable de seguridad (REQ-10):** Registro append-only en SQLite (`security_audit_log`) con triggers de base de datos que impiden cualquier modificación o eliminación de eventos de acceso.
- **Benchmark integral de calidad y matriz de confusión (REQ-07):** Suite de evaluación con dataset de referencia ground-truth que mide 6 categorías de código (vulnerabilidades reales, trampas de diff parcial, inferencias indebidas por falta de CI, citas fuera de rango y código limpio). Demuestra deterministamente:
  * 0.00 % de tasa de falsos positivos (FPR) frente a trampas adversariales.
  * 100.00 % de precisión y sensibilidad (Recall).
  * 0.00 % de falsos negativos (FNR).
  * Aprobación del Quality Gate y verificación de reglas CI (Q1–Q3).
- **Comandos CLI integrados:**
  * `agente_gestion security check --actor-id ID --role ROLE --permission PERMISSION`: evaluación y verificación de permisos RBAC en tiempo real.
  * `agente_gestion security audit PROJECT`: consulta cronológica del registro inmutable de auditoría de seguridad.
  * `agente_gestion quality benchmark`: ejecución de la suite de benchmark y reporte tabular/JSON de métricas de calidad.
- **Pruebas y regresión:** Suite completa de 738 pruebas automatizadas (731 en `tests/` y 7 en `pruebas/`, todas aprobadas, 2 omitidas opt-in) con cero errores y `git diff --check` limpio.

## 4. Funcionalidades adicionales propuestas por el equipo (no sustituyen REQ)

| Extensión | Relación | Prioridad de planificación |
|---|---|---|
| **Multi-repositorio por proyecto** (backend, frontend y varios repos del mismo equipo) | Amplía REQ-07 e integración; el código actual apunta a un repo configurado | Después de H0; coordinar con H1/H4 |
| **Historial de snapshots y tendencias** | Necesario para REQ-03 y fortalece REQ-04 | Integrar en H1 |
| **ContextBuilder / grounding / fail-closed** | Control transversal de fiabilidad LLM, ya implementado parcialmente | Mantener y validar en H0–H4 |
| **Routing local/remoto por rol; Groq/Mistral/Gemini/Ollama** | Especialización y ajuste a hardware, ya implementado offline | Validar H0; optimizar después |
| **Calidad CI verificable y reglas configurables** | Fortalece REQ-07 y exactitud de aprobación | H4, sin falsear métricas |
| **Dashboard/UI y experiencia multi-proyecto** | Facilita REQ-01/09/06; no reemplaza aprobación ni permisos | Después de contratos H1/H2 |
| **Historial/auditoría avanzada y despliegue continuo** | Escalabilidad/observabilidad | Cuando el flujo mínimo esté probado |

## 5. Regla de trabajo y documentación

1. **Cumplir todos los REQ-01…REQ-11 del PDF**: una presentación arquitectónica no reemplaza la funcionalidad requerida.
2. **Conservar el código existente cuando sea correcto**. Solo evitar refactorizaciones cosméticas de los agentes para encajar etiquetas académicas; implementar lo realmente faltante.
3. **No convertir mejoras futuras en capacidades actuales**; separar «implementado», «validado offline», «validado real» y «pendiente».
4. **Mantener revisión humana obligatoria y no enviar secretos ni código privado no autorizado a proveedores remotos**.
5. **Cerrar hitos con evidencia reproducible**: tests, commits, runs y demostración del caso de uso; actualizar esta matriz al completar cada REQ.

**Referencias del repositorio:** [arquitectura de referencia](../ARCHITECTURA.md), [arquitectura implementada](arquitectura-implementada.md), [validación T8/T9](mvp-01-validation.md), [reglas de calidad](../config/policies.yaml), [orquestador](../src/agente_gestion/application/orchestrator.py), [agentes](../src/agente_gestion/agents/).

