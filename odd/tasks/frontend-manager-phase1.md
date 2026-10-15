# Phase 1 — Project manager frontend experience
Objective: implement the user-requested frontend redesign without changing backend authority; baseline 209acac.
Branch: codex/frontend-manager-phase1. Delivery: ask-on-risk (chain decision pending before commits; forecast 1800–2600 authored lines). Runner: frontend npm run test:e2e, npm run typecheck, npm run build.

## Specs

### S1

## 1. Contexto y Objetivo Principal
Actúa como un Senior Frontend Architect y Product Designer experto en **React 19, TypeScript 5.9, TailwindCSS 4 (con variables semánticas en `styles.css`), TanStack Query v5, React Router v7 y Lucide React**.

Actualmente tenemos una SPA funcional en `frontend/src/` conectada a un backend en FastAPI (`/api/v1`). Aunque la ingeniería base es sólida, la interfaz actual refleja mecanismos técnicos internos de la base de datos en lugar de un flujo intuitivo para un **Gestor de Proyectos**.

Tu objetivo es **refactorizar y mejorar la experiencia visual (UX/UI) del Frontend** para que funcione como un verdadero **Asistente Inteligente de Seguimiento y Control de Proyectos**, cumpliendo estrictamente con las observaciones de revisión del producto:
1. **Hacer explícito y visual el flujo secuencial de los 3 agentes (`REQ-08`)**: `1. Agente Ejecutor (Analiza y propone) → 2. Agente Revisor (Valida consistencia / Dry-Run / Verifier) → 3. Agente Aprobador (Autorización humana y ejecución)`.
2. **Simplificar la toma de decisiones en Trello (`REQ-03`, `REQ-04`, `REQ-05`, `REQ-09`)**: Pasar de un flujo burocrático y técnico de múltiples pasos manuales (`approve_offline`, `dry-run-authorization`, `dry-runs`) a una **Tarjeta de Decisión Ejecutiva** donde el Gestor vea el problema, el cambio propuesto (`Antes → Después`), el sello de validación del Agente Revisor y pueda **Aprobar y Aplicar en Trello**, **Modificar antes de aprobar** o **Rechazar** de forma intuitiva.
3. **Evolucionar la Revisión de Calidad de Código (`REQ-07`, `REQ-09`)**: Pasar de un modal de solo lectura a **Tarjetas Interactivas de Mejora de Código** que muestren:
   - Diagnóstico del Agente Ejecutor y validación del Agente Revisor (`VerifierAgent`).
   - Vista comparativa lado a lado (Diff): **Código Actual (`code_excerpt`) vs. Código Ejemplo Recomendado (`suggested_code`)**.
   - Opción para que el gestor edite el código sugerido si solicita modificaciones.
   - Botón para **Aprobar e Implementar en Rama Separada de Git** (ej. `ai-fix/calidad-...`) para verificación humana, mostrando el estado de la rama creada y su enlace.
4. **Visibilidad de Notificaciones al Correo del Gestor (`REQ-06`, `REQ-09`)**: Mostrar claramente en la interfaz (cabecera, bandeja de salida/outbox y configuración) que las alertas de retrasos, riesgos y reportes semanales se notifican al correo electrónico del Gestor del Proyecto, incluyendo vista previa del correo enviado.

---

### S2

## 2. Paso Previo Obligatorio: Inspección del Código Actual
Antes de modificar cualquier archivo, lee y analiza los siguientes archivos del repositorio para respetar los contratos, estilos y hooks existentes:
1. `frontend/src/styles.css` (Variables semánticas CSS, soporte `data-theme="dark"` y `light`, clases utilitarias existentes como `.metric-grid`, `.metric-card`, etc.).
2. `frontend/src/api/types.ts`, `frontend/src/api/queries.ts`, `frontend/src/api/client.ts` y `frontend/src/api/formatters.ts`.
3. `frontend/src/app/App.tsx`, `frontend/src/app/AppShell.tsx` y `frontend/src/app/WorkspaceContext.tsx`.
4. `frontend/src/components/` (`Dialog.tsx`, `Drawer.tsx`, `FeedbackToast.tsx`, `States.tsx`).
5. `frontend/src/features/dashboard/DashboardPage.tsx` y `ExecutiveInsights.tsx`.
6. `frontend/src/features/proposals/ProposalsPage.tsx`.
7. `frontend/src/features/quality/QualityPage.tsx`.
8. `frontend/src/features/analysis/AnalysisPage.tsx`, `frontend/src/features/calendar/CalendarPage.tsx`, `frontend/src/features/reports/ReportsPage.tsx` y `frontend/src/features/settings/SettingsPage.tsx`.

