# MultiRepo for AgenteGestionProyectos
Enable isolated per-repository analysis and CI evidence with compatible historical contracts.
Branch: feature/multirepo; base: 99161b289996c8af4b1030ea30142c058fb57ad5; delivery: auto-chain; chain_strategy: feature-branch-chain; checks: .venv/bin/python -m unittest, frontend npm typecheck/build/Playwright; EXTERNAL_WRITES_ENABLED=false.

## Specs

### S1

> # Tarea: Implementar MultiRepo en AgenteGestionProyectos
>
> ## Contexto verificado
>
> Repositorio: `Saimol-Uta/AgenteGestionProyectos`.
>
> Rama base: `codex/frontend-manager-phase1`.
>
> HEAD de referencia al realizar la auditoría: `99161b289996c8af4b1030ea30142c058fb57ad5`. Confirmar nuevamente antes de crear la rama de trabajo.
>
> Objetivo: permitir que un único proyecto analice varios repositorios GitHub, empezando por HORMIGESTION:
>
> - `Josu-F1/hormigestion-backend`, rama `develop`.
> - `Josu-F1/hormigestion-frontend`, rama `develop` (privado).
>
> Ambos repositorios productores ya cuentan con CI Quality Metrics y un contrato compatible `ci-metrics.v1`.
>
> ## 1. Auditoría inicial
>
> Antes de modificar código, comprobar el estado de la rama y revisar los componentes existentes.
>
> La revisión previa identificó:
>
> - `application/config.py`: configuración de un repositorio.
> - `api/settings.py`: configuración API con repositorio individual.
> - `api/analysis_jobs.py`: preparación y preflight vinculados a repositorio/SHA individual.
> - `adapters/sources/factory.py`: construcción de fuentes individuales.
> - `adapters/sources/composite.py`: recopilación conjunta de Trello, GitHub y CI.
> - `adapters/sources/actions.py`: consulta de ejecuciones CI sin extraer todavía métricas de artefactos.
> - `application/orchestrator.py`: selección de un CI coincidente.
> - `domain/code.py`, `domain/findings.py`, `domain/snapshot.py`, `domain/report.py`: modelos que requieren adaptación.
> - `adapters/storage/sqlite.py`: persistencia existente.
> - `api/repository.py`, `api/schemas.py`, `api/routes.py`: consultas y API.
> - `frontend/src/api/types.ts` y vistas React: contratos y visualización actuales.
>
> Confirmar cuáles de estas limitaciones continúan presentes en el código local.
>
> Evitar duplicar funcionalidades, reescribir adaptadores funcionales o eliminar compatibilidad.

### S2

> ## 2. Configuración MultiRepo
>
> Incorporar una colección de repositorios asociados a cada proyecto.
>
> Cada entrada deberá identificar:
>
> - ID único.
> - Propietario y repositorio.
> - Rama base.
> - SHA objetivo resuelto.
> - Estado de permisos y disponibilidad.
>
> Conservar la configuración heredada:
>
> - `GITHUB_OWNER`
> - `GITHUB_REPO`
> - `GITHUB_BRANCH`
> - `GITHUB_COMMIT_SHA`
>
> Definir precedencia entre formato nuevo y heredado, con detección de contradicciones.
>
> Actualizar `config/hormigestion.yaml` para contemplar frontend y backend sobre `develop`, sin reutilizar el SHA histórico fijado para un análisis nuevo.
>
> El SHA debe resolverse y quedar fijado individualmente al iniciar cada ejecución.

### S3

> ## 3. Reutilización de fuentes
>
> Conservar:
>
> - `GitHubCodeSourceAdapter`
> - `GitHubActionsSourceAdapter`
> - `CompositeSourcesAdapter`
> - Puertos de recopilación y transporte HTTP limitado.
>
> Extender la fábrica y composición de fuentes para permitir varios repositorios.
>
> Requisitos:
>
> 1. Resolver SHA por repositorio.
> 2. Recopilar diffs y CI del commit correspondiente.
> 3. Mantener identidad y evidencia aisladas.
> 4. Registrar fallos individuales.
> 5. Preservar los resultados válidos ante un fallo parcial.
> 6. Aplicar límites globales y por repositorio de llamadas HTTP, archivos, bytes, tiempo y tokens.
> 7. Evitar asociaciones de CI entre repositorios o commits diferentes.
>
> No ejecutar análisis sobre un SHA distinto al fijado sin reportar explícitamente el cambio.

### S4

> ## 4. Consumo de métricas CI v1
>
> Utilizar como contrato de referencia:
>
> https://github.com/Josu-F1/hormigestion-backend/blob/develop/schemas/ci-metrics.v1.schema.json
>
> Documentación:
>
> https://github.com/Josu-F1/hormigestion-backend/blob/develop/docs/contracts/ci-metrics.v1.md
>
> Ambos repositorios publican artefactos bajo:
>
> `ci-quality-<SHA>-<run_id>-<attempt>`
>
> El documento interoperable es `ci-metrics.json`.
>
> Implementar una extensión reutilizable del adaptador GitHub Actions que:
>
> - Consulte ejecuciones CI del repositorio y SHA objetivo.
> - Seleccione la ejecución e intento apropiados.
> - Obtenga metadatos y descargue el artefacto.
> - Valide el JSON Schema v1 y las reglas semánticas.
> - Verifique repositorio, SHA, run ID y attempt.
> - Compare los valores declarados con GitHub Actions.
> - Compruebe el resultado final del workflow.
> - Rechace identidades inconsistentes, archivos corruptos y ZIP inseguros.
> - Aplique límites de descarga, descompresión y contenido.
> - Gestione artefactos ausentes, expirados y errores de permisos.
>
> No interpretar un workflow verde sin métricas válidas como evidencia completa.
>
> No utilizar un SHA de merge sintético para certificar el commit fuente de un PR.
>
> Preservar `null` cuando una métrica sea desconocida.
>
> No modificar los workflows productores salvo incompatibilidad demostrada y autorizada.
>
> ### 3. Seguridad de artefactos CI
>
> En la descarga de GitHub Actions:
>
> - Verificar los destinos de las redirecciones HTTP.
> - No reenviar tokens a orígenes no autorizados.
> - Aplicar límites al ZIP comprimido y a su contenido.
> - Rechazar archivos duplicados ambiguos, rutas inseguras, contenido malformado y archivos excesivos.
> - Validar el esquema v1 y la identidad completa del artefacto.
> - Distinguir el resultado del workflow de la validez del documento de métricas.
>
> ### 4. Selección de ejecuciones e intentos
>
> Utilizar una política determinista de selección de workflow, run e intento.
>
> No sustituir un intento fallido, pendiente o incompleto por uno anterior exitoso.
>
> La evidencia histórica puede mostrarse como referencia, pero nunca certificar silenciosamente el SHA objetivo de la ejecución actual.

