# Action proposals

## Objective
Advance H2/REQ-08 with structured, capture-bound action proposals and explicit evidence validation, preserving all agent roles and human control. This document is not an execution authorization.

## Scope and constraints
- Continue mvp/real-sources from H1 CLI commit a79300b; offline only, .venv/bin/python, one source writer at a time.
- Preserve existing run/source-check/decide, providers, storage, grounding, policies and context budgets. No dependencies, credentials, live API/inference, Trello writes, push/PR/merge.
- Snapshot version is exact stored JSON capture digest, not snapshot.id or parsed-model reserialization. Evidence is explicit, run-scoped; IDs are EvidenceItem.provenance.id.
- Exact fields only: start_date/due_date/priority/assignee_ids/state. Unknown remains unknown; no label-derived priorities, invented actors or evidence associations.
- Initial H2A proposals remain pending, not_authorized and not_executed. Technical validation/approval cannot grant execution authority.
- Date values retain existing datetime representation; numeric/boolean/date-only coercion is rejected. Do not invent timezone normalization or execution semantics. Future date/execution policy remains unresolved.
- Authentication, authorized actors, review transition policy and specific action consent must be resolved before permission or execution is implemented.

## Tasks
- [x] H2A — Closed offline in 214a9256c97adff9d546869d99a272a5800ebc22; native approved+acknowledged. New pure proposal schema and validate_action_proposal(proposal,capture,evidence_items); field-specific values, immutable capture/project/task binding, explicitly observed current fact and exact supporting evidence. No review transitions or executor. Route: delegated writer (new source/test files).
- [x] H2B1 — Closed offline in 2cabd581763a0d2abf84c9790827ccf246521e76; native approved+acknowledged. Persist immutable proposals plus exact referenced sanitized submission evidence, optional storage port APIs, idempotency/conflict guards and read-only catalogue queries. Route: one delegated writer; no CLI/roles/review/auth/executor changes.
- [x] H2B2a — Closed offline in 4d0d0868185386f702e6e28493ba8cf10d56203f; native approved+acknowledged. Read-only CLI proposals list PROJECT and show PROJECT PROPOSAL_ID --db-path, explicit JSON projection, no raw supporting DTO/payload/URL/snippet export; preserve existing commands and nonmutation. Route: one delegated writer after mapped scope.
- [x] H2B2b1 — Closed offline in ad72e8dfb4fef57fe1b91ec14b6780248e2885ea; native approved+acknowledged, independently reproduced regression green. Add optional exact stored capture receipt API without changing legacy snapshot saves; transactional verification and compatibility tests. Route: one bounded writer.
- [x] H2B2b2 — Closed offline in ce0d11b0d76e0a7a8c5c0b73ee241937ab264d19; native approved+acknowledged. Monitoring typed changes with explicit data/rule constraints, unsupported/missing facts abstained and limitation recorded; no free-text intent inference.
- [x] H2B2b3 — Closed offline in a32f6f431541399bf25a63358272e726e472c346; native approved+acknowledged. Bind constrained Monitoring changes to real stored capture/evidence, persist through existing catalogue and retain all existing roles/legacy analysis; no authorization.
- [ ] H2C — Pending. Separate technical review, human asserted identity/decision and authorization with immutable audit/transitions. If no trustworthy authentication exists, implement explicit offline-only scope; real execution blocked, OS/CLI identity never assumed authenticated.
- [ ] H2D — Pending H2C validation. Dry-run allowed operations, state/version/permission/conflict/idempotency guards and before/after audit, simulated adapters only; real Trello writes require concrete human permission.

## Acceptance and checks
Observe test-first RED/GREEN; tests reject wrong field/value, stale capture/hash, missing/unobserved target fact, wrong/duplicate evidence ID, wrong project/task/category and fabricated authorization/execution. Cover all exact proposal fields, explicit null versus unknown/empty, deterministic serialization and nonmutation. Run focused and full tests plus offline evaluator checks; git diff --check. Native review stays on, per exact current candidate consent and provider bindings. Each closed unit has a local Conventional Commit, identity recorded here.

