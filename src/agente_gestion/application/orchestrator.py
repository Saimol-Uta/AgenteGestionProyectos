"""Modular project analysis orchestrator with stage sequencing, budget tracking, and persistence."""

import json
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.domain.action_proposal import ActionProposal, validate_action_proposal
from agente_gestion.application.proposal_auto_review import ProposalAutoReviewService
from agente_gestion.agents.context import monitoring_choices
from agente_gestion.domain.tasks import (
    TaskCard,
    DeterministicTaskMetrics,
    calculate_task_metrics,
    validate_due_soon_days,
)
from agente_gestion.domain.code import (
    CodeDiffSnippet,
    CIRunResult,
    match_ci_for_commit,
)
from agente_gestion.domain.policies import (
    QualityRule,
    PolicyEvaluationResult,
    evaluate_policies,
)
from agente_gestion.domain.findings import (
    CandidateFinding,
    FindingVerification,
    VerificationVerdict,
)
from agente_gestion.domain.report import (
    MonitoringDraft,
    DomainProposalRuleSet,
    MonitoringRequestedChange,
    FinalReport,
    HumanDecisionState,
)
from agente_gestion.domain.run import (
    ExecutionRun,
    RunStatus,
    StageExecutionRecord,
    StageStatus,
    compute_sha256,
)
from agente_gestion.ports.sources import TaskSourcePort, CodeSourcePort, CISourcePort
from agente_gestion.ports.source_collection import SourceCollectionPort
from agente_gestion.ports.storage import StoragePort
from agente_gestion.ports.llm import LLMProviderPort
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.adapters.storage.artifacts import ArtifactStorageAdapter
from agente_gestion.agents.monitoring import ProjectMonitoringAgent
from agente_gestion.agents.quality import CodeQualityAgent
from agente_gestion.agents.verifier import VerifierAgent
from agente_gestion.agents.report import ReportAgent
from agente_gestion.application.generation import (
    GlobalBudgetTracker,
    StageInvocationCoordinator,
    GlobalBudgetExceededError,
)
from agente_gestion.application.escalation import EscalationGate, EscalationDecision
from agente_gestion.application.reporting import (
    generate_report_markdown,
    summarize_repository_results,
)
from agente_gestion.application.validation import SemanticValidator, ValidationError
from agente_gestion.quality.evaluator import QualityEvaluator
from agente_gestion.application.repository_analysis import analyze_repository_results
from agente_gestion.application.config import Settings, SystemConfig

DEFAULT_TIMEZONE = ZoneInfo("America/Guayaquil")


class OrchestratorConfig:
    """Settings passed into the Orchestrator for run execution."""

    def __init__(
        self,
        project_id: str = "proyecto-demo",
        default_fixture: str = "fixture_1_standard",
        allow_remote_ai: bool = False,
        max_llm_calls_per_run: int = 8,
        quality_timeout_seconds: float = 120.0,
        verifier_timeout_seconds: float = 120.0,
        quality_max_output_tokens: Optional[int] = None,
        quality_context_margin: int = 64,
        runs_dir: str = "runs",
        fixtures_dir: str = "fixtures",
        db_path: str = ".runtime/agente.db",
        policy_version: str = "v1",
        is_simulated: bool = False,
        timezone: str = "America/Guayaquil",
        commit_sha: Optional[str] = None,
        target_commit_sha: Optional[str] = None,
        due_soon_days: int = 7,
        monitoring_max_generation_tokens: int = 512,
    ):
        if verifier_timeout_seconds <= 0:
            raise ValueError("verifier_timeout_seconds must be positive")
        self.monitoring_max_generation_tokens = monitoring_max_generation_tokens
        self.project_id = project_id
        self.default_fixture = default_fixture
        self.allow_remote_ai = allow_remote_ai
        self.max_llm_calls_per_run = max_llm_calls_per_run
        self.quality_timeout_seconds = quality_timeout_seconds
        self.verifier_timeout_seconds = verifier_timeout_seconds
        self.quality_max_output_tokens = quality_max_output_tokens
        self.quality_context_margin = quality_context_margin
        self.runs_dir = runs_dir
        self.fixtures_dir = fixtures_dir
        self.db_path = db_path
        self.policy_version = policy_version
        self.is_simulated = is_simulated
        self.timezone = timezone
        self.due_soon_days = validate_due_soon_days(due_soon_days)
        self.commit_sha = commit_sha or target_commit_sha
        self.target_commit_sha = self.commit_sha

    def to_hash(self) -> str:
        return compute_sha256({
            "project_id": self.project_id,
            "default_fixture": self.default_fixture,
            "allow_remote_ai": self.allow_remote_ai,
            "max_llm_calls_per_run": self.max_llm_calls_per_run,
            "policy_version": self.policy_version,
            "timezone": self.timezone,
            "due_soon_days": self.due_soon_days,
            "commit_sha": self.commit_sha,
        })


