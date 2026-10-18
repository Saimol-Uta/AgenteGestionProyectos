"""Sequential repository-isolated code review, verification, and evaluation."""

from dataclasses import dataclass, field
import time
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import ValidationError as PydanticValidationError

from agente_gestion.agents.context import ContextBuilder
from agente_gestion.agents.quality import CodeQualityAgent
from agente_gestion.agents.verifier import VerifierAgent
from agente_gestion.application.escalation import EscalationGate, EscalationDecision
from agente_gestion.application.generation import (
    AuthenticationError,
    FormatValidationError,
    GlobalBudgetExceededError,
    PayloadTooLargeError,
    ProviderError,
    ProviderInternalError,
    RateLimitExceededError,
    StageInvocationCoordinator,
)
from agente_gestion.domain.ci_metrics import CiArtifactOutcome
from agente_gestion.domain.code import (
    CIRunResult,
    CiMetricsArtifactMetadata,
    CiMetricsArtifactValues,
    CodeDiffSnippet,
)
from agente_gestion.domain.findings import (
    CandidateFinding,
    FindingVerification,
    SuggestedCodeValidationStatus,
    VerificationVerdict,
)
from agente_gestion.domain.policies import (
    PolicyEvaluationResult,
    QualityRule,
    RuleEvaluation,
    RuleEvaluationStatus,
)
from agente_gestion.domain.repositories import (
    CommitResolutionStatus,
    RepositoryAnalysisState,
    RepositoryIdentity,
    RepositoryProvenance,
)
from agente_gestion.domain.report import MonitoringDraft, RepositoryReportResult
from agente_gestion.domain.run import StageExecutionRecord, StageStatus, compute_sha256
from agente_gestion.domain.snapshot import RepositorySnapshotResult
from agente_gestion.quality.evaluator import QualityEvaluator


@dataclass
class RepositoryAnalysisOutcome:
    """Combined project view plus the independently retained repository results."""

    repository_results: list[RepositoryReportResult] = field(default_factory=list)
    candidate_findings: list[CandidateFinding] = field(default_factory=list)
    confirmed_findings: list[CandidateFinding] = field(default_factory=list)
    rejected_findings: list[CandidateFinding] = field(default_factory=list)
    insufficient_findings: list[CandidateFinding] = field(default_factory=list)
    pending_findings: list[CandidateFinding] = field(default_factory=list)
    verifications: list[FindingVerification] = field(default_factory=list)
    escalation_decisions: list[EscalationDecision] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    stages: dict[str, StageExecutionRecord] = field(default_factory=dict)
    policy_evaluations: Optional[PolicyEvaluationResult] = None


@dataclass
class _RepositoryWork:
    source: RepositorySnapshotResult
    state: RepositoryAnalysisState
    identity: RepositoryIdentity
    target_sha: Optional[str]
    snippets: list[CodeDiffSnippet] = field(default_factory=list)
    ci_run: Optional[CIRunResult] = None
    ci_metrics: Optional[CiMetricsArtifactValues] = None
    evidence_ids: list[str] = field(default_factory=list)
    public_evidence_ids: list[str] = field(default_factory=list)
    source_problems: list[str] = field(default_factory=list)
    source_is_complete: bool = False
    code_is_complete: bool = False
    findings_complete: bool = False
    quality_status: StageStatus = StageStatus.pending
    quality_reason: Optional[str] = None
    verifier_status: StageStatus = StageStatus.pending
    verifier_reason: Optional[str] = None
    evaluation_status: StageStatus = StageStatus.pending
    evaluation_reason: Optional[str] = None
    candidates: list[CandidateFinding] = field(default_factory=list)
    confirmed: list[CandidateFinding] = field(default_factory=list)
    rejected: list[CandidateFinding] = field(default_factory=list)
    insufficient: list[CandidateFinding] = field(default_factory=list)
    pending: list[CandidateFinding] = field(default_factory=list)
    verifications: list[FindingVerification] = field(default_factory=list)
    decisions: list[EscalationDecision] = field(default_factory=list)
    policy_evaluations: Optional[PolicyEvaluationResult] = None
    stage_id: str = ""

    @property
    def prefix(self) -> str:
        if self.target_sha is None:
            return f"repo:{self.identity.id}:unresolved"
        return f"repo:{self.identity.id}:{self.target_sha}"


@dataclass(frozen=True)
class _AnalysisConfig:
    quality_timeout_seconds: float
    verifier_timeout_seconds: float
    quality_max_output_tokens: Optional[int]
    quality_context_margin: int
    allow_remote_ai: bool
    is_simulated: bool


def _append_once(values: list[str], value: Optional[str]) -> None:
    if value and value not in values:
        values.append(value)