## Evidence
- H1 closed offline:534tests532pass2opt-in skips +7 evaluator checks (not live performance benchmarks). Real T9PENDING. Existing SQLiteResourceWarning nonblocking.
- Scout mv31ql95-l-i5bn mapped minimal domain/action_proposal.py + tests/test_action_proposals.py; evidence IDs are provenance.id and captures bind exact storedJSON hash. HumanDecisionState has no established transition/authentication policy, so H2A does not invent it.

## H2A verification
- Writer mv333rkp-n-96x8: RED42tests1expected importerror; GREEN47focused/full540run538pass2skip/pruebas7pass; gitdiffcheckclean. New source/test308lines,6tests withsubcases; parent6pass. Pure schema and validator enforce observed current fact plus matching field evidence, wrongcapture/project/task/IDs and fabricatedpermissions rejected; original models unchanged. Native review-7c4b7c55ce601d69 approved6a936068…; exact acknowledgement burned911f2a66…; ASSESSmedium/consumedtrue/closedderived (no separate verifier). Commit214a9256c97adff9d546869d99a272a5800ebc22.
- Supporting evidence is explicit metadata, not proof of membership in a capture; storage-verification assumed for SnapshotCapture. Review/authentication/executionpolicy remains unimplemented. No API/network/provider calls or role changes.

## H2B1 mapped scope
- Read-only mv336qdt-o-t4q4 completed: exact surfaces ports/storage.py, adapters/storage/sqlite.py and new tests/test_action_proposal_storage.py. No source changes. Parent cancelled nothing: cancellation raced with already completed scout; original final report recovered. Supplemental read-only continuation mv33kt1a-p-u5im is not a source writer.
- APIs save_action_proposal(proposal,supporting_evidence), get_action_proposal(id), list_action_proposals(project), optional/nonabstract; reads return proposal plus frozen evidence. Catalogue keyed proposal_id, exact canonical submission SHA; sameID+samebody idempotent, differentbody rejected. Capture lookup/validation/insert one transaction.
- Add idempotent catalogue DDL/index in a new explicit BEGIN IMMEDIATE after existing history migration/backfill, not base executescript; never alter original snapshot/run/evidence behavior. Read-only never creates/migrates. Missing catalogue clearly unsupported.
- Freeze only explicitly referenced sanitized DTOs, not mutable evidence(id,run_id) rows. Digest proves byte consistency, not authorship, current live state, or capture association. Preserve pending/not_authorized/not_executed.
- Target250-350source/testlines; >400stop/split. RED/GREEN new storage suite, existing storage/history/proposal/CLI/hybrid checks, fulltests,7evaluators, whitespace/native review then localcommit. No real DB/API/credentials or publishing.

## H2B1 verification
- Writer mv33p5we-q-sk2c completed3allowedpaths; source/test360addedlines (soft350 exceeded10, below400hardstop);7tempfiletests. REDmissingAPIs14expectederrors, subsequentreadonlyPermissionError/binarytamper RED failures; GREEN49focused/full547run545pass2skip/pruebas7pass; gitdiffcheckclean; parent7pass.
- Optional typedtuple port APIs; separate transactional catalogue migration; capture verification/save atomic; exactbodySHA+metadata integrity checks; frozen selected DTOs survive later evidence replacement/mutation. SameIDidenticalretry succeeds, conflictingbody denied. Read-only noncreation/missingcatalogue errors preserved. No status/authorship/live-state/captureassociation claim. Caller sanitizedDTOprecondition unchanged. Native review-95c3fe5d27b2bb2f approvedddee67bf…; exactackburnc9f8ab15…; ASSESSmedium/consumedtrue/closedderived, no separate verifier. Commit2cabd581763a0d2abf84c9790827ccf246521e76.

