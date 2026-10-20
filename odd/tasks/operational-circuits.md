# Circuitos operativos Trello y GitHub

Objetivo: que el gestor pueda llevar un problema real hasta una acción correctiva autorizada, ejecutada y auditada, en Trello y en GitHub, sin debilitar las garantías de revisión.
Rama: `feature/multirepo`. Entrega: commits locales por unidad, sin push ni merge.
Runner: `.venv/bin/python -m unittest discover -s tests`; Playwright con API simulada y E2E aislado real.

Restricciones vigentes: `EXTERNAL_WRITES_ENABLED=false`; sin llamadas LLM reales; sin escrituras en Trello o GitHub; sin modificar HORMIGESTION externamente; sin alterar `.runtime/agente.db` ni `runs/`; sin push ni merge; REQ-06 fuera de alcance.

## Specs

- S1. "Corrige la recopilación del código para que el archivo que contiene el defecto pueda obtenerse íntegramente." El backend de HORMIGESTION cambió 14 archivos de código más 2 fuentes binarias; el tope de 8 archivos completos descartaba exactamente el archivo con el defecto y la ruta de acción correctiva era inalcanzable (`finding_source_unavailable`).
- S2. "Separa los presupuestos de parches y archivos completos cuando corresponda, manteniendo límites de tamaño y de consumo claramente acotados." El parche y el archivo completo son dos propósitos distintos y no pueden agotarse mutuamente.
- S3. "Evita procesar archivos binarios irrelevantes como fuentes de código." Un binario nunca es fuente de código ni base de un parche; se lee al final y con su propio presupuesto.
- S4. "No implementes un Lazy Fetch general ni una reestructuración del recopilador." La corrección vive en el adaptador existente.
- S5. "Verifica que la corrección permite obtener el archivo necesario y que no altera su integridad ni su identidad de commit."
- S6. "Implementa el mecanismo mínimo de revisión del parche exacto. Reutiliza VerifierAgent, la infraestructura de invocación y validación existente."
- S7. "Verificar el defecto con evidencia suficiente. Generar el contenido corregido efectivo. Revisar la corrección de forma independiente de su generación. Guardar el veredicto y el hash del parche exacto."
- S8. "Permitir aprobación únicamente con revisión favorable vigente. Bloquear la aprobación si cambia el parche, archivo, repositorio o commit de origen. Mantener el bloqueo cuando la revisión sea insuficiente, desfavorable o inexistente."
- S9. "No considerar suficiente una comprobación de sintaxis, un defecto confirmado o pruebas aprobadas."
- S10. "Registrar la revisión y la decisión en la auditoría."
- S11. "Primero utiliza proveedores simulados para las pruebas. No llames todavía a Gemini real."
- S12. "Revisa la propuesta de reasignación de la tarea vencida. No quiero sustituir al responsable actual únicamente porque la tarea está retrasada. Evalúa si es viable proponer una asignación adicional, manteniendo al responsable actual, o una medida correctiva más adecuada."
- S13. "Confirma los IDs exactos de la tarea y los miembros. Hay una discrepancia de ID en el diagnóstico."
- S14. "Verifica que la acción tenga justificación, revisión técnica, dry-run y ejecución sandbox válida. No actives reglas permanentes ni modifiques Trello todavía."
- S15. "Implementa la creación controlada de Pull Requests reutilizando GitHubCodeActionAdapter. Primero debe funcionar en sandbox: rama candidata, parche exacto revisado, commit resultante, PR simulado, número y URL de demostración claramente identificados como simulados, recibo y auditoría."
- S16. "El PR real será contra develop, nunca mediante un merge automático. No habilites publicaciones externas durante esta etapa."
- S17. "UI que diferencia revisión, autorización, rama y PR."
- S18. "No realices rediseños visuales, nuevas pantallas ni refactorizaciones ajenas al MVP."
- S19. "Crea commits locales por responsabilidad cuando las pruebas focalizadas correspondientes estén aprobadas. No repitas suites completas después de cada commit."

## Tasks