### S5

> ## 5. Modelos y orquestación
>
> Extender los modelos existentes de forma compatible:
>
> - `CodeDiffSnippet`
> - `CIRunResult`
> - `CandidateFinding`
> - `WeeklySnapshot`
> - `SourceCollectionResult`
> - `FinalReport`
>
> Conservar identidad de repositorio, SHA, ejecución CI y procedencia.
>
> Evitar colisiones entre hallazgos y evidencias mediante identidades compuestas o identificadores inequívocos.
>
> Actualizar `ProjectOrchestrator`, `CodeQualityAgent`, `VerifierAgent` y `QualityEvaluator` para procesar los repositorios sin mezclar contextos.
>
> Cada repositorio debe contar con una evaluación propia.
>
> El informe consolidado debe conservar:
>
> - Estado individual de cada repositorio.
> - Pruebas ejecutadas y fallidas, cuando se conozcan.
> - Cobertura individual y su base de medición.
> - Hallazgos y verificaciones por repositorio.
> - Problemas de acceso o evidencia insuficiente.
> - Resultado conjunto con incertidumbres explícitas.
>
> No promediar coberturas de frontend y backend ni presentarlas como cobertura global del proyecto.
>
> No convertir estados desconocidos en aprobados.

### S6

> ## 6. Persistencia y API
>
> Ampliar la persistencia SQLite mediante cambios aditivos y compatibles con históricos.
>
> Reutilizar la infraestructura de snapshots, evidencias, ejecuciones e informes.
>
> Los resultados deberán poder consultarse por:
>
> - Proyecto.
> - Repositorio.
> - Commit.
> - Ejecución CI.
> - Intento.
>
> Evitar duplicados al volver a recopilar un mismo artefacto.
>
> Actualizar los endpoints y modelos de API para exponer resultados individuales y consolidados, manteniendo compatibilidad con los consumidores anteriores.
>
> Actualizar también `api/analysis_jobs.py`, incluyendo preflight, fingerprint de configuración y preparación del análisis MultiRepo.
>
> No permitir que el fingerprint de un repositorio certifique la configuración de otro.
>
> ### 2. Compatibilidad de contratos históricos
>
> Preservar los contratos actuales de monorrepositorio.
>
> Para configuraciones MultiRepo, incorporar colecciones nuevas sin asignar arbitrariamente un repositorio principal.
>
> Cuando los registros históricos no tengan identidad de repositorio recuperable, conservar esa incertidumbre en lugar de inventar su procedencia.
>
> Documentar las reglas de compatibilidad entre API antigua y nueva.

### S7

> ## 7. Interfaz React
>
> Reutilizar las vistas actuales de proyectos, análisis, calidad e informes.
>
> Incorporar:
>
> - Listado de repositorios del proyecto.
> - Estado CI individual.
> - Cobertura individual con alcance explícito.
> - Filtros de hallazgos por repositorio.
> - Identificación de commit y workflow.
> - Estado de permisos y evidencias insuficientes.
> - Resumen consolidado del proyecto.
>
> Conservar las acciones y decisiones humanas existentes.
>
> Actualizar tipos TypeScript, consultas y pruebas E2E correspondientes.

### S8

> ## 8. Seguridad: condición crítica
>
> La auditoría detectó `external_writes_enabled: true` en `config/hormigestion.yaml` y un valor predeterminado `True` en `api/settings.py`.
>
> Antes de implementar, analizar el efecto de esta configuración y los controles de escritura existentes.
>
> Para esta tarea:
>
> - Ejecutar análisis y pruebas con `EXTERNAL_WRITES_ENABLED=false`.
> - No ejecutar escrituras remotas de GitHub, Trello ni servicios externos.
> - No realizar modificaciones productivas.
> - No eliminar los mecanismos actuales de aprobación humana.
> - No cambiar silenciosamente políticas operativas existentes.
> - Proponer cualquier corrección de valores predeterminados si supone alterar comportamiento anterior.
>
> Toda acción futura sobre código debe quedar vinculada al repositorio y SHA del hallazgo que la originó, sin redirección hacia otro repositorio.
>
> ### 1. Seguridad de acciones MultiRepo
>
> Toda decisión sobre un hallazgo debe conservar y verificar su repositorio y SHA de origen.
>
> Revisar especialmente `api/finding_actions.py`, `GitHubCodeActionAdapter`, los servicios de decisiones y sus contratos.
>
> No permitir que una acción originada en frontend termine ejecutándose sobre backend, ni viceversa.
>
> Rechazar solicitudes ambiguas, inconsistencias de procedencia o intentos de redirección entre repositorios.
>
> Mantener intactos los mecanismos existentes de aprobación humana, autorización, idempotencia y auditoría.
>
> ### 5. Seguridad durante la implementación
>
> Mantener `EXTERNAL_WRITES_ENABLED=false` durante esta tarea.
>
> Verificar que API, motor y adaptadores respeten esta restricción.
>
> Las pruebas deben usar transportes simulados o mecanismos equivalentes para demostrar que no realizan escrituras externas en GitHub, Trello, correo ni otros servicios.
>
> No registrar tokens ni secretos.

### S9

