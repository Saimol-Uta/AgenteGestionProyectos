"""Explicit context builder for bounded agent contexts with anti-silent-truncation guarantees."""

import json
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field

from agente_gestion.domain.tasks import TaskCard, DeterministicTaskMetrics
from agente_gestion.domain.code import CodeDiffSnippet, CIRunResult
from agente_gestion.domain.report import MonitoringDraft
from agente_gestion.domain.findings import CandidateFinding
from agente_gestion.domain.policies import QualityRule, PolicyEvaluationResult


class BoundedContextType(str, Enum):
    monitoring = "monitoring"
    code = "code"
    verifier = "verifier"
    report = "report"


class ContextBudgetExceededError(Exception):
    """Raised when critical context data exceeds the configured token budget."""
    status = "incomplete"
    context_request = "Request additional context or increase the explicit context budget."


class ContextPayload(BaseModel):
    """Scoped context package prepared for an agent role."""
    context_type: BoundedContextType
    project_id: str
    budget_tokens: int
    estimated_tokens: int
    is_truncated: bool = False
    status: str = "complete"
    incomplete_reason: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)


def estimate_tokens_from_data(data: Any) -> int:
    """
    Conservative token estimation based on JSON character length.
    Approximates ~3.5 characters per token.
    """
    try:
        raw_text = json.dumps(data, default=str)
    except Exception:
        raw_text = str(data)
    return max(1, len(raw_text) // 3 + 1)


class ContextBuilder:
    """Builds explicit bounded contexts without silent truncation of critical evidence."""

    def __init__(self, default_budget_tokens: int = 4096):
        if default_budget_tokens <= 0:
            raise ValueError("Context budget must be positive")
        self.default_budget_tokens = default_budget_tokens

    def _check_budget(
        self,
        context_type: BoundedContextType,
        project_id: str,
        data: dict[str, Any],
        budget_tokens: Optional[int],
        strict_budget: bool,
    ) -> ContextPayload:
        budget = self.default_budget_tokens if budget_tokens is None else budget_tokens
        if budget <= 0:
            raise ContextBudgetExceededError("No input context budget remains. Request additional context or increase the explicit context budget.")
        estimated = estimate_tokens_from_data(data)

        if estimated > budget:
            msg = (
                f"Context budget exceeded for {context_type.value}: "
                f"estimated {estimated} tokens > budget {budget} tokens. "
                "Silent truncation rejected per architectural policy. "
                "Request additional context or increase the explicit context budget."
            )
            if strict_budget:
                raise ContextBudgetExceededError(msg)
            return ContextPayload(
                context_type=context_type,
                project_id=project_id,
                budget_tokens=budget,
                estimated_tokens=estimated,
                is_truncated=True,
                status="incomplete",
                incomplete_reason=msg,
                data=data,
            )

        return ContextPayload(
            context_type=context_type,
            project_id=project_id,
            budget_tokens=budget,
            estimated_tokens=estimated,
            is_truncated=False,
            status="complete",
            data=data,
        )

    def runtime_payload(self, context: ContextPayload, instructions: str, schema: dict,
                        output_tokens: int, context_tokens: int) -> dict[str, Any]:
        """Bound the complete provider envelope, reserving generation space.

        Token estimates are conservative estimates, not tokenizer guarantees. No
        evidence is clipped to fit; a failed bound prevents provider invocation.
        """
        payload = dict(context.data)
        payload["available_evidence_ids"] = payload.pop("evidence_ids", [])
        payload["context_metadata"] = context.model_dump(exclude={"data"})
        overhead = estimate_tokens_from_data({"instructions": instructions, "schema": schema})
        available = context_tokens - output_tokens - overhead
        self._check_budget(context.context_type, context.project_id, payload,
                           min(context.budget_tokens, available), True)
        return payload

    def build_monitoring_context(
        self,
        project_id: str,
        metrics: DeterministicTaskMetrics,
        tasks: list[TaskCard],
        ci_result: Optional[CIRunResult] = None,
        evidence_ids: Optional[list[str]] = None,
        budget_tokens: Optional[int] = None,
        strict_budget: bool = False,
    ) -> ContextPayload:
        """Assembles monitoring bounded context for draft creation (elaborador)."""
        data = {
            "project_id": project_id,
            "metrics": metrics.model_dump(),
            "tasks": [t.model_dump() for t in tasks],
            "ci_result": ci_result.model_dump() if ci_result else None,
            "evidence_ids": evidence_ids or [],
        }
        return self._check_budget(
            BoundedContextType.monitoring, project_id, data, budget_tokens, strict_budget
        )

    def build_code_context(
        self,
        project_id: str,
        draft: MonitoringDraft,
        criteria: list[str],
        code_snippets: list[CodeDiffSnippet],
        rules: Optional[list[QualityRule]] = None,
        evidence_ids: Optional[list[str]] = None,
        budget_tokens: Optional[int] = None,
        strict_budget: bool = False,
    ) -> ContextPayload:
        """Assembles code bounded context for code review (revisor)."""
        ev_ids = list(evidence_ids) if evidence_ids is not None else [s.commit_sha for s in code_snippets]
        data = {
            "project_id": project_id,
            "draft_summary": draft.summary,
            "review_criteria": criteria,
            "code_snippets": [s.model_dump() for s in code_snippets],
            "rules": [r.model_dump() for r in rules] if rules else [],
            "evidence_ids": ev_ids,
        }
        return self._check_budget(
            BoundedContextType.code, project_id, data, budget_tokens, strict_budget
        )

    def build_verifier_context(
        self,
        project_id: str,
        findings: list[CandidateFinding],
        code_snippets: list[CodeDiffSnippet],
        rules: list[QualityRule],
        evidence_ids: Optional[list[str]] = None,
        budget_tokens: Optional[int] = None,
        strict_budget: bool = False,
    ) -> ContextPayload:
        """Assembles verifier bounded context for selective finding verification."""
        ev_ids = list(evidence_ids) if evidence_ids is not None else [s.commit_sha for s in code_snippets]
        data = {
            "project_id": project_id,
            "findings": [f.model_dump() for f in findings],
            "code_snippets": [s.model_dump() for s in code_snippets],
            "rules": [r.model_dump() for r in rules],
            "evidence_ids": ev_ids,
        }
        return self._check_budget(
            BoundedContextType.verifier, project_id, data, budget_tokens, strict_budget
        )

    def build_report_context(
        self,
        project_id: str,
        draft: MonitoringDraft,
        findings: list[CandidateFinding],
        policy_evaluations: PolicyEvaluationResult,
        evidence_ids: Optional[list[str]] = None,
        budget_tokens: Optional[int] = None,
        strict_budget: bool = False,
    ) -> ContextPayload:
        """Assembles report bounded context for final manager synthesis (aprobador)."""
        data = {
            "project_id": project_id,
            "draft": draft.model_dump(),
            "findings": [f.model_dump() for f in findings],
            "policy_evaluations": policy_evaluations.model_dump(),
            "evidence_ids": evidence_ids or [],
        }
        return self._check_budget(
            BoundedContextType.report, project_id, data, budget_tokens, strict_budget
        )
