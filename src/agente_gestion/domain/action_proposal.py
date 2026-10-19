"""Pure, consultative H2A proposals; no review transitions or execution authority."""

from datetime import datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, model_validator

from agente_gestion.domain.evidence import EvidenceItem
from agente_gestion.domain.risks import ProposalRiskRef
from agente_gestion.domain.snapshot import SnapshotCapture
from agente_gestion.domain.tasks import TaskState

ProposalField = Literal["start_date", "due_date", "priority", "assignee_ids", "state"]
ProposalValue = datetime | TaskState | StrictStr | list[StrictStr] | None

# Submission fields that a stored proposal may omit when they carry no value.
# Keeping them out preserves the exact bytes of every proposal created before
# the field existed, so its identifier and its content hash never change.
OPTIONAL_SUBMISSION_FIELDS = frozenset({"risk_refs"})


def _value(field, value):
    """Parse only explicit field types, without provider aliases or date coercion."""
    if field == "state":
        if isinstance(value, TaskState):
            return value
        if type(value) is str:
            return TaskState(value)
    elif value is None:
        return None
    elif field in ("start_date", "due_date"):
        if isinstance(value, datetime):
            return value
        if type(value) is str and re.match(r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}", value):
            return datetime.fromisoformat(value)
    elif field == "priority" and type(value) is str:
        return value
    elif field == "assignee_ids" and type(value) in (list, tuple):
        if all(type(item) is str for item in value):
            return list(value)
    raise ValueError("Invalid field-specific value")


def _same(field, left, right):
    if field == "assignee_ids" and left is not None and right is not None:
        return set(left) == set(right)
    return left == right


class ActionProposal(BaseModel):
    """A versioned proposal, never an authorization or an execution receipt."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["action_proposal_v1", "action_proposal_v2"] = "action_proposal_v1"
    proposal_id: StrictStr
    project_id: StrictStr
    task_id: StrictStr
    observed_capture_id: StrictStr
    field: ProposalField
    current_value: ProposalValue
    proposed_value: ProposalValue
    evidence_ids: list[StrictStr]
    rationale: StrictStr
    expected_impact: StrictStr
    risk_refs: list[ProposalRiskRef] = Field(
        default_factory=list, exclude_if=lambda refs: not refs,
    )
    review_status: Literal["pending"] = "pending"
    authorization_status: Literal["not_authorized"] = "not_authorized"
    execution_status: Literal["not_executed"] = "not_executed"

    @model_validator(mode="before")
    @classmethod
    def validate_values(cls, data):
        if not isinstance(data, dict):
            raise ValueError("Proposal must be an object")
        result = dict(data)
        for key in ("current_value", "proposed_value"):
            if key in result:
                result[key] = _value(result.get("field"), result[key])
        for key in ("proposal_id", "project_id", "task_id", "observed_capture_id",
                    "rationale", "expected_impact"):
            if key in result and (type(result[key]) is not str or not result[key].strip()):
                raise ValueError("Proposal identifiers and explanations must be nonempty strings")
        refs = result.get("risk_refs")
        version = result.get("schema_version", "action_proposal_v1")
        if refs and version != "action_proposal_v2":
            raise ValueError("Risk references require the action_proposal_v2 schema")
        if not refs and version == "action_proposal_v2":
            raise ValueError("The action_proposal_v2 schema requires risk references")
        return result


def validate_action_proposal(
    proposal: ActionProposal, capture: SnapshotCapture, evidence_items: list[EvidenceItem],
) -> list[str]:
    """Return stable error codes in check order, without changing any input.

    The authoritative snapshot must come from verified storage. Only the stored
    digest binding is checked here; parsed legacy models must NOT be rehashed.
    Passed evidence supports task metadata, not proven immutable capture provenance.
    Success grants neither review approval nor permission to execute an action.
    """
    try:
        # Revalidate even constructed/copied models, including undeclared claims.
        data = dict(proposal.__dict__) if isinstance(proposal, ActionProposal) else proposal
        proposal = ActionProposal.model_validate(data)
    except (ValidationError, ValueError, TypeError):
        return ["invalid_proposal"]
    errors = []
    if (capture.capture_id != capture.content_hash
            or re.fullmatch(r"[0-9a-f]{64}", capture.content_hash) is None):
        errors.append("invalid_capture")
    if proposal.observed_capture_id != capture.capture_id:
        errors.append("capture_mismatch")
    if proposal.project_id != capture.snapshot.project_id:
        errors.append("project_mismatch")
    for ref in proposal.risk_refs:
        if re.fullmatch(r"risk-[0-9a-f]{24}", ref.risk_id) is None:
            errors.append("risk_reference_invalid")
        if ref.capture_id != proposal.observed_capture_id:
            errors.append("risk_capture_mismatch")
        if ref.task_id != proposal.task_id:
            errors.append("risk_task_mismatch")
    targets = [card for card in capture.snapshot.tasks if card.id == proposal.task_id]
    if len(targets) != 1:
        errors.append("target_not_unique")
    if errors:
        return errors
    card = targets[0]
    field = proposal.field
    if card.has_contradiction:
        errors.append("conflicting_task")
    if card.observed_fields is None or field not in card.observed_fields:
        return errors + ["field_not_observed"]
    try:
        current = _value(field, getattr(card, field))
    except (ValueError, TypeError):
        return errors + ["invalid_current_fact"]
    if not _same(field, current, proposal.current_value):
        errors.append("current_value_mismatch")
    if _same(field, proposal.current_value, proposal.proposed_value):
        errors.append("no_change")
    ids = proposal.evidence_ids
    if not ids or any(not id.strip() for id in ids) or len(set(ids)) != len(ids):
        return errors + ["invalid_evidence_ids"]
    identities = {card.id}
    if card.external_id is not None:
        identities.add(card.external_id)
    for evidence_id in ids:
        matches = [item for item in evidence_items if item.provenance.id == evidence_id]
        if len(matches) != 1:
            errors.append("evidence_not_unique")
            continue
        item = matches[0]
        provenance = item.provenance
        payload = provenance.payload
        claims = [provenance.external_id] if provenance.external_id is not None else []
        claims.extend(payload[key] for key in ("task_id", "card_id", "external_id") if key in payload)
        if (item.category != "task" or provenance.project_id != proposal.project_id
                or not claims or any(type(claim) is not str or claim not in identities for claim in claims)):
            errors.append("evidence_target_mismatch")
            continue
        mask = payload.get("observed_fields")
        if (field not in payload or (mask is not None and
                (type(mask) not in (list, tuple) or field not in mask))):
            errors.append("evidence_fact_mismatch")
            continue
        try:
            fact = _value(field, payload[field])
            if not _same(field, current, fact):
                errors.append("evidence_fact_mismatch")
        except (ValueError, TypeError):
            errors.append("evidence_fact_mismatch")
    return list(dict.fromkeys(errors))
