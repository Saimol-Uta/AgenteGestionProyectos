# Claridad de gestión en Trello — Dashboard, Tareas y Detalle de tarea

Objetivo: que un gestor comprenda quién responde de una tarea, cuándo vence, por qué requiere atención, qué riesgos tiene, cuáles son sus acciones correctivas y qué cambios históricos se observaron, sin conocer los contratos técnicos del backend.
Rama: `feature/multirepo`. Entrega: commits locales separados (API aditiva, web, pruebas), sin push ni merge.
Runner: `.venv/bin/python -m unittest discover -s tests`; Playwright con API simulada. `npm run typecheck` y `npm run build` en `frontend/`.

## Specs

- S1. Responsables de Trello. El sistema no dispone de nombres de miembros: el adaptador Trello solo lee `idMembers`, la captura persistida guarda `assignee_ids` y la evidencia guarda `member_ids`. Se reutiliza lo disponible y se distinguen tres estados honestos: «Sin asignar» (la fuente observó la lista de responsables y está vacía), «No observado» (la fuente no informó ese campo) y «Nombre no disponible» (hay responsables pero el origen no expone sus nombres). Los identificadores técnicos no son la presentación principal; quedan en la información avanzada. No se inventa ningún nombre. Obtener nombres reales exige una lectura adicional de Trello que se explica al gestor antes de implementarse.
- S2. Actividades críticas y próximas a vencer. El backend calcula `is_critical` como la unión de «vencida», «próxima a vencer», «bloqueada» y «señales contradictorias»; ese cálculo y ese contrato se conservan. La presentación distingue visualmente cada señal real y deja de presentar la unión como una severidad propia: «Próxima a vencer» ya no aparece también como «Crítica». No se atribuye prioridad alta ni retraso sin datos que lo respalden.
- S3. Fechas planificadas. Para las tareas pendientes se muestran las fechas planificadas disponibles (inicio y vencimiento) como fechas, nunca como esfuerzo restante: la fuente no aporta una estimación y los días de calendario no se reinterpretan como esfuerzo.
- S4. Prioridad. Trello aporta prioridad solo si la tarjeta la expone o si hay un mapa de etiquetas de prioridad configurado. En la captura real de HORMIGESTION las 31 tarjetas tienen `priority` nulo y ninguna etiqueta, y no hay mapa de etiquetas configurado: la prioridad no está configurada ni puede determinarse. Se comunica ese estado con exactitud y no se infiere prioridad desde el vencimiento ni desde las etiquetas.
- S5. Información técnica innecesaria. La lista «Campos informed» de claves internas (`detail`, `is_archived`, `last_activity_at`, identificadores) desaparece del detalle principal. Esa información se conserva en un apartado avanzado de diagnóstico, con etiquetas legibles y los identificadores técnicos.
- S6. Clasificación de riesgos. Se sustituye el lenguaje técnico «Clasificación determinista» por «Alertas de la tarea» / «¿Por qué requiere atención?». Cada alerta presenta la condición observada, la fecha de referencia y el motivo concreto. Cuando un riesgo afecta a varias tareas, se muestran sus nombres y enlaces a sus detalles, no solo sus identificadores, y se distingue la alerta propia de la tarea del conjunto de tareas con la misma clasificación. La identidad y la trazabilidad de los riesgos de G2/G3 (`risk_id`, `kind`, `task_ids`, captura) no se alteran.
- S7. Evidencias de Trello. Ningún registro histórico se elimina. La evidencia se agrupa por tarjeta de origen y las observaciones se ordenan cronológicamente. Se presenta una fuente principal con los datos relevantes y un historial expandible. Solo se destacan como cambios comprobados los que el backend verificó entre capturas (estado, vencimiento, responsable u otros campos); una nueva captura sin diferencias comprobadas no se presenta como un cambio real.
- S8. Historial observado y comparación de capturas. Ambas secciones explican su finalidad y no duplican información. Se usan «Historial de cambios» para las diferencias verificadas entre capturas y «Cambios desde la última observación» para la comparación entre las dos capturas más recientes. Sin diferencias verificables se muestra una indicación sencilla; con datos insuficientes no se inventa un resultado.
- S9. Acciones correctivas. Se conserva la asociación tarea → riesgo → propuesta implementada en G3. Con propuesta existe, el detalle de la tarea ofrece un acceso claro a ella. Sin propuesta, se indica que no hay una propuesta vinculada y se reutiliza el diagnóstico persistido cuando permite explicar la causa. La interfaz no genera propuestas nuevas ni habilita reglas permanentes de HORMIGESTION.
- S10. La corrección es mínima: reutiliza componentes, esquemas y servicios existentes, mantiene el diseño responsive y la coherencia de la interfaz actual, no rediseña otras secciones, no crea pantallas nuevas, conserva la arquitectura, el pipeline, las reglas de autorización y la compatibilidad histórica.
- S11. Pruebas obligatorias: nombres y datos desconocidos, clasificación de alertas, navegación entre tareas, agrupación de evidencias, historial de cambios y propuestas vinculadas. Se ejecutan las pruebas focalizadas, la suite completa de Playwright, `typecheck` y `build`; si el backend cambia, también sus regresiones.