def _safe_failure(stage: str, error: Exception) -> str:
    """Expose a bounded diagnostic category, never provider-controlled exception text."""
    if isinstance(error, GlobalBudgetExceededError):
        return "Global LLM call budget exhausted; remaining repository work was not invoked."
    if type(error).__name__ == "ContextBudgetExceededError":
        return f"{stage.capitalize()} context budget exhausted; no evidence was truncated."
    if isinstance(error, PayloadTooLargeError) or type(error).__name__ == "PayloadTooLargeError":
        return f"{stage.capitalize()} stage failed with PayloadTooLargeError: input tokens exceed provider limit (HTTP 413); results remain unapproved."
    if isinstance(error, RateLimitExceededError) or type(error).__name__ == "RateLimitExceededError":
        return f"{stage.capitalize()} stage failed with RateLimitExceededError: rate limit reached (HTTP 429); results remain unapproved."
    if isinstance(error, AuthenticationError) or type(error).__name__ == "AuthenticationError":
        return f"{stage.capitalize()} stage failed with AuthenticationError: provider credentials rejected; results remain unapproved."
    if isinstance(error, FormatValidationError) or type(error).__name__ == "FormatValidationError":
        return f"{stage.capitalize()} stage failed with FormatValidationError: model output failed schema validation; results remain unapproved."
    if isinstance(error, ProviderInternalError) or type(error).__name__ == "ProviderInternalError":
        return f"{stage.capitalize()} stage failed with ProviderInternalError: provider returned internal server error; results remain unapproved."
    return f"{stage.capitalize()} stage failed with {type(error).__name__}; results remain unapproved."


def _scoped_evidence_id(work: _RepositoryWork, kind: str, value: str) -> str:
    return f"{work.prefix}:{kind}:{value}"


def _matches_provenance(
    provenance: Optional[RepositoryProvenance], identity: RepositoryIdentity, target_sha: str,
) -> bool:
    if provenance is None:
        return True
    if provenance.repository_identity is not None and provenance.repository_identity != identity:
        return False
    if provenance.commit_sha is not None and provenance.commit_sha.lower() != target_sha:
        return False
    return provenance.certifies_target is not False


def _validated_metrics(
    ci_run: Optional[CIRunResult], identity: RepositoryIdentity, target_sha: Optional[str],
) -> Optional[CiMetricsArtifactValues]:
    """Return only v1 values bound to this exact repository, SHA, run, attempt and workflow."""
    if ci_run is None or target_sha is None or ci_run.repository_identity != identity:
        return None
    metadata = ci_run.metrics_artifact
    metrics = metadata.metrics if metadata is not None else None
    if not (
        metadata is not None
        and metadata.outcome == CiArtifactOutcome.validated
        and metadata.certifies_metrics is True
        and metadata.schema_valid is True
        and metadata.semantics_valid is True
        and metadata.identity_bound is True
        and metadata.workflow_status_matches is True
        and metrics is not None
        and ci_run.run_attempt is not None
    ):
        return None
    expected_repo = f"{identity.owner}/{identity.repo}"
    expected_artifact = f"ci-quality-{target_sha}-{ci_run.id}-{ci_run.run_attempt}"
    if (
        metrics.repository.casefold() != expected_repo.casefold()
        or metrics.commit_sha.lower() != target_sha
        or metrics.workflow_run_id != ci_run.id
        or (metrics.workflow_run_attempt is not None
            and metrics.workflow_run_attempt != str(ci_run.run_attempt))
        or metrics.status.value.casefold() != ci_run.status.strip().casefold()
        or metadata.artifact_name != expected_artifact
    ):
        return None
    return metrics


def _safe_ci_view(
    ci_run: Optional[CIRunResult], metrics: Optional[CiMetricsArtifactValues],
) -> Optional[CIRunResult]:
    if ci_run is None:
        return None

    metrics_artifact = ci_run.metrics_artifact
    if metrics_artifact is not None and metrics is None and metrics_artifact.certifies_metrics:
        artifact_metrics = metrics_artifact.metrics
        workflow_status_mismatch = (
            artifact_metrics is not None
            and artifact_metrics.status.value.casefold() != ci_run.status.strip().casefold()
        )
        metadata_values = metrics_artifact.model_dump(mode="python")
        metadata_values.update(certifies_metrics=False, metrics=None)
        if workflow_status_mismatch:
            metadata_values.update(
                workflow_status_matches=False,
                reason=(
                    "Artifact workflow status differs from the selected Actions run; "
                    "metric values are uncertified in this repository report."
                ),
            )
        else:
            metadata_values["reason"] = (
                "Artifact metrics did not pass repository-analysis eligibility checks; "
                "values are uncertified in this report."
            )
        metrics_artifact = CiMetricsArtifactMetadata.model_validate(metadata_values)

    ci_values = ci_run.model_dump(mode="python")
    ci_values.update(
        total_tests=metrics.total_tests if metrics is not None else None,
        failed_tests=metrics.failed_tests if metrics is not None else None,
        coverage_percentage=metrics.coverage_percentage if metrics is not None else None,
        metrics_artifact=metrics_artifact,
    )
    return CIRunResult.model_validate(ci_values)