## H2B2a mapped scope
- Read-only scout mv33zv4o-r-o5qr: reuse CLI history handler/parser/dispatch pattern, direct SQLiteStorageAdapter(read_only=True), tupleget/list already validate frozen record/capturefacts; no schema/domain/validator change.
- Exactsource/test paths cli.py + new tests/test_cli_proposals.py; small README/requirements/validation updates to correct old H2-not-touched ledger. List explicit IDs/capture/field/status metadata and supporting_evidence_count; show allowlisted proposal fields including current/proposed values, rationale/impact/evidence_ids, never supporting EvidenceItemDTO.
- Not-found and foreign-project show indistinguishable; static errors never DB/model data. No Settings/providers/orchestrator/artifacts or migrations. No submit/import/transition/actor/executor; source/testtarget<=300lines, >400stop/split.
- Test RED/GREEN temporary persisted fixture, exact JSON/values/types, project isolation/errors/tamper/missingcatalogue/no evidence leakage, DB/schema/files unchanged, oldcommands/hybrid retained. Native review only exact completed unit, no accumulatedbranch.

## H2B2a verification
- Writer mv34ai5t-s-clrg completed5allowedpaths; source/test217addedlines,5newtests/subcases. RED62tests10missing-command errors included6duplicatedimportedTestCases; removedfixtureclassimport without removingoriginaltests; finalGREEN56focused/full552run550pass2skip/pruebas7pass. Parent5pass; gitdiffcheckclean.
- Explicit list/show JSON allowlist excludes supportingDTOpayload/summary/snippet/URL; statuses fixed, unknown/foreignshow indistinguishable, static errors. DBbytes/schema/dirs/artifacts unchanged in isolatedfixtures, oldcommands/hybrid preserved. Projection not a free-textproposal sanitizer or authorship/capture-provenance proof. SQLiteResourceWarning persists; no real DB/T9calls. Native review-4274773cf9fd69e3 approvedd822c7ae…; exactackburnb20d8cde…; ASSESSmedium/consumedtrue/closedderived, no separate verifier. Commit4d0d0868185386f702e6e28493ba8cf10d56203f.

## H2B2b indispensable product decision
- Scout mv34i2nx-t-3jrd completedread-only: Monitoring suggested_actions is free text and task_state_claims is observedstate, notchangeintent (monitoring.py17-25/report.py25-36); Quality findings/suggested_fix arecode-oriented, not taskfield intent (quality.py17-35,82+). Do not map these into invented task/field/value.
- Role context omits start_date/priority/assignee_ids (context.py169-223); labelsnotpriority. Fake/defaultoutput muststaycompatible. Orchestrator persists snapshot beforeanalysis170-210 but save_snapshot returns no exactcapture receipt; newoptionalreceiptcapability needed, unsupportedcustomstorage skiponlyproposals notanalysis.
- Human mustchoose structuredintent source: recommended explicittyped task_id/field/proposed_value/evidenceIDs withAIformatting only; alternatively typedrequested-change Monitoringoutput underdefinedper-field rules; alternatively only externallysuppliedcomplete drafts, nogeneration.
- No nextwriter untildecision. Chosenwork mustbe split into boundedunits; proposedmulti-area receipt/context/binding/fakeprovider integration isnot silentlyapproved as a >400lineunit. No reviewerflags/APIkeys/verified_by strings resolveintent/authorship; no auth/status/executor changes.

