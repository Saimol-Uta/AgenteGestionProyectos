"""Semantic validation of agent outputs against deterministic domain facts."""

from typing import Optional
from pydantic import BaseModel

from agente_gestion.domain.tasks import DeterministicTaskMetrics
from agente_gestion.domain.code import CodeDiffSnippet, CIRunResult
from agente_gestion.domain.report import MonitoringDraft
from agente_gestion.domain.findings import (
    CandidateFinding,
    FindingVerification,
    VerificationVerdict,
)
from agente_gestion.domain.policies import PolicyEvaluationResult


class ValidationError(Exception):
    """Raised when an agent output fails semantic domain validation."""
    pass


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
    ) -> list[str]:
        """
        Validates draft against ground-truth facts:
        - All referenced task IDs must exist.
        - No duplicate referenced task IDs.
        - All referenced evidence IDs must exist in supplied context.
        - Claimed metric counts and percentages must match computed domain metrics (no hallucinations).
        """
        errors: list[str] = []

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
    ) -> list[str]:
        """
        Validates candidate code review findings against strict domain contract:
        - Non-empty unique finding IDs.
        - Mandatory non-empty rule_id existing in valid_rule_ids.
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
            if not r_id or not str(r_id).strip():
                errors.append(f"Finding '{fid}' has missing or empty rule_id; must cite an existing quality rule.")
            elif str(r_id).strip() not in valid_rule_ids:
                errors.append(
                    f"Finding '{fid}' references nonexistent rule_id '{r_id}'."
                )

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