Restricciones vigentes: `EXTERNAL_WRITES_ENABLED=false`, sin llamadas LLM reales, sin escrituras Trello/GitHub, sin alterar bases ni capturas históricas, sin push y sin merge.

## Tasks

- T1 [S6] `RiskView` aditivo: títulos de las tareas afectadas y fecha de referencia de la captura, resueltos contra la captura. Sin alterar `risk_id`, `kind`, `task_ids` ni la captura. Route: inline. Estado: hecho. Evidencia: `tests/test_manager_clarity.py` (5 casos) y `tests/test_g2_risk_diagnosis.py` + `tests/test_g3_risk_proposal_chain.py` sin cambios de contrato.
- T2 [S1,S4,S5] Formateadores compartidos de presentación: responsable (sin asignar / no observado / nombre no disponible), prioridad (informada / sin especificar / no disponible en la fuente) y etiquetas legibles de campos técnicos. Route: inline. Estado: hecho.
- T3 [S2,S3,S5,S6,S7,S8,S9] Detalle de tarea: señales distintas, fechas planificadas, lista de campos informados retirada a información avanzada, alertas con condición/fecha/motivo y tareas afectadas con nombre, evidencia agrupada por tarjeta con historial expandible y cambios comprobados, «Historial de cambios» y «Cambios desde la última observación», y acciones correctivas con acceso a la propuesta o diagnóstico del motivo. Route: inline. Estado: hecho.
- T4 [S1,S2,S6] Listado de tareas: columna de responsable con nombres legibles, señales distintas sin «Crítica» superpuesta y fecha de inicio para tareas pendientes. Route: inline. Estado: hecho.
- T5 [S2,S6] Dashboard: «Requieren atención» como unión declarada, gráfico por tipo de señal sin doble conteo, panel de alertas con nombres de tareas y lenguaje sencillo. Route: inline. Estado: hecho.
- T6 [S10] Estilos responsive de los nuevos bloques, coherentes con la interfaz actual. Route: inline. Estado: hecho.
- T7 [S11] Regresión backend de la ampliación aditiva de `RiskView` y spec Playwright de los casos obligatorios. Route: inline. Estado: hecho. Evidencia: `frontend/e2e/manager-clarity.spec.ts` (13 casos) y `frontend/e2e-backend/g3-risk-proposal-chain.spec.ts` sobre la copia aislada de los datos reales.
- T8 [S11] Suites completas, `typecheck` y `build`. Route: inline. Estado: hecho. Evidencia: backend 1165 pruebas OK (1 omitida); Playwright 164 pruebas OK; `npm run typecheck` sin errores; `npm run build` correcto.

## Log

- L1. «Vamos a realizar una corrección acotada de usabilidad en la integración Trello de AgenteGestionProyectos.

Ya completamos REQ-08 y G1–G3. No quiero nuevas funcionalidades ni cambios de arquitectura.

La validación manual como gestor reveló problemas en Dashboard, Tareas y Detalle de tarea.

## Objetivo

Conseguir que un gestor comprenda quién es responsable de una tarea, cuándo vence, por qué requiere atención, qué riesgos tiene, cuáles son sus acciones correctivas y qué cambios históricos se han observado, sin conocer los contratos técnicos del backend.

## Cambios solicitados

### 1. Responsables de Trello

Actualmente se muestran identificadores largos en lugar de nombres.

Investiga si el backend, las capturas o el adaptador Trello ya disponen de nombres de miembros.

Reutiliza los datos disponibles para presentar nombres legibles en Tareas y Detalle de tarea.

No inventes nombres. Distingue correctamente entre «Sin asignar» y «Nombre no disponible».

Si obtener nombres requiere una nueva integración o llamadas externas adicionales, explícame primero el cambio necesario antes de implementarlo.

Los identificadores técnicos podrán mantenerse en información avanzada, pero no como presentación principal.

### 2. Actividades críticas y próximas a vencer

Revisa las etiquetas y explicaciones utilizadas en Dashboard y Tareas.

Actualmente una tarea próxima a vencer aparece también como crítica. Esto resulta confuso para el gestor.

Distingue visualmente vencimiento próximo, vencimiento superado, bloqueo y cualquier otra señal real. No atribuyas prioridad alta ni retraso sin datos que lo respalden.

Conserva los cálculos y contratos del backend salvo que exista un defecto demostrado.

