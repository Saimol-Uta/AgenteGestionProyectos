"""Independent review of a corrective patch, bound to the exact content applied.

The service prepares the exact patch from the complete source, asks the
independent verifier to judge the correction, and records the verdict together
with the digest of the patch. The recorded review is the only thing that can
unlock an approval, and it only applies to the content it judged.
"""

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


def _region(patched: str, suggested_code: str, window: int = 12) -> str:
    """The corrected region with a bounded window of its surroundings."""
    index = patched.find(suggested_code)
    if index < 0:
        return patched[: 2000]
    start = max(0, index - 1200)
    end = min(len(patched), index + len(suggested_code) + 1200)
    return patched[start:end]


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
                connection.execute(
                    "INSERT OR IGNORE INTO patch_reviews (review_id, project_id, run_id, finding_id,"
                    " repository, commit_sha, file_path, patch_sha256, original_sha256, review_json,"
                    " review_sha256, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        review.review_id, review.project_id, review.run_id, review.finding_id,
                        review.repository, review.commit_sha, review.file_path, review.patch_sha256,
                        review.original_sha256, raw, digest, review.reviewed_at,
                    ),
                )
        finally:
            connection.close()
        return review


def _read_reviews(storage: Any, project_id: str, finding_id: str) -> list[PatchReview]:
    factory = getattr(storage, "_get_connection", None)
    if not callable(factory):
        return []
    connection = factory()
    try:
        try:
            rows = connection.execute(
                "SELECT review_json, review_sha256 FROM patch_reviews WHERE project_id=? AND finding_id=?"
                " ORDER BY rowid DESC",
                (project_id, finding_id),
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


def latest_patch_review(storage: Any, project_id: str, finding_id: str) -> Optional[PatchReview]:
    """The most recent recorded review of the corrections proposed for a finding."""
    reviews = _read_reviews(storage, project_id, finding_id)
    return reviews[0] if reviews else None


def favorable_patch_review(
    storage: Any,
    *,
    project_id: str,
    finding_id: str,
    patch_sha256: str,
    repository: str,
    commit_sha: str,
    file_path: str,
) -> Optional[PatchReview]:
    """The current favorable review of exactly this patch, if there is one.

    The most recent review of that exact patch governs: a later unfavorable or
    insufficient review withdraws the earlier favorable one.
    """
    for review in _read_reviews(storage, project_id, finding_id):
        if (
            review.patch_sha256 == patch_sha256
            and review.repository == repository
            and review.commit_sha == commit_sha
            and review.file_path == file_path
        ):
            return review if review.verdict == PatchReviewVerdict.favorable else None
    return None


__all__ = [
    "PatchReviewError",
    "PatchReviewService",
    "favorable_patch_review",
    "latest_patch_review",
]