## Resumed human decision and current baseline
- Latest human continuation explicitly chooses Monitoring typed changes, overriding earlier typed_intent choice; no repeatquestion. Monitoring canchoose onlydata/rule-supportedtask/field/value, otherwiseabstain. No actor/date/priority/dependency invention; native/report/technicalapproval not actionpermission.
- HEADd378738, allGentletasks completed/noactivewriter; onlylocalchange was retained taskdecisionnotes. No implementationB2b/C beyondHEAD. Currentverifier reproduced552run550pass2opt-inskips (realOllama/Gemini),7evaluatorspass/diffcheckclean; no standalonefakeCLIrun, fakeCLI/e2etests passed. Historicalreal31Trello/6Github/0CI notrerun; T9pending.
- Inspection4415ef00… selectedonly passive tasknotes, notsourceunit; no new runtimecode tofreeze. Sourceunits keepnativeRDDon; don'trepeat acknowledgedunits orwholebranch.
- Splitnextimplementation into receipt, constrainedMonitoringcontract/rules, andpipelinebinding <=400source/testperunit. Review/auth/dryrunofflinefollow; H3/H4 independentofflinewhenH2stable. No network/privatecodeexport/paidprojectinference/Trellowrite/push/PR/merge permission.

## H2B2b1 recovered verification
- Both requiredtasks recovered via status/result: receiptwriter mv44jsd2-v-m083 and scout mv44jsgk-w-lnx1 completed, no duplicatedwriters. Receipt3paths205source/testaddedlines;7newtests. WorkerRED47tests8missing-methoderrors; GREEN47focused/full559run557pass2skip/pruebas7pass/diffcheckclean.
- Fresh independent verifier reproducedexact47/559run557pass2skip/7checks; actualstoredrawJSONmetadata identity, precommit verification/rollback, postcommit reread, legacy save_snapshot/hash/readonly/customport compatible. No requiredoperation incomplete; Error:terminated hasno demonstratedeffect, no unrelatedincident investigation. Native review-457c698b9a5b9bad approvedd7fb715a…; exactackburn8eec2dbf…; ASSESSmedium/consumedtrue/closedderived; commitad72e8dfb4fef57fe1b91ec14b6780248e2885ea.
- Scoutnextunit: actualmodelMonitoringDraft, no task-proposalrules currently. Fourpathsreport.py/context.py/monitoring.py/newtest. Ownerprovidedproject/task/field allowedtypedvalues+rationale/impact, modelselectonlyprecomputedchoices; old observations orfreeactionsnotnewintent. Missingrule/context/evidence/observation/value mismatch=>abstainlimitation. Standardfixtures sparse→no arbitrarypositivefakeoutput. Capture/persistencebindinglateractualreceipt, no fabricatedcapture inthisunit.

## H2B2b2 implementation scope
- Exactlyreport.py/context.py/monitoring.py/tests/test_monitoring_proposals.py. Add optional project-scoped DomainProposalRuleSet and typed requested_changes/limitationcodes to actualMonitoringDraft, not a newagenthierarchy.
- Rules specify exact eligible taskIDs, field, allowedtyped alternatives and nonempty owner rationale/impact. Monitoring choosesonlyprecomputedlegitimatechoices; canonicalcurrentfact/evidence/justification sourcedserver-side, not inventedmodel prose. Rules notauthorization/authentication and not default scheduling policy.
- Fields supportedbycontract remain5; unavailable/unobserved/context-omitted start/priority/assignees mustabstain; no labelinference. Missing/mismatched explicit task EvidenceItems reject. Currentstate/due existingvisiblefacts usable; optionalcontextbudget inability abstainswithoutdegradinglegacyanalysis.
- NewAPIoptional rules/evidence keeps oldclients/fake defaults/narrativeactions/stateclaims. Positivefixture requiresgenuineexplicitrule/data+queued modelselection, notfakegenericactivity. No syntheticcapture receipts/persistence/roleapproval inthisunit; actualreceipt bindinglater.
- Target<=350source/testlines, >400stop/split. RED/GREEN source/role/contextfocused andwhole regression, nativeexactunitreview thenlocalcommit. No newprovider/config/CLI surfaces or externalcalls.

