# Header action modals and section clarity
Improve section hierarchy and replace the repeated assistant panel with two header actions and simplified dialogs.
Branch: feature/header-action-modals; base: 8e7d970f9044595f1a850e1ac52e2a8685ed9255. No publishing, dependencies, backend or operational-default changes.

## Specs

### S1
> Mejora el diseno de la interfaz no se puede distingir las secciones
>
> si existe estilos mas echos. hay texto sobre puesto en secciones del proyecto como analisis de IA

### S2
> y el ASISTENTE DE SEGUIMIENTO Y CONTROL esta en cada seccion, simplificalo solo conservando el boton de sincronizacion y envio de correo .

### S3
> coloca estos dos en el encabezado. y al presionar tienen que salir un modal con su contenido simpleficado

## Tasks
- T1 | S1,S2,S3 | delegated/context-backstop; focused browser tests and isolated baseline | done | Actual job12 five missing-header failures; first visibility assertion + four per-test click waits. App/browser works, no outer/global timeout. Visual styling uses screenshots, not fabricated RED | commit: pending
- T2 | S1,S2,S3 | same worker; shell/header/workflow/email presentation and section styles | blocked | Preserve existing permissions, confirmations, remote consent, project/session scope, idempotency and repository-result refresh | commit: pending
- T3 | S1,S2,S3 | parent background checks + independent verifier/item 2 | pending | Focused/regression E2E, typecheck/build, desktop/mobile/light/dark visual readback; outside-view of protected dialog lifecycle | commit: pending
- T4 | S1,S2,S3 | parent | pending | Exact-byte reviewable local commits, documentation and closure; no push/PR/merge | commit: pending

## Log

### L1 — Original request
> Mejora el diseno de la interfaz no se puede distingir las secciones y el ASISTENTE DE SEGUIMIENTO Y CONTROL esta en cada seccion, simplificalo solo conservando el boton de sincronizacion y envio de correo . coloca estos dos en el encabezado. y al presionar tienen que salir un modal con su contenido simpleficado

### L2 — Bounded source map and implementation route
AppShell.tsx currently inserts AgentWorkflowBar before Outlet on every route. AgentWorkflowBar owns analysis preparation/remote confirmation, tracked-job polling and project-scoped repository-results invalidation; its email details mount EmailDeliveryPanel. Keep one workflow mounted in the header, not one per page/modal. Header clicks open dialogs only; actual synchronization/email remain explicit in-dialog actions, retaining server authority and existing consent/CSRF/capability/idempotency guards. Closing/switching project/session must not expose stale preparation, recipient, confirmation or late responses. Existing navigation/search/theme/session controls remain. Improve section boundaries and visual hierarchy in both themes and mobile without unrelated page redesign.
Named risk item 2: moving privileged synchronization/email UI and changing dialog lifetimes. Required self-checks plus one independent outside-view after functional proof; RDD off, ASSESS only. No default extra planners/review campaigns. One cohesive worker owns presentation, tests and docs under context backstop; parent relays unknown-duration browser/build checks if the role lacks background tools.
Use installed dependencies/browser cache only; isolated snapshot excludes dotenv/npmrc/human lock and uses owned HOME/TMPDIR/Vite/cache/output paths, mocked API and blocked external browser requests. No real API/provider/GitHub/Trello/SMTP/model calls; runtime external writes false. Preserve human-owned frontend/pnpm-lock.yaml SHA256 e33fa83397d1e6a89ca9f55cf713db5396119d37828e4121ee04782548110bc3. Prior MultiRepo is completed and committed at base; no reopening or backend recertification.

### L3 — Writer started
Same cohesive worker mvdv4dne-13-xzvd prepares focused browser tests and isolated baseline before source UI changes. Parent runs unknown-duration checks in background; no tests observed yet. Sync header trigger remains openable during a running job to inspect status; only in-dialog duplicate-start is gated. Opening/reopening dialogs never prepares, confirms or sends by itself. Worker owns source/test map, no separate exploration agent or backend recertification. Await pushed readiness/completion; no polling.

### L4 — Baseline browser check running
Worker13 returned five focused tests, preserved production files and prepared red-baseline/snapshot/frontend from HEAD8e7d970. Parent job-12 runs those tests ONCE with installed direct Playwright CLI, one worker, 150s global/180s outer bounds, env-i/owned caches/mock API/external browser block. Exact commands/cwd/stdout/stderr/exit are under .runtime/header-action-modals/red-baseline/observed/red.*. No RED result claimed yet; distinguish missing-header behavior from environment errors. No source implementation before observed RED. Existing CodeGraph status check reported index present, no creation required.

### L5 — Actual missing-header RED observed
Parent read actual red.stdout/red.stderr: five tests started/failed, exit1/130.906s, stderrEMPTY. First fails expectedheaderSyncvisibility after5s; otherfourhit30s per-test timeout waitingforabsentheaderSync/Emailtriggers. These are missing-UI behavior failures, NOTenvironment/outer180s/global150s failure; downstream permission/consent/polling/counter assertions were not reached, so no claim those independently failed. No baseline rerun/retry. T1 outcome complete, T2 starts using same worker and preserved baseline/evidence. Source implementation now authorized by observed RED; retain security/business assertions and old API body semantics.

SAME implementation continuation mvdvjyaj-14-acvn active, same narrow surfaces and immutable RED baseline; no extra agents. It prepares source/tests/docs plus a new candidate, exact parent check commands and visual artifacts. No functional GREEN/build or screenshot verdict observed yet; independent review is ordered after self-proof, not started.

