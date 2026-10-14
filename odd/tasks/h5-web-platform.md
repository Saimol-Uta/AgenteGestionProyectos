# H5 — Web product implementation
Objective: deliver the requested integrated Spanish SaaS interface over the existing engine. Branch: `mvp/real-sources`, initial HEAD `b692e54e4ec7489d342793a3e55dbe6687d63beb` (clean).
Delivery: uncommitted work only, as explicitly requested; no push. Checks: Python unittest, TypeScript/build, Playwright isolated fixtures and authentic historical read-only demonstration.

## Specs
S1. "La arquitectura existente constituye la fuente de verdad. No replantees el motor multiagente." "No crees otra aplicación Python independiente." "No generes commits ni realices push sin autorización."
S2. "React con TypeScript." "Vite." "Tailwind CSS." "FastAPI." "Contratos REST documentados mediante OpenAPI." "No duplicar la lógica del dominio." "No ejecutar subprocess de la CLI como mecanismo permanente de API."
S3. "Implementa ambos temas de manera completa." "Toda la interfaz debe estar en español." "Diseño responsive" "Toda interacción visible debe realizar una operación real o indicar claramente una limitación."
S4. "Todos los valores deben proceder del backend." "No inventes series temporales a partir de una sola captura." "Debe mostrar los proyectos realmente configurados o almacenados." "El piloto inicial será HORMIGESTION."
S5. "No inferir un estado de ejecución únicamente desde la lista de Trello." "No inventar responsables, prioridades o dependencias." "Distinguir entre cambio observado y acción atribuida a una persona."
S6. "Monitoring → Quality → Verifier condicional → Report" "No lanzar automáticamente modelos de IA al abrir una página." "Implementa ejecución asíncrona o un mecanismo de trabajos adecuado para evitar que la petición HTTP quede bloqueada durante todo el análisis." "sin inventar progreso."
S7. "No presentar todos los hallazgos de Quality como defectos confirmados." "Los fragmentos parciales deben identificarse como tales." "Si faltan resultados de CI o cobertura, representar `unknown` o `evidencia_insuficiente`, nunca convertir datos ausentes en cero."
S8. "La revisión, aprobación del informe y autorización de una mutación son decisiones distintas." "Existe una brecha previamente identificada en la verificación de identidad y tokens del backend H2. No habilites escrituras reales en Trello hasta corregirla y probarla." "Mientras tanto, ofrece acciones de consulta, revisión y simulación claramente identificadas."
S9. "Renderiza Markdown de manera segura, evitando HTML peligroso y XSS." "Distingue entre scheduler configurado y ejecución automática por un servicio del sistema." "No afirmar que existe ejecución desatendida si cron/systemd u otro disparador no está instalado."
S10. "Las marcas temporales deben interpretarse de acuerdo con la zona configurada, principalmente `America/Guayaquil`." "No atribuir autores a eventos cuando la identidad sea desconocida." "Las claves API deben permanecer exclusivamente en el backend."
S11. "Verificar sesiones y permisos en backend." "Proteger operaciones contra CSRF cuando se utilicen cookies." "No utilizar roles declarados por el cliente como prueba de identidad." "No realizar llamadas remotas de IA sin confirmación expresa del usuario." "No alterar capturas históricas."
S12. "Para pruebas deterministas utiliza fixtures controladas y bases temporales." "Realiza también una demostración integrada con datos históricos auténticos de HORMIGESTION." "No realizar llamadas pagadas ni escrituras externas automáticamente durante los tests."
S13. "Usa Playwright para verificar los recorridos esenciales:" "1. Abrir el dashboard de HORMIGESTION." "2. Consultar las tareas y aplicar filtros." "3. Inspeccionar una tarea crítica." "4. Abrir un análisis histórico." "5. Revisar sus hallazgos." "6. Consultar el informe generado." "7. Abrir una propuesta y ejecutar una simulación autorizada." "8. Consultar auditoría y reportes semanales."
S14. "Trabaja por módulos verticales completos:" "API funcional → interfaz visual → integración → pruebas → refinamiento UX." "No interpretes esta división como autorización para detenerte después del primer dashboard. El alcance solicitado comprende todos los módulos." "Al finalizar cada etapa, presenta qué funciona, qué archivos cambiaron, qué pruebas pasaron y cuál es la próxima etapa."
S15. "Documentación de instalación y ejecución." "Comandos para iniciar frontend y backend en CachyOS." "Evidencia de pruebas y capturas para presentación." "Documenta cualquier funcionalidad que permanezca restringida por razones de seguridad o ausencia de capacidades backend."

## Tasks
- T1 | S1-S3,S10-S11 | delegated parallel backend/frontend foundations (disjoint surfaces, independently substantial) | done | commit prohibited; backend14focused, parent747suite OK2skips; frontendtypecheck/build/9intercepted/audit0. Actual API foundation browser job12 exit0: themes, diff, verified session/HttpOnly/Strict/CSRF logout/revocation/mobile. Independent security verification remains explicit T6 final gate.
- T2 | S4-S5 | delegated parallel query API and tracking UI | done | commit prohibited; domain-backed full executive KPIs/graphs and tracking/history/diff connected. Parent job14 actual HTTP/browser exit0; actual historic API proof0remotes/90files unchanged, known incompatible comparison409 documented.
- T3 | S6-S7,S11 | delegated parallel execution API and intelligence/quality UI | done | commit prohibited; backend23web/756suite, frontend26intercepted/typecheck/build/audit0; parent757suite OK2skips and actual HTTP/Chromium job14 passed persisted runs/stages/partial findings/redaction plus explicit protected fake-executor preparation/confirm/terminalpoll. No real pipeline/provider invoked; final independent security verification T6.
- T4 | S8,S10-S11 | backend + atomic corrective unit + connected frontend | done | commit prohibited; parent771PythonOK2skips,37frontendOK/audit0; realHTTPjob27 review→separateauthorize→coreSIMULATED, unchangedtask/captures/reportdecision, linkedverifiedaudit and mobile. Independent global verify/RDD reservedT6. No external writes.
- T5 | S9-S10 | backend + connected frontend | done | commit prohibited; actualHTTPjob27 safeMarkdown/mdJSONdownloads, separatedreportdecision+audit, realWeeklyScheduler/sqliteEXECUTED/newreceipt/terminalpoll/noanalysis, auditfilter/detail andreadonlyconfig,390pxmobileroutes nooverflow.37frontendOK/audit0/typecheck/build. Global verify/RDD reservedT6.
- T6 | S3,S12-S15 | inline integration + one independent high-risk verifier | blocked | commit prohibited;771PythonOK2skips/37frontendOK/audit0,8fixtureHTTPjourneysPASS; independentverifier19isolatedPASS/noobservedcodeblockers. Authenticbrowser26timeout and correctedhuman-authorized28timeout;90hashcheckintact but S12fullbrowsernotproved. Await human decision: boundeddiagnostic/oneexplicitnewattempt or keepdemo unverified. NativeRDD notstarted, finalvisualreview notcomplete. Docstatusadvisorycorrected.

