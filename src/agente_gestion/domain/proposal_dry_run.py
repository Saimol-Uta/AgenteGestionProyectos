"""Pure simulation, never a persistent audit or live execution receipt.

Trust precondition: the adapter verifies current SQLite metadata and EXACT stored
submission bytes INCLUDING frozen evidence, supplying their SHA256 and all verified
project captures. No parsed-model rehash, caller trust flag, identity proof, live
freshness or timestamp audit is manufactured here. Freshness is stored-only.
"""
from datetime import datetime, timezone
import re
from typing import Literal

from pydantic import StrictStr, TypeAdapter, computed_field, field_validator

from agente_gestion.domain.action_proposal import ActionProposal, ProposalField, validate_action_proposal
from agente_gestion.domain.evidence import EvidenceItem
from agente_gestion.domain.proposal_review import (
    Digest, Version, DecisionEvent, OfflineActorClaim, _Frozen, _raw, fold_decisions,
)
from agente_gestion.domain.snapshot import SnapshotCapture
from agente_gestion.domain.tasks import TaskState


class DryRunRequest(_Frozen):
    request_id: StrictStr
    project_id: StrictStr
    proposal_id: StrictStr
    proposal_body_sha256: Digest
    capture_id: StrictStr
    permission_event_id: StrictStr
    expected_version: Version

    _nonblank = field_validator("request_id", "project_id", "proposal_id", "capture_id",
                               "permission_event_id")(OfflineActorClaim.nonblank.__func__)


class DryRunResult(_Frozen):
    status: Literal["denied", "SIMULATED"]
    reasons: tuple[StrictStr, ...] = ()
    field: ProposalField | None = None
    before: datetime | StrictStr | tuple[StrictStr, ...] | None = None
    after: datetime | StrictStr | tuple[StrictStr, ...] | None = None

    @computed_field
    @property
    def execution_status(self) -> Literal["not_executed"]:
        return "not_executed"

    @computed_field
    @property
    def live_state_changed(self) -> Literal[False]:
        return False

    @computed_field
    @property
    def real_execution_allowed(self) -> Literal[False]:
        return False


def _known_keys(raw, validated):
    """Legacy DTOs ignore extras; reject them, including forged nested attributes."""
    if isinstance(raw, dict) and isinstance(validated, dict):
        if raw.keys() - validated.keys():
            raise ValueError("undeclared_input")
        for key, value in raw.items():
            _known_keys(value, validated[key])
    elif isinstance(raw, list) and isinstance(validated, list):
        for value, checked in zip(raw, validated):
            _known_keys(value, checked)


def _checked(model, supplied):
    raw = _raw(supplied)
    checked = model.model_validate(raw, strict=True)
    _known_keys(raw, _raw(checked))
    return checked


def _utc(value):
    # Stored capture chronology follows SQLite: naive capture timestamps are UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _denied(*reasons):
    return DryRunResult(status="denied", reasons=tuple(dict.fromkeys(reasons)))


def simulate_proposal(*, request: DryRunRequest, proposal: ActionProposal,
                      capture: SnapshotCapture, evidence_items: list[EvidenceItem],
                      proposal_body_sha256: str, events: list[DecisionEvent],
                      project_captures: list[SnapshotCapture]) -> DryRunResult:
    """Recheck each attempt, even reused request IDs; D2 owns transactional audit.

    Action values come only from the actual stored proposal, never the request.
    Adapter verification is a precondition, not an accepted signed/trusted flag.
    """
    try:
        request = _checked(DryRunRequest, request)
        proposal = _checked(ActionProposal, proposal)
        capture = _checked(SnapshotCapture, capture)
        evidence = [_checked(EvidenceItem, item) for item in evidence_items]
        events = [_checked(DecisionEvent, event) for event in events]
        captures = [_checked(SnapshotCapture, item) for item in project_captures]
        digest = TypeAdapter(Digest).validate_python(proposal_body_sha256)
    except (ValueError, TypeError, AttributeError):
        return _denied("invalid_inputs")
    if (request.project_id, request.proposal_id, request.capture_id, request.proposal_body_sha256) != (
            proposal.project_id, proposal.proposal_id, capture.capture_id, digest):
        return _denied("binding_mismatch")
    if proposal.observed_capture_id != capture.capture_id:
        return _denied("binding_mismatch")
    if not captures:
        return _denied("missing_captures")
    by_id = {}
    for item in [capture, *captures]:
        if item.snapshot.project_id != proposal.project_id:
            return _denied("project_mismatch")
        if item.capture_id != item.content_hash or re.fullmatch(r"[0-9a-f]{64}", item.capture_id) is None:
            return _denied("invalid_capture")
        if item.capture_id in by_id and item != by_id[item.capture_id]:
            return _denied("capture_id_conflict")
        by_id[item.capture_id] = item
    if not any(item.capture_id == capture.capture_id for item in captures):
        return _denied("unknown_capture")
    latest = max(_utc(item.snapshot.captured_at) for item in by_id.values())
    newest = [item.capture_id for item in by_id.values() if _utc(item.snapshot.captured_at) == latest]
    if len(newest) != 1:
        return _denied("ambiguous_latest_capture")
    if newest[0] != capture.capture_id:
        return _denied("stale_capture")
    snapshot = capture.snapshot
    if snapshot.tasks_complete is not True:
        return _denied("incomplete_task_source")
    if not snapshot.task_source_identity or not snapshot.task_source_identity.strip():
        return _denied("unknown_task_source")
    errors = validate_action_proposal(proposal, capture, evidence)
    if errors:
        return _denied(*errors)
    card = next(card for card in snapshot.tasks if card.id == proposal.task_id)
    if not card.is_included or card.is_archived is True or card.state == TaskState.unknown:
        return _denied("target_ineligible")
    try:
        projection = fold_decisions(proposal, capture, evidence, digest, events)
    except (ValueError, TypeError, AttributeError):
        return _denied("invalid_decisions")
    if (projection.review_status != "approved_offline" or
            projection.authorization_status != "authorized_dry_run" or
            projection.permission_scope != "bound_proposal_dry_run"):
        return _denied("permission_not_effective")
    if request.expected_version != projection.version:
        return _denied("version_mismatch")
    # Identical retries do not move an old grant past later effective events.
    unique = {event.event_id: event for event in events}
    grant = next(event for event in reversed(list(unique.values())) if event.decision == "authorize_dry_run")
    if request.permission_event_id != grant.event_id:
        return _denied("permission_event_mismatch")
    before, after = proposal.current_value, proposal.proposed_value
    return DryRunResult(status="SIMULATED", field=proposal.field,
                        before=tuple(before) if isinstance(before, list) else before,
                        after=tuple(after) if isinstance(after, list) else after)
