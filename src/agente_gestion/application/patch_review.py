"""Independent review of a corrective patch, bound to the exact content applied.

The service prepares the exact patch from the complete source, asks the
independent verifier to judge the correction, and records the verdict together
with the digest of the patch. The recorded review is the only thing that can
unlock an approval, and it only applies to the content it judged: the same
execution, repository, file, origin commit and original source.

This module also owns the authorization policy that the decision gate and the
read-only eligibility service share, so no interface can interpret the rules
on its own: a confirmed defect, an exact patch and an independent favorable
review of that patch are required, and a missing or changed link blocks.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Optional

from agente_gestion.domain.code_action import CodeSourceSnapshot
from agente_gestion.domain.findings import CandidateFinding
from agente_gestion.domain.patch_review import (
    PatchCandidate,
    PatchReview,
    PatchReviewOutcome,
    PatchReviewVerdict,
    effective_patch,
)


class PatchReviewError(RuntimeError):
    """Stable error returned when a patch cannot be prepared or reviewed."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


REVIEW_CONFLICT_CODE = "revision_en_conflicto"

# Stable reasons that block an approval. They describe the missing link of the
# chain, never a weaker authorization.
BLOCK_CONTEXT_NOT_VERIFIABLE = "fuente_no_verificable"
BLOCK_DEFECT_NOT_CONFIRMED = "defecto_no_confirmado"
BLOCK_SUGGESTED_CODE_REQUIRED = "codigo_sugerido_requerido"
BLOCK_PATCH_NOT_IDENTIFIABLE = "parche_no_identificable"
BLOCK_REVIEW_MISSING = "revision_inexistente"
BLOCK_REVIEW_NOT_APPLICABLE = "revision_no_corresponde"
BLOCK_REVIEW_UNFAVORABLE = "revision_desfavorable"
BLOCK_REVIEW_INSUFFICIENT = "revision_insuficiente"


@dataclass(frozen=True)
class PatchReviewMatch:
    """The latest recorded review that covers one exact patch identity."""

    review: Optional[PatchReview] = None
    blocked_reason: Optional[str] = None


@dataclass(frozen=True)
class ApprovalEligibility:
    """The authorization fact of one finding, shared by every caller."""

    eligible: bool
    authorized: bool
    context_valid: bool
    defect_confirmed: bool
    blocked_reason: Optional[str]
    authorization_reason: Optional[str]
    patch_sha256: Optional[str]
    review_id: Optional[str]
    review: Optional[PatchReview]


def evaluate_approval_eligibility(
    *,
    context_valid: bool,
    defect_confirmed: bool,
    patch_sha256: Optional[str],
    matched_review: Optional[PatchReview] = None,
    review_blocked_reason: Optional[str] = None,
    patch_blocked_reason: Optional[str] = None,
) -> ApprovalEligibility:
    """Decide whether an approval is authorized. Pure: no storage, no clock.

    The chain is the one of the operation: a confirmed defect, an exact patch,
    and an independent favorable review of that patch. The context of the
    finding must be verifiable as well. A missing link blocks the approval and
    reports why; it never weakens the rule.
    """
    reason: Optional[str] = None
    if not defect_confirmed:
        reason = BLOCK_DEFECT_NOT_CONFIRMED
    elif patch_sha256 is None:
        reason = patch_blocked_reason or BLOCK_PATCH_NOT_IDENTIFIABLE
    elif matched_review is None:
        reason = review_blocked_reason or BLOCK_REVIEW_MISSING
    elif matched_review.verdict == PatchReviewVerdict.unfavorable:
        reason = BLOCK_REVIEW_UNFAVORABLE
    elif matched_review.verdict == PatchReviewVerdict.insufficient:
        reason = BLOCK_REVIEW_INSUFFICIENT
    authorized = reason is None
    eligible = authorized and context_valid
    if eligible:
        blocked_reason = None
    elif not context_valid:
        blocked_reason = BLOCK_CONTEXT_NOT_VERIFIABLE
    else:
        blocked_reason = reason
    return ApprovalEligibility(
        eligible=eligible,
        authorized=authorized,
        context_valid=context_valid,
        defect_confirmed=defect_confirmed,
        blocked_reason=blocked_reason,
        authorization_reason=reason,
        patch_sha256=patch_sha256,
        review_id=matched_review.review_id if matched_review is not None else None,
        review=matched_review,
    )