## Log
L1 — Original request (verbatim):

realiza lo siguiente>
# H5 — Implementación de interfaz web profesional para AgenteGestionProyectos

## ROL Y OBJETIVO

Actúa como un equipo senior de desarrollo de producto compuesto por:

- Arquitecto de software.
- Ingeniero frontend senior.
- Ingeniero backend Python/FastAPI.
- Diseñador UI/UX especializado en aplicaciones SaaS.
- Ingeniero QA y seguridad.

Estamos desarrollando **AgenteGestionProyectos**, una plataforma multiagente de seguimiento, gestión y control de proyectos de software.

El motor backend ya existe y contiene implementaciones funcionales de Monitoring, Quality, Verifier, Report, seguimiento de tareas, políticas, propuestas, auditoría y reportes.

Hasta ahora, el desarrollo se concentró principalmente en el motor Python y sus herramientas CLI.

**Tu objetivo es convertir ese motor en un producto web profesional, visualmente atractivo, interactivo y completamente funcional.**

No quiero una aplicación mínima, una maqueta, un prototipo visual, una colección de páginas estáticas ni un dashboard genérico generado automáticamente.

Quiero una plataforma coherente, pulida, con identidad visual propia y conectada a operaciones reales del backend.

El resultado debe tener calidad suficiente para una presentación universitaria formal y permitir demostrar el funcionamiento del sistema frente al docente.

---

## 1. CONTEXTO DEL REPOSITORIO

Repositorio:

`Saimol-Uta/AgenteGestionProyectos`

Rama:

`mvp/real-sources`

Sistema operativo: CachyOS.

Shell: Fish.

Entorno Python: `.venv/bin/python`.

Proyecto piloto: **HORMIGESTION**.

Antes de desarrollar:

1. Comprueba la rama actual, HEAD y estado de Git.
2. Inspecciona la arquitectura implementada.
3. Lee la documentación del producto y los requisitos REQ-01 a REQ-11.
4. Identifica servicios, modelos, contratos y repositorios de datos reutilizables.
5. Comprueba si existe alguna implementación web parcial.
6. Conserva el trabajo existente.

Documentos prioritarios:

- `ARCHITECTURA.md`
- `docs/arquitectura-implementada.md`
- `docs/requisitos-y-plan-de-implementacion.md`
- `odd/tasks/gemini-role-migration.md`
- `README.md`
- `config/hormigestion.yaml`

La arquitectura existente constituye la fuente de verdad. No replantees el motor multiagente.

## 2. GENTLE AI Y SKILLS

Antigravity dispone de Gentle AI y skills previamente instaladas.

Descubre sus instrucciones reales antes de trabajar.

Utilízalas para planificación incremental, implementación, revisión técnica, pruebas y evaluación de calidad.

Aplica revisión proporcional al riesgo. No desperdicies contexto ni tokens invocando varios agentes para revisar el mismo cambio sin necesidad.

El trabajo debe avanzar hacia código terminado y probado, no detenerse en documentación o análisis prolongado.

---

# 3. STACK TECNOLÓGICO

Propongo esta arquitectura:

### Frontend

- React con TypeScript.
- Vite.
- Tailwind CSS.
- shadcn/ui y componentes Radix cuando corresponda.
- Lucide React para iconografía.
- React Router para navegación.
- TanStack Query para consumo y caché de API.
- Recharts para visualización de métricas.
- React Hook Form + Zod para formularios complejos.
- Playwright para pruebas end-to-end.

Usa versiones compatibles y mantenidas, verificando las disponibles en el entorno.

No agregues dependencias redundantes.

### Backend web

- FastAPI.
- Pydantic.
- Servicios Python existentes.
- SQLite y adaptadores actuales.
- Contratos REST documentados mediante OpenAPI.

No sustituyas el motor ni dupliques reglas de negocio en el frontend.

### Estructura sugerida

```text
AgenteGestionProyectos/
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   ├── components/
│   │   ├── features/
│   │   ├── layouts/
│   │   ├── hooks/
│   │   ├── services/
│   │   ├── types/
│   │   └── styles/
│   └── ...
├── src/
│   └── agente_gestion/
│       ├── api/
│       │   ├── routers/
│       │   ├── schemas/
│       │   └── dependencies/
│       ├── agents/
│       ├── application/
│       ├── domain/
│       └── adapters/
└── ...
```

Adapta la estructura si el repositorio ya ofrece una organización apropiada.

No crees otra aplicación Python independiente.

---

# 4. DIRECCIÓN VISUAL Y EXPERIENCIA DE USUARIO

## Estilo general

Diseña una interfaz **premium, moderna, elegante y tecnológica**, inspirada en la calidad visual y la usabilidad de Linear, Vercel Dashboard, GitHub y otras plataformas SaaS profesionales.

No copies literalmente sus interfaces.

La aplicación debe transmitir:

- Profesionalismo.
- Claridad.
- Fiabilidad.
- Tecnología e inteligencia artificial.
- Orden y jerarquía visual.
- Sensación de producto terminado.

### Identidad visual

Define un sistema de diseño coherente con:

