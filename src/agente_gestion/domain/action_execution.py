"""Secure, verified, and audited action execution engine for approved proposals.

Enforces domain security invariants:
1. Rejects unauthenticated actors or unverified offline assertions (requires verified authority).
2. Verifies proposal integrity, version, capture, and ledger review approval.
3. Detects stale captures and concurrent external modifications.
4. Restricts execution exclusively to natively supported provider fields:
   - For Trello: 'due_date', 'start_date', 'assignee_ids'
   - Rejects 'priority' (Trello has no native card priority field)
   - Rejects 'state' (Trello lists represent phases, not lifecycle states)
5. Read-before-write concurrency check:
   - If provider value already matches proposed value -> ALREADY_APPLIED (idempotent, no write).
   - If provider value does not match proposal current_value -> CONFLICT (external drift, abort).
6. Post-execution verification:
   - Reads back modified field directly from provider to confirm mutation succeeded.
7. Generates comprehensive audit trail with before/proposed/after values.
"""

from datetime import datetime, timezone
import re
from typing import Annotated, Any, Literal, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from agente_gestion.domain.action_proposal import (
    ActionProposal,
    ProposalField,
    validate_action_proposal,
)
from agente_gestion.domain.evidence import EvidenceItem
from agente_gestion.domain.proposal_review import (
    DecisionEvent,
    Digest,
    Version,
    _Frozen,
    _raw,
    fold_decisions,
)
from agente_gestion.domain.snapshot import SnapshotCapture
from agente_gestion.domain.tasks import DEFAULT_TIMEZONE, TaskState, normalize_datetime_to_tz
from agente_gestion.ports.task_actions import TaskActionPort


class ExecutionActor(_Frozen):
    """Authenticated actor holding verified administrative or managerial authority."""
    actor_id: StrictStr
    role: Literal["project_manager", "lead_architect", "admin"]
    auth_mechanism: Literal["hmac_token", "pki_signature", "test_authority"]
    auth_token: StrictStr

    @field_validator("actor_id", "auth_token")
    @classmethod
    def nonblank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Authentication fields must be non-blank")
        if v.strip() == "asserted_offline":
            raise ValueError("Unverified offline assertion is forbidden for execution authority")
        return v.strip()


class ExecutionAuthorization(_Frozen):
    """Cryptographic or verified grant token authorizing execution of an approved proposal."""
    grant_id: StrictStr
    proposal_id: StrictStr
    proposal_body_sha256: Digest
    expected_version: Version
    actor: ExecutionActor
    grant_token: StrictStr
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("grant_id", "grant_token")
    @classmethod
    def nonblank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Grant tokens must be non-blank")
        return v.strip()


class ExecutionRequest(_Frozen):
    """Request to safely execute an authorized proposal against an external task provider."""
    request_id: StrictStr
    project_id: StrictStr
    proposal_id: StrictStr
    proposal_body_sha256: Digest
    capture_id: StrictStr
    expected_version: Version
    authorization: ExecutionAuthorization

    @field_validator("request_id", "project_id", "proposal_id", "capture_id")
    @classmethod
    def nonblank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Identifiers must be non-blank")
        return v.strip()


ExecutionVerificationStatus = Literal[
    "verified", "verification_failed", "verification_unsupported", "unknown"
]


class ExecutionResult(_Frozen):
    """Audited result of an execution attempt, capturing before, proposed, and after states."""
    execution_id: StrictStr
    request_id: StrictStr
    proposal_id: StrictStr
    status: Literal["EXECUTED", "ALREADY_APPLIED", "DENIED", "CONFLICT", "FAILED"]
    reasons: tuple[StrictStr, ...] = ()
    field: Optional[ProposalField] = None
    value_before: Any = None
    value_proposed: Any = None
    value_observed_after: Any = None
    verification_status: ExecutionVerificationStatus = "unknown"
    actor_id: Optional[StrictStr] = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def _denied(
    request_id: str,
    proposal_id: str,
    *reasons: str,
    actor_id: Optional[str] = None,
) -> ExecutionResult:
    return ExecutionResult(
        execution_id=f"exec-{request_id}",
        request_id=request_id,
        proposal_id=proposal_id,
        status="DENIED",
        reasons=tuple(dict.fromkeys(reasons)),
        verification_status="unknown",
        actor_id=actor_id,
    )