> ## 9. Pruebas obligatorias
>
> Cubrir como mínimo:
>
> - Configuración heredada de un repositorio.
> - Configuración MultiRepo de dos repositorios.
> - SHA independientes.
> - Resolución de `develop`.
> - Validación de artefactos reales o fixtures fieles de frontend y backend.
> - Identidades incorrectas y SHA de merge sintético.
> - Diferentes intentos de Actions.
> - Fallos parciales.
> - Frontend privado sin permisos.
> - Artefactos ausentes, expirados o corruptos.
> - Métricas desconocidas.
> - Persistencia y lectura histórica.
> - Aislamiento de hallazgos.
> - Seguridad de acciones GitHub.
> - API, frontend y pruebas E2E.
> - Ausencia de escrituras externas durante las pruebas.

### S10

> ## 10. Criterios de aceptación
>
> El trabajo estará listo cuando una sola ejecución analice los dos repositorios de HORMIGESTION y:
>
> 1. Cada repositorio conserve su SHA y evidencias.
> 2. Los artefactos CI v1 se validen correctamente.
> 3. Las métricas se asignen al repositorio y commit correctos.
> 4. Los fallos parciales se representen sin descartar resultados válidos.
> 5. Los informes consolidados no inventen métricas.
> 6. La interfaz permita explorar resultados por repositorio.
> 7. Los proyectos históricos de un repositorio continúen funcionando.
> 8. Las pruebas automatizadas pasen.
> 9. Se demuestre que no hubo escrituras externas.
>
> ### Criterio final de aceptación
>
> Debe demostrarse una ejecución MultiRepo de HORMIGESTION donde backend y frontend conserven sus SHA, CI, métricas, procedencia y hallazgos independientes, y se genere un informe consolidado sin inventar información.
>
> Deben mantenerse funcionales los proyectos históricos monorrepositorio y las decisiones humanas.
>
> Al finalizar, entregar una matriz de cumplimiento, resultados de pruebas, evidencia de integración y limitaciones pendientes.
>
> **Autorizo crear la rama e implementar. No autorizo push, merge ni escrituras externas.**

### S11

> ## 11. Flujo de trabajo y entrega
>
> Trabajar en una rama feature nueva basada en `codex/frontend-manager-phase1`.
>
> Primero presentar un diagnóstico breve con:
>
> - Capacidades reutilizables.
> - Cambios realmente necesarios.
> - Riesgos de compatibilidad.
> - Diseño propuesto de configuración y modelos.
> - Plan de implementación por etapas.
>
> No comenzar cambios estructurales hasta validar dicho diseño.
>
> Tras esa validación, implementar por etapas:
>
> 1. Configuración y dominio.
> 2. Recopilación MultiRepo y métricas CI.
> 3. Orquestación y evaluación.
> 4. Persistencia y API.
> 5. React e integración E2E.
>
> Entregar resultados y pruebas de cada etapa.
>
> No realizar push ni merge automáticamente. No modificar la rama base directamente ni activar escrituras externas.
>
> Valido el diseño propuesto para implementar MultiRepo en AgenteGestionProyectos.
>
> Autorizo comenzar la implementación, tomando como base `codex/frontend-manager-phase1` y creando una rama nueva `feature/multirepo`.
>
> Mantén los defaults operativos actuales por compatibilidad, pero ejecuta esta tarea con `EXTERNAL_WRITES_ENABLED=false`. No cambies los valores predeterminados globales sin una revisión separada.
>
>
> ### 6. Ejecución por etapas
>
> Autorizo seguir el plan propuesto:
>
> 1. Configuración y dominio.
> 2. Recopilación MultiRepo y métricas CI v1.
> 3. Orquestación, agentes y evaluación.
> 4. Persistencia SQLite y API.
> 5. React, pruebas E2E e integración.
>
> En cada etapa:
>
> - Preservar las funciones existentes.
> - Añadir pruebas de regresión y compatibilidad.
> - Registrar los cambios en la documentación ODD.
> - Realizar commits pequeños, descriptivos y revisables.
> - Informar resultados y cualquier bloqueo relevante antes de continuar con la siguiente etapa.
>
> No introducir cambios estructurales fuera del alcance sin justificarlos.
>
> pausemos el subagente, voy a salir y llegar el equipo
>
> ya puedes continuar

## Tasks

- T1 | S2,S5,S8,S9 | delegated/context-backstop (parent 83,382 tokens) | in_progress | Configuration and additive domain contracts; checks green, clean review worktree created; domain native medium/reviewDue=false, configuration commit pending | commit: 7220191c07c388aa1b4211888be31e5e93c32129 (domain), configuration pending
- T2 | S3,S8,S9 | delegated/parallel-independent repo collection vs artifact parser | pending | Per-repository pinning/composition and shared budgets | commit: pending
- T3 | S4,S8,S9 | delegated/parallel-independent artifact parser vs repo collection | pending | Safe CI v1 artifact download/validation | commit: pending
- T4 | S3,S4,S8,S9 | inline | pending | Deterministic Actions selection and artifact adapter integration | commit: pending
- T5 | S5,S8,S9,S10 | inline | pending | Isolated orchestration, agents, evaluator and consolidated report | commit: pending
- T6 | S6,S8,S9 | inline | pending | Additive SQLite persistence and historical/idempotent queries | commit: pending
- T7 | S2,S6,S8,S9 | inline | pending | API collections, filters, preflight and fingerprints | commit: pending
- T8 | S5,S6,S8,S9 | inline | pending | Repository/SHA-bound human finding decisions and actions | commit: pending
- T9 | S7,S8,S9 | inline | pending | Existing React views/types/query isolation and E2E | commit: pending
- T10 | S1,S2,S3,S4,S5,S6,S7,S8,S9,S10,S11 | inline + independent verifier/item 2 | pending | Integration, compatibility matrix, no-external-writes proof and final verification | commit: pending

## Log

### L1 — Original request (verbatim)

# Tarea: Implementar MultiRepo en AgenteGestionProyectos

## Contexto verificado

Repositorio: `Saimol-Uta/AgenteGestionProyectos`.

Rama base: `codex/frontend-manager-phase1`.

HEAD de referencia al realizar la auditoría: `99161b289996c8af4b1030ea30142c058fb57ad5`. Confirmar nuevamente antes de crear la rama de trabajo.