## H2B2b2 verification
- Writer mv4avwop-y-yqh0 completed4paths326added/5removedsource/testlines;7newtests. RED50focused1expectedmissingDomainProposalRuleSetimport; finalGREEN56focused/full566run564pass2skip/pruebas7pass/diffcheckclean; parent7pass. No positivegenericfakeactivity, queuedpositiveexplicitrules/facts.
- Optional DomainProposalRuleSet +actualMonitoringDraft requested_changes/defaultlimitations. Model selects selected_choice_ids only; canonicalchange/value/evidence/rationale/impact server-derived, forged/conflicting selections rejected. Missingrules/unobserved/context/evidence/project/type/allowedvalue oroptionalbudget failure abstains, legacyanalysis/fake defaults preserved. No fabricatedreceipt/persistence/authorization. SQLiteResourceWarning known. Native review-823dc56a5454240f approveda6f2cfee…; exactackburnbfa181b5…; ASSESSmedium/consumedtrue/closedderived; commitce0d11b0d76e0a7a8c5c0b73ee241937ab264d19.

## H2B2b3 scope after completed scout
- Scout mv4b00j1-z-qv7s completedread-only; onlyorchestrator.py +newtest_orchestrator_proposals.py, target350lineshard400. ActualAgentAPI run(project_id,metrics,tasks,proposal_rules=rules,task_evidence=evidenceitems); sourcecontractce0d11bstable.
- Per-executionoptionalownerDomainProposalRuleSet and normalizeddigest inlogicalconfig/runfingerprint (nonepreserveslegacyhash). Saveactualreceipt aftersave_run beforemetrics. Unsupportedcapability/methodabsenceNotImplemented fallback legacySAVEONCE; arbitrarysave/receiptfailure notfallback norfalseproof. No receipts disablesonlyproposals/stablelimitation.
- Real taskEvidence fromcurrentcollectionnotlegacytaskIDs, requiredsamecollection/snapshotor explicitlimitation ifnotestablishable; DTOmetadata/hash donotprovecaptureassociation. Canonical typedselection ->serverActionProposal boundactualstoredsnapshotid/version, purevalidate, frozenimmutablecatalogue. Stablelogicalfingerprint+choiceproposalID andbody forretries, notrandomrunID/newwallclock creation. No modelpermission/statusclaims.
- Proposalsstage output_summary records count/stablelimitations/per-choiceerrors aslimited/skipped, notanalysisfailure/needs_attention; legacyreport/Quality/Verifier/EscalationGate retained. Standardfixtureunknownanalysis-only; positivefixtureexplicitobservations/evidence/rule andqueuedallowedchoice. Config/CLI rule-file separatefutureunit, notqualitypolicies_path. Realactor/auth/executorblocked.

## H2B2b3 verified handoff
- Writer mv4bewck-10-i4hy completeONLY2source/testpaths305added306changedlines;9newtests. RED5tests9missingAPI/stageerrors; GREEN47focused/full575run573pass2skip/pruebas7pass/diffclean; parent9pass.
- Optional execute(...,proposal_rules=DomainProposalRuleSet), absentpreserveslegacyhash/rolecounts/cache; normalizedrules/currentevidencefingerprint isolateschanges. Authenticreceipt +samecurrentcollectionsnapshotidentity, no temporalprovenance claim; legacySAVEonce onlyabsent/NotImpl, arbitraryerrorspropagate.
- Canonicalownerchoice revalidated, actualcapture metadata serverboundActionProposal->puredomainvalidator->frozencatalogue. Stablelogicalfingerprint/choice/evidIDs/body retries, forged/mutabledraft/conflict rejected, proposalsstage isolatedlimitations/skipped notlegacyanalysisfailure. Pending/not_authorized/not_executed only. SQLiteResourceWarningpersists. Native0c36c7aa5f576f10 approvedfa583766 exactackburn7da1fced/ASSESSmediumconsumedtrueclosedderived; commita32f6f431541399bf25a63358272e726e472c346.
- Separate A91committedreviewreminder noexactdecline; inspection8already-unit-reviewedpaths/d378base, STARTblockedbeforelineage byworkernewtestintendedselection; inspectexcludechangeddefaulttarget2ba, explicitcommittedSTART retainedselectionmismatch/originalbindingreject allmutationfalse. No A91reviewclaimed, no authorityrecovery. Readonlyincidentexplorer mv4bkoru-11-el7d pending, activewriterfinished now. FutureunitreviewmustexactnormalizedB3candidate notA91substitute.