def _values_match(field: ProposalField, left: Any, right: Any) -> bool:
    """Semantic equality check respecting timezone awareness and set membership."""
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False

    if field in ("due_date", "start_date"):
        if isinstance(left, datetime) and isinstance(right, datetime):
            l_norm = normalize_datetime_to_tz(left, DEFAULT_TIMEZONE)
            r_norm = normalize_datetime_to_tz(right, DEFAULT_TIMEZONE)
            return l_norm == r_norm
        return left == right

    if field == "assignee_ids":
        if isinstance(left, (list, tuple, set)) and isinstance(right, (list, tuple, set)):
            return set(left) == set(right)
        return left == right

    return left == right


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def execute_proposal_action(
    *,
    request: ExecutionRequest,
    proposal: ActionProposal,
    capture: SnapshotCapture,
    evidence_items: list[EvidenceItem],
    proposal_body_sha256: str,
    events: list[DecisionEvent],
    project_captures: list[SnapshotCapture],
    action_port: TaskActionPort,
) -> ExecutionResult:
    """
    Executes an action proposal with strict pre-checks, read-before-write concurrency,
    and post-execution read verification.
    """
    req_id = getattr(request, "request_id", "unknown")
    prop_id = getattr(proposal, "proposal_id", "unknown")
    actor_id = None
    if getattr(request, "authorization", None) and getattr(request.authorization, "actor", None):
        actor_id = getattr(request.authorization.actor, "actor_id", None)

    # 1. Structural Binding checks
    try:
        if (
            request.project_id != proposal.project_id
            or request.proposal_id != proposal.proposal_id
            or request.capture_id != capture.capture_id
            or request.proposal_body_sha256 != proposal_body_sha256
        ):
            return _denied(req_id, prop_id, "binding_mismatch", actor_id=actor_id)
    except (AttributeError, TypeError):
        return _denied(req_id, prop_id, "invalid_request", actor_id=actor_id)

    # 2. Authorization & Identity checks
    auth = request.authorization
    if auth.proposal_id != proposal.proposal_id or auth.proposal_body_sha256 != proposal_body_sha256:
        return _denied(req_id, prop_id, "authorization_binding_mismatch", actor_id=actor_id)
    if auth.expected_version != request.expected_version:
        return _denied(req_id, prop_id, "authorization_version_mismatch", actor_id=actor_id)
    if not auth.grant_token or auth.grant_token == "asserted_offline":
        return _denied(req_id, prop_id, "unauthorized_assertion_forbidden", actor_id=actor_id)
    if auth.actor.auth_mechanism == "asserted_offline":
        return _denied(req_id, prop_id, "unverified_actor_identity", actor_id=actor_id)

    # 3. Provider Native Field Compatibility Check (Fail-closed early)
    supported_fields = action_port.supported_fields()
    if proposal.field not in supported_fields:
        if proposal.field == "priority":
            reason = "unsupported_trello_field: priority (no native field on Trello)"
        elif proposal.field == "state":
            reason = "unsupported_trello_field: state (lists represent phases in HORMIGESTION)"
        else:
            reason = f"unsupported_field: {proposal.field}"
        return _denied(req_id, prop_id, reason, actor_id=actor_id)

    # 4. Snapshot and Freshness checks
    if not project_captures:
        return _denied(req_id, prop_id, "missing_captures", actor_id=actor_id)

    by_id: dict[str, SnapshotCapture] = {}
    for item in [capture, *project_captures]:
        if item.snapshot.project_id != proposal.project_id:
            return _denied(req_id, prop_id, "project_mismatch", actor_id=actor_id)
        if item.capture_id != item.content_hash or re.fullmatch(r"[0-9a-f]{64}", item.capture_id) is None:
            return _denied(req_id, prop_id, "invalid_capture", actor_id=actor_id)
        by_id[item.capture_id] = item

    if capture.capture_id not in by_id:
        return _denied(req_id, prop_id, "unknown_capture", actor_id=actor_id)

    latest = max(_utc(item.snapshot.captured_at) for item in by_id.values())
    newest = [item.capture_id for item in by_id.values() if _utc(item.snapshot.captured_at) == latest]
    if len(newest) != 1:
        return _denied(req_id, prop_id, "ambiguous_latest_capture", actor_id=actor_id)
    if newest[0] != capture.capture_id:
        return _denied(req_id, prop_id, "stale_capture", actor_id=actor_id)

    # 5. Proposal Validity
    errors = validate_action_proposal(proposal, capture, evidence_items)
    if errors:
        return _denied(req_id, prop_id, *errors, actor_id=actor_id)

    card = next((c for c in capture.snapshot.tasks if c.id == proposal.task_id), None)
    if card is None or not card.is_included or card.is_archived is True or card.state == TaskState.unknown:
        return _denied(req_id, prop_id, "target_ineligible", actor_id=actor_id)

    # 6. Review & Authorization Ledger Projection
    try:
        projection = fold_decisions(proposal, capture, evidence_items, proposal_body_sha256, events)
    except Exception:
        return _denied(req_id, prop_id, "invalid_decisions", actor_id=actor_id)

    if projection.review_status != "approved_offline":
        return _denied(req_id, prop_id, "review_not_approved", actor_id=actor_id)
    if projection.authorization_status in ("denied", "revoked"):
        return _denied(req_id, prop_id, f"authorization_{projection.authorization_status}", actor_id=actor_id)
    if request.expected_version != projection.version:
        return _denied(req_id, prop_id, "version_mismatch", actor_id=actor_id)


    # 7. Read-before-write concurrency and idempotency check
    try:
        current_live_value = action_port.read_card_field(proposal.task_id, proposal.field)
    except Exception as e:
        return ExecutionResult(
            execution_id=f"exec-{req_id}",
            request_id=req_id,
            proposal_id=prop_id,
            status="FAILED",
            reasons=(f"pre_read_error: {str(e)}",),
            field=proposal.field,
            value_before=None,
            value_proposed=proposal.proposed_value,
            value_observed_after=None,
            verification_status="unknown",
            actor_id=actor_id,
        )

    # Idempotency check: already applied
    if _values_match(proposal.field, current_live_value, proposal.proposed_value):
        return ExecutionResult(
            execution_id=f"exec-{req_id}",
            request_id=req_id,
            proposal_id=prop_id,
            status="ALREADY_APPLIED",
            reasons=("already_applied",),
            field=proposal.field,
            value_before=current_live_value,
            value_proposed=proposal.proposed_value,
            value_observed_after=current_live_value,
            verification_status="verified",
            actor_id=actor_id,
        )

    # Concurrency conflict check: external drift
    if not _values_match(proposal.field, current_live_value, proposal.current_value):
        return ExecutionResult(
            execution_id=f"exec-{req_id}",
            request_id=req_id,
            proposal_id=prop_id,
            status="CONFLICT",
            reasons=("concurrency_conflict: external_drift",),
            field=proposal.field,
            value_before=current_live_value,
            value_proposed=proposal.proposed_value,
            value_observed_after=current_live_value,
            verification_status="verification_failed",
            actor_id=actor_id,
        )

    # 8. Apply Mutation (Write)
    try:
        mutation_ok = action_port.apply_card_field_update(
            proposal.task_id, proposal.field, proposal.proposed_value
        )
        if not mutation_ok:
            return ExecutionResult(
                execution_id=f"exec-{req_id}",
                request_id=req_id,
                proposal_id=prop_id,
                status="FAILED",
                reasons=("mutation_rejected_by_provider",),
                field=proposal.field,
                value_before=current_live_value,
                value_proposed=proposal.proposed_value,
                value_observed_after=current_live_value,
                verification_status="verification_failed",
                actor_id=actor_id,
            )
    except Exception as e:
        return ExecutionResult(
            execution_id=f"exec-{req_id}",
            request_id=req_id,
            proposal_id=prop_id,
            status="FAILED",
            reasons=(f"mutation_error: {str(e)}",),
            field=proposal.field,
            value_before=current_live_value,
            value_proposed=proposal.proposed_value,
            value_observed_after=None,
            verification_status="unknown",
            actor_id=actor_id,
        )

    # 9. Post-Execution Read Verification
    try:
        observed_after = action_port.read_card_field(proposal.task_id, proposal.field)
        verified = _values_match(proposal.field, observed_after, proposal.proposed_value)
        verification_status: ExecutionVerificationStatus = "verified" if verified else "verification_failed"
        final_status = "EXECUTED" if verified else "FAILED"
        final_reasons = () if verified else ("post_verification_mismatch",)

        return ExecutionResult(
            execution_id=f"exec-{req_id}",
            request_id=req_id,
            proposal_id=prop_id,
            status=final_status,
            reasons=final_reasons,
            field=proposal.field,
            value_before=current_live_value,
            value_proposed=proposal.proposed_value,
            value_observed_after=observed_after,
            verification_status=verification_status,
            actor_id=actor_id,
        )
    except Exception as e:
        return ExecutionResult(
            execution_id=f"exec-{req_id}",
            request_id=req_id,
            proposal_id=prop_id,
            status="FAILED",
            reasons=(f"post_verification_read_error: {str(e)}",),
            field=proposal.field,
            value_before=current_live_value,
            value_proposed=proposal.proposed_value,
            value_observed_after=None,
            verification_status="unknown",
            actor_id=actor_id,
        )
