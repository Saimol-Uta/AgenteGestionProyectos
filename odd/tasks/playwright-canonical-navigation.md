# Canonical analysis navigation and stage states
Objective: fix the eight classified Playwright failures without restoring removed panels. Branch: feature/multirepo; base: 0b78a98.
Delivery: one separate local commit, no push/merge. Preserve user EOF edits and prior task checkpoint.

## Specs
S1. Mantén intactos los seis commits de REQ-08 y conserva la nueva arquitectura de navegación.
S2. 1. Corrige el defecto real en RunDetailPage: distinguir etapa omitida explícitamente, etapa desconocida/no registrada, etapa completada y etapa fallida. Normaliza nombres históricos solo según el contrato real.
S3. 2. NO vuelvas a insertar RepositoryResultsPanel en Proyectos, Calidad ni en la vista principal de Análisis. La evidencia detallada MultiRepo debe permanecer en `/analysis/:runId`.
S4. 3. Adapta las cinco pruebas de ubicación para navegar a la página canónica. Añade enlaces contextuales solo cuando mejoren realmente la experiencia del gestor.
S5. 4. Conserva todas las comprobaciones originales sobre identidad de repositorios, CI, cobertura, datos legacy, refresco de resultados y aislamiento entre proyectos. No elimines aserciones simplemente para conseguir que las pruebas pasen.
S6. 5. Corrige el locator ambiguo del selector de proyecto y la expectativa textual del botón de análisis en ejecución. Verifica también las comprobaciones posteriores de cada escenario.
S7. 6. Ejecuta las ocho pruebas afectadas, Playwright completo, typecheck y build. Ejecuta regresiones backend si se modifica algún contrato compartido.
S8. 7. Crea un commit local independiente con las correcciones y presenta los resultados exactos. Si aparecen defectos adicionales al superar los bloqueos iniciales, investígalos y repórtalos antes de ampliar el alcance.
S9. Restricciones: sin cambios al pipeline multiagente, sin nuevas funcionalidades, EXTERNAL_WRITES_ENABLED=false, sin LLM reales, sin escrituras Trello/GitHub, sin modificar bases o ejecuciones históricas y sin push ni merge.
S10. Al terminar, espera mi revisión antes de continuar.

## Tasks
T1 [S7,S9] done; route=parent isolated harness reuse under ignored .runtime/playwright-canonical-navigation; immutable old guard helpers; commit=pending.
T2 [S2,S5] done; route=parent RED/GREEN page stage renderer using real API verifier name, no backend/shared formatter changes; commit=pending.
T3 [S3-S6] done; route=parent canonical test navigation and semantic locators, preserve all original assertions including later checks; commit=pending.
T4 [S1,S7-S10] in_progress; route=parent focused/full/typecheck/build, assessment/readback, exact results and local commit, stop for human review; commit=pending.

## Log
L1. Original user request verbatim:
Apruebo la corrección acotada de los ocho fallos Playwright clasificados.

Mantén intactos los seis commits de REQ-08 y conserva la nueva arquitectura de navegación.

Decisiones:

1. Corrige el defecto real en RunDetailPage: distinguir etapa omitida explícitamente, etapa desconocida/no registrada, etapa completada y etapa fallida. Normaliza nombres históricos solo según el contrato real.

2. NO vuelvas a insertar RepositoryResultsPanel en Proyectos, Calidad ni en la vista principal de Análisis. La evidencia detallada MultiRepo debe permanecer en `/analysis/:runId`.

3. Adapta las cinco pruebas de ubicación para navegar a la página canónica. Añade enlaces contextuales solo cuando mejoren realmente la experiencia del gestor.

4. Conserva todas las comprobaciones originales sobre identidad de repositorios, CI, cobertura, datos legacy, refresco de resultados y aislamiento entre proyectos. No elimines aserciones simplemente para conseguir que las pruebas pasen.

5. Corrige el locator ambiguo del selector de proyecto y la expectativa textual del botón de análisis en ejecución. Verifica también las comprobaciones posteriores de cada escenario.

6. Ejecuta las ocho pruebas afectadas, Playwright completo, typecheck y build. Ejecuta regresiones backend si se modifica algún contrato compartido.

7. Crea un commit local independiente con las correcciones y presenta los resultados exactos. Si aparecen defectos adicionales al superar los bloqueos iniciales, investígalos y repórtalos antes de ampliar el alcance.

Restricciones: sin cambios al pipeline multiagente, sin nuevas funcionalidades, EXTERNAL_WRITES_ENABLED=false, sin LLM reales, sin escrituras Trello/GitHub, sin modificar bases o ejecuciones históricas y sin push ni merge.

Al terminar, espera mi revisión antes de continuar.