def _region(patched: str, suggested_code: str, window: int = 12) -> str:
    """The corrected region with a bounded window of its surroundings."""
    index = patched.find(suggested_code)
    if index < 0:
        return patched[: 2000]
    start = max(0, index - 1200)
    end = min(len(patched), index + len(suggested_code) + 1200)
    return patched[start:end]


def _same_review_identity(left: PatchReview, right: PatchReview) -> bool:
    """Whether two reviews describe the same review, ignoring the instant."""
    return (
        left.project_id == right.project_id
        and left.run_id == right.run_id
        and left.finding_id == right.finding_id
        and left.repository.casefold() == right.repository.casefold()
        and left.commit_sha.casefold() == right.commit_sha.casefold()
        and left.file_path == right.file_path
        and left.patch_sha256 == right.patch_sha256
        and left.original_sha256 == right.original_sha256
        and left.verdict == right.verdict
        and left.reason == right.reason
        and left.reviewed_by == right.reviewed_by
        and list(left.evidence_ids) == list(right.evidence_ids)
    )


class PatchReviewService:
    """Prepares, reviews and records the independent review of a patch."""

    def __init__(self, storage: Any, verifier: Any, context_builder: Any = None):
        self._storage = storage
        self._verifier = verifier
        self._context_builder = context_builder

    def review(
        self,
        *,
        project_id: str,
        run_id: str,
        finding: CandidateFinding,
        source: Optional[CodeSourceSnapshot],
        original_excerpt: str,
        suggested_code: str,
        repository: str,
        commit_sha: str,
        file_path: str,
        evidence_ids: Optional[list[str]] = None,
    ) -> PatchReview:
        """Review one exact patch and record the verdict."""
        if source is None or not source.is_complete or not source.content:
            raise PatchReviewError(
                "fuente_no_verificable",
                "No hay un archivo completo y verificable para revisar la correccion.",
            )
        if not suggested_code.strip():
            raise PatchReviewError(
                "codigo_sugerido_requerido",
                "El hallazgo no contiene una correccion candidata que revisar.",
            )
        try:
            patched, patch_sha256 = effective_patch(source.content, original_excerpt, suggested_code)
        except ValueError as exc:
            raise PatchReviewError("parche_obsoleto_o_ambiguo", str(exc)) from None
        original_sha256 = hashlib.sha256(source.content.encode("utf-8")).hexdigest()
        candidate = PatchCandidate(
            finding_id=finding.id,
            repository=repository,
            commit_sha=commit_sha,
            file_path=file_path,
            original_sha256=original_sha256,
            patch_sha256=patch_sha256,
            original_excerpt=original_excerpt,
            suggested_code=suggested_code,
        )
        outcome = self._verifier.review_patch(
            project_id=project_id,
            finding=finding,
            patch=candidate,
            patched_region=_region(patched, suggested_code),
            evidence_ids=list(evidence_ids or []),
        )
        if not isinstance(outcome, PatchReviewOutcome):
            raise PatchReviewError(
                "revision_no_disponible",
                "El verificador independiente no devolvio un veredicto utilizable.",
            )
        review = PatchReview(
            review_id=self._review_id(project_id, run_id, finding.id, patch_sha256, outcome),
            project_id=project_id,
            run_id=run_id,
            finding_id=finding.id,
            repository=repository,
            commit_sha=commit_sha,
            file_path=file_path,
            patch_sha256=patch_sha256,
            original_sha256=original_sha256,
            verdict=outcome.verdict,
            reason=outcome.reason,
            reviewed_by="verifier_agent",
            evidence_ids=list(outcome.evidence_ids),
            reviewed_at=datetime.now(timezone.utc).isoformat(),
        )
        return self._append(review)

    @staticmethod
    def _review_id(
        project_id: str, run_id: str, finding_id: str, patch_sha256: str, outcome: PatchReviewOutcome,
    ) -> str:
        canonical = json.dumps(
            [project_id, run_id, finding_id, patch_sha256, outcome.verdict.value, outcome.reason],
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        return "patch-review-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]

    def _append(self, review: PatchReview) -> PatchReview:
        """Record the review, or return the persisted row when it already exists.

        The table is append-only, so a replay must never overwrite anything.
        A row with the same identifier but different content is a conflict that
        is refused, not ignored.
        """
        raw = json.dumps(review.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        factory = getattr(self._storage, "_get_connection", None)
        if not callable(factory):
            return review
        connection = factory()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("""CREATE TABLE IF NOT EXISTS patch_reviews (
                    review_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL,
                    repository TEXT NOT NULL,
                    commit_sha TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    patch_sha256 TEXT NOT NULL,
                    original_sha256 TEXT NOT NULL,
                    review_json TEXT NOT NULL,
                    review_sha256 TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                )""")
                for operation in ("UPDATE", "DELETE"):
                    connection.execute(f"""CREATE TRIGGER IF NOT EXISTS patch_reviews_no_{operation}
                        BEFORE {operation} ON patch_reviews
                        BEGIN SELECT RAISE(ABORT, 'Patch reviews are append-only'); END""")
                stored = connection.execute(
                    "SELECT review_json, review_sha256 FROM patch_reviews WHERE review_id=?",
                    (review.review_id,),
                ).fetchone()
                if stored is None:
                    connection.execute(
                        "INSERT INTO patch_reviews (review_id, project_id, run_id, finding_id,"
                        " repository, commit_sha, file_path, patch_sha256, original_sha256, review_json,"
                        " review_sha256, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            review.review_id, review.project_id, review.run_id, review.finding_id,
                            review.repository, review.commit_sha, review.file_path, review.patch_sha256,
                            review.original_sha256, raw, digest, review.reviewed_at,
                        ),
                    )
                    return review
                stored_raw, stored_digest = stored
                if stored_digest != digest:
                    persisted = PatchReview.model_validate_json(stored_raw)
                    if not _same_review_identity(persisted, review):
                        raise PatchReviewError(
                            REVIEW_CONFLICT_CODE,
                            "Ya existe una revision con el mismo identificador y otro contenido.",
                        )
                # The persisted row is the authority: a replay returns it exactly.
                return PatchReview.model_validate_json(stored_raw)
        finally:
            connection.close()


def _read_reviews(
    storage: Any, project_id: str, finding_id: str, run_id: Optional[str] = None,
) -> list[PatchReview]:
    factory = getattr(storage, "_get_connection", None)
    if not callable(factory):
        return []
    connection = factory()
    try:
        try:
            if run_id is None:
                rows = connection.execute(
                    "SELECT review_json, review_sha256 FROM patch_reviews WHERE project_id=? AND finding_id=?"
                    " ORDER BY rowid DESC",
                    (project_id, finding_id),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT review_json, review_sha256 FROM patch_reviews"
                    " WHERE project_id=? AND run_id=? AND finding_id=? ORDER BY rowid DESC",
                    (project_id, run_id, finding_id),
                ).fetchall()
        except Exception:
            return []
        reviews: list[PatchReview] = []
        for raw, digest in rows:
            if hashlib.sha256(raw.encode("utf-8")).hexdigest() != digest:
                raise ValueError("Stored patch review integrity mismatch")
            reviews.append(PatchReview.model_validate_json(raw))
        return reviews
    finally:
        connection.close()


def latest_patch_review(
    storage: Any, project_id: str, finding_id: str, run_id: Optional[str] = None,
) -> Optional[PatchReview]:
    """The most recent recorded review of the corrections proposed for a finding.

    Without a run the result may come from any execution, so it must never be
    used to authorize anything.
    """
    reviews = _read_reviews(storage, project_id, finding_id, run_id=run_id)
    return reviews[0] if reviews else None


def _review_covers_identity(
    review: PatchReview,
    *,
    project_id: str,
    run_id: str,
    finding_id: str,
    patch_sha256: str,
    original_sha256: str,
    repository: str,
    commit_sha: str,
    file_path: str,
) -> bool:
    """Whether the review judged exactly this execution, source and patch."""
    return (
        review.project_id == project_id
        and review.run_id == run_id
        and review.finding_id == finding_id
        and review.patch_sha256 == patch_sha256
        and review.original_sha256 == original_sha256
        and review.repository.casefold() == (repository or "").casefold()
        and review.commit_sha.casefold() == (commit_sha or "").casefold()
        and review.file_path == file_path
    )


def match_patch_review(
    storage: Any,
    *,
    project_id: str,
    run_id: str,
    finding_id: str,
    patch_sha256: str,
    original_sha256: str,
    repository: str,
    commit_sha: str,
    file_path: str,
) -> PatchReviewMatch:
    """The latest run-scoped review that covers exactly this patch identity.

    A review of another execution never applies, and a review of another
    patch, file, repository, commit or original source covers something else.
    """
    reviews = _read_reviews(storage, project_id, finding_id, run_id=run_id)
    if not reviews:
        return PatchReviewMatch(None, BLOCK_REVIEW_MISSING)
    for review in reviews:
        if _review_covers_identity(
            review,
            project_id=project_id, run_id=run_id, finding_id=finding_id,
            patch_sha256=patch_sha256, original_sha256=original_sha256,
            repository=repository, commit_sha=commit_sha, file_path=file_path,
        ):
            return PatchReviewMatch(review, None)
    return PatchReviewMatch(None, BLOCK_REVIEW_NOT_APPLICABLE)


def favorable_patch_review(
    storage: Any,
    *,
    project_id: str,
    run_id: str,
    finding_id: str,
    patch_sha256: str,
    original_sha256: str,
    repository: str,
    commit_sha: str,
    file_path: str,
) -> Optional[PatchReview]:
    """The current favorable review of exactly this patch, if there is one.

    The review is bound to one execution, repository, file, origin commit and
    original source, and the most recent review of that exact patch governs:
    a later unfavorable or insufficient review withdraws the earlier favorable
    one.
    """
    match = match_patch_review(
        storage,
        project_id=project_id, run_id=run_id, finding_id=finding_id,
        patch_sha256=patch_sha256, original_sha256=original_sha256,
        repository=repository, commit_sha=commit_sha, file_path=file_path,
    )
    if match.review is None:
        return None
    return match.review if match.review.verdict == PatchReviewVerdict.favorable else None


def confirmed_defect_verification(
    storage: Any, *, run_id: str, finding_id: str,
) -> Optional[dict[str, Any]]:
    """The independent confirmation of the defect recorded for this run.

    A confirmed defect is a necessary link of the chain, never a substitute
    for the review of the correction.
    """
    getter = getattr(storage, "get_verifications", None)
    if not callable(getter):
        return None
    for row in getter(run_id) or []:
        if not isinstance(row, dict) or row.get("finding_id") != finding_id:
            continue
        verdict = str(row.get("verdict") or "").strip().casefold()
        verified_by = str(row.get("verified_by") or "").strip()
        if verdict == "confirmed" and verified_by:
            return row
    return None


__all__ = [
    "ApprovalEligibility",
    "BLOCK_CONTEXT_NOT_VERIFIABLE",
    "BLOCK_DEFECT_NOT_CONFIRMED",
    "BLOCK_PATCH_NOT_IDENTIFIABLE",
    "BLOCK_REVIEW_INSUFFICIENT",
    "BLOCK_REVIEW_MISSING",
    "BLOCK_REVIEW_NOT_APPLICABLE",
    "BLOCK_REVIEW_UNFAVORABLE",
    "BLOCK_SUGGESTED_CODE_REQUIRED",
    "PatchReviewError",
    "PatchReviewMatch",
    "PatchReviewService",
    "REVIEW_CONFLICT_CODE",
    "confirmed_defect_verification",
    "evaluate_approval_eligibility",
    "favorable_patch_review",
    "latest_patch_review",
    "match_patch_review",
]