## H2B2b3 closure and exact reminder resolution
- Integrationapproved/acknowledged0c36c7aa; advisory R3-proposal-write-not-retried at orchestrator.py:812-823 is nonblocking separatelaterwork; normalrun-cachemeanscataloguefailedwritesarenotautomaticallyretried. No correction/reopen offered.
- Incident scout mv4bkoru-11-el7d completedsource-only. Facade INSPECT input explicitlyaccepts {baseRef,committedOnly:true}; plaininspectexcludedscopehadselectedmutabledefault, notproofworkercausality. No malformedauthority/reset/recovery needed.
- FreshINSPECT explicitd378738range committedOnlytrue excludeconfirmed EXACTa91bf4d7 original8paths. START review-e18990f1f35589a5 actualhostconsent,forecast1pi_host_relay review-reliability; finalapproved0668d348 exactackburn forA91. No scope substitution. AdvisoryR3-incomplete-change-validation report.py:81 nonblockingseparatelaterwork. B3fa currentcandidate remainedconsumedclosedderived, thencommittednormally.

## H2B2c owner-rule input scope
- [x] H2B2c — Closed offline in 29340e7930998e780399da1919b4a6f449675abc; native approved+acknowledged.
- Fourpathsapplication/config.py,newadapters/sources/local_monitoring_rules.py,cli.py,newtests/test_monitoring_rule_config.py. Distinct monitoring_rules_path settings/YAML/environment and --monitoring-rules optionalCLI flag; neverreuse qualitypolicies_path/provider/source policies.
- JSONDomainProposalRuleSet strictproject/task/field/value/rationaleimpact validation beforeexecution; staticerrors forinvalid/missing/foreignpolicyfiles, no paths/rawpayload/credentialleak. Explicitpolicynottrust/auth, no defaultfakechoices. execute receivesonlyvalidatedtypedoptionalrules, fingerprintalreadyincludesnormalizedrules. Existingofflinefake/quality/gates/reports/commands retained.
- Target350source/test hard400. RED/GREEN localtempfiles/parser/config/CLIintegration tests/fullregression/nativeexactunitreview/localcommit. No YAMLruleparser/deps/network/realauth/execution/autoactions. C/D offlineidentityreview thencontrolleddryrun remainpending.

## H2B2c verified handoff
- Writer mv4c2jql-12-r34k completedONLY4authorizedpaths266added/7removedlines (66source200tests),8newtests. REDmissing-loaderimport; GREEN8new+33config+10CLI/full583run581pass2skip/pruebas7pass/diffclean. Currentdoc/headerpreserved.
- Publicrun --monitoring-rules PATH overridesSettings/YAMLmonitoring_rules_path/environmentMONITORING_RULES_PATH, Nonepreserveslegacyexecution/cache+abstention. Newbounded1MiBJSONDomainProposalRuleSet loaderstrictprojectmatching/value/null/date/trustflags validation staticerrors; validatesBEFOREstorage/providercomposition, explicitinvalid/missing/foreignfile exit2 no partialrun.
- Constraintsonlynotauth; currentobservation/evidence stillneeded, sparsefixturesno inventedactivity. No orchestrator/context/domain/storage/provider/fixturechanges, noactuation. Nativee7ec43a43835620f approvede760c5b1 exactackburn8ce2035b ASSESSmediumconsumedtrueclosedderived; commit29340e7930998e780399da1919b4a6f449675abc; SQLiteResourceWarningknown.