L2. Read-only recovery: HEAD0b78a98 and six commits intact. Existing dirty RunDetailPage.tsx and run-detail.css each have one extra EOF blank, plus prior req08 task checkpoint; preserve/exclude. API repository.py:935 and orchestrator/domain use verifier, formatter already supports it: correct local constant, do not introduce unsupported aliases. Existing terminal effect AgentWorkflowBar:132 already invalidates repository-results (earlier preliminary exploration incompletely described it); exercise existing behavior, no speculative fix. Work inline: stage fix small, navigation/tests same behavioral unit, no pair of independent heavy writer units. Large tracking needed for isolated multi-turn proof/conditional stop and separate requested delivery; expected ~250 authored lines excluding this spec. No high-risk production contract changes intended. Harness writes only new ignored project-owned runtime, not old scratch or historical state.
L3. T1 mount observed: new ignored project-owned runner snapshots current tracked frontend (never .env/DB/history), imports immutable existing Node/browser guard helpers, preserves candidate spec prefixes with appended scratch-only hooks. Old scratch files unmodified. Preflight exit0 with allfive deliberate denials separated, childmethods intact, no unexpectednetwork. Runtime .runtime/playwright-canonical-navigation/runs/renewed-frontend-preflight-20261019T124412Z-2730013/results. Move T2in_progress before first test/source edit.
L4. Plan reviewed: T1 verifieddone; T2 in_progress, T3/T4 pending. REDjob13 exit1/11s: both originalunknownstage and new public-state regression failed at expected labels,2failed. Guard/method/source-prefix checksPASS0attempts. Production localfix changes constant verification→verifier (APIcanonical), label unrecorded/missing as No registrado; estado desconocido and CSSunknown whenunrecorded; existing formatterkept. GREENjob14 launched but not observed yet: do notmarkT2done or edit source snapshot whilejobverifies hashes. No newdefect/scope expansion identified.
L5. T2 GREENjob15 exit0/7s, both originalunknownstage+new4states/recordedfalse testsPASS; methods/source/overlay/portsPASS,0unexpectedattempts. Priorjob14 Playwright2PASS3.7s andallguardsPASS but wrapperexit1 due ourprintf unsetTYPECHECK_STATUS in selective mode; fixed reportingdefaults only (no guard/test change), not hidden. T2done, T3in_progress. Preserve all oldassertions by moving repositorychecks into canonical detail, not restoring panels; initialpreviousrun proof + activecurrentrun cache ensures terminalrefetch and no crossrunborrowing. Secondaryproject canonicalpage needs correctly project-bound syntheticrunfixture; no appcontract changes.
L6. Plan review after focusedjob16: sixPASS/twoFAIL32.0s; wrapper1, allguards/methods/prefix/portsPASS0unexpected. T3 stillin_progress, notdone. Two newly reached test-levelblocks investigated before any change: projectselector scoped to getByRole(banner) is excluded by finding modal's aria-hidden background (our locator must target exact existing DOM selector); legacycapability note text changed from acciones de decisión to decisiones protegidas in existingUI, no forbiddenbuttons present. No newappfunctionaldefect established; no productionquality/auth/modal change authorized. Preserve permission-note check by exactcurrentwording, not dropping it. Structural conservationcheck atthispoint confirms91originalassertions,89byte-equivalent/2intentionalsemanticlocator-labelchanges; update audit if additionalcopy correction. T4 remainspending untilfocused8actuallypass.
L7. Plan reviewed after job17:7PASS1FAIL11.1s, wrapper1, allguardsPASS0attempts. T3stillin_progress;T4pending, notdone. The remaining test now proves modalclosed/oldprojectnamesgone but our hard page.goto tosecondaryrun reloads WorkspaceProvider (selection intentionally inmemory), defaultingprimary; canonicalpage correctly rejects mismatchedproject. Error snapshot + WorkspaceContext.tsx prove safe appbehavior, not newappdefect. Fix only testnavigation via existing Análisis de IA→Abrir ejecución link, with project-bound secondaryrunlist fixture; do not add persistence, bypass scopechecks or restorepanels. ASTaudit replaces naive semicolon-based matcher (which truncatedpermissiontext), confirms91originalchecks:88exact and3intentionalsemanticlocator/copycorrections, allreplacementchecks present. Record allfailedattempts; no broader appchanges authorized.
L8. Plan reviewed on reminder: T1/T2 verifieddone; T3in_progress notdone, T4pending. Job18 still7PASS1FAIL32.2s with guardsPASS; secondarySPAselection correctlyretained, but synthetic runlist intercept targeted wrong endpoint. Real client.ts221-223 requests /runs?project_id=..., not /projects/:id/runs. Error snapshot shows primaryfixture rows undersecondary (defaulttestfixture), not productionAPI. Correct only predicatefixture to actualGETcontract, preserve allscope assertions; no third application fix or backendcontractchange. No automatic retry of unavailable proof/timeouts: each rerun follows diagnosed candidate fixture/locator change. All failedlogs retained.
L9. Focusedjob19 exit0/15s, all8casesPASS with every later assertion reached, no skips/filters beyond requested eight. Node/browserintegrity, five deliberate denials, candidateprefix/hash/overlay/portsPASS0unexpectedattempts. T3done,T4in_progress beforefullsuite. AuditAST verifies91oldassertions/88exact+3semanticallypreservedlocator-copycorrections, with additionalrun/projectscopeguards. Preparedindexpatch2ownedTSXhunks andexcluded existingEOFhunk; index/worktree notmodified. Fullsuite/typecheck/build/assessment/commit stillpending; do notclaimcompleteuntilobserved. No backend/sharedcontract change: onlylocalpage renderer and tests.
L10. Finalfulljob20 observedexit0/55s: Playwright135PASS42.8s,0F0skip; typecheck0(stderrempty); build0(Vite1.55s), onlyexpectednonfatal outDiroutsidecheckoutfrontend warning—newownoutput notemptied, protectionkept. Allguard/method/source-prefix/hash/portsPASS0unexpectedattempts; sixpreviouscommits intact, backend/sharedAPI/formatter untouched => requestedconditionalbackendregressions notapplicable. docs/playwright-canonical-navigation-validation.md records exactoutcomes, failedintermediates andlimits. All135 includesoriginal134plus1newpublicstateregression, no renamed/dropped/skipped tests. AST91originalchecks88exact+3semanticcorrections, laterchecks allreached. Risk: none (only local stage renderer; no shared contract/permissions/pipeline changes). T4in_progress until localcommit+committed-sliceASSESS/readback observed; do notmarkdoneearly. Stageonly6ownedpaths/2TSXhunks; preexistingEOF changes+oldtracker checkpoint stayout.