def _prepare_work(
    source: RepositorySnapshotResult,
    index: int,
    duplicate_ids: set[str],
    duplicate_names: set[tuple[str, str]],
) -> _RepositoryWork:
    state = source.repository_state
    identity = state.identity
    work = _RepositoryWork(source=source, state=state, identity=identity,
                           target_sha=state.target_commit_sha, stage_id=identity.id)
    duplicate_identity = (
        identity.id in duplicate_ids
        or (identity.owner.casefold(), identity.repo.casefold()) in duplicate_names
    )
    if duplicate_identity:
        work.source_problems.append("Repository identity is duplicated in this analysis; scoped evidence is ambiguous.")
    try:
        work.state = RepositoryAnalysisState.model_validate(state.model_dump(mode="python"))
        work.identity = work.state.identity
        work.target_sha = work.state.target_commit_sha
    except (PydanticValidationError, ValueError, TypeError):
        work.source_problems.append("Repository analysis state is malformed; no repository evidence was used.")
        work.target_sha = None

    if (work.state.commit_resolution_status != CommitResolutionStatus.resolved
            or work.target_sha is None):
        work.source_problems.append("No fixed target SHA was resolved for this repository.")
        work.target_sha = None
    if duplicate_identity:
        work.target_sha = None
    if source.is_complete is not True:
        _append_once(work.source_problems, source.incomplete_reason or "Repository source collection is incomplete.")
    if work.state.access_status.value == "denied":
        _append_once(work.source_problems, "Repository access was denied.")
    if work.state.availability_status.value == "unavailable":
        _append_once(work.source_problems, "Repository source is unavailable.")

    if work.target_sha is not None:
        expected_name = f"{work.identity.owner}/{work.identity.repo}"
        for item in source.code_diffs:
            raw_sha = getattr(item, "commit_sha", None)
            raw_identity = getattr(item, "repository_identity", None)
            raw_provenance = getattr(item, "provenance", None)
            if (
                raw_identity != work.identity
                or not isinstance(raw_sha, str)
                or raw_sha.lower() != work.target_sha
                or not _matches_provenance(raw_provenance, work.identity, work.target_sha)
            ):
                _append_once(work.source_problems, "Code evidence identity or SHA did not match the fixed repository scope.")
                continue
            try:
                snippet = CodeDiffSnippet.model_validate(item.model_dump(mode="python"))
            except (PydanticValidationError, ValueError, TypeError, AttributeError):
                _append_once(work.source_problems, "Malformed code evidence was rejected.")
                continue
            if snippet.repository.casefold() != expected_name.casefold():
                _append_once(work.source_problems, "Code evidence identity or SHA did not match the fixed repository scope.")
                continue
            work.snippets.append(snippet)

        eligible_ci: list[CIRunResult] = []
        for item in source.ci_runs:
            try:
                ci = CIRunResult.model_validate(item.model_dump(mode="python"))
            except (PydanticValidationError, ValueError, TypeError, AttributeError):
                _append_once(work.source_problems, "Malformed CI evidence was rejected.")
                continue
            if ci.repository_identity != work.identity:
                _append_once(work.source_problems, "CI repository identity did not match the configured repository.")
                continue
            if ci.commit_sha.lower() != work.target_sha or not _matches_provenance(
                ci.provenance, work.identity, work.target_sha,
            ):
                _append_once(work.source_problems, "CI evidence did not match the fixed repository SHA.")
                continue
            eligible_ci.append(ci)

        artifact_candidates = [ci for ci in eligible_ci if ci.metrics_artifact is not None]
        if len(artifact_candidates) == 1:
            work.ci_run = artifact_candidates[0]
        elif len(artifact_candidates) > 1:
            _append_once(work.source_problems, "Multiple CI artifact candidates are ambiguous; none was selected.")
        elif len(eligible_ci) == 1:
            work.ci_run = eligible_ci[0]
        elif len(eligible_ci) > 1:
            _append_once(work.source_problems, "Multiple CI workflows are ambiguous; no primary workflow was selected.")

    if work.ci_run is None:
        _append_once(work.source_problems, "No uniquely selected CI run matches this repository and target SHA.")
    work.ci_metrics = _validated_metrics(work.ci_run, work.identity, work.target_sha)
    if work.ci_run is not None and work.ci_metrics is None:
        _append_once(work.source_problems, "CI workflow metadata is present without certified identity-matched metrics.")

    work.source_is_complete = (
        source.is_complete is True
        and work.target_sha is not None
        and not any("identity or SHA" in issue or "Malformed code" in issue for issue in work.source_problems)
        and identity.id not in duplicate_ids
        and (identity.owner.casefold(), identity.repo.casefold()) not in duplicate_names
    )
    work.code_is_complete = work.source_is_complete

    if work.target_sha is not None:
        work.public_evidence_ids.append(_scoped_evidence_id(work, "commit", work.target_sha))
        for offset, snippet in enumerate(work.snippets):
            identity_hash = compute_sha256({
                "file_path": snippet.file_path,
                "commit_sha": snippet.commit_sha.lower(),
                "source_sha256": snippet.source_sha256,
                "lines": snippet.lines,
            })[:16]
            work.public_evidence_ids.append(
                _scoped_evidence_id(work, "code", f"{offset}:{identity_hash}")
            )
        if work.ci_run is not None:
            work.public_evidence_ids.append(
                _scoped_evidence_id(work, "ci", f"{work.ci_run.id}:{work.ci_run.run_attempt or 'unknown'}")
            )
        work.evidence_ids = [work.target_sha, *work.public_evidence_ids]
    return work


