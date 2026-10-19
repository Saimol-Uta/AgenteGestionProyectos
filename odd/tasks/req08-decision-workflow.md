# REQ-08 decision workflow
Objective: implement approved P0/P1 without changing the technical pipeline. Branch: feature/multirepo; base: d7d37a6.
Delivery: local responsibility-based commits only, no push/PR/merge; forecast ~1500 authored lines across reviewable units. Tests: isolated unittest and React/Playwright using existing dependencies, external writes disabled and no provider network.

## Specs
S1. "Mantener intacta la arquitectura y el pipeline técnico actual: Source Collection → Monitoring → Quality → Verification → Report."
S2. "No crear nuevos agentes LLM ni duplicar funcionalidades existentes."
S3. "Priorizar la corrección de la autorización GitHub (P0.1) y la prueba E2E del circuito de decisiones (P0.2)."
S4. "Mejorar Propuestas y Dashboard para que un gestor sin conocimientos técnicos comprenda el problema, la acción recomendada, la revisión, las condiciones de aprobación, su decisión y el resultado."
S5. "Reutilizar los contratos y servicios existentes. Ampliar la API únicamente donde sea necesario para recuperar decisiones, historial y recibos."
S6. "No confundir la validación del defecto con la validación del código correctivo."
S7. "No incluir Lazy Fetch ni funcionalidades adicionales fuera del alcance P0/P1."
S8. "Autorizo modificaciones locales, pruebas automatizadas aisladas y commits locales por responsabilidades."
S9. "EXTERNAL_WRITES_ENABLED=false"; "Sin llamadas reales a Gemini, Groq o Mistral."; "Sin escrituras en Trello o GitHub."; "Sin modificar HORMIGESTION."; "Sin alterar la base de datos ni los artefactos históricos."; "Sin push ni merge."; "RDD desactivado."
S10. "Antes de ejecutar pruebas, verifica que el entorno esté aislado y que las escrituras externas estén efectivamente deshabilitadas."
S11. "Al finalizar, entrégame los resultados de las pruebas, los commits realizados, las limitaciones pendientes y el estado final de REQ-08. Espera mi revisión antes de continuar con nuevas funcionalidades."
S12. "Bloquear hasta revisión independiente — recomendado" — selected in response to: "Sin revisión independiente del parche disponible, ¿qué política querés aplicar a la aprobación GitHub?" Option description: "No permitir aprobar ni ejecutar el cambio sin revisión del parche exacto; la funcionalidad queda limitada hasta disponer de esa revisión."
S13. "Mantén funcionando Quality, Verification, las recomendaciones técnicas y las decisiones de rechazar o solicitar cambios." "Bloquea únicamente la aprobación y ejecución de modificaciones GitHub cuando no exista una revisión suficiente y vinculada al parche exacto." "No implementes ahora Lazy Fetch, nuevos validadores ni otro agente LLM." "La prioridad es cerrar REQ-08 con los mecanismos existentes, garantizar la seguridad y mejorar la experiencia del gestor. El ciclo completo de acciones correctivas se demostrará inicialmente mediante Trello en sandbox." "La interfaz debe explicar claramente por qué una corrección GitHub no puede aprobarse todavía."

## Tasks
T1 [S1-S3,S6-S10,S12-S13] done; route=delegated (runtime context backstop); block GitHub approval without independent exact-patch review, keep reject/request_changes, RED/GREEN and docs; scratch authorized at /tmp/req08-decision-workflow-t1-isolated; RED observed, focused GREEN32, parent spot-check1, independent PASS32/zero sockets; Risk:item2 authorization; commit=recorded in next log entry after local commit.
T2 [S1-S3,S5-S10,S12] in_progress; route=delegated (context backstop); extend existing isolated deterministic E2E for proposal decisions, modifications, failures, replay, audit and Trello/GitHub separation; commit=pending.
T3 [S4-S5,S8-S10] pending; route=delegated (context backstop); expose existing proposal decisions, derived links and latest receipt safely through additive read API, include reusable pending counts if needed; Risk:item2 allowlisted governance reads; commit=pending.
T4 [S4-S10,S12-S13] pending; route=delegated (context backstop); decision-centered proposal detail, independent reject gate, typed real states, durable outcome/history and honest blocked GitHub UX; commit=pending.
T5 [S4-S5,S7-S10] pending; route=inline unless backstop; Dashboard counts actual actionable pending proposals without duplicating analysis; commit=pending.
T6 [S1-S13] pending; route=parent and independent high-risk verifier; final isolated suites/build/browser checks, differential checks for shared behaviors, documentation and requirement verdict; commit=pending.

## Log
L1. Original implementation authorization:

Apruebo tu propuesta de implementación P0 y P1 de la auditoría REQ-08.