Objetivo: permitir que un único proyecto analice varios repositorios GitHub, empezando por HORMIGESTION:

- `Josu-F1/hormigestion-backend`, rama `develop`.
- `Josu-F1/hormigestion-frontend`, rama `develop` (privado).

Ambos repositorios productores ya cuentan con CI Quality Metrics y un contrato compatible `ci-metrics.v1`.

## 1. Auditoría inicial

Antes de modificar código, comprobar el estado de la rama y revisar los componentes existentes.

La revisión previa identificó:

- `application/config.py`: configuración de un repositorio.
- `api/settings.py`: configuración API con repositorio individual.
- `api/analysis_jobs.py`: preparación y preflight vinculados a repositorio/SHA individual.
- `adapters/sources/factory.py`: construcción de fuentes individuales.
- `adapters/sources/composite.py`: recopilación conjunta de Trello, GitHub y CI.
- `adapters/sources/actions.py`: consulta de ejecuciones CI sin extraer todavía métricas de artefactos.
- `application/orchestrator.py`: selección de un CI coincidente.
- `domain/code.py`, `domain/findings.py`, `domain/snapshot.py`, `domain/report.py`: modelos que requieren adaptación.
- `adapters/storage/sqlite.py`: persistencia existente.
- `api/repository.py`, `api/schemas.py`, `api/routes.py`: consultas y API.
- `frontend/src/api/types.ts` y vistas React: contratos y visualización actuales.

Confirmar cuáles de estas limitaciones continúan presentes en el código local.

Evitar duplicar funcionalidades, reescribir adaptadores funcionales o eliminar compatibilidad.

## 2. Configuración MultiRepo

Incorporar una colección de repositorios asociados a cada proyecto.

Cada entrada deberá identificar:

- ID único.
- Propietario y repositorio.
- Rama base.
- SHA objetivo resuelto.
- Estado de permisos y disponibilidad.

Conservar la configuración heredada:

- `GITHUB_OWNER`
- `GITHUB_REPO`
- `GITHUB_BRANCH`
- `GITHUB_COMMIT_SHA`

Definir precedencia entre formato nuevo y heredado, con detección de contradicciones.

Actualizar `config/hormigestion.yaml` para contemplar frontend y backend sobre `develop`, sin reutilizar el SHA histórico fijado para un análisis nuevo.

El SHA debe resolverse y quedar fijado individualmente al iniciar cada ejecución.

## 3. Reutilización de fuentes

Conservar:

- `GitHubCodeSourceAdapter`
- `GitHubActionsSourceAdapter`
- `CompositeSourcesAdapter`
- Puertos de recopilación y transporte HTTP limitado.

Extender la fábrica y composición de fuentes para permitir varios repositorios.

Requisitos:

1. Resolver SHA por repositorio.
2. Recopilar diffs y CI del commit correspondiente.
3. Mantener identidad y evidencia aisladas.
4. Registrar fallos individuales.
5. Preservar los resultados válidos ante un fallo parcial.
6. Aplicar límites globales y por repositorio de llamadas HTTP, archivos, bytes, tiempo y tokens.
7. Evitar asociaciones de CI entre repositorios o commits diferentes.

No ejecutar análisis sobre un SHA distinto al fijado sin reportar explícitamente el cambio.

## 4. Consumo de métricas CI v1

Utilizar como contrato de referencia:

https://github.com/Josu-F1/hormigestion-backend/blob/develop/schemas/ci-metrics.v1.schema.json

Documentación:

https://github.com/Josu-F1/hormigestion-backend/blob/develop/docs/contracts/ci-metrics.v1.md

Ambos repositorios publican artefactos bajo:

`ci-quality-<SHA>-<run_id>-<attempt>`

El documento interoperable es `ci-metrics.json`.

Implementar una extensión reutilizable del adaptador GitHub Actions que:

- Consulte ejecuciones CI del repositorio y SHA objetivo.
- Seleccione la ejecución e intento apropiados.
- Obtenga metadatos y descargue el artefacto.
- Valide el JSON Schema v1 y las reglas semánticas.
- Verifique repositorio, SHA, run ID y attempt.
- Compare los valores declarados con GitHub Actions.
- Compruebe el resultado final del workflow.
- Rechace identidades inconsistentes, archivos corruptos y ZIP inseguros.
- Aplique límites de descarga, descompresión y contenido.
- Gestione artefactos ausentes, expirados y errores de permisos.

No interpretar un workflow verde sin métricas válidas como evidencia completa.

No utilizar un SHA de merge sintético para certificar el commit fuente de un PR.

Preservar `null` cuando una métrica sea desconocida.

No modificar los workflows productores salvo incompatibilidad demostrada y autorizada.

## 5. Modelos y orquestación

Extender los modelos existentes de forma compatible:

- `CodeDiffSnippet`
- `CIRunResult`
- `CandidateFinding`
- `WeeklySnapshot`
- `SourceCollectionResult`
- `FinalReport`

Conservar identidad de repositorio, SHA, ejecución CI y procedencia.

Evitar colisiones entre hallazgos y evidencias mediante identidades compuestas o identificadores inequívocos.

Actualizar `ProjectOrchestrator`, `CodeQualityAgent`, `VerifierAgent` y `QualityEvaluator` para procesar los repositorios sin mezclar contextos.

Cada repositorio debe contar con una evaluación propia.

El informe consolidado debe conservar:

- Estado individual de cada repositorio.
- Pruebas ejecutadas y fallidas, cuando se conozcan.
- Cobertura individual y su base de medición.
- Hallazgos y verificaciones por repositorio.
- Problemas de acceso o evidencia insuficiente.
- Resultado conjunto con incertidumbres explícitas.

No promediar coberturas de frontend y backend ni presentarlas como cobertura global del proyecto.

No convertir estados desconocidos en aprobados.

## 6. Persistencia y API

Ampliar la persistencia SQLite mediante cambios aditivos y compatibles con históricos.

Reutilizar la infraestructura de snapshots, evidencias, ejecuciones e informes.

Los resultados deberán poder consultarse por:

- Proyecto.
- Repositorio.
- Commit.
- Ejecución CI.
- Intento.