class ProjectOrchestrator:
    """
    Explicit modular Orchestrator coordinating analysis flow:
    Snapshot -> Facts/Metrics -> Monitoring -> conditional CodeQuality ->
    selective EscalationGate/Verifier -> Report -> final validation -> persistence.
    """

    def __init__(
        self,
        sqlite_storage: SQLiteStorageAdapter,
        artifact_storage: ArtifactStorageAdapter,
        elaborador_provider: LLMProviderPort,
        revisor_provider: LLMProviderPort,
        verificador_provider: Optional[LLMProviderPort] = None,
        aprobador_provider: LLMProviderPort = None,
        source_adapter: Optional[Any] = None,
        escalation_gate: Optional[EscalationGate] = None,
        config: Optional[OrchestratorConfig] = None,
        manager_notifications: Optional[Any] = None,
        manager_email: Optional[str] = None,
    ):
        self.sqlite = sqlite_storage
        self.artifacts = artifact_storage
        self.elaborador_provider = elaborador_provider
        self.revisor_provider = revisor_provider
        self.verificador_provider = verificador_provider
        self.aprobador_provider = aprobador_provider
        self.source_adapter = source_adapter
        self.escalation_gate = escalation_gate or EscalationGate()
        self.config = config or OrchestratorConfig()
        self.manager_notifications = manager_notifications
        self.manager_email = manager_email
        self._last_collection_result: Optional[Any] = None
        self._last_target_commit_sha: Optional[str] = None

    def execute(
        self,
        project_id: Optional[str] = None,
        snapshot: Optional[WeeklySnapshot] = None,
        fixture_name: Optional[str] = None,
        proposal_rules: Optional[DomainProposalRuleSet] = None,
    ) -> ExecutionRun:
        self._last_collection_result = None
        self._last_target_commit_sha = None

        proj_id = project_id or (snapshot.project_id if snapshot else self.config.project_id)
        start_time = time.time()
        now = datetime.now(DEFAULT_TIMEZONE)

        # 1. Obtain or collect snapshot
        if snapshot is None:
            if self.source_adapter is None:
                raise ValueError("No snapshot provided and no source_adapter injected")
            snapshot = self._collect_snapshot(proj_id, now, source_fixture=fixture_name)

        snapshot_hash = compute_sha256(snapshot)
        config_hash = self.config.to_hash()
        proposal_codes = []
        rules = None
        task_evidence = []
        collection = self._last_collection_result
        # Identity here ties evidence to this collection result, not to an equal
        # task ID/hash or a cached result from a previous load/execution.
        if collection is not None and collection.snapshot is snapshot:
            task_evidence = [e.model_copy(deep=True) for e in collection.evidence]
        elif proposal_rules is not None:
            proposal_codes.append("collection_unassociated")
        if proposal_rules is not None:
            rule_data = proposal_rules.model_dump(mode="json")
            try:
                rules = DomainProposalRuleSet.model_validate(rule_data)
                for rule in rules.rules:
                    rule.task_ids.sort()
                    rule.allowed_values.sort(key=lambda v: json.dumps(v, default=str, sort_keys=True))
                rules.rules.sort(key=lambda r: r.rule_id)
                rule_data = rules.model_dump(mode="json")
                if rules.project_id != proj_id or snapshot.project_id != proj_id:
                    proposal_codes.append("project_mismatch")
                    rules = None
            except (ValueError, TypeError, AttributeError):
                proposal_codes.append("invalid_rules")
            config_hash = compute_sha256({"config": config_hash, "proposal_rules": rule_data,
                "task_evidence": sorted((e.model_dump(mode="json") for e in task_evidence),
                                        key=lambda e: json.dumps(e, sort_keys=True))})

        # 2. Check idempotence: if identical snapshot & config completed run exists, return it
        existing_run = self.sqlite.find_run_by_fingerprint(
            snapshot_hash=snapshot_hash, config_hash=config_hash
        )
        if existing_run and existing_run.status in (RunStatus.completed, RunStatus.awaiting_human):
            return existing_run

        # 3. Create new ExecutionRun record
        run_id = f"run-{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        run = ExecutionRun(
            run_id=run_id,
            project_id=proj_id,
            status=RunStatus.analyzing,
            created_at=now,
            updated_at=now,
            snapshot_id=snapshot.id,
            config_hash=config_hash,
            snapshot_hash=snapshot_hash,
            policy_version=self.config.policy_version,
            prompt_versions={
                "monitoring": "monitoring_draft_v1",
                "quality": "code_findings_v1",
                "verifier": "verifier_output_v1",
                "report": "report_synthesis_v1",
            },
            schema_versions={
                "monitoring": "MonitoringDraftSchema",
                "quality": "CodeFindingsSchema",
                "verifier": "VerifierOutputSchema",
                "report": "ReportExplanationSchema",
            },
            is_simulated=getattr(self.elaborador_provider, "is_simulated", False) or self.config.is_simulated,
        )
        self.sqlite.save_run(run)
        capture = None
        receipt_save = getattr(self.sqlite, "save_snapshot_with_capture_receipt", None)
        if proposal_rules is not None and callable(receipt_save):
            try:
                capture = receipt_save(snapshot, content_hash=snapshot_hash)
            except NotImplementedError:
                self.sqlite.save_snapshot(snapshot, content_hash=snapshot_hash)
        else:
            self.sqlite.save_snapshot(snapshot, content_hash=snapshot_hash)
        if proposal_rules is not None and capture is None:
            proposal_codes.append("capture_unavailable")

        # Persist normalized evidence if composite collection occurred
        if self._last_collection_result and getattr(self._last_collection_result, "evidence", None):
            self.sqlite.save_evidence(run_id, self._last_collection_result.evidence)

        # Record source_collection stage if composite collection occurred
        if self._last_collection_result:
            collection_records_dump = [
                r.model_dump() for r in getattr(self._last_collection_result, "records", [])
            ]
            source_summary = json.dumps({
                "target_commit_sha": self._last_collection_result.target_commit_sha,
                "is_complete": self._last_collection_result.snapshot.is_complete,
                "records": collection_records_dump,
            })
            total_coll_duration = sum(
                r.duration_seconds for r in getattr(self._last_collection_result, "records", [])
            )
            run.stages["source_collection"] = StageExecutionRecord(
                stage_name="source_collection",
                status=StageStatus.completed,
                duration_seconds=total_coll_duration,
                output_summary=source_summary,
                data_hash=compute_sha256(collection_records_dump),
                completed_at=datetime.now(ZoneInfo("UTC")),
            )
            self.sqlite.save_stage_result(run_id, run.stages["source_collection"])

        # Initialize shared budget and attempt coordinator
        budget_tracker = GlobalBudgetTracker(max_calls=self.config.max_llm_calls_per_run)
        coordinator = StageInvocationCoordinator(
            budget_tracker=budget_tracker,
            max_transient_retries=1,
            max_repair_attempts=1,
        )

        def _format_model_tag(provider, default_provider_name: str) -> str:
            if provider is None:
                return "unavailable"
            is_sim = getattr(provider, "is_simulated", False) or self.config.is_simulated
            if is_sim:
                return getattr(provider, "model", "simulated")
            p_name = getattr(provider, "provider_name", default_provider_name)
            p_mod = getattr(provider, "model", "unknown")
            if "/" in str(p_mod):
                return str(p_mod)
            return f"{p_name}/{p_mod}"

        verificador_model_name = "unavailable"
        if self.verificador_provider is not None:
            is_sim = getattr(self.verificador_provider, "is_simulated", False) or self.config.is_simulated
            if is_sim:
                verificador_model_name = getattr(self.verificador_provider, "model", "simulated")
            elif self.config.allow_remote_ai and bool(getattr(self.verificador_provider, "api_key", None)):
                verificador_model_name = _format_model_tag(self.verificador_provider, "gemini")
            else:
                verificador_model_name = "unavailable"

        models_used = {
            "elaborador": _format_model_tag(self.elaborador_provider, "gemini"),
            "revisor": _format_model_tag(self.revisor_provider, "groq"),
            "verificador": verificador_model_name,
            "aprobador": _format_model_tag(self.aprobador_provider, "gemini"),
        }
        provider_errors: list[str] = []
        escalation_map: dict[str, list[str]] = {}

        # 4. Deterministic Backend facts & metrics calculation
        task_metrics: DeterministicTaskMetrics = calculate_task_metrics(
            cards=snapshot.tasks,
            cutoff=snapshot.cutoff_datetime,
            timezone_name=self.config.timezone,
            due_soon_days=self.config.due_soon_days,
        )

        # MultiRepo has no arbitrary project-wide primary repository or commit.
        multi_repository = bool(snapshot.repository_results)
        eval_commit_sha = None
        if not multi_repository:
            if self._last_collection_result and getattr(self._last_collection_result, "target_commit_sha", None):
                eval_commit_sha = self._last_collection_result.target_commit_sha
            elif snapshot.code_diffs:
                eval_commit_sha = snapshot.code_diffs[0].commit_sha
            elif getattr(self.config, "commit_sha", None):
                eval_commit_sha = self.config.commit_sha

        matching_ci: Optional[CIRunResult] = None
        if not multi_repository:
            if eval_commit_sha:
                matching_ci = match_ci_for_commit(snapshot.ci_runs, eval_commit_sha)
            elif snapshot.ci_runs:
                matching_ci = snapshot.ci_runs[0]

        # Stage variable containers
        monitoring_draft: Optional[MonitoringDraft] = None
        candidate_findings: list[CandidateFinding] = []
        escalation_decisions: list[EscalationDecision] = []
        escalated_findings: list[CandidateFinding] = []
        routine_findings: list[CandidateFinding] = []
        verifications: list[FindingVerification] = []
        final_report: Optional[FinalReport] = None
        report_md: Optional[str] = None

        has_partial_failure = False
        failure_reason = ""

        # ================= STAGE 1: Monitoring (Elaborador) =================
        stg1_start = time.time()
        try:
            monitoring_agent = ProjectMonitoringAgent(
                provider=self.elaborador_provider,
                model=getattr(self.elaborador_provider, "model", "gemini-3.5-flash-lite"),
                max_generation_tokens=self.config.monitoring_max_generation_tokens,
                coordinator=coordinator,
            )
            monitoring_evidence_ids = [t.id for t in snapshot.tasks]
            if matching_ci:
                monitoring_evidence_ids.append(matching_ci.id)
            monitoring_draft = monitoring_agent.run(
                project_id=proj_id,
                metrics=task_metrics,
                tasks=snapshot.tasks,
                ci_result=matching_ci,
                evidence_ids=monitoring_evidence_ids,
                source_is_complete=snapshot.is_complete,
                source_commit_sha=eval_commit_sha,
                **({"proposal_rules": rules.model_copy(deep=True) if rules else None,
                    "task_evidence": [e.model_copy(deep=True) for e in task_evidence]}
                   if proposal_rules is not None and not proposal_codes else {}),
            )
            run.stages["monitoring"] = StageExecutionRecord(
                stage_name="monitoring",
                status=StageStatus.completed,
                duration_seconds=time.time() - stg1_start,
                output_summary=f"Draft completed. Referenced {len(monitoring_draft.referenced_task_ids)} tasks.",
                data_hash=compute_sha256(monitoring_draft),
                completed_at=datetime.now(DEFAULT_TIMEZONE),
            )
            self.sqlite.save_stage_result(run_id, run.stages["monitoring"])
        except Exception as e:
            has_partial_failure = True
            failure_reason = f"Monitoring stage failed: {str(e)}"
            provider_errors.append(failure_reason)
            run.stages["monitoring"] = StageExecutionRecord(
                stage_name="monitoring",
                status=StageStatus.failed,
                duration_seconds=time.time() - stg1_start,
                error_message=failure_reason,
            )
            self.sqlite.save_stage_result(run_id, run.stages["monitoring"])
            return self._finalize_run_on_failure(
                run, snapshot, task_metrics, None, None, None,
                coordinator, provider_errors, failure_reason, start_time, models_used
            )

        self._persist_proposals(run, monitoring_draft, rules, capture, task_evidence,
                                proposal_codes)

        # ================= STAGE 2: Code Quality (Revisor) =================
        stg2_start = time.time()
        if multi_repository or not snapshot.code_diffs:
            # MultiRepo code is analyzed only through its repository-scoped containers.
            # Rule: No code => skip quality stage for the legacy snapshot path.
            run.stages["quality"] = StageExecutionRecord(
                stage_name="quality",
                status=StageStatus.skipped,
                duration_seconds=0.0,
                output_summary="Skipped: No code diffs present in snapshot.",
                completed_at=datetime.now(DEFAULT_TIMEZONE),
            )
            self.sqlite.save_stage_result(run_id, run.stages["quality"])
        else:
            try:
                quality_agent = CodeQualityAgent(
                    provider=self.revisor_provider,
                    model=getattr(self.revisor_provider, "model", "llama-3.3-70b-versatile"),
                    coordinator=coordinator,
                    timeout_seconds=getattr(self.config, "quality_timeout_seconds", 120.0),
                    max_output_tokens=getattr(self.config, "quality_max_output_tokens", None),
                    safety_margin_tokens=getattr(self.config, "quality_context_margin", 64),
                )
                candidate_findings = quality_agent.run(
                    project_id=proj_id,
                    draft=monitoring_draft,
                    snippets=snapshot.code_diffs,
                    rules=snapshot.policies,
                    evidence_ids=[s.commit_sha for s in snapshot.code_diffs],
                )
                run.stages["quality"] = StageExecutionRecord(
                    stage_name="quality",
                    status=StageStatus.completed,
                    duration_seconds=time.time() - stg2_start,
                    output_summary=f"Quality review identified {len(candidate_findings)} candidate findings.",
                    data_hash=compute_sha256([f.model_dump() for f in candidate_findings]),
                    completed_at=datetime.now(DEFAULT_TIMEZONE),
                )
                self.sqlite.save_stage_result(run_id, run.stages["quality"])
            except Exception as e:
                has_partial_failure = True
                failure_reason = f"Quality stage failed: {str(e)}"
                provider_errors.append(failure_reason)
                run.stages["quality"] = StageExecutionRecord(
                    stage_name="quality",
                    status=StageStatus.failed,
                    duration_seconds=time.time() - stg2_start,
                    error_message=failure_reason,
                )
                self.sqlite.save_stage_result(run_id, run.stages["quality"])
                return self._finalize_run_on_failure(
                    run, snapshot, task_metrics, monitoring_draft, None, None,
                    coordinator, provider_errors, failure_reason, start_time, models_used
                )

        # ================= STAGE 3: EscalationGate & Verifier =================
        stg3_start = time.time()
        rules_map = {r.id: r for r in snapshot.policies}

        if candidate_findings:
            escalated_findings, routine_findings, escalation_decisions = self.escalation_gate.filter_for_verification(
                findings=candidate_findings,
                rules_by_id=rules_map,
            )
            for dec in escalation_decisions:
                escalation_map[dec.finding_id] = dec.reasons
            self.sqlite.save_findings(run_id, candidate_findings, escalation_decisions)
        else:
            escalated_findings = []
            routine_findings = []
            escalation_decisions = []

        confirmed_findings: list[CandidateFinding] = []
        rejected_findings: list[CandidateFinding] = []
        insufficient_findings: list[CandidateFinding] = []
        pending_findings: list[CandidateFinding] = []

        if not escalated_findings:
            # Rule: No escalatable findings => skip verifier stage
            run.stages["verifier"] = StageExecutionRecord(
                stage_name="verifier",
                status=StageStatus.skipped,
                duration_seconds=0.0,
                output_summary="Skipped: No candidate findings met escalation criteria.",
                completed_at=datetime.now(DEFAULT_TIMEZONE),
            )
            self.sqlite.save_stage_result(run_id, run.stages["verifier"])
            # Routine findings that did not escalate remain pending or routine (never auto-confirmed)
            pending_findings.extend(routine_findings)
        else:
            # Escalated findings need independent verification
            # Check remote AI policy: if remote disabled or missing credential => needs_attention
            is_simulated = (
                getattr(self.verificador_provider, "is_simulated", False)
                if self.verificador_provider is not None
                else False
            ) or self.config.is_simulated

            if self.verificador_provider is None:
                verifier_reason = (
                    "Remote AI verifier disabled by project configuration (allow_remote_ai=False)"
                    if not self.config.allow_remote_ai
                    else "Remote AI verifier missing credentials (GEMINI_API_KEY not configured)"
                )
                provider_errors.append(verifier_reason)
                run.stages["verifier"] = StageExecutionRecord(
                    stage_name="verifier",
                    status=StageStatus.needs_attention,
                    duration_seconds=time.time() - stg3_start,
                    error_message=verifier_reason,
                    output_summary=f"Verifier unavailable: {verifier_reason}. Findings remain pending.",
                    completed_at=datetime.now(DEFAULT_TIMEZONE),
                )
                self.sqlite.save_stage_result(run_id, run.stages["verifier"])
                pending_findings.extend(escalated_findings)
                pending_findings.extend(routine_findings)
            elif not is_simulated and not self.config.allow_remote_ai:
                verifier_reason = "Remote AI verifier disabled by project configuration (allow_remote_ai=False)"
                provider_errors.append(verifier_reason)
                run.stages["verifier"] = StageExecutionRecord(
                    stage_name="verifier",
                    status=StageStatus.needs_attention,
                    duration_seconds=time.time() - stg3_start,
                    error_message=verifier_reason,
                    output_summary=f"Verifier unavailable: {verifier_reason}. Findings remain pending.",
                    completed_at=datetime.now(DEFAULT_TIMEZONE),
                )
                self.sqlite.save_stage_result(run_id, run.stages["verifier"])
                pending_findings.extend(escalated_findings)
                pending_findings.extend(routine_findings)
            elif not is_simulated and not getattr(self.verificador_provider, "api_key", None):
                verifier_reason = "Remote AI verifier missing credentials (GEMINI_API_KEY not configured)"
                provider_errors.append(verifier_reason)
                run.stages["verifier"] = StageExecutionRecord(
                    stage_name="verifier",
                    status=StageStatus.needs_attention,
                    duration_seconds=time.time() - stg3_start,
                    error_message=verifier_reason,
                    output_summary=f"Verifier unavailable: {verifier_reason}. Findings remain pending.",
                    completed_at=datetime.now(DEFAULT_TIMEZONE),
                )
                self.sqlite.save_stage_result(run_id, run.stages["verifier"])
                pending_findings.extend(escalated_findings)
                pending_findings.extend(routine_findings)
            else:
                try:
                    configured_verifier_model = getattr(
                        self.verificador_provider, "model", "gemini-2.5-flash"
                    )
                    verifier_agent = VerifierAgent(
                        provider=self.verificador_provider,
                        model=configured_verifier_model,
                        coordinator=coordinator,
                        timeout_seconds=getattr(self.config, "verifier_timeout_seconds", 120.0),
                    )
                    verifications = verifier_agent.run(
                        project_id=proj_id,
                        findings=escalated_findings,
                        snippets=snapshot.code_diffs,
                        rules=snapshot.policies,
                        evidence_ids=[s.commit_sha for s in snapshot.code_diffs],
                    )

                    self.sqlite.save_verifications(run_id, verifications)

                    # Partition findings by verdict
                    verif_by_id = {v.finding_id: v for v in verifications}
                    for f in escalated_findings:
                        v = verif_by_id.get(f.id)
                        if v and v.verdict == VerificationVerdict.confirmed:
                            confirmed_findings.append(f)
                        elif v and v.verdict == VerificationVerdict.rejected:
                            rejected_findings.append(f)
                        elif v and v.verdict == VerificationVerdict.insufficient:
                            insufficient_findings.append(f)
                        else:
                            pending_findings.append(f)
                    pending_findings.extend(routine_findings)

                    has_insufficient_or_human_review = any(
                        v.verdict == VerificationVerdict.insufficient for v in verifications
                    ) or bool(insufficient_findings)
                    verifier_status = StageStatus.needs_attention if has_insufficient_or_human_review else StageStatus.completed

                    run.stages["verifier"] = StageExecutionRecord(
                        stage_name="verifier",
                        status=verifier_status,
                        duration_seconds=time.time() - stg3_start,
                        output_summary=f"Verified {len(verifications)} findings ({len(insufficient_findings)} insufficient / needs human review).",
                        data_hash=compute_sha256([v.model_dump() for v in verifications]),
                        completed_at=datetime.now(DEFAULT_TIMEZONE),
                    )
                    self.sqlite.save_stage_result(run_id, run.stages["verifier"])

                except Exception as e:
                    # Rule: Gemini unavailable/disabled/invalid => needs_attention with pending verification,
                    # never invent confirmed verdict; do not mark mandatory failed stages complete.
                    verif_err_msg = f"Verifier unavailable or failed: {str(e)}"
                    provider_errors.append(verif_err_msg)
                    run.stages["verifier"] = StageExecutionRecord(
                        stage_name="verifier",
                        status=StageStatus.needs_attention,
                        duration_seconds=time.time() - stg3_start,
                        error_message=verif_err_msg,
                    )
                    self.sqlite.save_stage_result(run_id, run.stages["verifier"])
                    # All escalated and routine findings remain pending_findings!
                    pending_findings.extend(escalated_findings)
                    pending_findings.extend(routine_findings)

        repository_analysis = None
        if multi_repository:
            repository_analysis = analyze_repository_results(
                snapshot.repository_results,
                project_id=proj_id,
                draft=monitoring_draft,
                rules=snapshot.policies,
                quality_provider=self.revisor_provider,
                verifier_provider=self.verificador_provider,
                coordinator=coordinator,
                escalation_gate=self.escalation_gate,
                config=self.config,
            )
            candidate_findings = repository_analysis.candidate_findings
            confirmed_findings = repository_analysis.confirmed_findings
            rejected_findings = repository_analysis.rejected_findings
            insufficient_findings = repository_analysis.insufficient_findings
            pending_findings = repository_analysis.pending_findings
            verifications = repository_analysis.verifications
            escalation_decisions = repository_analysis.escalation_decisions
            escalation_map = {
                decision.finding_id: decision.reasons
                for decision in escalation_decisions
                if decision.reasons
            }
            for stage_name, stage_record in repository_analysis.stages.items():
                run.stages[stage_name] = stage_record
                self.sqlite.save_stage_result(run_id, stage_record)
            if candidate_findings:
                self.sqlite.save_findings(run_id, candidate_findings, escalation_decisions)
            if verifications:
                self.sqlite.save_verifications(run_id, verifications)

        # ================= STAGE 4: Policy Evaluation & Report (Aprobador) =================
        stg4_start = time.time()
        # Compute policy evaluations with confirmed findings and unresolved findings.
        unresolved_findings = [f for f in (insufficient_findings + pending_findings)]
        policy_eval_result = (
            repository_analysis.policy_evaluations
            if repository_analysis is not None and repository_analysis.policy_evaluations is not None
            else QualityEvaluator.evaluate(
                rules=snapshot.policies,
                ci_run=matching_ci,
                findings=confirmed_findings,
                unresolved_findings=unresolved_findings,
                source_is_complete=snapshot.is_complete,
                incomplete_reason=snapshot.incomplete_reason,
            )
        )

        # Assemble all sources evidence IDs (tasks + code diffs/commits + CI runs + audit evidence)
        all_sources_evidence_ids: list[str] = []
        for t in snapshot.tasks:
            if t.id not in all_sources_evidence_ids:
                all_sources_evidence_ids.append(t.id)
        for s in snapshot.code_diffs:
            if s.commit_sha not in all_sources_evidence_ids:
                all_sources_evidence_ids.append(s.commit_sha)
        for ci in snapshot.ci_runs:
            if ci.id not in all_sources_evidence_ids:
                all_sources_evidence_ids.append(ci.id)
        if eval_commit_sha and eval_commit_sha not in all_sources_evidence_ids:
            all_sources_evidence_ids.append(eval_commit_sha)
        if repository_analysis is not None:
            for evidence_id in repository_analysis.evidence_ids:
                if evidence_id not in all_sources_evidence_ids:
                    all_sources_evidence_ids.append(evidence_id)
        if self._last_collection_result and getattr(self._last_collection_result, "evidence", None):
            for ev in self._last_collection_result.evidence:
                ev_id = ev.provenance.id if hasattr(ev, "provenance") and hasattr(ev.provenance, "id") else getattr(ev, "id", None)
                if ev_id and ev_id not in all_sources_evidence_ids:
                    all_sources_evidence_ids.append(ev_id)

        try:
            report_agent = ReportAgent(
                provider=self.aprobador_provider,
                model=getattr(self.aprobador_provider, "model", "gemini-3.5-flash-lite"),
                coordinator=coordinator,
            )
            final_report = report_agent.run(
                project_id=proj_id,
                cutoff_datetime=snapshot.cutoff_datetime,
                metrics=task_metrics,
                draft=monitoring_draft,
                confirmed_findings=confirmed_findings,
                rejected_findings=rejected_findings,
                insufficient_findings=insufficient_findings,
                pending_findings=pending_findings,
                policy_evaluations=policy_eval_result,
                ci_result=matching_ci,
                evidence_ids=all_sources_evidence_ids,
            )

            # Insert authoritative metadata
            final_report.report_id = f"rep-{run_id}"
            final_report.run_id = run_id
            final_report.providers_used = models_used
            final_report.candidate_findings = candidate_findings
            final_report.all_evidence_ids = all_sources_evidence_ids
            if repository_analysis is not None:
                final_report.repository_results = repository_analysis.repository_results
                final_report.ci_result = None
                final_report.confirmed_findings = confirmed_findings
                final_report.rejected_findings = rejected_findings
                final_report.insufficient_findings = insufficient_findings
                final_report.pending_findings = pending_findings
                final_report.policy_evaluations = policy_eval_result
                final_report.summary = summarize_repository_results(
                    repository_analysis.repository_results,
                    policy_eval_result.recommended_action,
                )
                final_report.recommendations = [
                    "Revisar los resultados individuales y las incertidumbres de cada repositorio antes de decidir."
                ]

            run.stages["report"] = StageExecutionRecord(
                stage_name="report",
                status=StageStatus.completed,
                duration_seconds=time.time() - stg4_start,
                output_summary="Report assembled and verified with deterministic metrics.",
                data_hash=compute_sha256(final_report),
                completed_at=datetime.now(DEFAULT_TIMEZONE),
            )
            self.sqlite.save_stage_result(run_id, run.stages["report"])
        except Exception as e:
            if multi_repository and repository_analysis is not None:
                fallback_reason = (
                    "Report synthesis was unavailable; deterministic repository results were retained."
                )
                provider_errors.append(fallback_reason)
                final_report = FinalReport(
                    project_id=proj_id,
                    cutoff_datetime=snapshot.cutoff_datetime,
                    metrics=task_metrics,
                    ci_result=None,
                    confirmed_findings=confirmed_findings,
                    rejected_findings=rejected_findings,
                    insufficient_findings=insufficient_findings,
                    pending_findings=pending_findings,
                    policy_evaluations=policy_eval_result,
                    recommendation=policy_eval_result.recommended_action,
                    summary=(
                        "La síntesis del modelo no estuvo disponible. "
                        + summarize_repository_results(
                            repository_analysis.repository_results,
                            policy_eval_result.recommended_action,
                        )
                    ),
                    recommendations=[
                        "Revisar los resultados individuales y las incertidumbres de cada repositorio antes de decidir."
                    ],
                    human_decision=HumanDecisionState.pending,
                    report_id=f"rep-{run_id}",
                    run_id=run_id,
                    providers_used=models_used,
                    candidate_findings=candidate_findings,
                    all_evidence_ids=all_sources_evidence_ids,
                    repository_results=repository_analysis.repository_results,
                )
                run.stages["report"] = StageExecutionRecord(
                    stage_name="report",
                    status=StageStatus.needs_attention,
                    duration_seconds=time.time() - stg4_start,
                    error_message=fallback_reason,
                    output_summary=fallback_reason,
                    data_hash=compute_sha256(final_report),
                    completed_at=datetime.now(DEFAULT_TIMEZONE),
                )
                self.sqlite.save_stage_result(run_id, run.stages["report"])
            else:
                has_partial_failure = True
                failure_reason = f"Report synthesis stage failed: {str(e)}"
                provider_errors.append(failure_reason)
                run.stages["report"] = StageExecutionRecord(
                    stage_name="report",
                    status=StageStatus.failed,
                    duration_seconds=time.time() - stg4_start,
                    error_message=failure_reason,
                )
                self.sqlite.save_stage_result(run_id, run.stages["report"])
                return self._finalize_run_on_failure(
                    run, snapshot, task_metrics, monitoring_draft, candidate_findings, verifications,
                    coordinator, provider_errors, failure_reason, start_time, models_used
                )

        # ================= STAGE 5: Semantic Validation & Persistence =================
        total_duration = time.time() - start_time
        run.duration_seconds = total_duration
        run.updated_at = datetime.now(DEFAULT_TIMEZONE)
        run.report_id = final_report.report_id

        # Determine run status: if verifier had needs_attention, run is needs_attention; otherwise awaiting_human
        if any(s.status == StageStatus.needs_attention for s in run.stages.values()):
            run.status = RunStatus.needs_attention
        else:
            run.status = RunStatus.awaiting_human

        # Save all LLM attempts to SQLite for audit
        self.sqlite.save_llm_attempts(run_id, coordinator.recorded_attempts)

        # Generate Spanish Markdown report
        exec_metrics_payload = {
            "run_id": run_id,
            "status": run.status.value,
            "duration_seconds": total_duration,
            "models_used": models_used,
            "provider_errors": provider_errors,
            "escalations": escalation_map,
            "total_llm_calls": budget_tracker.total_calls_made,
        }
        if self._last_collection_result:
            exec_metrics_payload["source_collection"] = {
                "target_commit_sha": self._last_collection_result.target_commit_sha,
                "is_complete": self._last_collection_result.snapshot.is_complete,
                "records": [r.model_dump() for r in getattr(self._last_collection_result, "records", [])],
            }
        report_md = generate_report_markdown(
            report=final_report,
            execution_metrics=exec_metrics_payload,
            all_candidate_findings=candidate_findings,
            all_evidence_ids=all_sources_evidence_ids,
        )

        # Persist report to SQLite and files
        self.sqlite.save_report(
            report=final_report,
            report_id=final_report.report_id,
            report_md=report_md,
            run_id=run_id,
        )
        self.sqlite.save_run(run)

        self.artifacts.save_run_artifacts(
            run_id=run_id,
            snapshot=snapshot,
            metrics=task_metrics,
            monitoring=monitoring_draft,
            code_findings=candidate_findings,
            verifications=verifications,
            report=final_report,
            report_md=report_md,
            execution_metrics=exec_metrics_payload,
        )

        if not multi_repository and self.manager_notifications is not None and self.manager_email and (
            candidate_findings or task_metrics.overdue_card_ids or task_metrics.blocked_card_ids
            or (run.stages.get("proposals") and json.loads(run.stages["proposals"].output_summary).get("saved", 0))
        ):
            try:
                self.manager_notifications.send_project_email(
                    proj_id, recipient_email=self.manager_email,
                    idempotency_key=f"run:{run_id}",
                )
            except Exception:
                # A delivery failure must not rewrite or discard a completed analysis.
                logging.getLogger(__name__).warning("manager_notification_failed run_id=%s", run_id)
        return run

    def _persist_proposals(self, run, draft, rules, capture, evidence, limitations):
        """Catalogue recommendations only; pure fact checks do not prove chronology."""
        codes = list(limitations)
        saved, failures = 0, []
        technical_ready, technical_blocked = 0, 0
        choices = []
        if rules is None:
            codes.append("missing_rules")
        elif capture is not None and not codes:
            choices, choice_codes = monitoring_choices(run.project_id, capture.snapshot.tasks,
                [t.model_dump(mode="json") for t in capture.snapshot.tasks], rules, evidence)
            codes.extend(choice_codes)
        # Do not put arbitrary draft prose/exception text in the stage summary.
        safe_codes = {"unknown_choice", "conflicting_selection", "proposal_context_budget",
                      "field_not_visible", "missing_rules", "missing_evidence"}
        codes.extend(c for c in (draft.proposal_limitations or []) if c in safe_codes)
        selected = draft.requested_changes
        if type(selected) is not list:
            codes.append("invalid_choice")
            selected = []
        keys = [(str(getattr(c, "task_id", None)), str(getattr(c, "field", None))) for c in selected]
        if len(set(keys)) != len(keys):
            codes.append("conflicting_selection")
            selected = []
        for index, requested in enumerate(selected):
            errors = []
            canonical = None
            try:
                checked = MonitoringRequestedChange.model_validate(dict(requested.__dict__))
                canonical = next((c for c in choices if c == checked), None)
            except (ValueError, TypeError, AttributeError):
                pass
            if canonical is None:
                errors = ["invalid_choice"]
            else:
                supporting = sorted((e for e in evidence if e.provenance.id in canonical.evidence_ids),
                                    key=lambda e: e.provenance.id)
                body = canonical.model_dump(mode="python")
                body.pop("rule_id")
                body["observed_capture_id"] = capture.capture_id
                proposal_id = "proposal-" + compute_sha256({
                    "snapshot": run.snapshot_hash, "config": run.config_hash,
                    "choice": canonical.model_dump(mode="json"), "capture": capture.capture_id,
                    "evidence": [e.model_dump(mode="json") for e in supporting]})
                try:
                    proposal = ActionProposal(proposal_id=proposal_id, **body)
                    errors = validate_action_proposal(proposal, capture, supporting)
                    if not errors:
                        save = getattr(self.sqlite, "save_action_proposal", None)
                        if not callable(save):
                            raise NotImplementedError
                        save(proposal, supporting)
                        saved += 1
                        try:
                            list_captures = getattr(self.sqlite, "list_snapshot_captures", None)
                            project_captures = list_captures(run.project_id) if callable(list_captures) else [capture]
                            review = ProposalAutoReviewService(self.sqlite).review_and_record(
                                proposal, capture, supporting, project_captures=project_captures,
                            )
                            if review.technical_status == "ready" and review.dry_run.status == "SIMULATED":
                                technical_ready += 1
                            else:
                                technical_blocked += 1
                        except (NotImplementedError, ValueError, TypeError, AttributeError):
                            failures.append({"choice_index": index, "codes": ["technical_review_unavailable"]})
                except NotImplementedError:
                    errors = ["catalogue_unavailable"]
                except ValueError:
                    errors = ["catalogue_conflict"]
                except Exception:
                    errors = ["catalogue_error"]
            if errors:
                failures.append({"choice_index": index, "codes": errors})
        summary = json.dumps({"selected": len(keys), "saved": saved,
            "technical_ready": technical_ready, "technical_blocked": technical_blocked,
            "limitations": sorted(set(codes)), "failures": failures}, sort_keys=True)
        run.stages["proposals"] = StageExecutionRecord(stage_name="proposals",
            status=StageStatus.completed if saved else StageStatus.skipped,
            output_summary=summary, completed_at=run.created_at)
        self.sqlite.save_stage_result(run.run_id, run.stages["proposals"])

    def _collect_snapshot(
        self, project_id: str, now: datetime, source_fixture: Optional[str] = None
    ) -> WeeklySnapshot:
        # Early SourceCollectionPort branch BEFORE ANY legacy fixture lookup
        if isinstance(self.source_adapter, SourceCollectionPort) or (
            hasattr(self.source_adapter, "collect_snapshot") and callable(getattr(self.source_adapter, "collect_snapshot"))
        ):
            target_sha = getattr(self.config, "target_commit_sha", None) or getattr(self.config, "commit_sha", None)
            res = self.source_adapter.collect_snapshot(
                project_id=project_id,
                cutoff_datetime=now,
                commit_sha=target_sha,
            )
            self._last_collection_result = res
            self._last_target_commit_sha = res.target_commit_sha
            return res.snapshot

        source_id = source_fixture or project_id
        fixtures_dir = getattr(self.config, "fixtures_dir", "fixtures")
        source_file = os.path.join(fixtures_dir, f"{source_id}.json")
        if not os.path.exists(source_file) and not os.path.exists(os.path.join(fixtures_dir, source_id)):
            default_fix = getattr(self.config, "default_fixture", "fixture_1_standard")
            default_file = os.path.join(fixtures_dir, f"{default_fix}.json")
            if os.path.exists(default_file) or os.path.exists(os.path.join(fixtures_dir, default_fix)):
                source_id = default_fix

        tasks = self.source_adapter.get_tasks(source_id)
        diffs = self.source_adapter.get_diffs(source_id)
        ci_runs = self.source_adapter.get_ci_results(source_id)
        policies = self.source_adapter.get_policies(source_id)
        # Use fixture cutoff if available to maintain deterministic cutoffs
        cutoff = now
        if hasattr(self.source_adapter, "load_fixture"):
            try:
                fix_data = self.source_adapter.load_fixture(source_id)
                if "cutoff_datetime" in fix_data:
                    dt = datetime.fromisoformat(fix_data["cutoff_datetime"])
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=DEFAULT_TIMEZONE)
                    cutoff = dt
            except Exception:
                pass

        return WeeklySnapshot(
            id=f"snap-{project_id}",
            project_id=project_id,
            cutoff_datetime=cutoff,
            captured_at=cutoff,
            tasks=tasks,
            code_diffs=diffs,
            ci_runs=ci_runs,
            policies=policies,
            is_complete=True,
        )

    def _finalize_run_on_failure(
        self,
        run: ExecutionRun,
        snapshot: WeeklySnapshot,
        metrics: Optional[DeterministicTaskMetrics],
        monitoring: Optional[MonitoringDraft],
        findings: Optional[list[CandidateFinding]],
        verifications: Optional[list[FindingVerification]],
        coordinator: StageInvocationCoordinator,
        provider_errors: list[str],
        error_message: str,
        start_time: float,
        models_used: dict[str, str],
    ) -> ExecutionRun:
        total_duration = time.time() - start_time
        run.duration_seconds = total_duration
        run.status = RunStatus.failed
        run.error_message = error_message
        run.updated_at = datetime.now(DEFAULT_TIMEZONE)

        # Persist partial state to SQLite
        self.sqlite.save_run(run)
        self.sqlite.save_llm_attempts(run.run_id, coordinator.recorded_attempts)

        exec_metrics = {
            "run_id": run.run_id,
            "status": "failed",
            "duration_seconds": total_duration,
            "models_used": models_used,
            "provider_errors": provider_errors,
            "error_message": error_message,
        }
        if self._last_collection_result:
            exec_metrics["source_collection"] = {
                "target_commit_sha": self._last_collection_result.target_commit_sha,
                "is_complete": self._last_collection_result.snapshot.is_complete,
                "records": [r.model_dump() for r in getattr(self._last_collection_result, "records", [])],
            }
            if getattr(self._last_collection_result, "evidence", None):
                self.sqlite.save_evidence(run.run_id, self._last_collection_result.evidence)

        # Persist diagnostic partial artifacts only; do not write report.json/report.md as complete
        self.artifacts.save_partial_artifacts(
            run_id=run.run_id,
            snapshot=snapshot,
            metrics=metrics,
            monitoring=monitoring,
            code_findings=findings,
            verifications=verifications,
            execution_metrics=exec_metrics,
        )
        return run
