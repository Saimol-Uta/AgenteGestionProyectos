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
| **REQ-01** Planificación general | Alta | **Parcial (mejorado offline)** | H1A amplía `TaskCard` y normalización Trello/fixtures con IDs de responsables, inicio, finalización de vencimiento, archivo, actividad y prioridad explícita; conserva campos observados y procedencia normalizada. Validación offline. | Exponer planificación general en vista o informe consumible; prioridad desconocida permanece `null` sin evidencia explícita, nunca se infiere de etiquetas. |
| **REQ-02** Estado y avance | Alta | **Parcial (mejorado offline)** | H1B añade próximas a vencer con ventana inclusiva configurable (7 días por defecto), separada del estado base; preserva avance y contradicciones. IDs deterministas visibles en reporte y contexto. | Exponer estados de cada tarea en una vista consultable completa; validar el seguimiento integrado en vivo. |
| **REQ-03** Trello y cambios | Alta | **Parcial (historial CLI offline)** | `TrelloSourceAdapter` y `source-check` funcionan en vivo; `date_fields` contempla tableros por fases. H1A normaliza metadatos; H1C compara cambios observados sin atribuir actor ni operaciones físicas. H1D1 conserva capturas SQLite inmutables; H1D2 lista metadatos y compara IDs explícitos por CLI de solo lectura, validados offline. | Validación integrada en vivo y ejecución Trello pendientes **solo** tras autorización auditada; historial y comparador no autorizan ni ejecutan. |
| **REQ-04** Retrasos y riesgos | Alta | **Parcial (mejorado offline)** | H1B detecta proximidad al vencimiento desde el corte capturado; excluye completadas, desconocidas, archivadas, excluidas y `due_complete=True` de ambos riesgos temporales. Monitoring y reporte usan métricas backend. | Causas, dependencias e impacto sobre otras actividades evaluados con evidencia; validación integrada en vivo. |
| **REQ-05** Acciones correctivas | Alta | **Parcial** | `suggested_actions` y recomendaciones textuales en informes. | Propuestas estructuradas de fechas, prioridad, responsable/estado, alternativas, revisión, aprobación, ejecución y resultado verificable. |
| **REQ-06** Reporte semanal | Media | **Parcial** | `ReportAgent` genera JSON/Markdown y métricas al invocar `run`. | Scheduler semanal, reporte por categoría/estado/desviación, historial de propuestas aprobadas/rechazadas/ejecutadas y distribución/notificación. |
| **REQ-07** Revisión de código | Alta | **Parcial avanzado** | Lector GitHub diffs con SHA, `CodeQualityAgent`, `SemanticValidator`, `VerifierAgent`, reglas Q1–Q3 y adapters híbridos. | Validar T9 híbrido en vivo, revisar cobertura real de errores/duplicaciones/mantenibilidad/vulnerabilidades y ejecutar análisis periódicos sin editar código automáticamente. |
| **REQ-08** Tres roles y autorización | Alta | **Parcial (mapeo documentado)** | Orquestador; Monitoring (ejecución analítica), Quality (revisión código), Verifier auxiliar, Report (síntesis consultiva), `decide` humano. | Revisión de propuestas de acción de gestión, autorización explícita sobre cada acción y ejecución posterior controlada; preservar autoridad humana. |
| **REQ-09** Interacción/gestión | Alta | **Parcial** | Reportes y `decide --decision approved|rejected|changes_requested|postponed`. | Notificaciones, presentación de riesgos y justificación por propuesta, solicitudes de autorización, cambios a propuesta antes de ejecutarla. |
| **REQ-10** Trazabilidad | Alta | **Parcial avanzado** | SQLite registra runs, stages, snapshots, evidence, findings, verifications, llm_calls, reports y human_decisions. | Registrar identidad/autorización del actor por operación y cambios externos efectivamente ejecutados con before/after, timestamp y evidencia. |
| **REQ-11** Seguridad | Alta | **Parcial** | `SecretStr`, `.env` ignorado, sanitización de entradas, operaciones de lectura y opt-in remoto. | Permisos por identidad/rol, protección integral del almacenamiento de credenciales, políticas de envío remoto por proyecto, auditoría y pruebas de autorización. |

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