- Paleta principal azul profundo, índigo y tonos neutros.
- Color de acento moderado para acciones principales.
- Estados semánticos de éxito, advertencia, error y desconocido.
- Tipografía moderna y legible, como Inter o Geist.
- Escala consistente de espaciados.
- Bordes suaves.
- Sombras discretas.
- Iconografía consistente.
- Gráficos visualmente integrados.
- Microinteracciones sutiles.

Evita degradados exagerados, fondos recargados, sombras enormes y componentes decorativos sin utilidad.

### Modo claro y oscuro

Implementa ambos temas de manera completa.

El tema oscuro debe estar diseñado específicamente, no limitarse a invertir colores.

Verifica contraste, legibilidad y colores de gráficas en ambos modos.

### Layout principal

Diseña una estructura tipo SaaS:

**Sidebar lateral:**
- Marca y nombre del producto.
- Dashboard.
- Proyectos.
- Tareas.
- Análisis de IA.
- Calidad de código.
- Propuestas.
- Informes.
- Calendario o programación.
- Auditoría.
- Configuración.

**Topbar:**
- Proyecto seleccionado.
- Navegación contextual.
- Búsqueda.
- Estado del sistema.
- Notificaciones reales o historial de eventos.
- Perfil y sesión.
- Selector de tema.

**Área principal:**
- Encabezados y breadcrumbs.
- Tarjetas informativas.
- Tablas avanzadas.
- Gráficos.
- Paneles de detalles.
- Modales y formularios.
- Notificaciones de acciones.
- Estados de carga.

### Diseño responsive

La aplicación debe funcionar correctamente en:

- Escritorio.
- Laptop.
- Tablet.
- Móvil.

El escritorio es la experiencia principal, pero el móvil debe permitir consultar información y realizar las operaciones compatibles.

La sidebar debe adaptarse a pantallas pequeñas.

Las tablas complejas deben permitir desplazamiento o presentación adaptativa sin romper la interfaz.

---

# 5. MÓDULOS FUNCIONALES

Implementa todas las pantallas siguientes integrándolas con el backend.

## Módulo 1 — Dashboard ejecutivo

Esta será la pantalla principal y debe ser especialmente cuidada.

Mostrar:

- Nombre del proyecto activo.
- Fecha del último análisis.
- Avance global del proyecto.
- Total de tareas.
- Tareas completadas.
- Tareas pendientes.
- Tareas en curso.
- Tareas bloqueadas.
- Actividades vencidas.
- Próximas a vencer.
- Riesgos detectados.
- Estado de calidad.
- Estado de las políticas.
- Recomendación del último informe.

### Visualizaciones

Implementa gráficas para:

1. Distribución de tareas por estado.
2. Evolución del avance entre capturas históricas.
3. Actividades críticas.
4. Resultados de políticas de calidad.
5. Historial de análisis, cuando existan datos suficientes.

No inventes series temporales a partir de una sola captura.

### Interacciones

El usuario debe poder:

- Cambiar el proyecto seleccionado.
- Abrir el detalle de una métrica.
- Consultar las tareas que generan una alerta.
- Acceder al último informe.
- Navegar a los hallazgos y propuestas.
- Solicitar la preparación de un nuevo análisis.

Todos los valores deben proceder del backend.

No escribir métricas fijas como 60 % dentro de componentes.

---

## Módulo 2 — Gestión de proyectos

Construye una vista profesional de proyectos.

Debe mostrar los proyectos realmente configurados o almacenados.

Para cada proyecto:

- Nombre.
- Identificador.
- Última actualización.
- Avance.
- Riesgos.
- Origen de datos.
- Estado del último análisis.

Permite acceder a los detalles y a su historial.

Si el backend todavía no admite crear múltiples proyectos de manera segura, no simules esa capacidad. Diseña el componente para crecer y deja claramente identificada la operación pendiente.

El piloto inicial será HORMIGESTION.

---

## Módulo 3 — Seguimiento de tareas

Construye una pantalla completa de gestión y consulta de tareas.

### Funcionalidades

- Tabla avanzada.
- Búsqueda por texto.
- Filtros por estado.
- Filtros por responsable.
- Filtros por fase.
- Filtros por criticidad.
- Ordenamiento por fecha.
- Paginación o virtualización cuando sea necesaria.
- Indicadores de tareas vencidas.
- Indicadores de datos desconocidos.
- Acceso a detalles.

### Panel de detalle de tarea

Al seleccionar una tarea, mostrar:

- Título.
- Descripción disponible.
- Estado normalizado.
- Fase de Trello.
- Responsables.
- Fechas.
- Prioridad explícita.
- Riesgos.
- Señales contradictorias.
- Dependencias verificadas.
- Historial observado.
- Evidencias de origen.

No inferir un estado de ejecución únicamente desde la lista de Trello.

No inventar responsables, prioridades o dependencias.

### Comparación histórica

Permitir seleccionar dos capturas y visualizar los cambios mediante un diff visual:

- Estado anterior y nuevo.
- Fechas modificadas.
- Responsables modificados.
- Cambios observables de planificación.
- Advertencias cuando falte evidencia.

Distinguir entre cambio observado y acción atribuida a una persona.

---

## Módulo 4 — Centro de Inteligencia Artificial

Esta pantalla debe mostrar claramente el valor diferencial del proyecto.

Presenta visualmente el pipeline:

**Monitoring → Quality → Verifier condicional → Report**

Cada agente debe tener una tarjeta de estado con:

- Nombre.
- Responsabilidad.
- Proveedor y modelo registrado.
- Estado de ejecución.
- Duración.
- Resultado.
- Errores o advertencias.
- Evidencia producida.

Distingue las etapas ejecutadas de las omitidas.

### Ejecución de análisis

Implementa una experiencia guiada para ejecutar el análisis del proyecto.

El usuario debe poder:

1. Seleccionar el proyecto.
2. Revisar la configuración aplicable.
3. Verificar las fuentes disponibles.
4. Revisar si se utilizarán proveedores remotos.
5. Confirmar expresamente la ejecución.
6. Consultar el progreso de las etapas.
7. Abrir los resultados cuando termine.

No lanzar automáticamente modelos de IA al abrir una página.