def _namespace_finding(
    finding: CandidateFinding,
    work: _RepositoryWork,
    allowed_evidence: set[str],
) -> tuple[Optional[CandidateFinding], Optional[str]]:
    if work.target_sha is None or finding.commit_sha.lower() != work.target_sha:
        return None, "A model finding referenced a commit outside the repository's fixed target SHA."
    if finding.repository_identity is not None and finding.repository_identity != work.identity:
        return None, "A model finding asserted a foreign repository identity and was rejected."
    provenance = finding.provenance
    if provenance is not None and not _matches_provenance(provenance, work.identity, work.target_sha):
        return None, "A model finding carried mismatched repository or SHA provenance and was rejected."
    if any(evidence_id not in allowed_evidence for evidence_id in finding.evidence_ids):
        return None, "A model finding referenced evidence outside its repository context and was rejected."

    namespaced_ids = []
    for evidence_id in finding.evidence_ids:
        if evidence_id == work.target_sha:
            scoped_id = _scoped_evidence_id(work, "commit", evidence_id)
        elif evidence_id in work.public_evidence_ids:
            scoped_id = evidence_id
        else:
            scoped_id = _scoped_evidence_id(work, "evidence", evidence_id)
        namespaced_ids.append(scoped_id)
    # Keep the raw SHA only for existing complete-file/verifier contracts; the
    # globally exposed reference is repository- and target-scoped.
    scoped_evidence = list(dict.fromkeys([*namespaced_ids, work.target_sha]))
    # Bind provenance from backend-owned scope, never preserve model-asserted run metadata.
    bound_provenance = RepositoryProvenance(
        repository_identity=work.identity,
        commit_sha=work.target_sha,
        source="github_code_review",
        certifies_target=True,
    )
    data = finding.model_dump(mode="python")
    data.update(
        id=f"{work.identity.id}::{work.target_sha}::{finding.id}",
        commit_sha=work.target_sha,
        evidence_ids=scoped_evidence,
        repository_identity=work.identity,
        provenance=bound_provenance,
    )
    try:
        return CandidateFinding.model_validate(data), None
    except (PydanticValidationError, ValueError, TypeError):
        return None, "A model finding failed repository binding and was rejected."


def _aggregate_status(statuses: list[StageStatus]) -> StageStatus:
    if not statuses:
        return StageStatus.skipped
    if StageStatus.needs_attention in statuses:
        return StageStatus.needs_attention
    if StageStatus.failed in statuses:
        return StageStatus.failed
    if all(status == StageStatus.skipped for status in statuses):
        return StageStatus.skipped
    return StageStatus.completed


def _aggregate_policy_results(
    repository_results: list[RepositoryReportResult],
) -> PolicyEvaluationResult:
    """Combine only rule outcomes; never sum or average repository metrics."""
    by_rule: dict[str, list[tuple[str, RuleEvaluation]]] = {}
    for result in repository_results:
        repo_id = result.repository_state.identity.id
        if result.policy_evaluations is None:
            continue
        for evaluation in result.policy_evaluations.evaluations:
            by_rule.setdefault(evaluation.rule_id, []).append((repo_id, evaluation))

    evaluations: list[RuleEvaluation] = []
    for rule_id in sorted(by_rule):
        rows = by_rule[rule_id]
        observed_ids = {repo_id for repo_id, _ in rows}
        status_values = [item.status for _, item in rows]
        if any(item == RuleEvaluationStatus.fail for item in status_values):
            status = RuleEvaluationStatus.fail
        elif len(observed_ids) != len(repository_results) or any(
            item == RuleEvaluationStatus.unknown for item in status_values
        ):
            status = RuleEvaluationStatus.unknown
        else:
            status = RuleEvaluationStatus.pass_
        mandatory = any(item.mandatory for _, item in rows)
        detail = ", ".join(
            f"{repo_id}={item.status.value}" for repo_id, item in rows
        )
        if len(observed_ids) != len(repository_results):
            detail += ", missing=unknown"
        evaluations.append(RuleEvaluation(
            rule_id=rule_id,
            description=rows[0][1].description,
            status=status,
            observed_value=None,
            mandatory=mandatory,
            reason=(
                "Per-repository outcomes: " + detail
                + ". Individual values remain in repository_results; no cross-repository metric is calculated."
            ),
        ))

    passed = [item.rule_id for item in evaluations if item.status == RuleEvaluationStatus.pass_]
    failed = [item.rule_id for item in evaluations if item.status == RuleEvaluationStatus.fail]
    unknown = [item.rule_id for item in evaluations if item.status == RuleEvaluationStatus.unknown]
    mandatory_failed = [item.rule_id for item in evaluations
                        if item.mandatory and item.status == RuleEvaluationStatus.fail]
    mandatory_unknown = [item.rule_id for item in evaluations
                         if item.mandatory and item.status == RuleEvaluationStatus.unknown]
    if mandatory_failed or failed:
        recommendation = "solicitar_cambios"
    elif mandatory_unknown or unknown:
        recommendation = "evidencia_insuficiente"
    else:
        recommendation = "recomendar_aprobacion"
    return PolicyEvaluationResult(
        evaluations=evaluations,
        passed_rule_ids=passed,
        failed_rule_ids=failed,
        unknown_rule_ids=unknown,
        mandatory_failed_ids=mandatory_failed,
        mandatory_unknown_ids=mandatory_unknown,
        recommended_action=recommendation,
    )