**Salida pendiente del hito completo:** quedan vistas completas y riesgos justificables sin falsas inferencias de dependencias no registradas. REQ-03/10 siguen parciales por validación integrada en vivo y acciones/autorización/auditoría pendientes. El avance H2 comprende H2A, el catálogo H2B1 y la consulta de solo lectura H2B2a descritos abajo; scheduler y UI no están implementados. T9 permanece PENDING.

### Hito H2 — Propuestas, revisión, aprobación, ejecución segura (REQ-03/05/08/09/10/11)

Definir `ActionProposal` (acción, objetivo, valor anterior y propuesto, motivo, evidencia, impacto, autor); revisión de validez técnica y autorización separadas; petición al gestor, identidad/permiso, token de aprobación vinculado a versión, ejecutor Trello de lista permitida y verificación posterior de cambio; manejo idempotente de rechazos/duplicados y trazabilidad. **Nunca hacer escrituras sin autorización**. Mantener tres roles académicos y Verifier auxiliar; implementar nuevas funciones sin «recrear» agentes existentes.

**Avance H2A (dominio puro, offline):** `ActionProposal` versionada admite solo `start_date`, `due_date`, `priority`, `assignee_ids` y `state`, con valores actual/propuesto, motivo, impacto y IDs de evidencia. `validate_action_proposal` comprueba vínculo captura/hash/proyecto, tarea única, observación explícita, valor actual y evidencia de apoyo sin mutación. Conserva null/desconocido/vacío y rechaza coerciones, contradicciones, duplicados y cambios nulos. Las propuestas permanecen `pending`, `not_authorized`, `not_executed`; no hay integración, transición de revisión, actores autorizados ni ejecutor. Se presupone captura autoritativa desde almacenamiento verificado: no se rehashea el modelo legado. La evidencia pasada valida metadatos de tarea, **no demuestra pertenencia a la captura inmutable**. REQ-08 sigue PARCIAL y T9 PENDING.

**Avance H2B1/H2B2a (offline):** catálogo inmutable con digest del JSON exacto, evidencia de apoyo congelada y consultas que validan integridad/captura/hechos. `proposals list PROJECT --db-path DB` y `proposals show PROJECT PROPOSAL_ID --db-path DB` usan SQLite directo en solo lectura, sin Settings, composición, proveedores, artefactos ni migración. JSON por lista permitida: metadatos/conteo en list; campos propios conocidos de la propuesta en show, sin DTOs/payloads/snippets/resúmenes/URLs de apoyo ni snapshot completo. Ausencia/proyecto ajeno son indistinguibles en show; errores estáticos no exponen contenido privado. No hay transiciones, actores, autenticación, autorización ni ejecutor; la consistencia no prueba autoría ni pertenencia de evidencia a captura. REQ-08 sigue **PARCIAL**. Verificación actual: 552 tests ejecutados, 550 aprobados, 2 omitidos y 7 comprobaciones del evaluador (no rendimiento), solo bases temporales; base real no abierta y T9 **PENDING**.

**Salida pendiente H2B2b/H2C:** demo de una reprogramación propuesta, revisada, aprobada por gestor, ejecutada y auditada; rechazo demuestra cero escrituras. Antes de implementar autorización/ejecución deben resolverse identidad, permisos y política de revisión.

### Hito H3 — Reportería y notificación automática (REQ-04/06/09/10)

Scheduler semanal configurado y ejecución bajo entorno confiable; reportes que separen métricas, riesgos, pendientes, acciones propuestas/aprobadas/rechazadas/ejecutadas; notificaciones y entrega consultable con control de duplicados/errores.

**Salida:** evidencia de programación y entrega semanal, incluyendo fallos controlados.

### Hito H4 — Seguridad y calidad integral (REQ-07/10/11)

Autenticación y autorización por rol; seguridad de secretos en reposo/operación; política de transferencia a cloud; cobertura CI real (artefactos) y análisis de calidad más allá del diff puntual, con benchmark de falsos positivos/negativos.

**Salida:** tests de abuso, permisos y auditoría, revisión de findings reales y métricas CI.

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

