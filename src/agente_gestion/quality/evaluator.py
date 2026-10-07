"""Deterministic quality evaluation engine over collected evidence facts."""

from typing import Any, Optional

from agente_gestion.domain.policies import (
    QualityRule,
    PolicyEvaluationResult,
    evaluate_policies,
)
from agente_gestion.domain.code import CIRunResult
from agente_gestion.domain.findings import CandidateFinding


class QualityEvaluator:
    """Computes quality facts from CI and review findings, then evaluates policies."""

    @staticmethod
    def _is_severe_category(category: Any) -> bool:
        if category is None:
            return False
        cat_str = category.value if hasattr(category, "value") else str(category)
        normalized = cat_str.strip().casefold()
        return normalized in {"inyeccion_sql", "seguridad", "critical", "high"}

    @classmethod
    def _is_severe_finding(cls, finding: Any) -> bool:
        if finding is None:
            return False
        sev = getattr(finding, "severity", None)
        if sev is not None:
            sev_str = sev.value if hasattr(sev, "value") else str(sev)
            if sev_str.strip().casefold() in {"critical", "high"}:
                return True
        return cls._is_severe_category(getattr(finding, "category", None))

    @classmethod
    def extract_facts(
        cls,
        ci_run: Optional[CIRunResult] = None,
        findings: Optional[list[CandidateFinding]] = None,
        extra_facts: Optional[dict[str, Any]] = None,
        unresolved_findings: Optional[list[CandidateFinding]] = None,
    ) -> dict[str, Any]:
        """Assembles standard facts dictionary from verified CI and findings."""
        facts: dict[str, Any] = {}

        if ci_run is not None:
            facts["ci_status"] = ci_run.status
            facts["total_tests"] = ci_run.total_tests
            facts["failed_tests"] = ci_run.failed_tests
            facts["coverage_percentage"] = ci_run.coverage_percentage
        else:
            facts["ci_status"] = None
            facts["total_tests"] = None
            facts["failed_tests"] = None
            facts["coverage_percentage"] = None

        unresolved_severe = [
            f for f in (unresolved_findings or [])
            if cls._is_severe_finding(f)
        ]

        if findings is not None:
            facts["total_findings"] = len(findings)
            # Count critical findings
            critical_count = sum(
                1 for f in findings if cls._is_severe_finding(f)
            )
            if unresolved_severe and critical_count == 0:
                facts["open_critical_findings"] = None
            else:
                facts["open_critical_findings"] = critical_count
        else:
            if unresolved_severe:
                facts["total_findings"] = None
                facts["open_critical_findings"] = None
            else:
                facts["total_findings"] = 0
                facts["open_critical_findings"] = 0

        if extra_facts:
            facts.update(extra_facts)

        return facts

    @classmethod
    def evaluate(
        cls,
        rules: list[QualityRule],
        ci_run: Optional[CIRunResult] = None,
        findings: Optional[list[CandidateFinding]] = None,
        extra_facts: Optional[dict[str, Any]] = None,
        unresolved_findings: Optional[list[CandidateFinding]] = None,
        source_is_complete: bool = True,
        incomplete_reason: Optional[str] = None,
    ) -> PolicyEvaluationResult:
        """Extracts facts and runs deterministic policy evaluation."""
        unresolved_severe = [
            f for f in (unresolved_findings or [])
            if cls._is_severe_finding(f)
        ]

        facts = cls.extract_facts(
            ci_run=ci_run,
            findings=findings,
            extra_facts=extra_facts,
            unresolved_findings=unresolved_severe,
        )
        res = evaluate_policies(rules, facts)

        if unresolved_severe:
            has_q_crit = any(r.metric_key == "open_critical_findings" for r in rules)
            if not has_q_crit:
                from agente_gestion.domain.policies import RuleEvaluation, RuleEvaluationStatus
                unknown_eval = RuleEvaluation(
                    rule_id="Q_UNRESOLVED_CRITICAL",
                    description="Verificacion de hallazgos criticos/seguridad",
                    status=RuleEvaluationStatus.unknown,
                    observed_value=None,
                    mandatory=True,
                    reason=f"{len(unresolved_severe)} hallazgo(s) severo(s) con verificacion pendiente o insuficiente.",
                )
                res.evaluations.append(unknown_eval)
                res.unknown_rule_ids.append("Q_UNRESOLVED_CRITICAL")
                res.mandatory_unknown_ids.append("Q_UNRESOLVED_CRITICAL")

            if not res.mandatory_failed_ids and not res.failed_rule_ids:
                res.recommended_action = "evidencia_insuficiente"

        if not source_is_complete:
            from agente_gestion.domain.policies import RuleEvaluation, RuleEvaluationStatus
            source_eval = RuleEvaluation(
                rule_id="Q_SOURCE_AVAILABILITY_INCOMPLETE",
                description="Disponibilidad y completitud de fuentes de datos",
                status=RuleEvaluationStatus.unknown,
                observed_value=None,
                mandatory=True,
                reason=incomplete_reason or "Una o mas fuentes de datos estan incompletas o no disponibles.",
            )
            res.evaluations.append(source_eval)
            if "Q_SOURCE_AVAILABILITY_INCOMPLETE" not in res.unknown_rule_ids:
                res.unknown_rule_ids.append("Q_SOURCE_AVAILABILITY_INCOMPLETE")
            if "Q_SOURCE_AVAILABILITY_INCOMPLETE" not in res.mandatory_unknown_ids:
                res.mandatory_unknown_ids.append("Q_SOURCE_AVAILABILITY_INCOMPLETE")

            if not res.mandatory_failed_ids and not res.failed_rule_ids:
                res.recommended_action = "evidencia_insuficiente"

        return res