def _scope_payload(work: _RepositoryWork) -> dict[str, Any]:
    ci_view = _safe_ci_view(work.ci_run, work.ci_metrics)
    return {
        "identity": work.identity.model_dump(mode="json"),
        "branch": work.state.branch,
        "target_commit_sha": work.target_sha,
        "commit_resolution_status": work.state.commit_resolution_status.value,
        "access_status": work.state.access_status.value,
        "availability_status": work.state.availability_status.value,
        "source_is_complete": work.source_is_complete,
        "source_problems": list(work.source_problems),
        "code_evidence_ids": list(work.public_evidence_ids),
        "ci_result": ci_view.model_dump(mode="json", exclude_none=True) if ci_view else None,
    }


def analyze_repository_results(
    repository_results: list[RepositorySnapshotResult],
    *,
    project_id: str,
    draft: MonitoringDraft,
    rules: list[QualityRule],
    quality_provider: Any,
    verifier_provider: Any,
    coordinator: StageInvocationCoordinator,
    escalation_gate: EscalationGate,
    config: Any,
) -> RepositoryAnalysisOutcome:
    """Analyze each pinned repository serially while sharing the run coordinator."""
    outcome = RepositoryAnalysisOutcome()
    if not repository_results:
        outcome.policy_evaluations = QualityEvaluator.evaluate(rules=rules)
        return outcome

    ids = [item.repository_state.identity.id for item in repository_results]
    names = [
        (item.repository_state.identity.owner.casefold(), item.repository_state.identity.repo.casefold())
        for item in repository_results
    ]
    duplicate_ids = {value for value in ids if ids.count(value) > 1}
    duplicate_names = {value for value in names if names.count(value) > 1}
    works = [
        _prepare_work(item, index, duplicate_ids, duplicate_names)
        for index, item in enumerate(repository_results)
    ]
    for index, work in enumerate(works):
        if work.identity.id in duplicate_ids:
            work.stage_id = f"{work.identity.id}#{index + 1}"

    settings = _AnalysisConfig(
        quality_timeout_seconds=float(getattr(config, "quality_timeout_seconds", 120.0)),
        verifier_timeout_seconds=float(getattr(config, "verifier_timeout_seconds", 120.0)),
        quality_max_output_tokens=getattr(config, "quality_max_output_tokens", None),
        quality_context_margin=int(getattr(config, "quality_context_margin", 64)),
        allow_remote_ai=bool(getattr(config, "allow_remote_ai", False)),
        is_simulated=bool(getattr(config, "is_simulated", False)),
    )
    quality_start = time.monotonic()
    quality_deadline = quality_start + settings.quality_timeout_seconds
    quality_budget = 0
    if quality_provider is not None:
        try:
            capabilities = quality_provider.get_capabilities()
            configured = settings.quality_max_output_tokens or 768
            quality_budget = min(configured, capabilities.max_output_tokens)
        except Exception:
            quality_budget = 0
    raw_global_budget = getattr(config, "global_quality_max_output_tokens", None)
    global_quality_output_budget = int(raw_global_budget) if isinstance(raw_global_budget, (int, float)) and not isinstance(raw_global_budget, bool) else None
    global_quality_output_remaining = global_quality_output_budget
    quality_context_builder = ContextBuilder()

    for work_index, work in enumerate(works):
        stage_start = time.monotonic()
        if work.target_sha is None:
            work.quality_status = StageStatus.needs_attention
            work.quality_reason = "No fixed repository SHA is available for quality analysis."
        elif not work.snippets:
            if work.source_is_complete:
                work.quality_status = StageStatus.skipped
                work.quality_reason = "No code diff was reported for the pinned commit."
                work.findings_complete = True
            else:
                work.quality_status = StageStatus.needs_attention
                work.quality_reason = "Code quality is unknown because repository source evidence is incomplete."
        elif quality_provider is None:
            work.quality_status = StageStatus.needs_attention
            work.quality_reason = "Quality model provider is unavailable; findings remain unknown."
        elif time.monotonic() >= quality_deadline:
            work.quality_status = StageStatus.needs_attention
            work.quality_reason = "Shared quality-stage time budget was exhausted before this repository."
        elif not coordinator.budget_tracker.can_call():
            work.quality_status = StageStatus.needs_attention
            work.quality_reason = "Global LLM call budget exhausted before this repository's quality stage."
        elif quality_budget <= 0 or (global_quality_output_remaining is not None and global_quality_output_remaining <= 0):
            work.quality_status = StageStatus.needs_attention
            work.quality_reason = "Shared quality output-token budget was exhausted before this repository."
        else:
            timeout = min(settings.quality_timeout_seconds, quality_deadline - time.monotonic())
            try:
                agent = CodeQualityAgent(
                    provider=quality_provider,
                    model=getattr(quality_provider, "model", "llama-3.3-70b-versatile"),
                    coordinator=coordinator,
                    timeout_seconds=timeout,
                    max_output_tokens=quality_budget,
                    safety_margin_tokens=settings.quality_context_margin,
                    context_builder=quality_context_builder,
                )
                raw_findings = agent.run(
                    project_id=project_id,
                    draft=draft,
                    snippets=work.snippets,
                    rules=rules,
                    evidence_ids=work.evidence_ids,
                    repository_scope=_scope_payload(work),
                    stage_name=f"quality:{work.stage_id}",
                )
                selection = getattr(agent, "last_selection", {})
                if selection.get("omitted_count", 0) > 0:
                    work.code_is_complete = False

                allowed = set(work.evidence_ids)
                for finding in raw_findings:
                    bound, issue = _namespace_finding(finding, work, allowed)
                    if issue:
                        _append_once(work.source_problems, issue)
                        work.code_is_complete = False
                    if bound is not None:
                        work.candidates.append(bound)
                work.quality_status = (
                    StageStatus.completed if work.source_is_complete and work.code_is_complete
                    else StageStatus.needs_attention
                )
                work.quality_reason = (
                    None if work.quality_status == StageStatus.completed
                    else (
                        f"Reviewed {selection.get('selected_count', len(work.snippets))} of {len(work.snippets)} repository-scoped code item(s); "
                        f"{selection.get('omitted_count', 0)} omitted due to context budget."
                        if selection.get("omitted_count", 0) > 0
                        else "Repository source or model output was incomplete; retained only identity-matched findings."
                    )
                )
                work.findings_complete = work.quality_status == StageStatus.completed
            except Exception as exc:
                work.quality_status = StageStatus.needs_attention
                work.quality_reason = _safe_failure("quality", exc)
                work.findings_complete = False
            finally:
                if global_quality_output_remaining is not None:
                    global_quality_output_remaining = max(0, global_quality_output_remaining - quality_budget)

        stage_name = f"quality:{work.stage_id}"
        outcome.stages[stage_name] = StageExecutionRecord(
            stage_name=stage_name,
            status=work.quality_status,
            duration_seconds=max(0.0, time.monotonic() - stage_start),
            output_summary=(
                work.quality_reason
                or f"Reviewed {len(work.snippets)} repository-scoped code item(s); retained {len(work.candidates)} candidate finding(s)."
            ),
            data_hash=compute_sha256([item.model_dump(mode="json") for item in work.candidates]),
            completed_at=datetime.now(timezone.utc),
        )

    verification_start = time.monotonic()
    verification_deadline = verification_start + settings.verifier_timeout_seconds
    verifier_budget = 0
    if verifier_provider is not None:
        try:
            verifier_budget = min(1024, verifier_provider.get_capabilities().max_output_tokens)
        except Exception:
            verifier_budget = 0
    verifier_remaining = verifier_budget
    verifier_work = []
    for work in works:
        rules_by_id = {rule.id: rule for rule in rules}
        escalated, routine, decisions = escalation_gate.filter_for_verification(
            findings=work.candidates,
            rules_by_id=rules_by_id,
        )
        work.decisions = decisions
        for routine_item in routine:
            work.pending.append(routine_item.model_copy(update={
                "verification_status": "pending",
                "verification_reason": "No escalado para verificación independiente (severidad baja o sin regla mandatoria).",
                "suggested_code_validation_status": SuggestedCodeValidationStatus.not_validated,
            }))
        if escalated:
            verifier_work.append((work, escalated))

    verifier_work_count = len(verifier_work)
    verifier_context_builder = ContextBuilder()
    for work, escalated in verifier_work:
        stage_start = time.monotonic()
        status = StageStatus.needs_attention
        reason: Optional[str] = None
        if verifier_provider is None:
            reason = "Verifier model provider is unavailable; candidate findings remain pending."
        else:
            is_simulated = bool(getattr(verifier_provider, "is_simulated", False) or settings.is_simulated)
            if not is_simulated and not settings.allow_remote_ai:
                reason = "Remote AI verifier disabled by project configuration; findings remain pending."
            elif not is_simulated and not getattr(verifier_provider, "api_key", None):
                reason = "Remote AI verifier credentials are unavailable; findings remain pending."
            elif time.monotonic() >= verification_deadline:
                reason = "Shared verifier-stage time budget was exhausted before this repository."
            elif not coordinator.budget_tracker.can_call():
                reason = "Global LLM call budget exhausted before this repository's verifier stage."
            else:
                allocation = verifier_remaining // max(1, verifier_work_count)
                if allocation <= 0:
                    reason = "Shared verifier output-token budget was exhausted before this repository."
                else:
                    timeout = min(settings.verifier_timeout_seconds, verification_deadline - time.monotonic())
                    try:
                        agent = VerifierAgent(
                            provider=verifier_provider,
                            model=getattr(verifier_provider, "model", "gemini-2.5-flash"),
                            coordinator=coordinator,
                            timeout_seconds=timeout,
                            context_builder=verifier_context_builder,
                        )
                        work_verifications = agent.run(
                            project_id=project_id,
                            findings=escalated,
                            snippets=work.snippets,
                            rules=rules,
                            evidence_ids=work.evidence_ids,
                            repository_scope=_scope_payload(work),
                            stage_name=f"verifier:{work.stage_id}",
                            max_output_tokens=allocation,
                        )
                        candidate_map = {finding.id: finding for finding in escalated}
                        valid_verifications: dict[str, FindingVerification] = {}
                        for verification in work_verifications:
                            finding = candidate_map.get(verification.finding_id)
                            if finding is None or not set(verification.evidence_ids) <= set(finding.evidence_ids):
                                _append_once(work.source_problems, "Verifier output referenced another repository or unrelated evidence.")
                                continue
                            namespaced_verification = verification.model_copy(update={
                                "evidence_ids": [
                                    _scoped_evidence_id(work, "commit", item)
                                    if item == work.target_sha
                                    else item if item in work.public_evidence_ids
                                    else _scoped_evidence_id(work, "evidence", item)
                                    for item in verification.evidence_ids
                                ],
                            })
                            valid_verifications[verification.finding_id] = namespaced_verification
                        work.verifications = list(valid_verifications.values())
                        for finding in escalated:
                            verification = valid_verifications.get(finding.id)
                            if verification is None:
                                work.pending.append(finding.model_copy(update={
                                    "verification_status": "pending",
                                    "verification_reason": "Verificación independiente pendiente.",
                                    "suggested_code_validation_status": SuggestedCodeValidationStatus.not_validated,
                                }))
                            elif verification.verdict == VerificationVerdict.confirmed:
                                work.confirmed.append(finding.model_copy(update={
                                    "verification_status": VerificationVerdict.confirmed.value,
                                    "verification_reason": verification.reason,
                                    "suggested_code_validation_status": SuggestedCodeValidationStatus.not_validated,
                                }))
                            elif verification.verdict == VerificationVerdict.rejected:
                                work.rejected.append(finding.model_copy(update={
                                    "verification_status": VerificationVerdict.rejected.value,
                                    "verification_reason": verification.reason,
                                    "suggested_code_validation_status": SuggestedCodeValidationStatus.not_validated,
                                }))
                            elif verification.verdict == VerificationVerdict.insufficient:
                                work.insufficient.append(finding.model_copy(update={
                                    "verification_status": VerificationVerdict.insufficient.value,
                                    "verification_reason": verification.reason,
                                    "suggested_code_validation_status": SuggestedCodeValidationStatus.not_validated,
                                }))
                            else:
                                work.pending.append(finding.model_copy(update={
                                    "verification_status": "pending",
                                    "verification_reason": verification.reason,
                                    "suggested_code_validation_status": SuggestedCodeValidationStatus.not_validated,
                                }))
                        if any(item.verdict == VerificationVerdict.insufficient for item in work.verifications) or any(
                            finding.id not in valid_verifications for finding in escalated
                        ) or work.source_problems:
                            status = StageStatus.needs_attention
                            reason = "Some findings remain insufficient or unverified within this repository."
                        else:
                            status = StageStatus.completed
                        verifier_remaining = max(0, verifier_remaining - allocation)
                    except Exception as exc:
                        reason = _safe_failure("verifier", exc)
                        for f in escalated:
                            work.pending.append(f.model_copy(update={
                                "verification_status": "pending",
                                "verification_reason": reason,
                                "suggested_code_validation_status": SuggestedCodeValidationStatus.not_validated,
                            }))
                        verifier_remaining = max(0, verifier_remaining - allocation)
        resolved_ids = {
            finding.id
            for group in (work.confirmed, work.rejected, work.insufficient, work.pending)
            for finding in group
        }
        for finding in escalated:
            if finding.id not in resolved_ids:
                work.pending.append(finding.model_copy(update={
                    "verification_status": "pending",
                    "verification_reason": "No resuelto en la etapa de verificación.",
                    "suggested_code_validation_status": SuggestedCodeValidationStatus.not_validated,
                }))
        candidate_status_map = {
            f.id: (f.verification_status, f.verification_reason)
            for group in (work.confirmed, work.rejected, work.insufficient, work.pending)
            for f in group
        }
        work.candidates = [
            f.model_copy(update={
                "verification_status": candidate_status_map.get(f.id, ("pending", None))[0],
                "verification_reason": candidate_status_map.get(f.id, ("pending", None))[1],
                "suggested_code_validation_status": SuggestedCodeValidationStatus.not_validated,
            })
            for f in work.candidates
        ]
        work.verifier_status = status
        work.verifier_reason = reason
        verifier_work_count = max(0, verifier_work_count - 1)
        stage_name = f"verifier:{work.stage_id}"
        outcome.stages[stage_name] = StageExecutionRecord(
            stage_name=stage_name,
            status=status,
            duration_seconds=max(0.0, time.monotonic() - stage_start),
            output_summary=(reason or f"Verified {len(work.verifications)} repository-scoped finding(s)."),
            data_hash=compute_sha256([item.model_dump(mode="json") for item in work.verifications]),
            completed_at=datetime.now(timezone.utc),
        )

    for work in works:
        if work.verifier_status == StageStatus.pending:
            work.verifier_status = StageStatus.skipped
            stage_name = f"verifier:{work.stage_id}"
            outcome.stages[stage_name] = StageExecutionRecord(
                stage_name=stage_name,
                status=StageStatus.skipped,
                duration_seconds=0.0,
                output_summary="No candidate findings required independent verification.",
                completed_at=datetime.now(timezone.utc),
            )
        if work.candidates and not any(work.decisions):
            # Defensive fallback: every candidate remains pending if routing failed.
            work.pending.extend(item for item in work.candidates if item not in work.pending)

        candidate_status_map = {
            f.id: (f.verification_status, f.verification_reason)
            for group in (work.confirmed, work.rejected, work.insufficient, work.pending)
            for f in group
        }
        work.candidates = [
            f.model_copy(update={
                "verification_status": candidate_status_map.get(f.id, ("pending", None))[0],
                "verification_reason": candidate_status_map.get(f.id, ("pending", None))[1],
                "suggested_code_validation_status": SuggestedCodeValidationStatus.not_validated,
            })
            for f in work.candidates
        ]

        ci_view = _safe_ci_view(work.ci_run, work.ci_metrics)
        policy_result = QualityEvaluator.evaluate(
            rules=rules,
            ci_run=ci_view,
            findings=work.confirmed,
            unresolved_findings=work.insufficient + work.pending,
            source_is_complete=work.source_is_complete,
            incomplete_reason=("; ".join(work.source_problems) or work.source.incomplete_reason),
            findings_complete=work.findings_complete,
            metric_evidence_complete=work.ci_metrics is not None,
            require_ci_status=True,
        )
        work.policy_evaluations = policy_result
        work.evaluation_status = (
            StageStatus.needs_attention
            if policy_result.unknown_rule_ids else StageStatus.completed
        )
        if work.evaluation_status == StageStatus.needs_attention:
            work.evaluation_reason = "Repository policy evaluation retains unknown evidence; no favorable inference was made."
        stage_name = f"evaluation:{work.stage_id}"
        outcome.stages[stage_name] = StageExecutionRecord(
            stage_name=stage_name,
            status=work.evaluation_status,
            duration_seconds=0.0,
            output_summary=(work.evaluation_reason or policy_result.recommended_action),
            data_hash=compute_sha256(policy_result.model_dump(mode="json")),
            completed_at=datetime.now(timezone.utc),
        )
        outcome.repository_results.append(RepositoryReportResult(
            repository_state=work.state,
            ci_result=ci_view,
            policy_evaluations=policy_result,
            confirmed_findings=work.confirmed,
            rejected_findings=work.rejected,
            insufficient_findings=work.insufficient,
            pending_findings=work.pending,
            verifications=work.verifications,
            coverage_percentage=(work.ci_metrics.coverage_percentage if work.ci_metrics is not None else None),
            coverage_scope=(work.ci_metrics.coverage_basis if work.ci_metrics is not None else None),
            source_is_complete=work.source_is_complete,
            source_problems=list(work.source_problems),
            quality_status=work.quality_status,
            quality_reason=work.quality_reason,
            verification_status=work.verifier_status,
            verification_reason=work.verifier_reason,
            evaluation_status=work.evaluation_status,
            evaluation_reason=work.evaluation_reason,
        ))
        outcome.candidate_findings.extend(work.candidates)
        outcome.confirmed_findings.extend(work.confirmed)
        outcome.rejected_findings.extend(work.rejected)
        outcome.insufficient_findings.extend(work.insufficient)
        outcome.pending_findings.extend(work.pending)
        outcome.verifications.extend(work.verifications)
        outcome.escalation_decisions.extend(work.decisions)
        outcome.evidence_ids.extend(work.public_evidence_ids)

    outcome.policy_evaluations = _aggregate_policy_results(outcome.repository_results)
    for stage, status_values in (
        ("quality", [work.quality_status for work in works]),
        ("verifier", [work.verifier_status for work in works]),
    ):
        status = _aggregate_status(status_values)
        stage_records = [record for name, record in outcome.stages.items() if name.startswith(stage + ":")]
        stage_name = stage
        duration = sum(record.duration_seconds for record in stage_records)
        summaries = [
            f"{work.identity.id}:{(work.quality_status if stage == 'quality' else work.verifier_status).value}"
            for work in works
        ]
        outcome.stages[stage_name] = StageExecutionRecord(
            stage_name=stage_name,
            status=status,
            duration_seconds=duration,
            output_summary="Repository stage outcomes: " + ", ".join(summaries),
            data_hash=compute_sha256(summaries),
            completed_at=datetime.now(timezone.utc),
        )
    return outcome