Evitar duplicados al volver a recopilar un mismo artefacto.

Actualizar los endpoints y modelos de API para exponer resultados individuales y consolidados, manteniendo compatibilidad con los consumidores anteriores.

Actualizar también `api/analysis_jobs.py`, incluyendo preflight, fingerprint de configuración y preparación del análisis MultiRepo.

No permitir que el fingerprint de un repositorio certifique la configuración de otro.

## 7. Interfaz React

Reutilizar las vistas actuales de proyectos, análisis, calidad e informes.

Incorporar:

- Listado de repositorios del proyecto.
- Estado CI individual.
- Cobertura individual con alcance explícito.
- Filtros de hallazgos por repositorio.
- Identificación de commit y workflow.
- Estado de permisos y evidencias insuficientes.
- Resumen consolidado del proyecto.

Conservar las acciones y decisiones humanas existentes.

Actualizar tipos TypeScript, consultas y pruebas E2E correspondientes.

## 8. Seguridad: condición crítica

La auditoría detectó `external_writes_enabled: true` en `config/hormigestion.yaml` y un valor predeterminado `True` en `api/settings.py`.

Antes de implementar, analizar el efecto de esta configuración y los controles de escritura existentes.

Para esta tarea:

- Ejecutar análisis y pruebas con `EXTERNAL_WRITES_ENABLED=false`.
- No ejecutar escrituras remotas de GitHub, Trello ni servicios externos.
- No realizar modificaciones productivas.
- No eliminar los mecanismos actuales de aprobación humana.
- No cambiar silenciosamente políticas operativas existentes.
- Proponer cualquier corrección de valores predeterminados si supone alterar comportamiento anterior.

Toda acción futura sobre código debe quedar vinculada al repositorio y SHA del hallazgo que la originó, sin redirección hacia otro repositorio.

## 9. Pruebas obligatorias

Cubrir como mínimo:

- Configuración heredada de un repositorio.
- Configuración MultiRepo de dos repositorios.
- SHA independientes.
- Resolución de `develop`.
- Validación de artefactos reales o fixtures fieles de frontend y backend.
- Identidades incorrectas y SHA de merge sintético.
- Diferentes intentos de Actions.
- Fallos parciales.
- Frontend privado sin permisos.
- Artefactos ausentes, expirados o corruptos.
- Métricas desconocidas.
- Persistencia y lectura histórica.
- Aislamiento de hallazgos.
- Seguridad de acciones GitHub.
- API, frontend y pruebas E2E.
- Ausencia de escrituras externas durante las pruebas.

## 10. Criterios de aceptación

El trabajo estará listo cuando una sola ejecución analice los dos repositorios de HORMIGESTION y:

1. Cada repositorio conserve su SHA y evidencias.
2. Los artefactos CI v1 se validen correctamente.
3. Las métricas se asignen al repositorio y commit correctos.
4. Los fallos parciales se representen sin descartar resultados válidos.
5. Los informes consolidados no inventen métricas.
6. La interfaz permita explorar resultados por repositorio.
7. Los proyectos históricos de un repositorio continúen funcionando.
8. Las pruebas automatizadas pasen.
9. Se demuestre que no hubo escrituras externas.

## 11. Flujo de trabajo y entrega

Trabajar en una rama feature nueva basada en `codex/frontend-manager-phase1`.

Primero presentar un diagnóstico breve con:

- Capacidades reutilizables.
- Cambios realmente necesarios.
- Riesgos de compatibilidad.
- Diseño propuesto de configuración y modelos.
- Plan de implementación por etapas.

No comenzar cambios estructurales hasta validar dicho diseño.

Tras esa validación, implementar por etapas:

1. Configuración y dominio.
2. Recopilación MultiRepo y métricas CI.
3. Orquestación y evaluación.
4. Persistencia y API.
5. React e integración E2E.

Entregar resultados y pruebas de cada etapa.

No realizar push ni merge automáticamente. No modificar la rama base directamente ni activar escrituras externas.

### L2 — Design validation and implementation authorization (verbatim)

Valido el diseño propuesto para implementar MultiRepo en AgenteGestionProyectos.

Autorizo comenzar la implementación, tomando como base `codex/frontend-manager-phase1` y creando una rama nueva `feature/multirepo`.

Mantén los defaults operativos actuales por compatibilidad, pero ejecuta esta tarea con `EXTERNAL_WRITES_ENABLED=false`. No cambies los valores predeterminados globales sin una revisión separada.

Antes de implementar, incorpora estas precisiones al diseño:

### 1. Seguridad de acciones MultiRepo

Toda decisión sobre un hallazgo debe conservar y verificar su repositorio y SHA de origen.

Revisar especialmente `api/finding_actions.py`, `GitHubCodeActionAdapter`, los servicios de decisiones y sus contratos.

No permitir que una acción originada en frontend termine ejecutándose sobre backend, ni viceversa.

Rechazar solicitudes ambiguas, inconsistencias de procedencia o intentos de redirección entre repositorios.

Mantener intactos los mecanismos existentes de aprobación humana, autorización, idempotencia y auditoría.

### 2. Compatibilidad de contratos históricos

Preservar los contratos actuales de monorrepositorio.

Para configuraciones MultiRepo, incorporar colecciones nuevas sin asignar arbitrariamente un repositorio principal.

Cuando los registros históricos no tengan identidad de repositorio recuperable, conservar esa incertidumbre en lugar de inventar su procedencia.

Documentar las reglas de compatibilidad entre API antigua y nueva.

### 3. Seguridad de artefactos CI

En la descarga de GitHub Actions:

- Verificar los destinos de las redirecciones HTTP.
- No reenviar tokens a orígenes no autorizados.
- Aplicar límites al ZIP comprimido y a su contenido.
- Rechazar archivos duplicados ambiguos, rutas inseguras, contenido malformado y archivos excesivos.
- Validar el esquema v1 y la identidad completa del artefacto.
- Distinguir el resultado del workflow de la validez del documento de métricas.

### 4. Selección de ejecuciones e intentos

Utilizar una política determinista de selección de workflow, run e intento.

No sustituir un intento fallido, pendiente o incompleto por uno anterior exitoso.