Para tareas pendientes, muestra las fechas planificadas disponibles. No interpretes los días de calendario como esfuerzo restante si el origen no proporciona una estimación.

### 3. Prioridad

Investiga por qué aparece «Prioridad desconocida».

Comprueba si Trello aporta prioridad mediante etiquetas u otra configuración ya soportada.

Si la prioridad no está configurada o no puede determinarse, comunícalo correctamente.

No infieras prioridad a partir del vencimiento ni asignes valores arbitrarios.

### 4. Información técnica innecesaria

Retira del detalle principal la lista «Campos informados».

No debe mostrarse al gestor una lista de claves internas como detail, is_archived, last_activity_at o identificadores técnicos.

Conserva esa información únicamente en un apartado avanzado cuando sea necesaria para diagnóstico o auditoría.

### 5. Clasificación de riesgos

Sustituye el lenguaje técnico «Clasificación determinista» por una explicación sencilla, como «Alertas de la tarea» o «¿Por qué requiere atención?».

Presenta la condición observada, la fecha de referencia y el motivo concreto.

Cuando un riesgo afecte a varias tareas, muestra sus nombres y enlaces a sus detalles, no solamente sus IDs.

Distingue la alerta propia de la tarea del conjunto de otras tareas con la misma clasificación.

No alteres la identidad ni la trazabilidad de los riesgos de G2/G3.

### 6. Evidencias de Trello

Investiga por qué Detalle de tarea muestra numerosas entradas de la misma tarjeta Trello.

No elimines los registros históricos.

Agrupa por tarjeta de origen y organiza las observaciones cronológicamente.

Presenta una fuente principal con los datos relevantes y un historial expandible.

Cuando existan varias capturas, destaca cambios comprobados de estado, vencimiento, responsable u otros campos relevantes.

No presentes una captura nueva como si fuera un cambio real si los datos observados no cambiaron.

### 7. Historial observado y comparación de capturas

Revisa ambas secciones desde la perspectiva del gestor.

Explica claramente su finalidad y evita que dupliquen información.

Usa términos como «Historial de cambios» y «Cambios desde la última observación» cuando sean apropiados.

Si no hay diferencias verificables, muestra una indicación sencilla. Si los datos son insuficientes, no inventes resultados.

### 8. Acciones correctivas

Conserva la asociación tarea → riesgo → propuesta implementada en G3.

Cuando exista una propuesta, muestra un acceso claro desde el detalle de la tarea.

Cuando no exista, indica que no hay propuesta vinculada y reutiliza el diagnóstico disponible cuando permita explicar la causa.

No generes propuestas nuevas desde la interfaz ni habilites reglas permanentes de HORMIGESTION.

## Condiciones de implementación

Antes de modificar código, inspecciona los contratos y componentes existentes para determinar cuáles de estos cambios son exclusivamente de presentación y cuáles necesitan información adicional de la API.

Después implementa la corrección mínima, reutilizando componentes, esquemas y servicios disponibles.

Mantén el diseño responsive y coherente con la interfaz actual. No rediseñes las otras secciones ni crees nuevas pantallas.

Añade pruebas para nombres y datos desconocidos, clasificación de alertas, navegación entre tareas, agrupación de evidencias, historial de cambios y propuestas vinculadas.

Ejecuta las pruebas focalizadas, Playwright completo, typecheck y build. Si se modifica backend, ejecuta también sus regresiones.

Conserva la arquitectura, el pipeline, las reglas de autorización y la compatibilidad histórica.

Restricciones: EXTERNAL_WRITES_ENABLED=false, sin llamadas LLM reales, sin escrituras Trello/GitHub, sin alterar bases ni capturas históricas, sin push y sin merge.

Crea commits locales separados cuando sea necesario.

Al finalizar, presenta los cambios, pruebas exactas y una comparación de la experiencia anterior y posterior. Detente para mi revisión antes de modificar otras áreas del frontend.»

