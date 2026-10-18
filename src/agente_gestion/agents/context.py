"""Explicit context builder for bounded agent contexts with anti-silent-truncation guarantees."""

import copy
import json
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field

from agente_gestion.domain.tasks import TaskCard, TaskState, DeterministicTaskMetrics
from agente_gestion.domain.code import CodeDiffSnippet, CIRunResult
from agente_gestion.domain.report import MonitoringDraft, DomainProposalRuleSet, MonitoringRequestedChange
from agente_gestion.domain.action_proposal import _value, _same
from agente_gestion.domain.evidence import EvidenceItem
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


def monitoring_choices(project_id, tasks, rows, rules, evidence: Optional[list[EvidenceItem]]):
    """Build only observed, visible, rule-backed choices; never infer change intent."""
    choices, limitations = [], []
    if rules is None:
        return [], ["missing_rules"]
    try:
        rules = DomainProposalRuleSet.model_validate(rules.model_dump(mode="python"))
    except (ValueError, TypeError, AttributeError):
        return [], ["invalid_rules"]
    if rules.project_id != project_id:
        return [], ["project_mismatch"]
    if not rules.rules:
        return [], ["missing_rules"]
    keys = [(tid, r.field) for r in rules.rules for tid in r.task_ids]
    rule_ids = [r.rule_id for r in rules.rules]
    if len(set(keys)) != len(keys) or len(set(rule_ids)) != len(rule_ids):
        return [], ["conflicting_rules"]
    visible = {row["id"]: row for row in rows}
    for rule in rules.rules:
        for tid in rule.task_ids:
            cards = [t for t in tasks if t.id == tid]
            code = None
            if len(cards) != 1:
                code = "unknown_task"
            elif cards[0].has_contradiction:
                code = "conflicting_task"
            elif not cards[0].observed_fields or rule.field not in cards[0].observed_fields:
                code = "field_not_observed"
            elif tid not in visible or rule.field not in visible[tid]:
                code = "field_not_visible"
            if code:
                limitations.append(code)
                continue
            card = cards[0]
            try:
                current = _value(rule.field, getattr(card, rule.field))
            except (ValueError, TypeError):
                limitations.append("invalid_current_fact")
                continue
            if current == TaskState.unknown:
                limitations.append("unknown_current_fact")
                continue
            if not _same(rule.field, current, _value(rule.field, visible[tid][rule.field])):
                limitations.append("current_fact_mismatch")
                continue
            matched = []
            for item in evidence or []:
                p = item.provenance
                claims = [p.external_id] if p.external_id is not None else []
                claims += [p.payload[k] for k in ("task_id", "card_id", "external_id") if k in p.payload]
                mask = p.payload.get("observed_fields")
                if (item.category != "task" or p.project_id != project_id or type(p.id) is not str or not p.id.strip()
                        or not claims or any(type(c) is not str or c not in {tid, card.external_id} for c in claims)
                        or rule.field not in p.payload or (mask is not None and
                        (type(mask) not in (list, tuple) or rule.field not in mask))):
                    continue
                try:
                    if _same(rule.field, current, _value(rule.field, p.payload[rule.field])):
                        matched.append(p.id)
                except (ValueError, TypeError):
                    continue
            if not matched or any(sum(e.provenance.id == i for e in evidence) != 1 for i in matched):
                limitations.append("evidence_mismatch" if evidence else "missing_evidence")
                continue
            alternatives = []
            for value in rule.allowed_values:
                if _same(rule.field, current, value):
                    limitations.append("no_change")
                elif any(_same(rule.field, value, v) for v in alternatives):
                    limitations.append("conflicting_rules")
                    alternatives = []
                    break
                else:
                    alternatives.append(value)
            for value in alternatives:
                choices.append(MonitoringRequestedChange(project_id=project_id, rule_id=rule.rule_id,
                    task_id=tid, field=rule.field, current_value=current, proposed_value=value,
                    evidence_ids=sorted(matched), rationale=rule.rationale, expected_impact=rule.expected_impact))
    return choices, sorted(set(limitations))