> **REGLA DE ORO DE COMPATIBILIDAD**:
> - **No elimines rutas existentes en `App.tsx`** (`/dashboard`, `/tasks`, `/proposals`, `/reports`, `/quality`, `/analysis`, `/calendar`, `/audit`, `/projects`, `/settings`) para no romper enlaces internos ni pruebas E2E de Playwright.
> - Como estamos en la **Fase 1 (Visual/Frontend)** y algunos campos nuevos del backend (como `suggested_code`, `branch_name` o el endpoint de mutación directa en GitHub/Trello) se completarán en la Fase 2 del backend, **extiende los tipos en `types.ts` con propiedades opcionales (`?`) y crea adaptadores/generadores de respaldo (fallbacks deterministas y estado optimista persistido en sesión/localStorage)** para que toda la experiencia visual funcione al 100% en vivo durante la demostración.

---

### S3

### 3.1. Barra Global del Flujo Multiagente y Sincronización (`AppShell.tsx` + nuevo componente `AgentWorkflowBar.tsx`)
1. Crea un componente limpio y compacto `frontend/src/components/AgentWorkflowBar.tsx` e intégralo en la parte superior del área de contenido o en el `DashboardPage.tsx` / `AppShell.tsx`.
2. Este componente debe visualizar claramente la arquitectura exigida (`REQ-08`):
   - **Paso 1: Agente Ejecutor** (Monitoreo de Trello y análisis de código en GitHub).
   - **Paso 2: Agente Revisor** (Validación de consistencia, simulación *Dry-Run* de Trello y verificación independiente con `VerifierAgent`).
   - **Paso 3: Agente Aprobador** (Bandeja de autorización del Gestor Humano y ejecución segura en Trello / Rama Git).
3. Incluye un indicador visual de **Notificación por Correo Activa**: `"Alertas y reportes dirigidos a: [correo del gestor configurable, ej. gestor@hormigestion.ec]"`.
4. Incluye un botón de acción rápida **"Sincronizar y Analizar Proyecto"** que encapsule de forma amigable el flujo de `/analysis/prepare` y `/analysis/confirm` (o permita disparar el análisis en 1 clic con feedback visual paso a paso por los 3 agentes), sin obligar al usuario a navegar a una pantalla técnica separada.
5. En el menú lateral de `AppShell.tsx`, organiza visualmente los ítems por categorías claras para el Gestor (ej. *1. Seguimiento y Control*, *2. Bandeja de Decisiones (Trello y Código)*, *3. Reportes y Auditoría*), mostrando contadores (badges) de propuestas o hallazgos pendientes de decisión.

### S4

### 3.2. Bandeja de Decisiones Pendientes en el Dashboard (`features/dashboard/DashboardPage.tsx`)
1. **Conserva intactos** los 10 KPIs compactos (`.metric-grid`), los gráficos (`StateDistributionChart`, `CriticalSignalsChart`, `CaptureCharts`) y `ExecutiveInsights`.
2. Añade en la parte superior del Dashboard una sección destacada llamada **"Centro de Decisiones del Gestor (Requieren tu Autorización)"** dividida en 3 tarjetas resumen de acción rápida:
   - **Acciones Correctivas en Trello (`REQ-05`)**: Cantidad de tareas retrasadas/en riesgo con propuestas listas y validadas por el Agente Revisor → Botón directo a `/proposals`.
   - **Mejoras de Calidad de Código (`REQ-07`)**: Cantidad de sugerencias de código con ejemplo listo para aprobar y crear rama en GitHub → Botón directo a `/quality`.
   - **Informe Semanal y Notificaciones (`REQ-06`)**: Estado del último reporte semanal y confirmación de envío al correo del gestor → Botón directo a `/reports` y `/calendar`.

### S5

### 3.3. Rediseño de Propuestas de Trello: De Burocracia Técnica a Decisión de 1 Clic (`features/proposals/ProposalsPage.tsx`)
Actualmente la vista de propuestas expone pasos técnicos (`approve_offline`, `dry-run-authorization`, `dry-runs`) que confunden al usuario. Rediséñala bajo el patrón **"Propuesta de Acción Correctiva"**:
1. **Encabezado claro del problema**: Mostrar la tarea afectada, días de retraso o riesgo detectado, y la explicación de **Causa e Impacto sobre el proyecto** generada por el *Agente Ejecutor* (`REQ-04`).
2. **Comparativa Visual del Cambio Propuesto (`Antes → Después`)**:
   - Fecha límite actual vs. Nueva fecha reprogramada propuesta.
   - Prioridad actual vs. Nueva prioridad propuesta.
   - Responsable actual vs. Nuevo responsable propuesto.