## H2B2c closure
- Commit 29340e7930998e780399da1919b4a6f449675abc, native review-e7ec43a43835620f approved and acknowledged; the approved receipt stands. Advisory R3-duplicate-json-keys at local_monitoring_rules.py:23 is separate, non-blocking later work. No correction transition or reopening.
- Eight new tests; 583 run, 581 passed, 2 opt-in skipped, 7 evaluator checks passed. SQLite ResourceWarning remains. No real action or authentication added.

## H2C bounded offline units
The original omnibus H2C task is refined into the three authoritative units below:
- [ ] H2C1 — In progress: pure domain for technical eligibility, explicit offline actor claims, review decisions, dry-run permissions and event-folded projection. Only new domain/proposal_review.py and tests/test_proposal_review.py. Target <=350 changed source/test lines, hard stop >400.
- [ ] H2C2 — Pending: append-only SQLite decision ledger, exact stored proposal JSON hash binding, CAS/version conflicts and idempotent replay. Separate port/storage/test unit; never alter immutable proposal rows.
- [ ] H2C3 — Pending: CLI offline review and explicit dry-run authorization/denial/revocation, explicit actor argument without UID/environment fallback. No live executor.
- Scout mv4c4ugv-13-faao completed read-only: existing human/report decisions have no actor and are not action approval; security.py protects remote providers, not human authentication. Current catalogue has no decision events or exposed stored-body hash.
- Offline approval records an asserted identifier, not identity proof or organizational authority. Technical validation, human assertion, dry-run grant and execution remain separate. Owner rules and byte hashes grant no permission.
- Initial projection pending / not_authorized / not_executed. Every event bound to exact project/proposal/capture/stored proposal-body hash and expected prior version; append-only fold with identical replay versus changed-content conflict. Pure unit receives adapter-verified body hash, does not fabricate one by reserializing parsed models.
- Extra actor roles/authenticated flags/UID claims rejected; explicit nonempty actor claim has fixed asserted_offline provenance. Real execution always denied; only explicit dry-run permission after valid technical assessment and offline review.
- Tests first for initial state, technical eligibility without approval, offline approval without permission, dry-run grant/deny/revoke, rejection, invalid transitions, forged/mutable claims, stale/mismatched bindings/version, replay/conflicts and capture/evidence failures. No storage/CLI changes in C1.

## H2C1 verified handoff
- Writer mv4cgep3-14-v0of completed two new files, 319 source/test lines, six new tests; no existing source changed. RED expected missing proposal_review module; GREEN 28 focused, 589 run / 587 passed / 2 opt-in skipped, 7 evaluator checks, diff check clean.
- OfflineActorClaim, DecisionEvent, TechnicalAssessment, Projection; assess_proposal and fold_decisions derive eligibility from actual proposal/capture/evidence, reject extra/forged/mutable claims, strict digest/version/bindings.
- Approval grants no permission; authorize_dry_run separately requires approved_offline and eligibility, bound_proposal_dry_run scope only. Terminal rejection withdraws permission; deny/revoke clear it, new explicit grant while approved may reauthorize. Replay preserves version; changed event ID content conflicts, stale/mismatched bindings fail without mutation. Real execution always false.
- Adapter supplies verified lowercase 64-hex SHA256 of exact stored catalogue JSON INCLUDING evidence, never parsed-model reserialization. C2 must expose this digest and append events transactionally; C3 requires explicit actor without identity fallback. Native exact-unit review/commit pending; SQLite warning remains.

## Next step
H2B2b2closed. Rule input and pipeline closed. Launch only bounded H2C1 pure-domain writer; follow its completed handoff with checks/native exact-unit review/local commit, then ledger and CLI. No unclosed native authority. Bind/persist through actual stored receipt in H2B2b3 later; unsupportedcustomstorage preserveslegacyanalysis. H2C/D offline/liveblocked andH3/H4follow safely. No realauth/execution/T9 or repeated decision.

Locator: odd/tasks/action-proposals.md