No ejecutar el pipeline completo durante una operación de simple consulta.

Implementa ejecución asíncrona o un mecanismo de trabajos adecuado para evitar que la petición HTTP quede bloqueada durante todo el análisis.

El backend debe registrar estados reales de ejecución y errores.

Si la implementación actual no permite seguimiento granular en vivo, muestra el estado persistido mediante polling controlado, sin inventar progreso.

### Histórico de ejecuciones

Mostrar:

- Fecha.
- ID de ejecución.
- Proyecto.
- Resultado.
- Modelos.
- Duración.
- Etapas completadas.
- Enlace al informe.

Cada ejecución debe poder inspeccionarse.

---

## Módulo 5 — Calidad de código

Visualiza los resultados de CodeQualityAgent y VerifierAgent.

Mostrar:

- Repositorio analizado.
- SHA del commit.
- Hallazgos candidatos.
- Severidad.
- Categoría.
- Regla asociada.
- Archivo.
- Línea.
- Descripción técnica.
- Evidencias.
- Estado de verificación.

### Detalle del hallazgo

Diseña una vista similar a un panel de revisión de código profesional.

Incluye fragmentos de código disponibles, número de línea y explicación del hallazgo.

Diferencia visualmente:

- Candidato sin verificar.
- Confirmado.
- Rechazado.
- Evidencia insuficiente.
- Pendiente de revisión humana.

No presentar todos los hallazgos de Quality como defectos confirmados.

Los fragmentos parciales deben identificarse como tales.

### Políticas

Mostrar resultados de Q1, Q2 y Q3.

Si faltan resultados de CI o cobertura, representar `unknown` o `evidencia_insuficiente`, nunca convertir datos ausentes en cero.

---

## Módulo 6 — Propuestas y aprobaciones

Implementa una interfaz visual para el circuito de gobernanza.

### Bandeja de propuestas

Mostrar:

- ID de propuesta.
- Proyecto y tarea.
- Acción solicitada.
- Valor anterior.
- Valor propuesto.
- Motivo.
- Impacto esperado.
- Evidencia.
- Estado de revisión.
- Estado de autorización.
- Estado de ejecución.

### Detalle y revisión

Diseña un panel que permita comparar claramente:

**Valor actual → Valor propuesto**

Incluir:

- Evidencia asociada.
- Motivos.
- Captura y versión.
- Historial de decisiones.
- Simulación de ejecución.
- Resultado y auditoría.

Las acciones deben respetar los contratos actuales.

### Seguridad fundamental

No implementar botones que modifiquen Trello directamente desde React.

Toda operación debe atravesar la API y el motor de autorización.

La revisión, aprobación del informe y autorización de una mutación son decisiones distintas.

**Existe una brecha previamente identificada en la verificación de identidad y tokens del backend H2. No habilites escrituras reales en Trello hasta corregirla y probarla.**

Mientras tanto, ofrece acciones de consulta, revisión y simulación claramente identificadas.

No basta con ocultar botones por rol: la autorización debe verificarse en el servidor.

---

## Módulo 7 — Informes y reportería

Construye un módulo para consultar informes generados.

Debe permitir:

- Listar informes.
- Consultar su fecha.
- Identificar la ejecución de origen.
- Visualizar métricas.
- Consultar riesgos.
- Ver recomendaciones.
- Consultar políticas.
- Ver la decisión humana.
- Abrir el informe completo.
- Exportar o descargar los formatos que el backend realmente produzca.

Renderiza Markdown de manera segura, evitando HTML peligroso y XSS.

### Informes semanales

Implementa una vista para:

- Consultar la programación semanal.
- Ver la próxima ventana.
- Consultar el historial.
- Revisar el outbox.
- Ejecutar un ciclo manual cuando esté permitido.
- Abrir archivos y recibos generados.

Distingue entre scheduler configurado y ejecución automática por un servicio del sistema.

No afirmar que existe ejecución desatendida si cron/systemd u otro disparador no está instalado.

---

## Módulo 8 — Auditoría y actividad

Construye un historial visual con:

- Ejecuciones de análisis.
- Decisiones humanas.
- Propuestas.
- Intentos de ejecución.
- Cambios verificados.
- Conflictos.
- Fallos.
- Eventos de seguridad.
- Entregas de informes.

Utiliza una línea temporal visual, filtros y paneles de detalle.

Conserva fechas y horas precisas.

Las marcas temporales deben interpretarse de acuerdo con la zona configurada, principalmente `America/Guayaquil`.

No atribuir autores a eventos cuando la identidad sea desconocida.

---

## Módulo 9 — Configuración

Diseña pantallas para:

- Configuración general del proyecto.
- Proveedores IA configurados.
- Modelos seleccionados.
- Repositorio GitHub.
- Tablero Trello.
- Políticas de calidad.
- Ventanas de vencimiento.
- Programación de reportes.
- Preferencias de interfaz.

Las claves API deben permanecer exclusivamente en el backend.

Nunca mostrar secretos completos ni entregarlos al frontend.

Distingue configuración consultable y modificable según las capacidades reales y permisos.

---

# 6. EXPERIENCIA DE USUARIO AVANZADA

La interfaz debe tener un nivel alto de refinamiento.

Implementa:

- Skeleton loaders.
- Estados vacíos diseñados.
- Estados de error comprensibles.
- Toasts de confirmación.
- Modales de confirmación.
- Diálogos para operaciones sensibles.
- Navegación mediante breadcrumbs.
- Búsqueda y filtros persistentes cuando corresponda.
- Tablas paginadas.
- Tooltips informativos.
- Transiciones sutiles.
- Formularios con validación.
- Feedback después de cada acción.
- Diseño consistente en todas las páginas.

Las animaciones no deben comprometer el rendimiento.

Usa accesibilidad adecuada:

- Navegación por teclado.
- Foco visible.
- Labels asociados.
- Contraste suficiente.
- Mensajes para lectores de pantalla.
- Estados no representados únicamente por color.

Toda interacción visible debe realizar una operación real o indicar claramente una limitación.

No crear botones decorativos sin funcionalidad.

---