3. **Sello de Validación del Agente Revisor**:
   - Mostrar un badge claro indicando el estado de validación técnica y simulación (*Dry-Run*). Si la propuesta aún está en estado inicial en el backend, permite que el botón principal encadene internamente las llamadas necesarias (`review` → `dry-run-authorization` → `dry-runs`) de forma transparente para el usuario, manteniendo un acordeón opcional de *"Ver detalles técnicos de simulación y auditoría"* para cuando el docente quiera inspeccionar las entrañas.
4. **Controles de Autorización del Gestor (`REQ-05`, `REQ-09`)**:
   - **Botón Primario: "Aprobar y Aplicar en Trello"**: Ejecuta el flujo de aprobación, muestra un toast de confirmación y marca visualmente la propuesta como *"Ejecutada en Trello por el Agente Aprobador"* con marca de tiempo.
   - **Botón Secundario: "Modificar antes de Aprobar"**: Abre un modo de edición inline en la misma tarjeta donde el gestor puede ajustar la fecha límite propuesta, la prioridad o el responsable antes de confirmar la aprobación.
   - **Botón de Rechazo: "Rechazar Propuesta"**: Solicita una breve justificación y registra el rechazo.

### S6

### 3.4. Rediseño del Centro de Calidad de Código: Código Ejemplo + Creación de Rama en Git (`features/quality/QualityPage.tsx`)
Transforma la vista de hallazgos (`/quality`) para cumplir con las exigencias clave de la revisión:
1. Cada hallazgo debe presentarse (en tarjeta expandible o vista de detalle rica) como una **Recomendación Técnica de Calidad de Código** con la barra de trazabilidad `Agente Ejecutor (Detección) → Agente Revisor / VerifierAgent (Validación) → Agente Aprobador (Rama Git)`.
2. **Metadatos Claros**: Regla evaluada (`Q1`, `Q2`, `Q3`), categoría (Mantenibilidad, Seguridad/Vulnerabilidad, Duplicación, Complejidad, Buenas Prácticas), severidad, archivo (`file_path`), línea (`line`) y commit SHA.
3. **Vista Comparativa de Código (Code Diff: Código Actual vs. Código Ejemplo Sugerido)**:
   - **Panel Izquierdo / Superior (Rojo - Código Actual Detectado)**: Muestra el `code_excerpt` original del repositorio.
   - **Panel Derecho / Inferior (Verde - Código Ejemplo Recomendado)**: Muestra el código refactorizado/corregido (`suggested_code`).
   - *Nota de implementación importante*: Si el endpoint actual `/runs/{run_id}/findings` aún no devuelve el campo `suggested_code`, implementa un generador/mapper determinista en el frontend (`frontend/src/features/quality/codeSuggestionHelper.ts`) que, a partir del `code_excerpt`, la regla (`Q1`–`Q3`) y el mensaje del hallazgo, genere automáticamente un **bloque de código ejemplo refactorizado realista y sintácticamente limpio en Python/TypeScript**, además de explicar qué mejora aplica (ej. manejo seguro de excepciones, tipado, extracción de función, cierre seguro de recursos o validación de entrada).
4. **Flujo de Aprobación Humana y Creación de Rama (`REQ-07`, `REQ-09`)**:
   - Permite al gestor hacer clic en **"Editar código sugerido"** por si desea ajustar el código ejemplo antes de aprobarlo.
   - Muestra un campo editable con el nombre de la **rama separada de verificación** (por defecto: `ai-fix/calidad-{rule}-{id_corto}`).
   - **Botón "Aprobar e Implementar en Rama"**: Al hacer clic, muestra un estado de carga breve (*"Agente Aprobador creando rama y aplicando commit..."*), transiciona el hallazgo al estado `APPROVED_BRANCH`, muestra el badge verde con el nombre de la rama creada, el hash del commit simulado/real, un botón para **"Ver Rama / Pull Request en GitHub"** y registra la decisión del gestor.
   - **Botón "Rechazar Sugerencia"**: Permite descartar la recomendación registrando el motivo.

### S7

### 3.5. Notificaciones al Correo del Gestor en Ciclo Semanal y Reportes (`features/calendar/CalendarPage.tsx` y `features/reports/ReportsPage.tsx`)
1. En `CalendarPage.tsx` (donde se visualiza el `WeeklySchedulerService` y la bandeja `Outbox`) y en `ReportsPage.tsx`:
   - Añade una cabecera/panel claro de **"Configuración de Entrega por Correo Electrónico al Gestor"**, mostrando el destinatario configurado (con opción de editar el correo del gestor en UI), estado del servidor de notificaciones y disparo manual de prueba (*"Enviar Notificación por Correo al Gestor"*).
   - En el detalle de cada recibo del `Outbox` (`/projects/{id}/schedule/outbox/{id}`), presenta el contenido con el diseño visual de un **Correo Electrónico Ejecutivo Recibido** (Cabecera `De: Agente IA de Gestión`, `Para: Gestor del Proyecto`, `Asunto: ...`, resumen de avance, retrasos, acciones correctivas y botón de acción), además de conservar los botones de descarga `.md` y `.json`.