- T1 [S1-S5] hecho; route=inline; `DEFAULT_MULTIREPO_MAX_FULL_FILES` 8 → 32; presupuesto propio `max_full_file_bytes` (16 MiB) separado del de parches; techo por archivo corregido; binarios leídos al final con su presupuesto. Evidencia: 5 pruebas focalizadas (`tests/test_github_full_file_collection.py`), RED observado (2 fallos + 2 errores) y GREEN; 58 pruebas existentes de GitHub en verde; verificación real de solo lectura: 15 archivos completos y `despacho-store.ts` íntegro (682 líneas, sha256 coincidente, commit fijado).
- T2 [S6-S11] hecho; route=inline; `domain/patch_review.py` (contrato y digest del parche efectivo), `application/patch_review.py` (servicio, tabla `patch_reviews` append-only y consulta de revisión vigente), `VerifierAgent.review_patch` (reutiliza proveedor, coordinador y contexto acotado), etapa de verificación del orquestador (revisan las correcciones con fuente completa) y puerta de aprobación en `FindingDecisionService`. Evidencia: 11 pruebas focalizadas (`tests/test_patch_review.py`) y 113 pruebas del área en verde; la negativa conserva su código y ahora explica que un defecto confirmado, una sintaxis válida o unas pruebas aprobadas no sustituyen la revisión. Commit `b0653d0`.
- T3 [S12-S14] hecho; route=inline; acción reevaluada como asignación adicional (se mantiene el responsable actual y se suma el dueño de la actividad siguiente de la misma fase, sin tocar fechas) y verificada de punta a punta en sandbox aislado: 1 propuesta `assignee_ids` con el riesgo `overdue` enlazado, revisión técnica `ready`, dry-run `SIMULATED`, decisión `approve_and_execute` con resultado `SIMULATED`/`EXECUTED`/`verified` y `live_state_changed=false`, recibo e historial persistidos y SHA-256 de la base real idéntico antes y después. La verificación reveló y corrigió un defecto: el agente de Monitoring y el orquestador calculaban los candidatos con filas distintas, de modo que una regla sobre un campo que el contexto acotado no transporta (por ejemplo `assignee_ids`) se descartaba como `field_not_visible` y la acción correctiva era imposible de seleccionar.
- T4 [S15-S17] pendiente; route=inline; creación de PR en sandbox reutilizando `GitHubCodeActionAdapter`, recibo, auditoría y UI diferenciada. Pendiente: la interfaz todavía no expone la revisión del parche al gestor ni habilita la aprobación con revisión favorable.

## Log

- L1. Autorizaciones del gestor: A1 (reglas permanentes) no; A2 (modificar responsables reales) no; A3 (`EXTERNAL_WRITES_ENABLED=false` se mantiene); A4 (Gemini real) pospuesto hasta que el mecanismo de revisión y sus pruebas existan; A5 (ramas o PR externos) no; A6 (consultas de solo lectura a GitHub) sí; A7 (indicador y notificaciones) pospuesto.
- L2. Hallazgo de G1 verificado sobre la captura real: el backend capturó 8 de 14 archivos de código completos y `src/infrastructure/database/despacho-store.ts` quedó con `full_file_available: False`; el tope `DEFAULT_MULTIREPO_MAX_FULL_FILES = 8` y el orden alfabético lo descartaban. Con el tope a 32 y el presupuesto separado, los 15 archivos de código se obtienen íntegros y el único hueco restante son las dos fuentes binarias, que siguen sin ser fuente de código.
- L3. Frescura observada: `develop` de `hormigestion-backend` avanzó de `af6aa5975788` a `8b166e0045d3` durante el diagnóstico. El hallazgo y la revisión deben ligarse al commit vigente en el momento de la demostración, nunca a un identificador fijo.
- L4. Contrato preservado: un puntero Git LFS sigue siendo un hueco honesto (su contenido no es obtenible y se reporta como ruta no disponible); el cambio no lo convierte en fuente de código ni oculta el hueco.
- L5. Hallazgo de la etapa Trello: la acción correctiva de la tarea vencida no era seleccionable. El agente de Monitoring recomputaba los candidatos con las filas acotadas del contexto (que solo transportan `id`, `title`, `state`, `is_included`, `has_contradiction`, `due_date` y `external_id`), mientras el orquestador los recomputaba con el volcado completo de las tarjetas. Una regla sobre `assignee_ids` se descartaba como `field_not_visible` en el agente y el modelo no tenía nada que seleccionar, aunque el orquestador sí calculaba el candidato. Ambos cálculos usan ahora las mismas filas: el contexto acotado sigue siendo lo que el modelo lee y el cálculo local de candidatos no consume presupuesto. El rechazo por campo no observado se mantiene, con el código preciso `evidence_mismatch`.
- L6. Etapa Trello verificada con la asignación adicional: valor actual `["6a957d40818245a80281e563"]` (Jiménez Chango Saimol Steph) y valor propuesto `["6a957d40818245a80281e563", "6a957d229c6fbe665e8c0647"]` (se mantiene el responsable actual y se suma Cristian Jurado, dueño de «Integración de módulos administrador y cliente», la actividad siguiente de la Fase 3). Las tarjetas de HORMIGESTION no registran dependencias explícitas, así que la justificación es una secuencia observada y no una relación declarada; esa limitación queda escrita en la propuesta.
- L7. Frescura: `develop` de `hormigestion-backend` avanzó de `af6aa5975788` a `8b166e0045d3` durante el trabajo. El hallazgo de código y su revisión deben ligarse al commit vigente en el momento de la demostración.