Procede con los cambios siguiendo el orden y los criterios de aceptación que definiste en tu informe.

Ten presentes estas decisiones:

1. Mantener intacta la arquitectura y el pipeline técnico actual: Source Collection → Monitoring → Quality → Verification → Report.
2. No crear nuevos agentes LLM ni duplicar funcionalidades existentes.
3. Priorizar la corrección de la autorización GitHub (P0.1) y la prueba E2E del circuito de decisiones (P0.2).
4. Mejorar Propuestas y Dashboard para que un gestor sin conocimientos técnicos comprenda el problema, la acción recomendada, la revisión, las condiciones de aprobación, su decisión y el resultado.
5. Reutilizar los contratos y servicios existentes. Ampliar la API únicamente donde sea necesario para recuperar decisiones, historial y recibos.
6. No confundir la validación del defecto con la validación del código correctivo.
7. No incluir Lazy Fetch ni funcionalidades adicionales fuera del alcance P0/P1.

Autorizo modificaciones locales, pruebas automatizadas aisladas y commits locales por responsabilidades.

Restricciones:
- `EXTERNAL_WRITES_ENABLED=false`.
- Sin llamadas reales a Gemini, Groq o Mistral.
- Sin escrituras en Trello o GitHub.
- Sin modificar HORMIGESTION.
- Sin alterar la base de datos ni los artefactos históricos.
- Sin push ni merge.
- RDD desactivado.

Antes de ejecutar pruebas, verifica que el entorno esté aislado y que las escrituras externas estén efectivamente deshabilitadas.

Al finalizar, entrégame los resultados de las pruebas, los commits realizados, las limitaciones pendientes y el estado final de REQ-08. Espera mi revisión antes de continuar con nuevas funcionalidades.

L2. User follow-up: "Review the delivered subagent output and continue."
L3. Product decision: "Bloquear hasta revisión independiente — recomendado". No independent corrective-code review contract exists; never treat confirmed defect, pinned source or syntax as semantic corrective review. Keep approval fail-closed rather than inventing a reviewer or new agent. This intentionally limits GitHub action availability and must be reported at closure.
L4. User clarification, verbatim:

Selecciono «Bloquear hasta revisión independiente».

Mantén funcionando Quality, Verification, las recomendaciones técnicas y las decisiones de rechazar o solicitar cambios.

Bloquea únicamente la aprobación y ejecución de modificaciones GitHub cuando no exista una revisión suficiente y vinculada al parche exacto.

No implementes ahora Lazy Fetch, nuevos validadores ni otro agente LLM.

La prioridad es cerrar REQ-08 con los mecanismos existentes, garantizar la seguridad y mejorar la experiencia del gestor. El ciclo completo de acciones correctivas se demostrará inicialmente mediante Trello en sandbox.

La interfaz debe explicar claramente por qué una corrección GitHub no puede aprobarse todavía.

L5. Scratch interaction: worker stopped before edits/tests because scratch was outside its allowed surfaces. Parent relayed the two-choice request. User selected "Autorizar el directorio temporal" (approve_t1_scratch), authorizing `/tmp/req08-decision-workflow-t1-isolated` exclusively for disposable scratch and test state in addition to the six existing repository edit paths. Resume the same T1 worker; no implementation or RED/GREEN claimed yet.
L6. T1 implementation received: API and service deny approve with 409/revision_independiente_requerida before replay or code actions. Worker observed four RED probes; focused GREEN quality14 + MultiRepo14 + integration4, all zero sockets. Initial missing `closing` import corrected before final GREEN. Base/candidate differential reject/request_changes matched; approval denial is intentional. Parent compared production files with tested green2 copy and reran direct confirmed-defect denial: exit0, zero sockets. Native ASSESS returned high via item2 escalation; nativeRisk unassessable due untracked tracker, RDDoff, requires independent verifier. No commit yet; T1 remains in_progress pending independent verification.
L7. Independent T1 verification PASS/no blockers. Fresh verify copy: 32 tests exit0 in3.204s, guard checked parent+child, zero sockets. Initial isolation probe cwd assertion failed before any project action, corrected cwd and preflight exit0. No .env contents read or repository writes by verifier. Parent already reproduced one focused command. T1 accepted; no review lifecycle started (RDDoff). T2 begins after local T1 commit.
L8. Baseline rechecked: feature/multirepo HEAD d7d37a6; pre-existing single blank-line changes in frontend/src/features/analysis/RunDetailPage.tsx and run-detail.css must remain uncommitted/untouched. No tests or project code executed yet. Missing process EXTERNAL_WRITES_ENABLED is not isolation; every test command must set false explicitly, disable dotenv/real provider credentials, block network and isolate state before imports. Existing .runtime data and real databases are not test inputs.
