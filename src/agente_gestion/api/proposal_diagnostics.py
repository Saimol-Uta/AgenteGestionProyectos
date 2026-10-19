"""Safe projections for proposal-stage diagnostics.

Only allowlisted technical codes leave this module. Raw persisted payloads are
never returned to clients, and classification stays conservative: when the
stored evidence cannot prove a cause, the outcome is "unknown".
"""

import json
from typing import Any

from agente_gestion.api.schemas import ProposalStageDiagnostic


PROPOSAL_LIMITATION_CODES = frozenset({
    # Rule and catalogue availability.
    "missing_rules", "invalid_rules", "project_mismatch", "conflicting_rules",
    "catalogue_unavailable", "catalogue_conflict", "catalogue_error",
    # Task and field visibility.
    "unknown_task", "conflicting_task", "field_not_observed", "field_not_visible",
    # Values and evidence.
    "invalid_current_fact", "unknown_current_fact", "current_fact_mismatch",
    "evidence_mismatch", "missing_evidence", "no_change",
    # Selection and review.
    "unknown_choice", "conflicting_selection", "invalid_choice",
    "proposal_context_budget", "technical_review_unavailable",
    # Collection and capture binding.
    "collection_unassociated", "capture_unavailable",
})


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _codes(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return sorted({
        code for code in value
        if isinstance(code, str) and code in PROPOSAL_LIMITATION_CODES
    })


def proposal_stage_diagnostic(raw: Any) -> ProposalStageDiagnostic:
    """Build a bounded diagnostic from one persisted stage summary."""
    if not isinstance(raw, str) or not raw.strip():
        return ProposalStageDiagnostic(outcome="unknown")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return ProposalStageDiagnostic(outcome="unknown")
    if not isinstance(payload, dict):
        return ProposalStageDiagnostic(outcome="unknown")

    offered = _count(payload.get("offered"))
    selected = _count(payload.get("selected"))
    saved = _count(payload.get("saved"))
    technical_ready = _count(payload.get("technical_ready"))
    technical_blocked = _count(payload.get("technical_blocked"))
    limitation_codes = _codes(payload.get("limitations"))
    raw_failures = payload.get("failures")
    failures = raw_failures if isinstance(raw_failures, list) else []
    failure_codes = sorted({
        code
        for item in failures
        if isinstance(item, dict)
        for code in _codes(item.get("codes"))
    })
    discarded = sum(1 for item in failures if isinstance(item, dict))

    outcome = "unknown"
    if saved is not None and saved > 0:
        outcome = "saved"
    elif "missing_rules" in limitation_codes:
        outcome = "not_configured"
    elif set(limitation_codes) & {"invalid_rules", "project_mismatch"}:
        outcome = "rules_invalid"
    elif failures and selected is not None and selected > 0:
        outcome = "candidates_discarded"
    elif offered is not None and offered > 0 and selected == 0:
        outcome = "not_selected"
    elif offered is not None and offered == 0:
        outcome = "no_candidates"

    return ProposalStageDiagnostic(
        outcome=outcome,
        offered=offered,
        selected=selected,
        saved=saved,
        technical_ready=technical_ready,
        technical_blocked=technical_blocked,
        limitation_codes=limitation_codes,
        failure_codes=failure_codes,
        discarded=discarded,
    )
