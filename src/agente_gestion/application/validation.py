"""Semantic validation of agent outputs against deterministic domain facts."""

import re
import unicodedata
from typing import Optional
from pydantic import BaseModel

from agente_gestion.domain.tasks import DeterministicTaskMetrics, TaskCard
from agente_gestion.domain.code import CodeDiffSnippet, CIRunResult
from agente_gestion.domain.report import MonitoringDraft
from agente_gestion.domain.findings import (
    CandidateFinding,
    FindingVerification,
    VerificationVerdict,
)
from agente_gestion.domain.policies import PolicyEvaluationResult, QualityRule


class ValidationError(Exception):
    """Raised when an agent output fails semantic domain validation."""
    pass


def _normalized(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower())
                   if not unicodedata.combining(c))


def _absence_claim(finding: CandidateFinding) -> bool:
    """Structured scope is authoritative; legacy prose gets a conservative guard.

    Lexical matching is deliberately not a complete natural-language interpreter.
    It covers common English/Spanish absence and boundary claims without blocking
    every local visible-code defect in a partial snippet.
    """
    if finding.claim_basis == "file_absence":
        return True
    text = _normalized(finding.claim)
    patterns = (
        r"no (?:existe|hay|contiene).*(?:implementacion|logica|actualizacion|codigo)",
        r"(?:funcion|metodo|implementacion).*(?:incomplet|no implementad|sin implementacion|truncad)",
        r"(?:se corta|termina|se interrumpe).*(?:abrupt|fragmento)",
        r"(?:function|method|implementation).*(?:not implemented|unimplemented|incomplete|missing|absent|truncat|ends abruptly)",
        r"(?:missing|absent|no|incomplete|truncated|unimplemented).*(?:implementation|function body|method body|update logic)",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _unavailable_coverage_inference(finding: CandidateFinding) -> bool:
    text = _normalized(finding.claim)
    unavailable = re.search(r"(?:coverage.*(?:unavailable|unknown|not reported|missing)|(?:no hay|sin|no existe).*cobertura|sin ci|no ci)", text)
    absence = re.search(r"(?:tests? (?:are )?(?:missing|absent)|(?:faltan|falta de|no hay|ausencia de).*pruebas|no tests)", text)
    return bool(unavailable and absence)


def _has_complete_file(finding: CandidateFinding, snippets: list[CodeDiffSnippet]) -> bool:
    return any(s.commit_sha == finding.commit_sha and s.file_path == finding.file_path
               and s.evidence_scope == "full_file" and s.is_complete and not s.truncated
               and finding.line in s.lines and finding.commit_sha in finding.evidence_ids for s in snippets)


def _task_grounding_errors(draft: MonitoringDraft, tasks: list[TaskCard]) -> list[str]:
    errors = []
    task_map = {task.id: task for task in tasks}
    mentioned = set()
    # The structured channel provides exact state equality independently of prose.
    for claim in draft.task_state_claims:
        mentioned.add(claim.task_id)
        task = task_map.get(claim.task_id)
        if task is None or claim.state != task.state:
            errors.append(f"Task state claim '{claim.task_id}' contradicts deterministic TaskCard.state.")
    narrative = "\n".join([draft.summary, *draft.risks, *draft.suggested_actions, draft.ci_status_note or ""])
    state_patterns = {
        "in_progress": r"\b(?:in[_ ]progress|en curso|en progreso|en proceso)\b",
        "completed": r"\b(?:completed|complete|finished|completad[oa]|finalizad[oa]|terminad[oa])\b",
        "pending": r"\b(?:pending|pendiente)\b",
        "unknown": r"\b(?:unknown|desconocid[oa])\b",
        "blocked": r"\b(?:blocked|bloquead[oa])\b",
    }
    active_task_ids: set[str] = set()
    for sentence in re.split(r"[.!?\n]+", narrative):
        text = _normalized(sentence)
        named_task_ids = set()
        for task in tasks:
            # Match identifiers/titles as complete tokens, not T1 inside T10.
            names = (task.id, task.title)
            if any(name and re.search(r"(?<!\w)" + re.escape(_normalized(name)) + r"(?!\w)", text) for name in names):
                named_task_ids.add(task.id)
        mentioned.update(named_task_ids)
        states = {state for state, pattern in state_patterns.items() if re.search(pattern, text)}
        if named_task_ids:
            active_task_ids = named_task_ids
        elif states and len(active_task_ids) > 1:
            errors.append("Insufficient task-status attribution after multiple task mentions; cite the exact task ID or request additional context.")
            continue
        # Keep discourse context rather than guessing a growing set of pronouns.
        # State-bearing follow-ups inherit a single task; unresolved attribution
        # fails closed. Non-state project/metric prose remains unrestricted.
        for task_id in active_task_ids if states else ():
            task = task_map[task_id]
            for state in states:
                if task.state.name != state:
                    errors.append(f"Narrative task '{task.id}' state '{state}' contradicts deterministic '{task.state.value}'.")
    for task_id in mentioned:
        if task_id not in draft.referenced_task_ids or task_id not in draft.evidence_ids:
            errors.append(f"Mentioned task '{task_id}' must occur in referenced_task_ids and evidence_ids.")
    return errors


class SemanticValidator:
    """Enforces contextual integrity, ground-truth consistency, and policy boundaries."""

    @staticmethod
    def assert_valid(errors: list[str]) -> None:
        """Raises ValidationError if any errors are present."""
        if errors:
            raise ValidationError(f"Semantic validation failed: {'; '.join(errors)}")

    @staticmethod
    def validate_monitoring_draft(
        draft: MonitoringDraft,
        available_task_ids: set[str],
        metrics: DeterministicTaskMetrics,
        ci_result: Optional[CIRunResult] = None,
        available_evidence_ids: Optional[set[str]] = None,
        tasks: Optional[list[TaskCard]] = None,
    ) -> list[str]:
        """
        Validates draft against ground-truth facts:
        - All referenced task IDs must exist.
        - No duplicate referenced task IDs.
        - All referenced evidence IDs must exist in supplied context.
        - Claimed metric counts and percentages must match computed domain metrics (no hallucinations).
        """
        errors: list[str] = []

        if tasks is not None:
            errors.extend(_task_grounding_errors(draft, tasks))

        # Check duplicate referenced task IDs
        seen_refs: set[str] = set()
        for tid in draft.referenced_task_ids:
            if tid in seen_refs:
                errors.append(f"Duplicate referenced task ID in draft: '{tid}'.")
            seen_refs.add(tid)
            if tid not in available_task_ids:
                errors.append(
                    f"Referenced task ID '{tid}' does not exist in available task context."
                )

        # Check referenced evidence IDs
        if available_evidence_ids is not None:
            seen_evs: set[str] = set()
            for eid in draft.evidence_ids:
                if eid in seen_evs:
                    errors.append(f"Duplicate referenced evidence ID in draft: '{eid}'.")
                seen_evs.add(eid)
                if eid not in available_evidence_ids:
                    errors.append(
                        f"Referenced evidence ID '{eid}' does not exist in supplied context."
                    )

        # Check claimed progress percentage
        if draft.claimed_progress_percentage is not None:
            if metrics.progress_percentage is None:
                errors.append(
                    f"Draft claimed progress percentage {draft.claimed_progress_percentage}% "
                    "but actual progress is None (zero included tasks)."
                )
            elif abs(draft.claimed_progress_percentage - metrics.progress_percentage) > 0.15:
                errors.append(
                    f"Draft claimed progress percentage {draft.claimed_progress_percentage}% "
                    f"does not match actual metric {metrics.progress_percentage}%."
                )

        # Check claimed completed cards
        if draft.claimed_completed_cards is not None:
            if draft.claimed_completed_cards != metrics.completed_cards:
                errors.append(
                    f"Draft claimed {draft.claimed_completed_cards} completed cards "
                    f"does not match actual metric {metrics.completed_cards}."
                )

        # Check claimed total cards
        if draft.claimed_total_cards is not None:
            if draft.claimed_total_cards != metrics.total_cards:
                errors.append(
                    f"Draft claimed {draft.claimed_total_cards} total cards "
                    f"does not match actual metric {metrics.total_cards}."
                )

        return errors

    @staticmethod
    def validate_candidate_findings(
        findings: list[CandidateFinding],
        available_snippets: list[CodeDiffSnippet],
        valid_rule_ids: set[str],
        available_evidence_ids: Optional[set[str]] = None,
        rules: Optional[list[QualityRule]] = None,
        metric_facts: Optional[dict[str, object]] = None,
        ci_result: Optional[CIRunResult] = None,
    ) -> list[str]:
        """
        Validates candidate code review findings against strict domain contract:
        - Non-empty unique finding IDs.
        - Existing rule_id or explicit null for an unassociated code finding.
        - Mandatory non-empty technical claim.
        - Mandatory severity enum: low, medium, high, critical.
        - Mandatory non-empty evidence_ids citing existing evidence IDs.
        - file_path, commit_sha, and line number must match an actual code snippet in the diff context.
        - Valid confidence score between 0.0 and 1.0.
        - requires_verification must be boolean.
        """
        errors: list[str] = []
        seen_ids: set[str] = set()

        # Build index of valid locations: (commit_sha, file_path) -> set of lines
        valid_locations: dict[tuple[str, str], set[int]] = {}
        valid_files: set[str] = set()
        for snip in available_snippets:
            valid_files.add(snip.file_path)
            key = (snip.commit_sha, snip.file_path)
            if key not in valid_locations:
                valid_locations[key] = set()
            valid_locations[key].update(snip.lines)

        for f in findings:
            fid = getattr(f, "id", "") or ""
            if not fid.strip():
                errors.append("Finding has empty id.")
            elif fid in seen_ids:
                errors.append(f"Duplicate finding ID: '{fid}'.")
            seen_ids.add(fid)

            # Check rule ID (must be non-empty and exist in valid_rule_ids)
            r_id = getattr(f, "rule_id", None)
            if r_id is not None and not str(r_id).strip():
                errors.append(f"Finding '{fid}' has missing or empty rule_id; must cite an existing quality rule.")
            elif r_id is not None and str(r_id).strip() not in valid_rule_ids:
                errors.append(
                    f"Finding '{fid}' references nonexistent rule_id '{r_id}'."
                )

            if _unavailable_coverage_inference(f):
                errors.append(f"Finding '{fid}' infers missing tests from unavailable coverage/CI, which is not evidence of absence.")
            if _absence_claim(f) and not _has_complete_file(f, available_snippets):
                errors.append(f"Finding '{fid}' asserts code absence/incompleteness from partial evidence; request additional context.")

            if rules is not None and r_id is not None:
                rule = next((rule for rule in rules if rule.id == r_id), None)
                if rule is not None and (f.claim_basis == "policy_metric" or (rule.source != "review" and rule.metric_key != "open_critical_findings")):
                    # Unbound facts are not provenance: only an exact, cited CI run
                    # can support CI-policy metric assertions at this boundary.
                    reported = getattr(ci_result, rule.metric_key, None) if ci_result else None
                    ci_matches = (ci_result is not None and ci_result.commit_sha == f.commit_sha
                                  and ci_result.id in f.evidence_ids and rule.source == "ci")
                    if (not ci_matches or f.claim_basis != "policy_metric" or f.metric_key != rule.metric_key
                            or reported is None or f.observed_value != reported):
                        errors.append(f"Finding '{fid}' has incompatible policy association '{r_id}'; a reported matching metric is required.")
            if f.claim_basis == "policy_metric" and (not rules or r_id is None or f.metric_key is None or f.observed_value is None):
                errors.append(f"Finding '{fid}' policy_metric requires rule_id, metric_key and observed_value.")

            # Check technical claim
            claim_text = getattr(f, "claim", "") or ""
            if not claim_text.strip():
                errors.append(f"Finding '{fid}' has missing or empty claim; must describe the defect assertion.")

            # Check severity enum
            sev_obj = getattr(f, "severity", None)
            sev_str = sev_obj.value if hasattr(sev_obj, "value") else str(sev_obj or "")
            if sev_str.strip().lower() not in {"low", "medium", "high", "critical"}:
                errors.append(f"Finding '{fid}' has invalid severity '{sev_str}'.")

            # Check evidence IDs (must be non-empty list of valid context IDs)
            e_ids = getattr(f, "evidence_ids", [])
            if not e_ids:
                errors.append(f"Finding '{fid}' has empty evidence_ids; must cite at least one evidence record.")
            else:
                for eid in e_ids:
                    if not eid or not str(eid).strip():
                        errors.append(f"Finding '{fid}' contains an empty evidence ID.")
                    elif available_evidence_ids is not None and str(eid).strip() not in available_evidence_ids:
                        errors.append(
                            f"Finding '{fid}' references evidence ID '{eid}' which is not in the supplied context."
                        )

            # Check confidence score
            conf = getattr(f, "confidence", None)
            if conf is None or not (0.0 <= float(conf) <= 1.0):
                errors.append(f"Finding '{fid}' has invalid confidence: {conf}.")

            # Check requires_verification
            req_ver = getattr(f, "requires_verification", None)
            if not isinstance(req_ver, bool):
                errors.append(f"Finding '{fid}' requires_verification must be a boolean.")

            # Check file, commit and line match diff snippet
            loc_key = (f.commit_sha, f.file_path)
            if f.file_path not in valid_files:
                errors.append(
                    f"Finding '{fid}' references file '{f.file_path}' which is not in the diff context."
                )
            elif loc_key not in valid_locations:
                errors.append(
                    f"Finding '{fid}' references commit '{f.commit_sha}' not matching snippet file '{f.file_path}'."
                )
            else:
                lines = valid_locations[loc_key]
                if f.line not in lines:
                    errors.append(
                        f"Finding '{fid}' line {f.line} is outside diff lines {sorted(lines)} in '{f.file_path}'."
                    )

        return errors

    @staticmethod
    def validate_finding_verifications(
        verifications: list[FindingVerification],
        candidate_findings: list[CandidateFinding],
        available_evidence_ids: Optional[set[str]] = None,
        available_snippets: Optional[list[CodeDiffSnippet]] = None,
        rules: Optional[list[QualityRule]] = None,
    ) -> list[str]:
        """
        Validates verifier outputs:
        - verification finding id exact: each verification must correspond to an existing candidate finding.
        - verification references subset of associated evidence: verification evidence_ids
          must be a subset of candidate finding's evidence_ids (and exist in available_evidence_ids).
        - candidate no automatic confirmation: candidate finding cannot be assumed confirmed without explicit verdict.
        """
        errors: list[str] = []
        candidate_map = {f.id: f for f in candidate_findings}
        seen_v_ids: set[str] = set()

        for v in verifications:
            if v.finding_id in seen_v_ids:
                errors.append(f"Duplicate verification for finding_id: '{v.finding_id}'.")
            seen_v_ids.add(v.finding_id)

            if v.finding_id not in candidate_map:
                errors.append(
                    f"Verification references nonexistent finding_id '{v.finding_id}'."
                )
                continue

            target_cand = candidate_map[v.finding_id]

            if (v.verdict == VerificationVerdict.confirmed and available_snippets is not None
                    and _absence_claim(target_cand)
                    and (not _has_complete_file(target_cand, available_snippets)
                         or target_cand.commit_sha not in v.evidence_ids)):
                errors.append(f"Verification '{v.finding_id}' cannot confirm absence from partial evidence; use insufficient.")

            if v.verdict == VerificationVerdict.confirmed and rules is not None:
                policy_errors = SemanticValidator.validate_candidate_findings(
                    [target_cand], available_snippets or [], {rule.id for rule in rules},
                    available_evidence_ids, rules=rules,
                )
                errors.extend(f"Cannot confirm '{v.finding_id}': {error}" for error in policy_errors)

            # Verification evidence_ids must be a subset of the candidate finding's associated evidence
            if target_cand.evidence_ids:
                cand_eids = set(target_cand.evidence_ids)
                for eid in v.evidence_ids:
                    if eid not in cand_eids:
                        errors.append(
                            f"Verification for '{v.finding_id}' references evidence ID '{eid}' "
                            f"which is not associated with the candidate finding."
                        )

            # Check against global available evidence if provided
            if available_evidence_ids is not None:
                for eid in v.evidence_ids:
                    if eid not in available_evidence_ids:
                        errors.append(
                            f"Verification for '{v.finding_id}' references nonexistent evidence ID '{eid}'."
                        )

        return errors

    @staticmethod
    def validate_approval_recommendation(
        claimed_recommendation: str,
        actual_policy_result: PolicyEvaluationResult,
    ) -> list[str]:
        """
        Enforces policy invariants for approvals:
        - Favorable recommendation cannot be emitted if any mandatory rule failed or is unknown.
        """
        errors: list[str] = []
        norm_rec = claimed_recommendation.strip().lower()

        is_favorable = norm_rec in ("recomendar_aprobacion", "favorable", "approved")

        if is_favorable:
            if actual_policy_result.mandatory_failed_ids:
                errors.append(
                    f"Cannot recommend favorable approval: mandatory rules failed: "
                    f"{actual_policy_result.mandatory_failed_ids}."
                )
            if actual_policy_result.mandatory_unknown_ids:
                errors.append(
                    f"Cannot recommend favorable approval: mandatory rules have unknown evidence: "
                    f"{actual_policy_result.mandatory_unknown_ids}."
                )

        return errors