def _snippet_priority_key(snippet: CodeDiffSnippet) -> tuple[int, int, int, str, int]:
    path = snippet.file_path.lower()
    suffix = path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    if suffix in {"ts", "tsx", "js", "jsx", "py", "pyw", "go", "rs", "java", "c", "cpp", "cs", "php", "rb"}:
        cat_prio = 0
    elif suffix in {"json", "yaml", "yml", "toml", "html", "css", "scss"}:
        cat_prio = 1
    elif suffix in {"sql", "sh", "bash"}:
        cat_prio = 2
    else:
        cat_prio = 3
    scope_prio = 0 if snippet.evidence_scope in {"diff", "snippet"} else 1
    token_prio = len(snippet.content)
    return (cat_prio, scope_prio, token_prio, snippet.file_path, snippet.start_line or 0)


class ContextBuilder:
    """Builds explicit bounded contexts without silent truncation of critical evidence."""

    def __init__(self, default_budget_tokens: Optional[int] = None):
        if default_budget_tokens is not None and default_budget_tokens <= 0:
            raise ValueError("Context budget must be positive")
        self._explicit_default = default_budget_tokens is not None
        self.default_budget_tokens = 4096 if default_budget_tokens is None else default_budget_tokens

    def calculate_safe_input_budget(
        self,
        context_window: int,
        max_output_tokens: int,
        safety_margin: int = 64,
        overhead_tokens: int = 0,
    ) -> int:
        """
        Calculates safe input token budget as:
        safe_input_budget = context_window - max_output_tokens - safety_margin - overhead_tokens
        Guarantees non-negative integer floor.
        """
        budget = context_window - max_output_tokens - safety_margin - overhead_tokens
        return max(0, budget)

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

    @staticmethod
    def estimate_prompt(payload: dict[str, Any], instructions: str, schema: dict,
                        request_fields: Optional[dict[str, Any]] = None) -> int:
        # Count escaped user JSON and message/schema framing, not just evidence.
        # This is a character estimate, not the provider's tokenizer or template.
        fields = request_fields or {}
        envelope = {"model": fields.get("model", ""), "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": json.dumps(payload, default=str)},
        ], "format": schema, "request_metadata": fields}
        return estimate_tokens_from_data(envelope)

    def runtime_payload(self, context: ContextPayload, instructions: str, schema: dict,
                        output_tokens: int, context_tokens: int,
                        max_output_tokens: Optional[int] = None,
                        request_fields: Optional[dict[str, Any]] = None,
                        safety_margin_tokens: int = 64) -> dict[str, Any]:
        """Select whole role items before checking the serialized envelope.

        The unchanged configured window reserves exact output plus a 64-token
        framing margin. Estimates do not guarantee actual tokenizer usage.
        Critical items/code are never clipped or omitted to make a request fit.
        """
        if output_tokens <= 0 or (max_output_tokens is not None and output_tokens > max_output_tokens):
            raise ContextBudgetExceededError("Requested output exceeds configured capability; request a supported output reservation.")
        payload = copy.deepcopy(context.data)
        payload["available_evidence_ids"] = payload.pop("evidence_ids", [])
        metadata = context.model_dump(exclude={"data"})
        metadata.update(context_window_tokens=context_tokens, reserved_output_tokens=output_tokens,
                        safety_margin_tokens=safety_margin_tokens, estimation_method="JSON character estimate (3 chars/token); not tokenizer exact",
                        estimated_prompt_tokens=0, status="complete", is_truncated=False, incomplete_reason=None)
        payload["context_metadata"] = metadata
        # Self-describing estimates count their own bytes. Stabilize integer
        # metadata before the final bound, rather than hide metadata overhead.
        for _ in range(10):
            estimated = estimate_tokens_from_data(payload)
            prompt = self.estimate_prompt(payload, instructions, schema, request_fields)
            budget = min(context.budget_tokens, context_tokens - output_tokens - safety_margin_tokens - (prompt - estimated))
            updated = (estimated, prompt, budget)
            previous = (metadata["estimated_tokens"], metadata["estimated_prompt_tokens"], metadata["budget_tokens"])
            metadata.update(estimated_tokens=estimated, estimated_prompt_tokens=prompt, budget_tokens=budget)
            if updated == previous:
                break
        else:
            raise ContextBudgetExceededError("Context estimates could not stabilize; request additional context.")
        if estimated <= budget:
            return payload
        self._check_budget(context.context_type, context.project_id, payload, budget, True)

    def build_monitoring_context(
        self,
        project_id: str,
        metrics: DeterministicTaskMetrics,
        tasks: list[TaskCard],
        ci_result: Optional[CIRunResult] = None,
        evidence_ids: Optional[list[str]] = None,
        budget_tokens: Optional[int] = None,
        strict_budget: bool = False,
        source_is_complete: Optional[bool] = None,
        source_commit_sha: Optional[str] = None,
    ) -> ContextPayload:
        """Compact snapshot facts; critical signals are required, never clipped."""
        required = set(metrics.overdue_card_ids + metrics.due_soon_card_ids + metrics.blocked_card_ids +
                       metrics.unknown_card_ids + metrics.contradictory_card_ids)
        required.update(task.id for task in tasks if task.state in
                        {TaskState.in_progress, TaskState.blocked, TaskState.unknown} or task.has_contradiction)
        all_ids = {task.id for task in tasks}
        if not required <= all_ids:
            raise ValueError("Critical metric task IDs lack snapshot evidence; request additional context.")
        selected = sorted((task for task in tasks if task.id in required), key=lambda task: task.id)
        reason = "active_and_problem_cards"
        if not selected:
            # At most one included example grounds a concrete progress narrative
            # when no active/problem card exists. Never fill remaining capacity.
            representatives = sorted((task for task in tasks if task.is_included),
                                    key=lambda task: (task.state != TaskState.completed, task.id))
            selected = representatives[:1]
            reason = "single_representative_for_global_progress" if selected else "metrics_only_no_included_tasks"
        selected_ids = {task.id for task in selected}
        omitted_count = len(tasks) - len(selected)
        ci_matches = ci_result is not None and (source_commit_sha is None or ci_result.commit_sha == source_commit_sha)
        rows = []
        for task in selected:
            row = {"id": task.id, "title": task.title, "state": task.state.value,
                   "is_included": task.is_included, "has_contradiction": task.has_contradiction}
            if task.due_date is not None:
                row["due_date"] = task.due_date.isoformat()
            if task.external_id is not None and task.external_id != task.id:
                row["external_id"] = task.external_id
            rows.append(row)
        ci_data = ci_result.model_dump(mode="json", exclude_none=True) if ci_matches else None
        data = {
            "project_id": project_id, "metrics": metrics.model_dump(), "tasks": rows,
            "ci_result": ci_data, "ci_availability": "reported" if ci_matches else "unavailable",
            "source_context": {"is_complete": source_is_complete, "commit_sha": source_commit_sha,
                               "task_provenance": "deterministic_snapshot"},
            "selection": {"selected_task_ids": [task.id for task in selected], "omitted_task_count": omitted_count,
                          "required_task_ids": sorted(required), "reason": reason,
                          "status": "partial" if omitted_count else "complete"},
            "evidence_ids": sorted((set(evidence_ids or []) - all_ids) | selected_ids),
        }
        if ci_result is not None and not ci_matches:
            data["evidence_ids"] = [eid for eid in data["evidence_ids"] if eid != ci_result.id]
        return self._check_budget(BoundedContextType.monitoring, project_id, data, budget_tokens, strict_budget)

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
        repository_scope: Optional[dict[str, Any]] = None,
    ) -> ContextPayload:
        """Assembles bounded code context prioritizing relevant snippets within budget."""
        effective_budget = budget_tokens if budget_tokens is not None else self.default_budget_tokens
        if effective_budget <= 0:
            raise ContextBudgetExceededError("No input context budget remains. Request additional context or increase the explicit context budget.")

        ev_ids = list(evidence_ids) if evidence_ids is not None else [s.commit_sha for s in code_snippets]
        full_data: dict[str, Any] = {
            "project_id": project_id,
            "draft_summary": draft.summary,
            "review_criteria": criteria,
            "code_snippets": [s.model_dump() for s in code_snippets],
            "rules": [r.model_dump() for r in rules] if rules else [],
            "evidence_ids": ev_ids,
        }
        if repository_scope is not None:
            full_data["repository_scope"] = copy.deepcopy(repository_scope)

        full_estimated = estimate_tokens_from_data(full_data)

        # Case 1: All snippets fit within the allocated budget
        if full_estimated <= effective_budget:
            full_data["selection"] = {
                "total_snippets": len(code_snippets),
                "selected_count": len(code_snippets),
                "omitted_count": 0,
                "omitted_files": [],
                "status": "complete",
                "is_complete": True,
                "reason": None,
            }
            return ContextPayload(
                context_type=BoundedContextType.code,
                project_id=project_id,
                budget_tokens=effective_budget,
                estimated_tokens=full_estimated,
                is_truncated=False,
                status="complete",
                incomplete_reason=None,
                data=full_data,
            )

        # Case 2: Budget exceeded and not in strict budgeting mode
        if not strict_budget:
            msg = (
                f"Context budget exceeded for code: "
                f"estimated {full_estimated} tokens > budget {effective_budget} tokens. "
                "Silent truncation rejected per architectural policy. "
                "Request additional context or increase the explicit context budget."
            )
            full_data["selection"] = {
                "total_snippets": len(code_snippets),
                "selected_count": len(code_snippets),
                "omitted_count": 0,
                "omitted_files": [],
                "status": "incomplete",
                "is_complete": False,
                "reason": msg,
            }
            return ContextPayload(
                context_type=BoundedContextType.code,
                project_id=project_id,
                budget_tokens=effective_budget,
                estimated_tokens=full_estimated,
                is_truncated=True,
                status="incomplete",
                incomplete_reason=msg,
                data=full_data,
            )

        # Case 3: Budget exceeded and strict budgeting mode -> prioritize snippets
        base_data = dict(full_data)
        base_data["code_snippets"] = []
        base_overhead = estimate_tokens_from_data(base_data)
        if base_overhead >= effective_budget:
            raise ContextBudgetExceededError(
                f"Base quality context overhead ({base_overhead} tokens) exceeds allocated budget ({effective_budget} tokens). "
                "Request additional context or increase the explicit context budget."
            )

        sorted_snippets = sorted(code_snippets, key=_snippet_priority_key)
        selected_snippets: list[CodeDiffSnippet] = []
        omitted_snippets: list[CodeDiffSnippet] = []
        current_snippets: list[dict[str, Any]] = []

        for s in sorted_snippets:
            candidate_snippets = current_snippets + [s.model_dump()]
            cand_omitted = [sn for sn in sorted_snippets if sn not in selected_snippets and sn != s]
            cand_data = dict(base_data)
            cand_data["code_snippets"] = candidate_snippets
            cand_data["selection"] = {
                "total_snippets": len(code_snippets),
                "selected_count": len(candidate_snippets),
                "omitted_count": len(cand_omitted),
                "omitted_files": sorted(list({sn.file_path for sn in cand_omitted})),
                "status": "partial" if cand_omitted else "complete",
                "is_complete": not cand_omitted,
                "reason": (
                    None if not cand_omitted
                    else f"Context budget accommodated {len(candidate_snippets)} of {len(code_snippets)} code snippet(s); "
                         f"{len(cand_omitted)} snippet(s) omitted to respect {effective_budget} token limit."
                ),
            }
            # Reserve 160 tokens headroom for runtime_payload context_metadata insertion
            if estimate_tokens_from_data(cand_data) + 160 <= effective_budget:
                current_snippets = candidate_snippets
                selected_snippets.append(s)
            else:
                omitted_snippets.append(s)

        if code_snippets and not selected_snippets:
            if sorted_snippets:
                first = sorted_snippets[0]
                cand_data = dict(base_data)
                cand_data["code_snippets"] = [first.model_dump()]
                if estimate_tokens_from_data(cand_data) <= effective_budget:
                    selected_snippets = [first]
                    omitted_snippets = sorted_snippets[1:]
            if not selected_snippets:
                raise ContextBudgetExceededError(
                    f"Context budget ({effective_budget} tokens) is insufficient to include even the smallest code snippet. "
                    "Request additional context or increase the explicit context budget."
                )

        selected_shas = {s.commit_sha for s in selected_snippets}
        if evidence_ids is not None:
            ev_ids = [eid for eid in evidence_ids if eid in selected_shas or eid not in {s.commit_sha for s in code_snippets}]
        else:
            ev_ids = sorted(selected_shas)

        is_complete = len(omitted_snippets) == 0
        status_val = "complete" if is_complete else "incomplete"
        reason_val = (
            None if is_complete
            else f"Context budget accommodated {len(selected_snippets)} of {len(code_snippets)} code snippet(s); "
                 f"{len(omitted_snippets)} snippet(s) omitted to respect {effective_budget} token limit."
        )

        data = dict(base_data)
        data["code_snippets"] = [s.model_dump() for s in selected_snippets]
        data["evidence_ids"] = ev_ids
        data["selection"] = {
            "total_snippets": len(code_snippets),
            "selected_count": len(selected_snippets),
            "omitted_count": len(omitted_snippets),
            "omitted_files": sorted(list({s.file_path for s in omitted_snippets})),
            "status": "complete" if is_complete else "partial",
            "is_complete": is_complete,
            "reason": reason_val,
        }

        estimated = estimate_tokens_from_data(data)

        return ContextPayload(
            context_type=BoundedContextType.code,
            project_id=project_id,
            budget_tokens=effective_budget,
            estimated_tokens=estimated,
            is_truncated=not is_complete,
            status=status_val,
            incomplete_reason=reason_val,
            data=data,
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
        repository_scope: Optional[dict[str, Any]] = None,
    ) -> ContextPayload:
        """Assembles verifier bounded context for selective finding verification."""
        selected_snippets = []
        for finding in findings:
            matches = [snippet for snippet in code_snippets if snippet.commit_sha == finding.commit_sha
                       and snippet.file_path == finding.file_path and finding.line in snippet.lines]
            if not matches:
                raise ValueError("Required finding location lacks code evidence; request additional context.")
            if len({snippet.diff_side for snippet in matches}) > 1:
                raise ValueError("Finding diff side is ambiguous; request exact side evidence before verification.")
            for snippet in matches:
                if snippet not in selected_snippets:
                    selected_snippets.append(snippet)
        cited = {eid for finding in findings for eid in finding.evidence_ids}
        available = set(evidence_ids) if evidence_ids is not None else {s.commit_sha for s in selected_snippets}
        if not cited <= available:
            raise ValueError("Required finding citations lack available evidence; request additional context.")
        selected_rules = [rule for rule in rules if rule.id in {finding.rule_id for finding in findings}]
        data = {
            "project_id": project_id,
            "findings": [finding.model_dump() for finding in findings],
            "code_snippets": [snippet.model_dump() for snippet in selected_snippets],
            "rules": [rule.model_dump() for rule in selected_rules],
            "evidence_ids": sorted(cited & available),
        }
        if repository_scope is not None:
            data["repository_scope"] = copy.deepcopy(repository_scope)
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