- L2. 2026-10-19. Inspección previa (solo lectura) sobre `src/agente_gestion/adapters/sources/trello.py`, `src/agente_gestion/domain/tasks.py`, `src/agente_gestion/domain/risks.py`, `src/agente_gestion/application/tasks_query.py`, `src/agente_gestion/api/{schemas,routes,repository}.py`, `frontend/src/features/{dashboard,tasks}`, `frontend/src/api/{types,formatters,queries}.ts` y la base real `.runtime/agente.db` (solo lectura).
- L3. 2026-10-19. Hallazgo S1: el adaptador Trello solicita `boards/{id}/cards/all` sin el campo `members` y solo conserva `idMembers`; `TaskCard` solo tiene `assignee_ids`; la captura real de HORMIGESTION (31 tarjetas) guarda identificadores y la evidencia guarda `member_ids`. No existe ningún nombre de miembro en el código, en las capturas, en la evidencia ni en `runs/`. Los nombres reales exigirían pedir `members` en la llamada de tarjetas o una llamada a `/1/members/{id}`, más un campo nuevo en el dominio y una captura nueva: no se implementa sin autorización expresa.
- L4. 2026-10-19. Hallazgo S4: las 31 tarjetas de la captura real tienen `priority` nulo y `labels` vacío. El mecanismo de prioridad por etiquetas (`TRELLO_PRIORITY_LABELS` → `settings.trello_priority_labels`) existe y está validado, pero solo lo usa la ruta de escritura y no está configurado para HORMIGESTION. Conclusión: la prioridad no está configurada ni es determinable con los datos actuales; la corrección es de presentación.
- L5. 2026-10-19. Hallazgo S2: `query_tasks_view` calcula `is_critical = is_overdue or is_due_soon or blocked or has_contradiction`. El cálculo es correcto como unión de señales; el defecto está en la presentación, que lo rotulaba como «Crítica» junto a las señales concretas. Se conserva el contrato y se corrige el rótulo.
- L6. 2026-10-19. Hallazgo S7: HORMIGESTION tiene 17 ejecuciones sobre una única captura, por lo que cada tarjeta produce 17 elementos de evidencia con carga útil idéntica (solo cambian el identificador y la marca de tiempo). `task_history` está vacío porque el proyecto tiene una sola captura. Se agrupa por tarjeta y solo se destacan los cambios verificados por el backend.
- L7. 2026-10-19. Decisión de diseño: la única ampliación de API es la de S6 (`task_titles` y `cutoff_datetime` en `RiskView`), porque sin los títulos de las tareas afectadas no se puede cumplir «muestra sus nombres y enlaces a sus detalles». Es aditiva y no altera la identidad ni la trazabilidad de los riesgos.

- L8. 2026-10-19. T1 implementado. `RiskView` suma `task_titles` y `cutoff_datetime`; `SQLiteQueryRepository.task_titles` resuelve los títulos contra la captura y `capture_cutoff` normaliza la fecha de referencia a la zona configurada. `risk_id`, `kind`, `task_ids`, `capture_id` y `source` no cambian: la identidad y la trazabilidad de G2/G3 quedan intactas.
- L9. 2026-10-19. T2 a T6 implementados. El detalle de tarea y el listado presentan señales concretas (vencida, próxima a vencer, bloqueada, contradictoria, estado desconocido) sin «Crítica» superpuesta; el responsable distingue «Sin asignar», «No observado» y «Nombre no disponible»; la prioridad se declara como «No disponible en la fuente» con su motivo; la lista «Campos informados» salió del detalle principal y quedó en «Información técnica de la tarea»; las alertas muestran condición, fecha de referencia y motivo, separan esta tarea del resto y nombran a las demás; la evidencia se agrupa por tarjeta con historial expandible y solo destaca los cambios verificados por el backend; «Historial de cambios» y «Cambios desde la última observación» explican finalidades distintas; y «Acciones correctivas» da acceso a la propuesta o declara la ausencia con el diagnóstico persistido.
- L10. 2026-10-19. Verificación. Backend: `.venv/bin/python -m unittest discover -s tests` → 1165 pruebas, OK (1 omitida). Frontend: `npx playwright test` → 164 pruebas OK, incluidas las 13 de `frontend/e2e/manager-clarity.spec.ts`. `npm run typecheck` sin errores y `npm run build` correcto. Validación sobre datos reales: `frontend/e2e-backend/playwright.g3demo.config.ts` → 1 prueba OK sobre la copia aislada de HORMIGESTION; `playwright.g1demo.config.ts` y `playwright.g1.config.ts` → 1 prueba OK cada una.
- L11. 2026-10-19. Comprobación sobre la captura real (copia temporal de `.runtime/agente.db`, base de origen intacta): 31 tareas, 11 pendientes, 1 en curso, 18 completadas, 1 desconocida; 0 vencidas, 2 próximas a vencer, 0 bloqueadas, 0 contradictorias. La alerta de vencimiento próximo nombra ahora «Módulo administrativo: Dashboard y reportería» e «Integración de módulos administrador y cliente». 4 identificadores de miembro y ningún nombre. 0 tareas con prioridad y 0 con etiquetas. Una tarea acumula 17 elementos de evidencia de una sola tarjeta y 0 cambios verificados, porque el proyecto tiene una única captura.
- L12. 2026-10-19. Pendiente de autorización: los nombres reales de los responsables exigen leer `members` en la llamada de tarjetas o consultar `/1/members/{id}`, más un campo nuevo en el dominio y una captura nueva. No se implementa hasta que el gestor lo autorice; mientras tanto la interfaz declara «Nombre no disponible» y conserva los identificadores en la información avanzada.