La evidencia histórica puede mostrarse como referencia, pero nunca certificar silenciosamente el SHA objetivo de la ejecución actual.

### 5. Seguridad durante la implementación

Mantener `EXTERNAL_WRITES_ENABLED=false` durante esta tarea.

Verificar que API, motor y adaptadores respeten esta restricción.

Las pruebas deben usar transportes simulados o mecanismos equivalentes para demostrar que no realizan escrituras externas en GitHub, Trello, correo ni otros servicios.

No registrar tokens ni secretos.

### 6. Ejecución por etapas

Autorizo seguir el plan propuesto:

1. Configuración y dominio.
2. Recopilación MultiRepo y métricas CI v1.
3. Orquestación, agentes y evaluación.
4. Persistencia SQLite y API.
5. React, pruebas E2E e integración.

En cada etapa:

- Preservar las funciones existentes.
- Añadir pruebas de regresión y compatibilidad.
- Registrar los cambios en la documentación ODD.
- Realizar commits pequeños, descriptivos y revisables.
- Informar resultados y cualquier bloqueo relevante antes de continuar con la siguiente etapa.

No introducir cambios estructurales fuera del alcance sin justificarlos.

### Criterio final de aceptación

Debe demostrarse una ejecución MultiRepo de HORMIGESTION donde backend y frontend conserven sus SHA, CI, métricas, procedencia y hallazgos independientes, y se genere un informe consolidado sin inventar información.

Deben mantenerse funcionales los proyectos históricos monorrepositorio y las decisiones humanas.

Al finalizar, entregar una matriz de cumplimiento, resultados de pruebas, evidencia de integración y limitaciones pendientes.

**Autorizo crear la rama e implementar. No autorizo push, merge ni escrituras externas.**

### L3 — Baseline, approved design and routing evidence

Base and HEAD rechecked before branch creation: 99161b289996c8af4b1030ea30142c058fb57ad5. Created feature/multirepo; preserve pre-existing untracked frontend/pnpm-lock.yaml. No source changes or tests yet. Remote audit used read-only GET; no producer changes or remote mutations. Current operational defaults remain unchanged.

Approved design: sources.github.repositories / GITHUB_REPOSITORIES JSON collection; legacy singleton fallback, explicit contradictions rejected, no arbitrary primary repo. Fields id, owner, repo, branch and quality workflow selector; resolve per-run SHA and record access/availability separately. Per-repo provenance throughout the six named models; no averaged coverage. Reuse adapters, additive storage/API and existing React views/approval gates.

Producer schema identical (blob 09033371c03dd9458c45065b9fb1930bbff230ac); workflow_run_attempt and test_suites optional; unknowns stay null. Reference backend develop f275af93d37bd886b063f6090b0a8991cbdf0ac4 run38833954051/1 artifact12018119032:49 tests,97.9% loaded TS unit lines. Frontend develop71404615a18038b9209a1028acbae0d522445fea run38842647221/1 artifact12022370289:31 tests,92.73% loaded TS lines excluding TSX/UI,e2e_status not_implemented. Observations are not future analysis pins. Audit gh identity access does not prove app token access.

Size: large (multi-stage deliverables and durable requirements required for resumption). Forecast 3,500–6,000 authored changed lines, generated files excluded; coherent work units around 400 lines, not a hard cap or a reason to omit tests/docs. Delivery shape selected before the first commit: auto-chain / feature-branch-chain; publication remains unauthorized. Context backstop fired at 83,382 parent tokens; next bounded T1 reads/edits/checks go to gentle-ai-worker. T2/T3 may later run together only with disjoint edit surfaces and the shared domain contract settled.

Risk: item 2 (artifact trust and action origin routing); item 1 (SQLite historical compatibility); item 3 (shared config/domain/API consumers). Independent verification required for affected work units; functional tests remain mandatory. RDD only follows actual user-owned review mode and native assess/continuations, never task checkboxes.

T1 launched as worker mvayhv3y-2-2isg with narrow config/domain/test/doc surfaces; RED/GREEN, regression and differential checks assigned with real network blocked. No outcome or proof has returned; T1 remains in_progress. T2–T10 remain pending. Full Engram mirror1195 readback matches local document after trimming the trailing newline (Engram normalization).

Tooling note: first document-construction script had a JavaScript syntax error; the tool confirmed no calls/mutations. Corrected construction uses stored exact request strings. No source or repository incident occurred.

### L4 — Delivery selection and review-mode observation

Human selected: “Cadena sobre feature/multirepo — recomendado” (value: feature-branch-chain). Cache delivery_strategy=auto-chain, chain_strategy=feature-branch-chain; work-unit boundaries become future review slices targeting the feature/tracker branch. This grants no publication. No PRs are being created. Review mode status: receipt-driven development on (global on, clone-local unset); native assess/consent follows only actual work-unit candidate boundaries. T1 was running at this observation; later user stop L5 supersedes automatic continuation.

### L5 — Explicit user pause (verbatim)

pausemos el subagente, voy a salir y llegar el equipo

Action: cancelled worker mvayhv3y-2-2isg successfully. Do not continue implementation, checks, commits or delegated work until the human explicitly resumes. T1 blocked on that external decision; T2–T10 remain pending.

Pause snapshot: branch feature/multirepo; HEAD still99161b289996c8af4b1030ea30142c058fb57ad5; index unchanged and no new commits. Tracked partial edits: config/hormigestion.yaml, api/settings.py, application/config.py, domain/code.py, domain/findings.py, domain/report.py, domain/snapshot.py, ports/source_collection.py (Python paths under src/agente_gestion). Tracked diff332 insertions/11 deletions across8files, excluding untracked additions. New untracked agent files: docs/multirepo.md, domain/repositories.py, tests/test_multirepo_config.py, tests/test_multirepo_domain.py, this tracking document. Preserve pre-existing human frontend/pnpm-lock.yaml separately. No files discarded or reset.

Proof: worker was cancelled before delivering its final report; parent has no verified RED/GREEN, regression or differential outcomes and has not reviewed the partial code. Do not claim stage1 complete or tests passing. No further checks run after user stop. Operational defaults must remain unchanged; all future analyses/tests false and offline/simulated transports, no external writes or publication.