# 7. API REST — INTEGRACIÓN REAL

Implementa una API FastAPI modular que reutilice los servicios de aplicación existentes.

Como referencia de organización, considera recursos como:

```text
/api/v1/projects
/api/v1/projects/{id}/summary
/api/v1/projects/{id}/tasks
/api/v1/projects/{id}/history
/api/v1/runs
/api/v1/runs/{id}
/api/v1/runs/{id}/stages
/api/v1/findings
/api/v1/proposals
/api/v1/reports
/api/v1/schedule
/api/v1/audit
/api/v1/quality
/api/v1/system/health
```

Estas rutas son propuestas. Antes de implementarlas, determina sus capacidades y contratos según el código existente.

### Reglas de integración

- No duplicar la lógica del dominio.
- No calcular métricas críticas nuevamente en JavaScript.
- No leer directamente SQLite desde el frontend.
- No ejecutar subprocess de la CLI como mecanismo permanente de API.
- Reutilizar los servicios Python.
- Utilizar DTOs tipados.
- Proporcionar contratos OpenAPI.
- Estandarizar respuestas y errores.
- Implementar paginación y filtros apropiados.
- Proteger rutas sensibles.
- Restringir CORS.
- No exponer variables secretas.
- No permitir escritura externa por defecto.

La API debe separar claramente las operaciones de consulta de las operaciones que modifican estado.

Las ejecuciones largas deben manejarse de manera controlada.

---

# 8. CALIDAD VISUAL: NO ACEPTAR UNA INTERFAZ GENÉRICA

El diseño debe sentirse como un producto comercial.

Exijo coherencia entre:

- Dashboard.
- Tablas.
- Paneles de detalle.
- Formularios.
- Gráficos.
- Informes.
- Flujos de aprobación.
- Menús y navegación.
- Estados de error y carga.

No utilices diferentes estilos visuales entre páginas.

No construyas toda la aplicación como tarjetas idénticas.

Usa correctamente jerarquía, espacios en blanco, densidad de información, tipografía y composición.

Evita introducir imágenes de stock sin relación con el software.

Prioriza interfaces con información, gráficas, tablas y componentes interactivos.

Toda la interfaz debe estar en español.

---

# 9. PRUEBAS Y CRITERIOS DE ACEPTACIÓN

El proyecto no se considerará completado solo porque React compile.

Debes verificar que los flujos principales funcionen realmente.

### Backend

- Pruebas de endpoints FastAPI.
- Validación de DTOs.
- Autorización.
- Manejo de errores.
- Integración con servicios existentes.
- Aislamiento de llamadas externas en tests.
- Regresiones del motor Python.

### Frontend

- TypeScript sin errores.
- Build exitoso.
- Validación de formularios.
- Estados de carga y error.
- Navegación.
- Componentes críticos.
- Manejo de respuestas vacías.
- Diseño responsive.
- Accesibilidad básica.

### End-to-end

Usa Playwright para verificar los recorridos esenciales:

1. Abrir el dashboard de HORMIGESTION.
2. Consultar las tareas y aplicar filtros.
3. Inspeccionar una tarea crítica.
4. Abrir un análisis histórico.
5. Revisar sus hallazgos.
6. Consultar el informe generado.
7. Abrir una propuesta y ejecutar una simulación autorizada.
8. Consultar auditoría y reportes semanales.

Para pruebas deterministas utiliza fixtures controladas y bases temporales.

Realiza también una demostración integrada con datos históricos auténticos de HORMIGESTION.

No realizar llamadas pagadas ni escrituras externas automáticamente durante los tests.

---

# 10. ESTRATEGIA DE IMPLEMENTACIÓN

No quiero que te limites a crear el frontend y dejes los endpoints pendientes.

Tampoco quiero implementar diez pantallas visuales con datos falsos antes de conectar el backend.

Trabaja por módulos verticales completos:

**API funcional → interfaz visual → integración → pruebas → refinamiento UX.**

### Etapa 1 — Fundamentos del producto

- FastAPI integrado al proyecto existente.
- Aplicación React/TypeScript.
- Sistema de diseño.
- Layout principal.
- Navegación.
- Temas claro/oscuro.
- Cliente API tipado.
- Estados globales necesarios.
- Mecanismo inicial de sesiones y seguridad, sin conceder privilegios ficticios.

### Etapa 2 — Seguimiento y dashboards

- Dashboard completo.
- Selector de proyectos.
- Tareas.
- Detalles.
- Historial.
- Gráficas.
- Métricas reales.

### Etapa 3 — Motor de IA y calidad

- Histórico de runs.
- Visualización de agentes.
- Inicio controlado de análisis.
- Seguimiento de ejecuciones.
- Hallazgos.
- Veredictos.
- Políticas.

### Etapa 4 — Gobernanza

- Propuestas.
- Revisión.
- Simulación.
- Decisiones autorizadas.
- Auditoría.
- Validaciones de acceso.

No habilites mutaciones externas hasta contar con autenticación y autorización verificables.

### Etapa 5 — Informes y configuración

- Informes.
- Historial.
- Programación semanal.
- Outbox.
- Configuración.
- Estados de proveedores.

### Etapa 6 — Refinamiento profesional

- Optimización visual.
- Responsive.
- Accesibilidad.
- Microinteracciones.
- Validación de flujos completos.
- Corrección de errores.
- Documentación.
- Preparación para presentación.

Cada etapa debe entregar funcionalidad terminada y conectada.

No interpretes esta división como autorización para detenerte después del primer dashboard. El alcance solicitado comprende todos los módulos.

Puedes continuar automáticamente entre etapas offline, siempre que las pruebas pasen y no aparezcan bloqueos de seguridad o decisiones arquitectónicas importantes.

---

# 11. SEGURIDAD Y CONTROL

No comprometas la seguridad del motor por mejorar la experiencia visual.

Reglas obligatorias:

