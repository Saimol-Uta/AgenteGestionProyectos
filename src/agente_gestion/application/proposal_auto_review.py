"""Technical-only proposal review and immutable dry-run preview.

These records describe mechanical readiness; they are not manager approval,
execution authorization, or proof that a live provider accepted an update.
"""

from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agente_gestion.domain.action_proposal import ActionProposal
from agente_gestion.domain.evidence import EvidenceItem
from agente_gestion.domain.proposal_review import assess_proposal
from agente_gestion.domain.snapshot import SnapshotCapture


class ProposalDryRunPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["SIMULATED", "denied"]
    reasons: tuple[str, ...] = ()
    field: str | None = None
    before: Any = None
    after: Any = None
    live_state_changed: Literal[False] = False


class ProposalTechnicalReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    review_id: str
    project_id: str
    proposal_id: str
    proposal_body_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capture_id: str
    technical_status: Literal["ready", "blocked"]
    technical_errors: tuple[str, ...] = ()
    dry_run: ProposalDryRunPreview
    human_approval_status: Literal["required"] = "required"
    execution_authorized: Literal[False] = False
    live_state_changed: Literal[False] = False
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _submission_digest(proposal: ActionProposal, evidence_items: list[EvidenceItem]) -> str:
    proposal_data = proposal.model_dump(mode="json", warnings="error")
    evidence_data = [item.model_dump(mode="json", warnings="error") for item in evidence_items]
    evidence_data = [item for item in evidence_data if item["provenance"]["id"] in proposal.evidence_ids]
    raw = json.dumps(
        {"proposal": proposal_data, "evidence": evidence_data},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def review_proposal_technically(
    proposal: ActionProposal,
    capture: SnapshotCapture,
    evidence_items: list[EvidenceItem],
    *,
    project_captures: list[SnapshotCapture] | None = None,
    proposal_body_sha256: str | None = None,
) -> ProposalTechnicalReview:
    """Validate immutable proposal facts and compute a no-write simulated preview."""
    digest = proposal_body_sha256 or _submission_digest(proposal, evidence_items)
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("proposal_body_sha256 must be a lowercase SHA-256 digest")

    errors = list(assess_proposal(proposal, capture, evidence_items).errors)
    captures = list(project_captures or [capture])
    by_id: dict[str, SnapshotCapture] = {}
    for item in captures:
        if item.snapshot.project_id != proposal.project_id:
            errors.append("project_mismatch")
        if item.capture_id != item.content_hash or re.fullmatch(r"[0-9a-f]{64}", item.capture_id) is None:
            errors.append("invalid_capture")
        old = by_id.get(item.capture_id)
        if old is not None and old != item:
            errors.append("capture_id_conflict")
        by_id[item.capture_id] = item
    if capture.capture_id not in by_id or by_id[capture.capture_id] != capture:
        errors.append("unknown_capture")
    if not by_id:
        errors.append("missing_captures")
    else:
        latest = max(_utc(item.snapshot.captured_at) for item in by_id.values())
        newest = [item.capture_id for item in by_id.values() if _utc(item.snapshot.captured_at) == latest]
        if len(newest) != 1:
            errors.append("ambiguous_latest_capture")
        elif newest[0] != capture.capture_id:
            errors.append("stale_capture")
    if capture.snapshot.tasks_complete is not True:
        errors.append("incomplete_task_source")
    if not capture.snapshot.task_source_identity or not capture.snapshot.task_source_identity.strip():
        errors.append("unknown_task_source")
    errors = list(dict.fromkeys(errors))
    status = "ready" if not errors else "blocked"
    dry_run = ProposalDryRunPreview(
        status="SIMULATED" if not errors else "denied",
        reasons=tuple(errors),
        field=proposal.field,
        before=proposal.current_value,
        after=proposal.proposed_value,
    )
    identity = "|".join((proposal.project_id, proposal.proposal_id, digest, capture.capture_id))
    review_id = "tech-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return ProposalTechnicalReview(
        review_id=review_id,
        project_id=proposal.project_id,
        proposal_id=proposal.proposal_id,
        proposal_body_sha256=digest,
        capture_id=capture.capture_id,
        technical_status=status,
        technical_errors=tuple(errors),
        dry_run=dry_run,
    )


class ProposalAutoReviewService:
    """Persist append-only technical assessments beside, not inside, human decisions."""

    def __init__(self, storage: Any):
        self.storage = storage

    def review_and_record(
        self,
        proposal: ActionProposal,
        capture: SnapshotCapture,
        evidence_items: list[EvidenceItem],
        *,
        project_captures: list[SnapshotCapture] | None = None,
    ) -> ProposalTechnicalReview:
        digest = None
        get_state = getattr(self.storage, "get_proposal_review_state", None)
        if callable(get_state):
            try:
                state = get_state(proposal.project_id, proposal.proposal_id)
            except NotImplementedError:
                state = None
            if state is not None:
                digest = state[0]
        review = review_proposal_technically(
            proposal, capture, evidence_items, project_captures=project_captures,
            proposal_body_sha256=digest,
        )
        connection_factory = getattr(self.storage, "_get_connection", None)
        if not callable(connection_factory):
            return review
        return self._append(connection_factory, review)

    @staticmethod
    def _append(connection_factory, review: ProposalTechnicalReview) -> ProposalTechnicalReview:
        raw = json.dumps(review.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        connection = connection_factory()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("""CREATE TABLE IF NOT EXISTS proposal_technical_reviews (
                    review_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    proposal_body_sha256 TEXT NOT NULL,
                    capture_id TEXT NOT NULL,
                    review_json TEXT NOT NULL,
                    review_sha256 TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    UNIQUE(project_id, proposal_id, proposal_body_sha256, capture_id)
                )""")
                for operation in ("UPDATE", "DELETE"):
                    connection.execute(f"""CREATE TRIGGER IF NOT EXISTS proposal_technical_reviews_no_{operation}
                        BEFORE {operation} ON proposal_technical_reviews
                        BEGIN SELECT RAISE(ABORT, 'Proposal technical reviews are append-only'); END""")
                row = connection.execute(
                    "SELECT review_json, review_sha256 FROM proposal_technical_reviews WHERE review_id=?",
                    (review.review_id,),
                ).fetchone()
                if row is not None:
                    if hashlib.sha256(row["review_json"].encode("utf-8")).hexdigest() != row["review_sha256"]:
                        raise ValueError("Stored technical review integrity mismatch")
                    stored = ProposalTechnicalReview.model_validate_json(row["review_json"])
                    if (
                        stored.project_id, stored.proposal_id, stored.proposal_body_sha256,
                        stored.capture_id, stored.technical_status, stored.technical_errors,
                        stored.dry_run.model_dump(mode="json"),
                    ) != (
                        review.project_id, review.proposal_id, review.proposal_body_sha256,
                        review.capture_id, review.technical_status, review.technical_errors,
                        review.dry_run.model_dump(mode="json"),
                    ):
                        raise ValueError("Stored technical review binding conflicts with current facts")
                    return stored
                connection.execute("""INSERT INTO proposal_technical_reviews
                    (review_id, project_id, proposal_id, proposal_body_sha256, capture_id,
                     review_json, review_sha256, recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (
                        review.review_id, review.project_id, review.proposal_id,
                        review.proposal_body_sha256, review.capture_id, raw, digest,
                        review.recorded_at.astimezone(timezone.utc).isoformat(),
                    ))
                return review
        except sqlite3.IntegrityError:
            raise ValueError("Conflicting immutable technical review") from None
        finally:
            connection.close()