Resume: mem_context -> feature-scoped mem_search -> mem_get_observation1195 -> actual task file, reconcile diff and any retained child proof. Reauthorize only by explicit user resume; cancelled worker does not resume itself. Delegate the remaining T1 reads/edits/checks due context backstop, with the existing partial/untracked agent files explicitly within its allowed surfaces, preserving all human files. Complete and observe checks before committing or progressing to stage2. Cached delivery auto-chain/feature-branch-chain remains; no push/PR/merge permission.

### L6 — Explicit user resume (verbatim)

ya puedes continuar

Resume reconciliation completed in required order: mem_context, project/feature search, full observation1195, actual task file. Mirror readback matches after trim. Branch feature/multirepo,HEAD/base99161b289996c8af4b1030ea30142c058fb57ad5,index and partial file list/diff stat exactly match pause snapshot. Old worker confirmed cancelled, no final proof. No tasks completed; T1 returns to in_progress, T2–T10 stay pending. Remaining T1 reads/edits/checks will be delegated to a fresh scoped worker due recorded context backstop; preserve partial code/tests/docs and human pnpm-lock. Existing defaults and no-external-writes/publication restrictions unchanged.

### L7 — Tracking refresh request (verbatim)

The todo plan has not been reviewed during several tool-use turns. Check whether it still matches the work performed and update tasks where needed. Do not mark work done without verification.

Tracking refreshed from reconciled durable tasks: only T1 active; zero outcomes verified or tasks completed. No new feature scope.

Resume dispatch: fresh worker mvb1g33x-3-gscj now owns remaining T1 reads/edits/checks under the same narrow allowed surfaces, including existing agent additions. User stop is superseded by L6; do not restart old cancelled worker. Worker must preserve partial work, report any reconstructed baseline RED honestly, observe RED before new corrections, run focused GREEN/config-domain regressions and safe differential with real network blocked. No proof yet. Resume automatically on this task's completion event; no polling or duplicate launches.

### L8 — Runner incident and correction dispatch

Worker mvb1g33x-3-gscj made no new edits. Focused and regression commands importing pytest each exited1 before collection: ModuleNotFoundError: No module named 'pytest'; zero tests, not RED. Parent read-only diagnosis: pyproject has no pytest dependency, README.md:156 uses unittest, both new modules subclass unittest.TestCase. No install or failed-command retry. Native ASSESS item3: self+independent verification required; nativeRisk unassessable/undeclared untracked scope, no authority.

Corrected runner (socket denial BEFORE imports) uses EXTERNAL_WRITES_ENABLED=false .venv/bin/python -c with socket.socket.connect and connect_ex raising AssertionError('External network blocked'), unittest.TextTestRunner(verbosity=2), defaultTestLoader.loadTestsFromNames, and SystemExit(not result.wasSuccessful()). Focused modules: tests.test_multirepo_config,tests.test_multirepo_domain; regression: tests.test_settings,tests.test_unified_runtime_config,tests.test_sources_contracts. No live providers/state or pytest installation. Correction mvb1q013-4-l5b0 assigned missing independent-SHA coverage and stage1 self-review; preserved all original requirements/surfaces. No task complete then.

### L9 — Stage1 self-checks and initial independent verification

Worker mvb1q013-4-l5b0 added explicit distinct-SHA public-loader test and isolated existing legacy-alias fixture root; no source correction. Focused19 exit0/0.092s. Regression first exit1,33tests/one inherited-default fixture error; after isolation exit0,33tests/0.074s. New SHA test passed existing behavior; original source RED chronology unknown. No CLI differential performed; not a blanket shared-consumer exemption.

Parent guarded focused run: exit0,19tests/0.091s,OK; bounded config/API/YAML/legacy-test/guide review, Git structural/status commandexit0. Full draft13files1209authoredlines (1174add/35delete), not commits. Native ASSESS item3 retained independent plan, native scope unassessable, no approval. Independent verifier mvb2ahsb-5-01zm assigned whole spec, baseline99161b289996c8af4b1030ea30142c058fb57ad5, isolated52tests and public singleton/CLI differentials, explicitly challenging owner/fullrepo compatibility and swallowed YAML errors. No stage2 work.

### L10 — Independent defects and one bounded correction

Verifier mvb2ahsb-5-01zm: S2blocked,S5contracts/S8safetypass,S9partial. Guarded52tests exit0/OK; no repo edits/live requests. --help both exit0,byte-identical stdout/stderr. Consistent aliases and source precedence match baseline; legacy fixture-root isolation accepted.

Blocker: isolated YAML project.id=probe-project,sources.github.repositories={invalid:collection},noGITHUB_REPOSITORIES,public load_api_settings(env_file=None) returnsprojects=[]/noexception. Engine raises 'sources.github.repositories must be a non-empty list'; API error matcher269–276 misses marker and catch435–444 swallows it.

Legacy differential: no collection,GITHUB_OWNER=legacy-owner,GITHUB_REPOSITORY=encoded-owner/repository. Base Settings normalizes encoded-owner/repository,API retainsprobe-project; candidate raisesValueError/APIprojects[]. Fixture source-check baseexit0/success;candidateexit2/emptystdout/stderr'ERROR: Configuration validation failed.\n'. Verifier called advisory; parent adopts unrequested regression under user's exact 'Preservar los contratos actuales de monorrepositorio.'. Concrete YAML blocker starts correction; advisory did not alone trigger it. Preserve baseline normalization only without collection; keep collection-present rawowner conflicts strict.

Single correction mvb2p4mv-6-q4i7 limited to application/config.py,api/settings.py,tests/test_multirepo_config.py,tests/test_unified_runtime_config.py,docs/multirepo.md. Require public RED before minimal fixes, GREEN/regression and exact fixture CLI differential; no redesign/default changes/install/livewrites. Afterwards limited same-defect verifier recheck, not new sweep. T1 active, no commits.

### L11 — Correction proof and limited independent recheck