- Ninguna clave API debe llegar al navegador.
- No almacenar tokens de proveedores en localStorage.
- Verificar sesiones y permisos en backend.
- Proteger operaciones contra CSRF cuando se utilicen cookies.
- Validar entradas.
- Evitar XSS.
- No permitir que los parámetros del frontend eludan reglas del dominio.
- No ejecutar escrituras en Trello sin autorización humana verificable.
- No utilizar roles declarados por el cliente como prueba de identidad.
- No realizar llamadas remotas de IA sin confirmación expresa del usuario.
- Preservar auditoría y trazabilidad.
- No alterar capturas históricas.

Revisa especialmente los límites de seguridad ya conocidos de H2 antes de implementar acciones privilegiadas.

---

# 12. ENTREGA FINAL

La entrega debe incluir:

1. Aplicación frontend profesional.
2. API FastAPI integrada.
3. Todos los módulos funcionales descritos.
4. Datos auténticos consultables de HORMIGESTION.
5. Flujo real de visualización de resultados multiagente.
6. Gestión visual de tareas e historial.
7. Propuestas, decisiones y simulaciones seguras.
8. Reportería y auditoría.
9. Temas claro y oscuro.
10. Diseño responsive.
11. Pruebas automatizadas.
12. Documentación de instalación y ejecución.
13. Comandos para iniciar frontend y backend en CachyOS.
14. Evidencia de pruebas y capturas para presentación.

Documenta cualquier funcionalidad que permanezca restringida por razones de seguridad o ausencia de capacidades backend.

No presentes operaciones simuladas como si fueran reales.

No modifiques arbitrariamente la arquitectura del motor.

No generes commits ni realices push sin autorización.

---

# INSTRUCCIÓN FINAL

**Comienza a implementar la interfaz web completa de AgenteGestionProyectos.**

Primero recupera el contexto del repositorio, identifica las interfaces existentes del motor y configura la arquitectura FastAPI + React.

Después construye los módulos de manera incremental hasta completar la plataforma.

No te detengas únicamente en una propuesta de diseño, un wireframe, un landing page o un dashboard mínimo.

Quiero ver una **aplicación web bonita, funcional, profesional, conectada al backend y preparada para una demostración académica completa con HORMIGESTION**.

Utiliza Gentle AI y sus skills para mejorar la calidad del trabajo, pero prioriza la implementación y las pruebas reales.

Al finalizar cada etapa, presenta qué funciona, qué archivos cambiaron, qué pruebas pasaron y cuál es la próxima etapa.

L2 — Setup: current branch/HEAD confirmed, no prior modifications. Baseline Python suite: 731 tests, OK (2 pre-existing skips), exit 0. Authentic SQLite inventory verified read-only: 16 runs, 8 captures, 9 reports, 8 findings, 3 verifications, 3 outbox receipts, no stored proposals; documented real report exists. T1/T2 workers launched together: backend mv7m1075-2-3w41, frontend mv7m108e-3-irls, disjoint edit surfaces. Parent coordination: task detail evidence/history contract must be explicit and no invented authors. .gitignore now excludes frontend dependencies/build/browser artifacts. Installed Gentle AI exploration/worker/verifier roles found. RDD switch on. Node 26.9.0 / npm 11.19.1 / Python 3.14.8. FastAPI web dependencies not yet installed. Scope is large because multi-stage deliverables, security boundaries and demonstration evidence cannot be resumed from a diff alone. Expected authored change exceeds 400 lines by necessity of requested full product; retain bounded vertical stages, no Git delivery permitted. No new engine architecture and no external writes. Initial read-only architecture map delegated. Backend and frontend workers will own disjoint trees; parent owns task tracking, documentation and integration evidence.

L3 — Interim coordination: T1/T2 workers remain active; API/query/security and frontend sources exist but no completion is verified. Task detail evidence/history DTO contract aligned; actors remain unknown. Initial npm audit exit 1: 2 high React Router package advisories; frontend reports targeted7.18.4 and audit0, final checks pending. Baseline731 is pre-H5 evidence only. Parent added loopback-only scripts/serve_web_demo.py for historical observer/temporary fixture modes with two CLI tests: RED2failures, GREEN2passes, exit0. New docs/interfaz-web.md explains local security/config; .env.example read denied by policy and never retried. No commits, paid calls or external writes.

L4 — Real HTTP seam: started parent-owned job-4 (API8000, helper temporary fixture) and job-5 (Vite5173, H5_API_PROXY_TARGET); ports were free, dependencies FastAPI0.143/uvicorn0.54/httpx0.28 installed. GET health/projects/summary/critical tasks returned200 both direct API and frontend proxy, with domain metrics and secret redaction. Browser smoke job-6 exited0: actual dashboard/tasks rendered without JS errors. Screenshots `.runtime/h5-browser/dashboard-fixture-light.png` and `tasks-fixture-light.png` visually inspected. Frontend owner asked to replace default black main-focus outline with themed keyboard treatment. Job-7 launched for real task detail/login/HttpOnly cookie/logout revocation, pending. Frontend self-tests remain intercepted fixtures, not integration proof. Router targeted update audit0 reported. Both T1/T2 writers still active; T3-T5 not implemented.

L5 — Frontend T1/T2 delivered (mv7m108e-3-irls): typecheck/build/9 intercepted E2E and audit0 passed, Router7.18.4; visual focus regression RED auto outline then GREEN. Job-7 failed exit1 due Playwright locator timeout: wrong submit label Iniciar sesión vs actual Verificar y continuar. Detail/evidence displayed, login never submitted: no session/cookie/logout integration proof. Do not replay that timed-out job. Persistent scripts/check_web_foundation.mjs now has correct public form label, theme/mobile/diff/session checks; execution pending final backend candidate. ASSESS returned unassessable because untracked files need explicit scope declaration; plan requires writer checks plus independent verifier (high). No native START while implementation incomplete. Parent reproduced new API bug with missing config+DB: GET/projects200 fabricated proyecto-demo, DB not created. Sent backend owner public regression/minimal correction; valid configured/stored discovery must stay covered. Backend still active; T1/T2 remain open, T3-T5 not started.