---

### S8

## 4. Criterios de Aceptación y Verificación Técnica
Al finalizar los cambios, ejecuta y verifica obligatoriamente en la terminal:
1. `npm run typecheck` (en `frontend/`): Debe pasar con **0 errores de TypeScript**.
2. `npm run build` (en `frontend/`): El bundle de producción debe compilar limpio sin errores.
3. Verifica que tanto el modo claro (`data-theme="light"`) como el modo oscuro (`data-theme="dark"`) se vean con excelente contraste, jerarquía tipográfica limpia y sin desbordamientos horizontales en escritorio ni móvil.
4. Entrégame un resumen de los archivos creados y modificados, explicando cómo probar cada uno de los nuevos flujos visuales en el navegador.

## Tasks

- [ ] T1 — S1–S4, S8; route inline, parent-owned shared contracts/storage, workflow bar, sidebar and decision center; focused RED observed (2 missing-UI failures), GREEN 2/2 plus typecheck 0 errors; final 4/4 manager browser tests passed; commit pending.
- [ ] T2 — S1–S2, S5, S8; route delegated, parallel independent proposals unit; disjoint proposal page/helper/styles/tests; RED missing executive controls observed; GREEN 13/13 (17.7s), typecheck passed; commit pending.
- [ ] T3 — S1–S2, S6, S8; route delegated, parallel independent quality unit; disjoint quality page/helper/styles/tests; RED 5 missing controls observed; GREEN 8/8 (12.5s), typecheck and original quality regression passed; commit pending.
- [ ] T4 — S1–S2, S7, S8; route delegated, parallel independent delivery unit; disjoint delivery components/calendar/reports/settings/styles/tests; RED 5 missing features observed; GREEN 8/8 (15.9s), typecheck and four theme/viewport checks passed; commit pending.
- [ ] T5 — S1–S8; route delegated verification; all specs verified after one bounded B1/B2 correction/recheck; typecheck/build PASS; full suite 72/73 then sole failed test 1/1 PASS; native review/delivery choice/commit pending.

## Log

### L1 — Original authorized request (verbatim)

# Prompt de Implementación Fase 1 (Frontend UX/UI): Rediseño Centrado en el Gestor de Proyecto y Flujo Multiagente

## 1. Contexto y Objetivo Principal
Actúa como un Senior Frontend Architect y Product Designer experto en **React 19, TypeScript 5.9, TailwindCSS 4 (con variables semánticas en `styles.css`), TanStack Query v5, React Router v7 y Lucide React**.

Actualmente tenemos una SPA funcional en `frontend/src/` conectada a un backend en FastAPI (`/api/v1`). Aunque la ingeniería base es sólida, la interfaz actual refleja mecanismos técnicos internos de la base de datos en lugar de un flujo intuitivo para un **Gestor de Proyectos**.

Tu objetivo es **refactorizar y mejorar la experiencia visual (UX/UI) del Frontend** para que funcione como un verdadero **Asistente Inteligente de Seguimiento y Control de Proyectos**, cumpliendo estrictamente con las observaciones de revisión del producto:
1. **Hacer explícito y visual el flujo secuencial de los 3 agentes (`REQ-08`)**: `1. Agente Ejecutor (Analiza y propone) → 2. Agente Revisor (Valida consistencia / Dry-Run / Verifier) → 3. Agente Aprobador (Autorización humana y ejecución)`.
2. **Simplificar la toma de decisiones en Trello (`REQ-03`, `REQ-04`, `REQ-05`, `REQ-09`)**: Pasar de un flujo burocrático y técnico de múltiples pasos manuales (`approve_offline`, `dry-run-authorization`, `dry-runs`) a una **Tarjeta de Decisión Ejecutiva** donde el Gestor vea el problema, el cambio propuesto (`Antes → Después`), el sello de validación del Agente Revisor y pueda **Aprobar y Aplicar en Trello**, **Modificar antes de aprobar** o **Rechazar** de forma intuitiva.
3. **Evolucionar la Revisión de Calidad de Código (`REQ-07`, `REQ-09`)**: Pasar de un modal de solo lectura a **Tarjetas Interactivas de Mejora de Código** que muestren:
   - Diagnóstico del Agente Ejecutor y validación del Agente Revisor (`VerifierAgent`).
   - Vista comparativa lado a lado (Diff): **Código Actual (`code_excerpt`) vs. Código Ejemplo Recomendado (`suggested_code`)**.
   - Opción para que el gestor edite el código sugerido si solicita modificaciones.
   - Botón para **Aprobar e Implementar en Rama Separada de Git** (ej. `ai-fix/calidad-...`) para verificación humana, mostrando el estado de la rama creada y su enlace.
