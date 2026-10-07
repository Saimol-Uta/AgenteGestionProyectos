"""Quality rules and deterministic policy evaluation."""

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class RuleEvaluationStatus(str, Enum):
    pass_ = "pass"
    fail = "fail"
    unknown = "unknown"


class QualityRule(BaseModel):
    """Configurable quality policy rule."""
    id: str = Field(description="Unique rule identifier e.g. Q1")
    description: str = Field(description="Human-readable rule requirement")
    metric_key: str = Field(description="Fact key in evidence facts map")
    operator: str = Field(
        default="==",
        description="Comparison operator: ==, <=, >=, <, >, !=, is_true, is_false"
    )
    threshold: Any = Field(default=None, description="Expected value or boundary")
    source: str = Field(default="ci", description="Evidence source e.g. ci, static_analysis, review, manual")
    mandatory: bool = Field(default=True, description="Whether passing this rule is mandatory for approval")


class RuleEvaluation(BaseModel):
    """Outcome of evaluating an individual quality rule."""
    rule_id: str
    description: str
    status: RuleEvaluationStatus
    observed_value: Any = None
    mandatory: bool = True
    reason: Optional[str] = None


class PolicyEvaluationResult(BaseModel):
    """Comprehensive outcome of policy evaluation across all configured rules."""
    evaluations: list[RuleEvaluation]
    passed_rule_ids: list[str]
    failed_rule_ids: list[str]
    unknown_rule_ids: list[str]
    mandatory_failed_ids: list[str]
    mandatory_unknown_ids: list[str]
    recommended_action: str = Field(
        description="Deterministic recommendation: recomendar_aprobacion, solicitar_cambios, or evidencia_insuficiente"
    )


def evaluate_single_rule(rule: QualityRule, facts: dict[str, Any]) -> RuleEvaluation:
    """Evaluate a single rule against facts map returning pass, fail, or unknown."""
    if rule.metric_key not in facts:
        return RuleEvaluation(
            rule_id=rule.id,
            description=rule.description,
            status=RuleEvaluationStatus.unknown,
            observed_value=None,
            mandatory=rule.mandatory,
            reason=f"Fact key '{rule.metric_key}' missing from available evidence facts.",
        )

    val = facts[rule.metric_key]
    if val is None:
        return RuleEvaluation(
            rule_id=rule.id,
            description=rule.description,
            status=RuleEvaluationStatus.unknown,
            observed_value=None,
            mandatory=rule.mandatory,
            reason=f"Fact key '{rule.metric_key}' is null/unreported.",
        )

    passed = False
    try:
        op = rule.operator.strip().lower()
        if op == "==":
            passed = (val == rule.threshold)
        elif op == "!=":
            passed = (val != rule.threshold)
        elif op == "<=":
            passed = (float(val) <= float(rule.threshold))
        elif op == ">=":
            passed = (float(val) >= float(rule.threshold))
        elif op == "<":
            passed = (float(val) < float(rule.threshold))
        elif op == ">":
            passed = (float(val) > float(rule.threshold))
        elif op in ("is_true", "true"):
            passed = bool(val is True)
        elif op in ("is_false", "false"):
            passed = bool(val is False)
        else:
            return RuleEvaluation(
                rule_id=rule.id,
                description=rule.description,
                status=RuleEvaluationStatus.unknown,
                observed_value=val,
                mandatory=rule.mandatory,
                reason=f"Unsupported operator '{rule.operator}'.",
            )
    except (ValueError, TypeError) as ex:
        return RuleEvaluation(
            rule_id=rule.id,
            description=rule.description,
            status=RuleEvaluationStatus.unknown,
            observed_value=val,
            mandatory=rule.mandatory,
            reason=f"Comparison failed with type error: {ex}",
        )

    status = RuleEvaluationStatus.pass_ if passed else RuleEvaluationStatus.fail
    reason = "Requirement satisfied." if passed else f"Observed {val} does not satisfy {rule.operator} {rule.threshold}."

    return RuleEvaluation(
        rule_id=rule.id,
        description=rule.description,
        status=status,
        observed_value=val,
        mandatory=rule.mandatory,
        reason=reason,
    )


def evaluate_policies(rules: list[QualityRule], facts: dict[str, Any]) -> PolicyEvaluationResult:
    """
    Evaluates all rules and determines non-favorable mandatory fail/unknown policy recommendation.

    Invariants:
    - Any failed mandatory rule => 'solicitar_cambios'
    - Any unknown mandatory rule (with no failed mandatory) => 'evidencia_insuficiente'
    - Any failed optional rule => 'solicitar_cambios'
    - All pass => 'recomendar_aprobacion'
    """
    evaluations: list[RuleEvaluation] = []
    passed_ids: list[str] = []
    failed_ids: list[str] = []
    unknown_ids: list[str] = []
    mandatory_failed_ids: list[str] = []
    mandatory_unknown_ids: list[str] = []

    for rule in rules:
        ev = evaluate_single_rule(rule, facts)
        evaluations.append(ev)

        if ev.status == RuleEvaluationStatus.pass_:
            passed_ids.append(rule.id)
        elif ev.status == RuleEvaluationStatus.fail:
            failed_ids.append(rule.id)
            if rule.mandatory:
                mandatory_failed_ids.append(rule.id)
        elif ev.status == RuleEvaluationStatus.unknown:
            unknown_ids.append(rule.id)
            if rule.mandatory:
                mandatory_unknown_ids.append(rule.id)

    # Determine recommended action based on mandatory rules first
    if mandatory_failed_ids or failed_ids:
        recommended = "solicitar_cambios"
    elif mandatory_unknown_ids:
        recommended = "evidencia_insuficiente"
    elif unknown_ids:
        # Non-mandatory unknown rule: still missing evidence
        recommended = "evidencia_insuficiente"
    else:
        recommended = "recomendar_aprobacion"

    return PolicyEvaluationResult(
        evaluations=evaluations,
        passed_rule_ids=passed_ids,
        failed_rule_ids=failed_ids,
        unknown_rule_ids=unknown_ids,
        mandatory_failed_ids=mandatory_failed_ids,
        mandatory_unknown_ids=mandatory_unknown_ids,
        recommended_action=recommended,
    )
