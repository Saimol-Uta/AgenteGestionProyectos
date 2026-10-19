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
T1 [S1-S3,S6-S10,S12-S13] done; route=delegated (runtime context backstop); block GitHub approval without independent exact-patch review, keep reject/request_changes, RED/GREEN and docs; scratch authorized at /tmp/req08-decision-workflow-t1-isolated; RED observed, focused GREEN32, parent spot-check1, independent PASS32/zero sockets; Risk:item2 authorization; commit=6f31f61.
T2 [S1-S3,S5-S10,S12] done; route=delegated (context backstop); real Monitoring/orchestrator→HTTP→sandbox/audit E2E plus linked failure tests; coverage-only no product RED; worker53 GREEN, parent53 GREEN4.526s/zero sockets; commit=532f170.
T3 [S4-S5,S8-S10] done; route=delegated (context backstop); expose existing proposal decisions, derived links and latest receipt safely through additive read API, include reusable pending counts if needed; Risk:item2 allowlisted governance reads; worker RED7 missing-contract errors and GREEN44/5.828s; parent spotcheck GREEN44/zero sockets; independent BLOCKER corrected with RED/GREEN regression1+modules45 and parent1+45/zero sockets; independent blocker-only recheck PASS/no fallback to earlier favorable receipt; commit=recorded after local commit.
T4 [S4-S10,S12-S13] in_progress; route=delegated (context backstop); decision-centered proposal detail, independent reject gate, typed real states, durable outcome/history and honest blocked GitHub UX; commit=pending.
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
L8. Local work-unit commit 6f31f61 (`fix(governance): block GitHub approval without corrective review`) contains T1 and initial tracker; no push. Global diff-check reported the two known pre-existing EOF blank lines in RunDetail files; T1 scoped diff-check had passed. Those human changes remain outside the commit. Verifier completion event repeats the accepted PASS, no new finding.
L9. T2 complete: new tests/test_req08_decision_workflow.py uses real ProjectOrchestrator/Monitoring and temporary HTTP storage with deterministic provider/source. No production changes; no meaningful product RED. Initial test-assumption failures (3 then1) corrected without expanding production scope. Final worker53 passed4.480s; parent repeated53 passed4.526s, preflight0 and zero sockets. Evidence includes approval/replay/audit, derived digest/review binding, blocked rejection, missing evidence/no proposal, no session; existing failure/authorization suites linked. React/backend browser seam remains T4/T6. Warnings: Starlette/httpx deprecation and SQLite ResourceWarning, non-failing. ASSESS RDDoff: medium fallback/unassessable due untracked files; independentVerifier=false, self verification stands.
L10. T2 local commit532f170 (`test(governance): demonstrate Trello sandbox decision lifecycle`). T3 now starts with public-API extensions only where needed; protect actor/history details by existing project-scoped audit permissions, preserve public summaries, never migrate DB or fabricate favorable history. No changes to pipeline or new corrective reviewer.
L11. T3 worker adds protected detail history/relationships/receipt and public pending_actionable_count using existing ledgers without migrations. RED7 contract errors then final GREEN44/5.828s after one correction; base-vs-candidate public-summary differential passed after isolated fixture-copy setup correction. Parent fresh-copy preflight0/focused0 with zero sockets. Unit ~710 authored lines is cohesive API/storage/test/docs read projection; above advisory400, not split by file type. ASSESS risk high/item2, reviewDue:true reviewDueReason:high_risk and native continuation relayed unchanged; RDDoff, no lifecycle commands run, independent verification required. T3 remains in_progress, no commit until verification. Frontend tasks T4/T5 and full final T6 still pending.
L12. T3 independent verification BLOCKER (one): missing only proposal_execution_outcomes row leaves execution row intact; public GET reports receipt_availability unknown but still latest_receipt.execution_mode sandbox. Require conservative unknown/no proof-dependent latest receipt fields when outcome missing, not a favorable reconstruction from incomplete ledger. Scratch public reproduction under t3/verify; tampered-hash case already fails closed. Other probes (anonymous/wrong-scope privacy, safe fields, GET no mutation) and focused44 passed6.059s; zero sockets. Initial probe import-path setup error corrected; no repo writes by verifier. One correction batch requested: regression for missing outcome alone and corruption, minimal read-projection fix; then verifier rechecks only blocker, not a new sweep. T3 remains in_progress; no commit.
L13. Single T3 correction applied: only validated outcome decisions project execution events/latest_receipt; missing/corrupt outcome yields unknown availability/null receipt, retains actual offline decision with asserted_offline provenance. Worker RED missing-outcome reproduction then GREEN1+45/6.376s; parent fresh-copy regression1+modules45 passed with preflight0/zero sockets. No wire fields changed. Bugfix saved immediately. Independent blocker-only recheck still pending; T3 not done, no commit. T4/T5/T6 remain pending.
L14. Independent T3 blocker-only recheck PASS: new public regression1/0.303s plus probe with earlier valid receipt and newer missing outcome returned unknown/null/no fallback, summary unchanged/no schema or row mutations. Preflight0, guard child checked, zero sockets. Two probe fixture/reuse setup errors corrected in fresh scratch, no suite rerun or new blocker. T3 accepted after one correction; no new native lifecycle. T4 frontend begins after local T3 commit.
L15. Baseline rechecked: feature/multirepo HEAD d7d37a6; pre-existing single blank-line changes in frontend/src/features/analysis/RunDetailPage.tsx and run-detail.css must remain uncommitted/untouched. No tests or project code executed yet. Missing process EXTERNAL_WRITES_ENABLED is not isolation; every test command must set false explicitly, disable dotenv/real provider credentials, block network and isolate state before imports. Existing .runtime data and real databases are not test inputs.