### L6 — Candidate implementation and actual first checks
Worker14 produced header/dialog/controller/compact-email/theme-section changes, new help doc and relevant UI selector updates. Canonical main tsc/diffcheck reported0; ad-hoc E2E tsc diagnostics in existing fixtures/tests are retained, not canonical behavior proof. Draftcandidate-t2 abandonedDO_NOT_USE; validcandidate-t2-ready only. Parentpreflight bound selectedtracked/newfrontendMainbytes andhelpdoc to snapshot exceptdeclaredownedVitecacheoverride/locks, protectedhumanlockunchanged. owned observed/source-binding.json recordshashes.

Parentjob13 actual results: focused5/4PASS1FAIL21.5s(command22.057s),stderrEMPTY; shared5file regressions78/78PASS1.8m(command109.324s),stderrEMPTY; canonical typecheck/buildexit0/5.528s(Vite1.42s),onlynonblockingchunk>500kwarning. No process/globaltimeout. Exactcommand/env/cwd/exit/stdout/stderr/results under candidate-t2-ready/observed/*; no actualbackend/provider/email/GHcalls, APIsmocked/externalbrowserabort/ownedstate. Fournewcasespassplacement/desktopmobile/openingread-only/cloudconsent/emaildelayed-sendduplicateguard/projectsessionlatepreparationreset. Downstreamlastpollcompletionassertions not reached in failingcase.

Actual direct ASSESS escalation2 returnsHIGH/nativeunassessableuntracked/RDDoff/unknownoutcome/selfverification+independentVerifier. Advisorynotapproval/no lifecycle/status/consent/nativeceremony. ONEoutside-view only aftercorrectfunctional+visualproof, notstarted.

### L7 — One consolidated polling/visual correction
Onlyfocusedcase111 failsline139: reopenedSyncPrepararanálisis enabled ratherthandisabled. Mock jobPollStatuses running→completed advancesautomatically; testdoesnotassertactualstill-runningstatefirst. Thisispossiblefixturetiming/cachedsame-job issue, notcertifiedproductionbug. Sameworker mustdiagnoseactualstate/requests beforefix, holdRUNNINGdeterministicallyandretainALLdisabled/noextra-writeassertions, explicitlyreleaseCOMPLETEDwhileclosedthenobservepolling. Neverchangeexpectedenabledorsleep/timingmask a realguarddefect.

Parent directlyread mobilelight390Sync anddesktopdark1280Email PNGs: backgroundtextvisiblybleeds/superimposesthroughmodal. Initialcodemodetext(imageobject)dumpedbase64/truncatedoutput andwasNOTvisualreadback; directimageattachmentreadresolvedtoolingissue. CurrentcapturesimmediatelyfollowtoBeVisible; actualstableopacity/background/z-index/animationmustbediagnosed. Use animations-disabled/stablestate screenshotcapture iftransitiononly; narrowlyfixmodalCSS ifstablelayeringactuallywrong. S1visualNOTapproved. Preserveoriginalimages/newtestassertions; do notsimulatevisualPASSfromcode.

SAME correction **mvdwphbi-15-fs57** active, existingnarrowedit surfaces, no addedagents or baseline/fullcampaign. NEWcorrectioncandidate/sourcebinding only; existingreadycandidate/observationsimmutable. Ifonlymock/test/capturechanges, proveprodApp/CSSbytesunchangedandreuse78/build0, rerunonlyfailedpollcase+changedvisualcapturecase. Ifactualprodchanges, scopedaffectedchecks/build asneeded, notblanketunrelated78repeat. ParentBGrelayunknownbrowser/build/server, stablelightdark1280/390sync/email/no-modal-sectionimagesmustbereadbeforeapproval. No permission/backend/API/deps/package/default/lock/ODD/Git changes byworker. T2in_progress/T3T4pending; no Green/visual/outsideview/commitclaim.

### L8 — User-reported overlap on AI Analysis
> si existe estilos mas echos. hay texto sobre puesto en secciones del proyecto como analisis de IA

S1 now explicitly includes the reported /analysis section overlap. This refines the existing visual-distinction task T2 and its T3 visual verification; both remain open. Original missing-header RED for S2/S3 stays valid and must not be rerun. Parent relayed exact report to SAMEactiveworker15: reproduce /analysis no-modal overlap with stable desktop/mobile light/dark screenshots and actual bounds/style evidence before deciding it works. Reuse existing project styles/components, no new library/dependency mandate. Existing78PASS/build0 do not prove visual correctness. Do not mask user-visible defects by screenshot-only changes if stable UI really overlaps. Narrow analysis-specific file surface is mapped only if actual cause requires it; globalstyles.css already allowed. No duplicate diagnostic/newagent/fullcampaign; protected action guards unchanged.

### L9 — Explicit user pause
> pausa

Parent immediately cancelled activeworker16 mvdxhrzk-16-vgfp. No further implementation, tests, commits or publishing until explicit user resume. Current code/tests/snapshots are partial and preserved. Last actualcheckjob14: controlledpoll1FAIL(missing running-label selector, not demonstratedenabledguard), stablevisual1PASS(default/emptyAnalysis4layouts) with opaque stablemodalcapture. ParentreadAIAnalysislight1280/dark390andSyncmobilelight: nooverlap in those controlled states, but populatedrepository/run state stillunverified. Existing78regressions/typecheckbuild0 apply only unchangedproductionbytes. Worker16 had been assigned exactrunninglabelselector preservation and populatedanalysisrepro; conditionalrepocomponentCSS/layout-only surfaces andsharedtestfixtureaddition authorized, no data/identity/eligibility/guardchanges. Its partial work may have landed before cancellation and must be reconciled against Git/diff, not assumed complete. T2blockedwaitinguserresume; T3/T4pending. PriorRED/job13/job14 evidence immutable. No independentreview orfeaturecommit done.