Writer mvb2p4mv-6-q4i7 fixed five authorized files only: owner guard conditional on collection; YAML shape/discovery errors propagated; public regressions/docs updated. Target methods: tests.test_multirepo_config.MultiRepoSettingsTests.test_api_loader_rejects_non_list_yaml_collection_instead_of_dropping_project; tests.test_unified_runtime_config.UnifiedRuntimeConfigTests.test_legacy_owner_with_full_repository_alias_preserves_singleton_api_and_cli; tests.test_multirepo_config.MultiRepoSettingsTests.test_collection_still_rejects_owner_conflicting_with_full_repository_alias.

Target RED exit1,3tests/1failure+1error (API noexception,legacyowner rejection);strictness passed. Intermediate exit1: early YAML discovery error still swallowed and CLI spacing assertion wrong despite success; corrected same batch. Target GREEN exit0,3tests/0.076s;focused21 exit0/0.099s;regression34 exit0/0.140s. No earlier cancelled-source RED claim. Parent exact guarded target3 exit0/0.078s/OK, bounded diff/guide review and Gitdiffcheckexit0.

Fixture CLI source-check github --sources fixtures --project fixture_1_standard --config <scratch>/probe.yaml --env-file /dev/null: before fix base0/candidate2 with stderr'ERROR: Configuration validation failed.\n'; after both0,byte-identical stdout/stderr (stderr empty,GitHubsuccess/2diffs/unknownSHA). Preliminary unsupported fixture ID baseline1 was not comparable. Final probes falseenv/socket-denied, no secrets/live services.

Direct ASSESS item3 retained independent plan; untracked native scope not approval. An earlier codemode attempt was refused before execution (only prior memory save ran); native facade must be called directly. No source/tracking mutation from refused call.

Limited verifier continuation mvb39llw-7-tvzn now rechecks ONLY original YAML rejection, legacy Settings/API/fixture CLI exact differential and collection strictness: three named tests plus independent isolated guarded probes, no full sweep/edits. T1 not closed; commits/native review/stage2 pending. Wait for completion, no polling.

### L12 — Limited independent recheck resolved; work-unit packaging

Verifier mvb39llw-7-tvzn resolved original YAML rejection, legacy singleton normalization/API retention/fixture-CLI compatibility, and collection-present strictness. Isolated guarded3tests exit0/0.118s; no broader sweep. Independent base/candidate fixture CLI both exit0, byte-identical stdout (githubsuccess,2diffs,SHAunknown,duration0.000,errornone), empty stderr. No repo edits/live requests. Combined evidence: self21+34green,parent3green,independent scoped recheck green; original source chronology and future stages remain explicitly unverified.

T1 functional evidence ready; keep task active until coherent local commit/review records. Packaging: passive tracking bootstrap, domain/provenance contracts with domain tests/guide, then collection/API-settings config with config/regression tests/guide. Domain and config are separate high-risk work units so review can target each alone; ~400lines advisory, natural cohesive units may exceed it without code-golf. No publication. Normalize generated blockquote blank markers only (remove Markdown marker trailing spaces), preserve every user quote/log body. Documentation bootstrap requires structural readback, no fabricated RED. Native review only for provider-due normalized committed units, not TODO/accumulated branch.

### L13 — Local commits and native scope

Tracking bootstrap9adf69a41e7d552cefc917e01f9519621e86fb8f: only tracking861lines,structural/verbatim/stagedwhitespace checks passed; no source. Domain7220191c07c388aa1b4211888be31e5e93c32129: eightfiles696add/4delete=700changed,models/domain tests/domain guide. Config separate/pending; natural cohesive700 exceeds advisory400 without code-golf. No hooks/signoverride/publication. Actualbase99161b...; verifiedlogscope excludes unrelated toolhint origin/mvp/real-sources25362lines. Native modeglobalon/cloneunset; rootinspectexclude offeredambientworkspace/no existinglineages. Review only domaincommitted9ad..722,notpendingconfig/wholebranch. No assessmentapproval inferred.

### L14 — Native committed-range routing blocker; human decision required

Domain9adf69a..7220191 ASSESS still unassessable for untracked declaration after inspectexclude. START(multirepo-domain-7220191-native-01,ordinary,committedOnly/base9adf69a) refused native-start-retained-selection-candidate-mismatch,mutation_performed=false/mutation_outcome=none. No lineage created: do not status/advance inspect-proposed lineage. Provider nextaction plaininspect returned workspace intended-untracked selection, not committed domain range. Do not loop/replay START, review wrong workspace, ignore human files or reset/recover authority.

Need explicit authorization for clean same-clone review worktree /home/saimoljimenez/Univercidad/GestionProyectos/AgenteGestionProyectos/.runtime/multirepo-review, or explicit stage1-native-review disposition/pause. T1 blocked on external choice;T2–T10pending. Config commit pending; full verified configuration guide restored unstaged. Passing proof55self,parent3,independentbounded3 unchanged. No native consent/verdict invented, no publication. A first bookkeeping script restored guide then failed an exact task-line match before tracking edits; repaired from current task line, no source changes.

### L15 — Explicit review-worktree authorization

Human selected 'Autorizar worktree limpio — recomendado' (authorize_multirepo_review_worktree): create/register /home/saimoljimenez/Univercidad/GestionProyectos/AgenteGestionProyectos/.runtime/multirepo-review in same clone for this task's commit reviews. Path authorization only, not native consent/verdict/publication. No secrets/runtime copied. T1 active again; prepare detached domain7220191 worktree, register, then fresh native root-bound operations; never reuse old workspace bindings.

### L16 — Clean worktree assessment; no review due for domain unit

Authorized worktree created/registered clean at domain7220191,common .git confirmed,no.env copied. Root-bound committed-range ASSESS(base9adf69a,committedOnly=true,item3) now succeeds:8paths/700changedlines,nativeRisk=medium,agent risk=high,reviewDue=false,reviewDueReason=under_budget,candidate.consumed=false,nativeReviewOutcome=unknown. Independent item3 requirement already covered by stage1 verification on identical source; no extra verifier or native START/capture is due. This is NOT native approval. Keep every later native call bound to authorized review root. Proceed separate configuration work-unit commit; native review only when provider says due.