L6 — Backend T1/T2 delivered mv7m1075-2-3w41: missing/malformed-config project fabrication fixed with public RED2failures/1error then GREEN14focused; full747tests OK2skips, diffcheck0. Parent re-ran fullsuite once:747 OK2skips exit0 (.runtime/h5-t1-t2-seam.log). ASSESS again unassessable due untracked declaration, mandatory high-risk independent final verification retained. Stopped old parent servers4/5; jobs8API8000 and9Vite5173 serve final T1/T2candidate. Job10 running persistent real-API foundation integration (no interception), completion pending. Process-local sessions explicitly single Uvicorn worker.

L7 — Foundation integration complete: job12 exit0 using actual FastAPI/SQLite fixture without interception: dashboard light/dark, task details/evidence/capture diff, verified operator/HttpOnly/SameSite=Strict, no credential in localStorage, CSRF logout/revocation, mobile navigation/no overflow/noJSerrors. Prior job10 failed pre-browser startup race (Vite ECONNREFUSED); diagnosed separately; job11 passed UI/diff/login then failed parent incorrect Lax cookie expectation. Actual backend Strict is stronger; checker corrected, backend not weakened. Public HTTP login200/logout204/revocationfalse additionally observed. T1 done functionally, independent security verification remains T6. T2 stays active because executive task-distribution/critical/quality/policy/latest-report/run metrics are not complete; finish with T3 resources. Preparing paired T3 API/UI continuations. docs/interfaz-web.md includes verified install/start/check commands.

L8 — T2/T3 paired continuations launched together: backend mv7nozlt-4-4p09, frontend mv7nozn4-5-6yvy, same disjoint surfaces. Frontend identified missing exact T3 wire contract; backend prioritized docs/h5-api-contract.md typed paths/DTOs/prepare-confirm/remoteconsent/capabilities/terminalstates, frontend proceeds existing T2 state_counts/counts charts without fabricated shapes. Parent added observational scripts/check_web_history.py (data proof, no new engine behavior/meaningful RED): actual API health/projects/summary/tasks/history200, 31total/30included/18completed/60% from backend,5HORMIGESTIONcaptures,2criticaltasks,4storedprojects. Zero remote HTTP calls;90 historical database/artifact fingerprints unchanged. Expected oldest-newest409comparacion_no_disponible documented as evidence limitation, no invented diff. Proof .runtime/h5-authentic-readonly.json. This supersedes the first probe's unconditional200 assertion failure. Foundation light/dark/mobile screenshots visually inspected after focus fix; complete-product screenshots/authentic browser presentation still pending. T2 stays active; T3 implementation in progress inside grouped pair, not verified. T4/T5 not started. Guide now includes install/start/fixturecheck/authentichistory commands.

L9 — T2/T3 complete functionally: backend mv7nozlt-4-4p09 delivered23web/756suiteOK2skips, frontend mv7nozn4-5-6yvy delivered26intercepted/typecheck/build/audit0 (partial only because parent actualHTTPproof missing). Parent helperfixture analysis support RED503→GREEN3tests; fake config/executor/ledger isolated, historical analysis forcedoff. Parent seam757tests OK2skips (.runtime/h5-t2-t3-seam.log). ActualAPI/proxy7newresources200; job14 actualChromium exit0: executive dashboard, persisted historical run/omittedVerifier, noanalysis-onconsultation, insufficientfinding/partialcode/redaction, explicitprepare/confirm/durableterminaljob/polling. Job used injectedfixtureexecutor returningexisting simulatedrun, NOTengine/provider/sourceexecution. Screenshots .runtime/h5-browser/executive-fixture-light.png,analysis-fixture-light.png,finding-fixture-light.png,confirmed-fixture-job.png. ASSESS unassessable due untracked explicit declaration; independenthighriskfinalverify retained. Process-local sessions/preparations1worker; non-failing SQLiteResourceWarning noted. T4/T5 now next paired unit; preserve user prohibition of commits/push/remote autoexecution.

L10 — T4/T5 grouped writers launched together after T2/T3 seam: backend mv7p3t8b-7-zzft, frontend mv7p3t7i-6-9yk0, disjoint existing surfaces. Backend must publish exact governance/report/schedule/config contracts early; safe core dry-run only, H2 external-write gap not repaired or enabled. Frontend must replace all remaining pending routes and safely render Markdown (skipHTML/default safe URL transform/no external-image beacons). Manual weekly cycle server-gated/confirmed/asynchronous, never claim unverified system trigger. Parent README links H5 guide/contract and accurately says platform integration ongoing. T3 analysis/finding screenshots visually inspected; final full-product refinement/screenshots remain T6. Parent job13 API fixture8000 andjob9Vite5173 remain running; fixture job14 already created one job, so fresh fixture required for next clean integration. All previous actual proof and failures retained.

L11 — T4/T5 frontend mv7p3t7i-6-9yk0 returned PARTIAL: only basic Settings (existing project/health metadata and local theme), typecheck/build/28intercepted/audit0 passed. Proposals/reports/schedule/audit and full config metadata NOT implemented. Backend mv7p3t8b-7-zzft stillactive33turns64calls/read, contractT4/T5 absent. Parent reprioritized next backend action to publish exact agreed typed interfaces before more handlers; resume frontend only after interface exists. This corrects routing dependency: disjoint paths did not make unagreed interface work independent. No human blocker. ASSESS again unassessable/untracked declaration→highfinalverify. Parent job15 actualHTTP basicSettings/keyboardtheme/noPOST proof exited0: preferences selectable by keyboard in both themes, backend read-only state visible, noPOST orJSerrors. Screenshots settings-basic-light.png/settings-basic-dark.png. This proves only basicmetadata/theme, not missingfullconfig/governance/reporting. FullT4/T5 and final8journeys remain open; don't treat28intercepted tests as appcompletion.

L12 — Backend mv7p3t8b-7-zzft delivered stable T4/T5 contract and handlers:33webtests/767suiteOK2skips reported. Added control_plane/control_schemas/weekly_cycles plus routes/settings/fixtures/governancetests. Parent read contract+decisionpath and reproduced reported non-atomicreport/audit on isolatedfixture(publicHTTP503butpending→approved+1history). Correction mv7qsux7-8-4ef2 nowactive: realDBfaultRED→atomicpersist/rollback/idempotency/legacycompat, narrowAPI+storagehelper+tests+contract. No authenticmutation, paidcalls, stagingorcommits.