4. **Visibilidad de Notificaciones al Correo del Gestor (`REQ-06`, `REQ-09`)**: Mostrar claramente en la interfaz (cabecera, bandeja de salida/outbox y configuración) que las alertas de retrasos, riesgos y reportes semanales se notifican al correo electrónico del Gestor del Proyecto, incluyendo vista previa del correo enviado.

---

## 2. Paso Previo Obligatorio: Inspección del Código Actual
Antes de modificar cualquier archivo, lee y analiza los siguientes archivos del repositorio para respetar los contratos, estilos y hooks existentes:
1. `frontend/src/styles.css` (Variables semánticas CSS, soporte `data-theme="dark"` y `light`, clases utilitarias existentes como `.metric-grid`, `.metric-card`, etc.).
2. `frontend/src/api/types.ts`, `frontend/src/api/queries.ts`, `frontend/src/api/client.ts` y `frontend/src/api/formatters.ts`.
3. `frontend/src/app/App.tsx`, `frontend/src/app/AppShell.tsx` y `frontend/src/app/WorkspaceContext.tsx`.
4. `frontend/src/components/` (`Dialog.tsx`, `Drawer.tsx`, `FeedbackToast.tsx`, `States.tsx`).
5. `frontend/src/features/dashboard/DashboardPage.tsx` y `ExecutiveInsights.tsx`.
6. `frontend/src/features/proposals/ProposalsPage.tsx`.
7. `frontend/src/features/quality/QualityPage.tsx`.
8. `frontend/src/features/analysis/AnalysisPage.tsx`, `frontend/src/features/calendar/CalendarPage.tsx`, `frontend/src/features/reports/ReportsPage.tsx` y `frontend/src/features/settings/SettingsPage.tsx`.

> **REGLA DE ORO DE COMPATIBILIDAD**:
> - **No elimines rutas existentes en `App.tsx`** (`/dashboard`, `/tasks`, `/proposals`, `/reports`, `/quality`, `/analysis`, `/calendar`, `/audit`, `/projects`, `/settings`) para no romper enlaces internos ni pruebas E2E de Playwright.
> - Como estamos en la **Fase 1 (Visual/Frontend)** y algunos campos nuevos del backend (como `suggested_code`, `branch_name` o el endpoint de mutación directa en GitHub/Trello) se completarán en la Fase 2 del backend, **extiende los tipos en `types.ts` con propiedades opcionales (`?`) y crea adaptadores/generadores de respaldo (fallbacks deterministas y estado optimista persistido en sesión/localStorage)** para que toda la experiencia visual funcione al 100% en vivo durante la demostración.

---

## 3. Intervenciones Detalladas a Implementar

### 3.1. Barra Global del Flujo Multiagente y Sincronización (`AppShell.tsx` + nuevo componente `AgentWorkflowBar.tsx`)
1. Crea un componente limpio y compacto `frontend/src/components/AgentWorkflowBar.tsx` e intégralo en la parte superior del área de contenido o en el `DashboardPage.tsx` / `AppShell.tsx`.
2. Este componente debe visualizar claramente la arquitectura exigida (`REQ-08`):
   - **Paso 1: Agente Ejecutor** (Monitoreo de Trello y análisis de código en GitHub).
   - **Paso 2: Agente Revisor** (Validación de consistencia, simulación *Dry-Run* de Trello y verificación independiente con `VerifierAgent`).
   - **Paso 3: Agente Aprobador** (Bandeja de autorización del Gestor Humano y ejecución segura en Trello / Rama Git).
3. Incluye un indicador visual de **Notificación por Correo Activa**: `"Alertas y reportes dirigidos a: [correo del gestor configurable, ej. gestor@hormigestion.ec]"`.
4. Incluye un botón de acción rápida **"Sincronizar y Analizar Proyecto"** que encapsule de forma amigable el flujo de `/analysis/prepare` y `/analysis/confirm` (o permita disparar el análisis en 1 clic con feedback visual paso a paso por los 3 agentes), sin obligar al usuario a navegar a una pantalla técnica separada.
5. En el menú lateral de `AppShell.tsx`, organiza visualmente los ítems por categorías claras para el Gestor (ej. *1. Seguimiento y Control*, *2. Bandeja de Decisiones (Trello y Código)*, *3. Reportes y Auditoría*), mostrando contadores (badges) de propuestas o hallazgos pendientes de decisión.

