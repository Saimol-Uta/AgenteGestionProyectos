"""Isolated EscalationGate for selective finding verification."""

from typing import Optional
from pydantic import BaseModel, Field

from agente_gestion.domain.findings import CandidateFinding, FindingCategory
from agente_gestion.domain.policies import QualityRule


class EscalationPolicy(BaseModel):
    """Configuration governing when a candidate finding must escalate to verification."""
    severe_categories: set[str] = Field(
        default_factory=lambda: {
            FindingCategory.inyeccion_sql.value,
            "seguridad",
            "critical",
            "high",
        }
    )
    confidence_threshold: float = Field(
        default=0.7,
        description="Findings with model confidence strictly below this threshold escalate"
    )
    escalate_on_contradiction: bool = Field(
        default=True,
        description="Escalate findings that have contradictory signals"
    )
    escalate_on_insufficient_evidence: bool = Field(
        default=True,
        description="Escalate findings lacking attached evidence records"
    )
    escalate_mandatory_rule_findings: bool = Field(
        default=True,
        description="Escalate findings linked to mandatory rules"
    )
    auto_escalate_all: bool = Field(
        default=False,
        description="If False, routine low-risk findings are NOT automatically escalated"
    )


class EscalationDecision(BaseModel):
    """Structured decision explaining whether and why a finding requires verification."""
    finding_id: str
    should_escalate: bool
    reasons: list[str] = Field(default_factory=list)


class EscalationGate:
    """
    Isolated policy gate that selectively routes findings to independent verifier.
    Guarantees that routine findings are not escalated indiscriminately.
    """

    def __init__(self, policy: Optional[EscalationPolicy] = None):
        self.policy = policy or EscalationPolicy()

    def evaluate_finding(
        self,
        finding: CandidateFinding,
        rule: Optional[QualityRule] = None,
        has_contradictions: bool = False,
    ) -> EscalationDecision:
        reasons: list[str] = []

        severe_set = {
            (c.value if hasattr(c, "value") else str(c)).strip().casefold()
            for c in self.policy.severe_categories
        }

        # 1. Declared severity check (high, critical)
        sev_raw = finding.severity.value if hasattr(finding.severity, "value") else str(getattr(finding, "severity", "") or "")
        sev_norm = sev_raw.strip().casefold()
        if sev_norm in severe_set:
            reasons.append(f"Severe category detected: '{sev_raw.strip()}'")

        # 2. Severe category check (normalized case and spacing) for backward compatibility
        cat_raw = finding.category.value if hasattr(finding.category, "value") else str(getattr(finding, "category", "") or "")
        cat_norm = cat_raw.strip().casefold()
        if cat_norm in severe_set and not any("Severe" in r for r in reasons):
            reasons.append(f"Severe category detected: '{cat_raw.strip()}'")

        # 3. Low confidence check
        if finding.confidence is not None:
            if finding.confidence < self.policy.confidence_threshold:
                reasons.append(
                    f"Low confidence ({finding.confidence:.2f} < threshold {self.policy.confidence_threshold:.2f})"
                )

        # 3. Contradiction check
        if self.policy.escalate_on_contradiction and has_contradictions:
            reasons.append("Contradiction signals detected in finding or context")

        # 4. Insufficient evidence check
        if self.policy.escalate_on_insufficient_evidence:
            if not finding.evidence_ids or not finding.evidence.strip():
                reasons.append("Insufficient evidence records attached to finding")

        # 5. Mandatory rule check
        if self.policy.escalate_mandatory_rule_findings and rule and rule.mandatory:
            reasons.append(f"Finding associated with mandatory quality rule: '{rule.id}'")

        # 6. Global override check
        if self.policy.auto_escalate_all and not reasons:
            reasons.append("Global policy mandates verification for all findings")

        should_escalate = len(reasons) > 0
        return EscalationDecision(
            finding_id=finding.id,
            should_escalate=should_escalate,
            reasons=reasons,
        )

    def filter_for_verification(
        self,
        findings: list[CandidateFinding],
        rules_by_id: Optional[dict[str, QualityRule]] = None,
        contradictory_ids: Optional[set[str]] = None,
    ) -> tuple[list[CandidateFinding], list[CandidateFinding], list[EscalationDecision]]:
        """
        Partitions candidate findings into (escalated_findings, passed_through_findings, decisions).
        """
        rules = rules_by_id or {}
        contra = contradictory_ids or set()
        escalated: list[CandidateFinding] = []
        routine: list[CandidateFinding] = []
        decisions: list[EscalationDecision] = []

        for f in findings:
            r = rules.get(f.rule_id) if f.rule_id else None
            is_contra = f.id in contra
            dec = self.evaluate_finding(f, rule=r, has_contradictions=is_contra)
            decisions.append(dec)
            if dec.should_escalate:
                escalated.append(f)
            else:
                routine.append(f)

        return escalated, routine, decisions