L13 — Frontend continuation mv7qsuz8-9-86zd returned interaction_required, NOcodewrites: dependency react-markdown absent, published10.1.0. It requests exact cd frontend && npm install --save-exact react-markdown@10.1.0 authorization before implementing remainingT4/T5. Existing28intercepted/typecheck/build/audit0passed again but no newmoduleproof. Complete lossless two-choice envelope must be relayed; T5blocked on human dependency authorization, T4backendcorrective active. Oncehumananswers return exact1/2once to sameblockedactor; do not infergrant from thishistoricalnote.

L14 — Human selected canonical token `1` for: "Aprobar: `cd frontend && npm install --save-exact react-markdown@10.1.0` (modifica `frontend/package.json`, `frontend/package-lock.json` y `frontend/node_modules`)." Relayed once to same frontend blockedactor; active continuation mv80kq8p-a-xw2c. This resolves dependency permission only; no new publication, externalwrite or paidAI authority.

L15 — Atomic corrective backend mv7qsux7-8-4ef2 completed: real SQLite abort-trigger RED exposed report approved/proposal approved_offline/dryrunreceipt after503; GREEN now commits domainwrites+verifiedaudit atomically with rollback/retry/replay/versionbinding and existingCLIcompatibility.35webtests/769fullOK2skips reported. Parentno furthercodefix; independentglobal verifier stilldue. Corestorage modifications deliberately narrow.

L16 — Parentdemo helper extended only disposablefixture: governance/reportdecision/manualsqlitecycle enabled, tempoutbox, fixedclock2026-01-08T18Z andThursday13:00Guayaquil. Historicalmodeforcesallflagsfalse/operatorNone. Publichelper test observedREDmissingcapabilities→GREEN4tests; addedcheck_web_engine --help testcase separatelypassed1test. docs/interfaz-web.md updated switches/atomicity/knownactors/scheduler-not-verified/fixedfakeclock/limits; fullUI explicitlyunfinished. OldAPIjob13stopped; freshAPIjob19running, readinessjob20exit0 (healthok/external_writes_enabledfalse). Vitejob9unchanged.

L17 — Default-executor functionalproof: job16 failed BEFOREconfirmation because genericfixture emptyYAML resolvesOllama; job17 failed BEFOREconfirmation due wrong assertion server_allowedfalse. Diagnosed analysis_jobs.py251: server_allowed=notremote_ai_required ORallow_remote_ai, so vacuouslytrueforallfake. Correctedprobe enforcesfixtures+fakeproviders/no remotenecessity/remote_consentfalse/HTTPtransportblocked. Job18exit0: actualdefaultwebexecutor→actualorchestrator created run-20261014-062406-886245 and rep-run-20261014-062406-886245, persistedstages/report,0remotecalls, tempdata/artifactsdeletednormally. Evidence.runtime/h5-offline-engine.json. No injectedexecutor; sources/LLMstillfake, NOTauthenticpipeline/paidAIproof. Full8UIjourneys andindependentfinalverify stillpending.

L18 — Frontend mv80kq8p-a-xw2c delivered ALLremainingT4/T5 modules, exactreact-markdown10.1.0 installedonce, newtargeted9tests/typecheck/buildgreen; parentfullseam771PythonOK2skips17.574s (.runtime/h5-final-python.log), frontendjob21all37E2EinterceptedOK16s/npm audit0. No source edits sinceworker. Job22realHTTPfoundation/intelligencePASS (sameT3injectedfixtureexecutor distinction), governanceonlyfailedscriptdownloadpathdouble-slashbeforeprivilegedmutations; normalizedpathonly, job27GOVERNANCE_INTEGRATION_OKallrealAPI/coreproposal/weekly/report/audit/config/mobile. Evidence.runtime/h5-governance-proof.json/screenshots. T4/T5functionaldone, globalhighriskverify/RDDstillT6.

L19 — Authenticbrowserprebaseline90files .runtime/h5-authentic-browser-before.json; historicalAPIjob23port8001 andVitejob24port5174running, readiness25PASS. Authenticjob26exit1timeout at comparisonwaitForResponse: sourceTasksPage85sortsasc/defaultoldest→latest, scriptselectedalreadyactiveoldest soquerykeyunchanged/noGET. Finallyproofshows90unchanged/noPOST/noexternalbrowserrequests/noJSerrors and actual31total30included18done60%,5projectcaptures5runs3reports0proposals; dashboardandcriticaltaskscreenshotsproduced, restNOTproved. CheckerfixedlistenBEFOREComparebuttonclick+assertpair/409visiblemessage+completedflag, but notauto-rerun aftertimeout. Exactpermissionforcorrectedcheckpending. No historicaldata mutation.

L20 — Independentverifier mv81wo4o-b-3oli completed: readwhole993-linefeature/S1–S15, baselineb692e54 unchanged, source-readonly and oneisolatedscratchAPIsecurity/governanceprobe19testsOK12.406s. Noobservedhighriskcodeblockers. PASS S1-S11/S13/S14 (visualscopequalified); S12partialauthenticbrowser/S15staledocstatusadvisory. Parentcorrectedguide+contractstatusanddocumentedactualevidence/limits; noredundantverifier orsourcecorrectionneeded. NativeRDDstillpending, noinspect/start yet.

L21 — Humanselected `run_corrected_readonly_demo` / "Ejecutar el chequeo corregido". Parentexecuted exact node scripts/check_web_historical_browser.mjs once asjob28. It alsoFAILEDexit1after13s atwaitForResponse10s. Noautomaticretry/restart/furtherverification. Priorstaticdiagnosis(defaultselectionreuse) was insufficient to resolve secondtimeout; doNOTclaimconclusivecause orcompleteauthenticdemo. Finallyhistoricalfingerprintcomparison didnotfail;proof.completedfalse. S12remainsunproved, T6blockedpendingonehuman decision on boundeddiagnosis+explicitnewattempt versus preservingmissingproof. RDDnotstarted whileclosureproofincomplete; no authorityconsumed, no commits/staging/push/externalmutations.