### 3.2. Bandeja de Decisiones Pendientes en el Dashboard (`features/dashboard/DashboardPage.tsx`)
1. **Conserva intactos** los 10 KPIs compactos (`.metric-grid`), los gráficos (`StateDistributionChart`, `CriticalSignalsChart`, `CaptureCharts`) y `ExecutiveInsights`.
2. Añade en la parte superior del Dashboard una sección destacada llamada **"Centro de Decisiones del Gestor (Requieren tu Autorización)"** dividida en 3 tarjetas resumen de acción rápida:
   - **Acciones Correctivas en Trello (`REQ-05`)**: Cantidad de tareas retrasadas/en riesgo con propuestas listas y validadas por el Agente Revisor → Botón directo a `/proposals`.
   - **Mejoras de Calidad de Código (`REQ-07`)**: Cantidad de sugerencias de código con ejemplo listo para aprobar y crear rama en GitHub → Botón directo a `/quality`.
   - **Informe Semanal y Notificaciones (`REQ-06`)**: Estado del último reporte semanal y confirmación de envío al correo del gestor → Botón directo a `/reports` y `/calendar`.

### 3.3. Rediseño de Propuestas de Trello: De Burocracia Técnica a Decisión de 1 Clic (`features/proposals/ProposalsPage.tsx`)
Actualmente la vista de propuestas expone pasos técnicos (`approve_offline`, `dry-run-authorization`, `dry-runs`) que confunden al usuario. Rediséñala bajo el patrón **"Propuesta de Acción Correctiva"**:
1. **Encabezado claro del problema**: Mostrar la tarea afectada, días de retraso o riesgo detectado, y la explicación de **Causa e Impacto sobre el proyecto** generada por el *Agente Ejecutor* (`REQ-04`).
2. **Comparativa Visual del Cambio Propuesto (`Antes → Después`)**:
   - Fecha límite actual vs. Nueva fecha reprogramada propuesta.
   - Prioridad actual vs. Nueva prioridad propuesta.
   - Responsable actual vs. Nuevo responsable propuesto.
3. **Sello de Validación del Agente Revisor**:
   - Mostrar un badge claro indicando el estado de validación técnica y simulación (*Dry-Run*). Si la propuesta aún está en estado inicial en el backend, permite que el botón principal encadene internamente las llamadas necesarias (`review` → `dry-run-authorization` → `dry-runs`) de forma transparente para el usuario, manteniendo un acordeón opcional de *"Ver detalles técnicos de simulación y auditoría"* para cuando el docente quiera inspeccionar las entrañas.
4. **Controles de Autorización del Gestor (`REQ-05`, `REQ-09`)**:
   - **Botón Primario: "Aprobar y Aplicar en Trello"**: Ejecuta el flujo de aprobación, muestra un toast de confirmación y marca visualmente la propuesta como *"Ejecutada en Trello por el Agente Aprobador"* con marca de tiempo.
   - **Botón Secundario: "Modificar antes de Aprobar"**: Abre un modo de edición inline en la misma tarjeta donde el gestor puede ajustar la fecha límite propuesta, la prioridad o el responsable antes de confirmar la aprobación.
   - **Botón de Rechazo: "Rechazar Propuesta"**: Solicita una breve justificación y registra el rechazo.

### 3.4. Rediseño del Centro de Calidad de Código: Código Ejemplo + Creación de Rama en Git (`features/quality/QualityPage.tsx`)
Transforma la vista de hallazgos (`/quality`) para cumplir con las exigencias clave de la revisión:
1. Cada hallazgo debe presentarse (en tarjeta expandible o vista de detalle rica) como una **Recomendación Técnica de Calidad de Código** con la barra de trazabilidad `Agente Ejecutor (Detección) → Agente Revisor / VerifierAgent (Validación) → Agente Aprobador (Rama Git)`.
2. **Metadatos Claros**: Regla evaluada (`Q1`, `Q2`, `Q3`), categoría (Mantenibilidad, Seguridad/Vulnerabilidad, Duplicación, Complejidad, Buenas Prácticas), severidad, archivo (`file_path`), línea (`line`) y commit SHA.
3. **Vista Comparativa de Código (Code Diff: Código Actual vs. Código Ejemplo Sugerido)**:
   - **Panel Izquierdo / Superior (Rojo - Código Actual Detectado)**: Muestra el `code_excerpt` original del repositorio.
   - **Panel Derecho / Inferior (Verde - Código Ejemplo Recomendado)**: Muestra el código refactorizado/corregido (`suggested_code`).
   - *Nota de implementación importante*: Si el endpoint actual `/runs/{run_id}/findings` aún no devuelve el campo `suggested_code`, implementa un generador/mapper determinista en el frontend (`frontend/src/features/quality/codeSuggestionHelper.ts`) que, a partir del `code_excerpt`, la regla (`Q1`–`Q3`) y el mensaje del hallazgo, genere automáticamente un **bloque de código ejemplo refactorizado realista y sintácticamente limpio en Python/TypeScript**, además de explicar qué mejora aplica (ej. manejo seguro de excepciones, tipado, extracción de función, cierre seguro de recursos o validación de entrada).
