"""Pure offline decisions, never authentication or live execution authority.

Trust precondition: the adapter verifies the actual immutable proposal submission,
its supporting evidence and capture, and supplies the SHA256 of exact stored
submission JSON bytes (catalogue data_json, including evidence). This module never
rehashes parsed models. Hashes prove consistency, not authorship or permission.
"""
from collections.abc import Iterable
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from agente_gestion.domain.action_proposal import ActionProposal, validate_action_proposal
from agente_gestion.domain.evidence import EvidenceItem
from agente_gestion.domain.snapshot import SnapshotCapture

Digest = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)]
Version = Annotated[StrictInt, Field(ge=0)]
Decision = Literal["approve_offline", "reject", "authorize_dry_run", "deny", "revoke"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OfflineActorClaim(_Frozen):
    """An explicit assertion only; no OS identity, role or authentication inference."""
    actor_id: StrictStr
    provenance: Literal["asserted_offline"] = "asserted_offline"

    @field_validator("actor_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("blank_identifier")
        return value


class DecisionEvent(_Frozen):
    event_id: StrictStr
    project_id: StrictStr
    proposal_id: StrictStr
    capture_id: StrictStr
    proposal_body_sha256: Digest
    expected_version: Version
    actor: OfflineActorClaim
    decision: Decision

    _nonblank = field_validator("event_id", "project_id", "proposal_id", "capture_id")(
        OfflineActorClaim.nonblank.__func__)


class TechnicalAssessment(_Frozen):
    errors: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        return not self.errors


class Projection(_Frozen):
    project_id: StrictStr
    proposal_id: StrictStr
    capture_id: StrictStr
    proposal_body_sha256: Digest
    version: Version = 0
    review_status: Literal["pending", "approved_offline", "rejected"] = "pending"
    authorization_status: Literal[
        "not_authorized", "authorized_dry_run", "denied", "revoked"
    ] = "not_authorized"
    permission_scope: Literal["bound_proposal_dry_run"] | None = None
    execution_status: Literal["not_executed"] = "not_executed"

    @property
    def real_execution_allowed(self) -> Literal[False]:
        """Even unsafe preconstructed/copied projections cannot enable a live executor."""
        return False


def _raw(value):
    # model_dump can hide forged attributes; recurse to revalidate mutable children.
    if isinstance(value, BaseModel):
        return _raw(dict(value.__dict__) | (value.model_extra or {}))
    if isinstance(value, dict):
        return {key: _raw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_raw(item) for item in value]
    return value


def _inputs(proposal, capture, evidence_items):
    return (ActionProposal.model_validate(_raw(proposal)),
            SnapshotCapture.model_validate(_raw(capture)),
            [EvidenceItem.model_validate(_raw(item)) for item in evidence_items])


def assess_proposal(proposal: ActionProposal, capture: SnapshotCapture,
                    evidence_items: list[EvidenceItem]) -> TechnicalAssessment:
    """Revalidate actual inputs; technical success is neither review nor permission."""
    try:
        inputs = _inputs(proposal, capture, evidence_items)
        errors = validate_action_proposal(*inputs)
    except (ValueError, TypeError, AttributeError):
        errors = ["invalid_inputs"]
    return TechnicalAssessment(errors=tuple(errors))


def fold_decisions(proposal: ActionProposal, capture: SnapshotCapture,
                   evidence_items: list[EvidenceItem], proposal_body_sha256: str,
                   events: Iterable[DecisionEvent]) -> Projection:
    """Fold append-only decisions without modifying inputs or catalogue statuses.

    Rejection is terminal for this immutable submission. Rejecting an approved
    review withdraws any grant. Denial/revocation permit only a new explicit grant
    while review remains approved and eligibility holds. Repeated approval is
    invalid. Identical event retries are ignored even after subsequent events;
    conflicting IDs, stale versions and mismatched bindings fail atomically.
    """
    proposal, capture, evidence = _inputs(proposal, capture, evidence_items)
    assessment = assess_proposal(proposal, capture, evidence)
    projection = Projection(project_id=proposal.project_id, proposal_id=proposal.proposal_id,
                            capture_id=proposal.observed_capture_id,
                            proposal_body_sha256=proposal_body_sha256)
    binding = (proposal.project_id, proposal.proposal_id, capture.capture_id,
               proposal_body_sha256)
    if proposal.observed_capture_id != capture.capture_id:
        raise ValueError("binding_mismatch")
    seen: dict[str, DecisionEvent] = {}
    for supplied in events:
        event = DecisionEvent.model_validate(_raw(supplied))
        if event.event_id in seen:
            if seen[event.event_id] != event:
                raise ValueError("event_id_conflict")
            continue
        if (event.project_id, event.proposal_id, event.capture_id,
                event.proposal_body_sha256) != binding:
            raise ValueError("binding_mismatch")
        if event.expected_version != projection.version:
            raise ValueError("stale_version")
        review, permission = projection.review_status, projection.authorization_status
        if event.decision in ("approve_offline", "authorize_dry_run"):
            if review == "rejected":
                raise ValueError("review_terminal")
            if not assessment.eligible:
                raise ValueError("technical_ineligible")
            if event.decision == "approve_offline":
                if review != "pending":
                    raise ValueError("review_not_pending")
                review = "approved_offline"
            else:
                if review != "approved_offline":
                    raise ValueError("review_not_approved")
                permission = "authorized_dry_run"
        elif event.decision == "reject":
            review, permission = "rejected", "revoked"
        else:
            permission = "denied" if event.decision == "deny" else "revoked"
        projection = projection.model_copy(update=dict(
            version=projection.version + 1, review_status=review,
            authorization_status=permission,
            permission_scope="bound_proposal_dry_run" if permission == "authorized_dry_run" else None))
        seen[event.event_id] = event
    return projection