4. **Flujo de Aprobación Humana y Creación de Rama (`REQ-07`, `REQ-09`)**:
   - Permite al gestor hacer clic en **"Editar código sugerido"** por si desea ajustar el código ejemplo antes de aprobarlo.
   - Muestra un campo editable con el nombre de la **rama separada de verificación** (por defecto: `ai-fix/calidad-{rule}-{id_corto}`).
   - **Botón "Aprobar e Implementar en Rama"**: Al hacer clic, muestra un estado de carga breve (*"Agente Aprobador creando rama y aplicando commit..."*), transiciona el hallazgo al estado `APPROVED_BRANCH`, muestra el badge verde con el nombre de la rama creada, el hash del commit simulado/real, un botón para **"Ver Rama / Pull Request en GitHub"** y registra la decisión del gestor.
   - **Botón "Rechazar Sugerencia"**: Permite descartar la recomendación registrando el motivo.

### 3.5. Notificaciones al Correo del Gestor en Ciclo Semanal y Reportes (`features/calendar/CalendarPage.tsx` y `features/reports/ReportsPage.tsx`)
1. En `CalendarPage.tsx` (donde se visualiza el `WeeklySchedulerService` y la bandeja `Outbox`) y en `ReportsPage.tsx`:
   - Añade una cabecera/panel claro de **"Configuración de Entrega por Correo Electrónico al Gestor"**, mostrando el destinatario configurado (con opción de editar el correo del gestor en UI), estado del servidor de notificaciones y disparo manual de prueba (*"Enviar Notificación por Correo al Gestor"*).
   - En el detalle de cada recibo del `Outbox` (`/projects/{id}/schedule/outbox/{id}`), presenta el contenido con el diseño visual de un **Correo Electrónico Ejecutivo Recibido** (Cabecera `De: Agente IA de Gestión`, `Para: Gestor del Proyecto`, `Asunto: ...`, resumen de avance, retrasos, acciones correctivas y botón de acción), además de conservar los botones de descarga `.md` y `.json`.

---

## 4. Criterios de Aceptación y Verificación Técnica
Al finalizar los cambios, ejecuta y verifica obligatoriamente en la terminal:
1. `npm run typecheck` (en `frontend/`): Debe pasar con **0 errores de TypeScript**.
2. `npm run build` (en `frontend/`): El bundle de producción debe compilar limpio sin errores.
3. Verifica que tanto el modo claro (`data-theme="light"`) como el modo oscuro (`data-theme="dark"`) se vean con excelente contraste, jerarquía tipográfica limpia y sin desbordamientos horizontales en escritorio ni móvil.
4. Entrégame un resumen de los archivos creados y modificados, explicando cómo probar cada uno de los nuevos flujos visuales en el navegador.

### L2 — Inspection and safety boundary

2026-10-14: Full mandatory preflight completed by frontend_phase1_map; Dialog.tsx/Drawer.tsx/FeedbackToast.tsx absent, existing Radix/ConfirmActionDialog/ToastProvider inspected instead. All routes preserved; /dashboard alias is additive. Baseline clean. User explicitly approved gpt-6.1-sol instead of unavailable gpt-6.1-luna. No backend edit or external service call is authorized. Unsupported Trello execution/Git branches/email delivery will remain visibly simulated; existing actual dry-runs/receipt statuses retain their truthful meaning. Scoped versioned browser state is never backend authorization. Risk items 2/3/4: existing capability/CSRF/idempotency/version guards, shared UI contracts and async orchestration require independent verification.

### L3 — Verification policy and ownership

2026-10-14: Use deterministic Playwright route interception; observe rule-specific RED before behavior writes, then GREEN. Preserve previous public behavior/guards with existing E2E tests. No dependency/config changes, real LLM/Trello/GitHub/SMTP requests or global formatters. Parent owns git, shared DTOs/storage/styles/shell/dashboard and existing e2e/web-platform.spec.ts. Three independent substantive write units will launch together after shared storage contracts are ready. Each worker owns dedicated tests/styles and runs exact focused checks; one full seam suite/build at feature end. Required npm run typecheck/build and light/dark desktop/mobile browser checks remain pending. Commit and native review consent remain pending; no push/PR/merge is authorized.

### L4 — T1 shared manager workflow

Parent wrote additive /dashboard alias, shared browser-only validated/versioned state, optional DTO fields, workflow bar/remote-consent quick action, grouped navigation badges and decision center while preserving all KPI/chart/insight definitions. Original intercepted E2E fixture was extracted without semantic changes for reuse. RED: 2 missing-UI tests failed; GREEN: 2/2 passed, typecheck 0 errors. Extra permission/responsive checks and full seam remain pending. Fixed a shared DTO optional commit_sha collision and missing expect import identified by workers.

### L5 — T2 proposals functional delivery

Worker phase1_proposals stayed within four declared surfaces. GREEN 13/13 including stages 2→3→4, CSRF/event/idempotency, duplicate suppression, partial failure/retry, immutable edited drafts, required rejection reason, unknown fields, 390px light/dark no overflow. Approval result explicitly simulated/no external writes. Parent adjusted legacy manual regression to open requested optional technical accordion. No commits yet.

### L6 — T3 quality functional delivery

Worker frontend_phase1_map reused as T3 writer stayed within four quality surfaces. GREEN 8/8 plus original XSS/text/evidence detail regression 1/1; typecheck passed. Diff/current provenance, illustrative deterministic Python/TS examples, editable code/branch, simulated hash and scoped local decisions, authenticated decision guard and safe real-link handling. Light/dark 1280/375px screenshots inspected; fixed scoped dialog-width collision. No commits yet.

### L7 — T4 email functional delivery

Worker phase1_delivery stayed within eight delivery surfaces. GREEN 8/8; typecheck passed. Validated project-scoped local recipient shared across calendar/reports/settings/header, explicitly unsent preview; genuine outbox file/sqlite/console status and recorded recipient/downloads preserved. Four light/dark desktop/mobile screenshots inspected. Standalone Vite retained session47035 on :4173 after parallel runners initially killed reused ephemeral servers. No installs/backend/external operations.

### L8 — Shared integration and verification state

Parent added task-wide Spanish testing guide docs/frontend-manager-phase1.md, preserved legacy proposal regression assertions and observed final combined typecheck 0 errors. git diff --check clean. Initial native assessment high/unassessable because new files are not yet declared/staged; never downgrade. Independent whole-feature verifier and full suite/build/browser proof pending. Early wrong-directory npm/Python attempts performed no behavior changes and were corrected. Authored-line/chain choice remains pending before any commit.

### L9 — Independent verification and bounded correction

Fresh verifier phase1_verify used isolated /tmp/phase1-verify-7SfrsG/{candidate,baseline}; no workspace edits/install/external calls. Candidate npm run typecheck PASS, npm run build PASS; CI=1 npm run test:e2e -- --workers=2 --output=/tmp/phase1-verifier-e2e: 72/73 PASS (56.1s). Four extra real browser-request probes PASS (ineligible analysis, server-disabled remote, stable confirmation retry, redacted evidence rejection), plus outbox/proposal visual probe PASS. Actual theme/viewport screenshots independently inspected with no horizontal overflow.

Two change-caused blockers: B1 EvidenceProvenance.commit_sha optional baseline field accidentally deleted by a parent's broad replacement intended only for FindingDetail; baseline compile PASS vs candidate TS2353. B2 legacy fuzzy Propuestas link selector became ambiguous after the legitimate new Revisar propuestas CTA. One correction batch restored only that field and exact:true selector, plus committed-shape types.test-d.ts regression. Parent observed type regression RED TS2344/TS2339 then GREEN0 errors. Verifier blocker-only recheck copied just3 files: provenance probe PASS, npm run typecheck PASS0, npm run build PASS2603modules, sole prior failing login test1/1PASS2.7s. All S1–S8 verified within explicit phase1 demo boundary. No new full73/73 rerun is claimed. Parent final manager UI4/4PASS6.1s; no backend/dependency/config diffs.

### L10 — Delivery remains pending human choice

No staged files, commits, push, PR, merge or real external effects. Working branch codex/frontend-manager-phase1, original baseline209acac. Native mode on; assess high/unassessable because new files lack declared inventory; independent verification completed without lowering risk. Before any work-unit commit, ask-on-risk requires chain strategy because forecast exceeds400authoredlines. Recommend feature-branch-chain (local, no publication); await user confirmation. Native risk assessment/review still pending; do not claim native approval or feature delivery complete. Source implementation and guide ready; preserve changes until choice. Required reviewer will use canonical fresh inventory/continuations, not stale assessment hash.
