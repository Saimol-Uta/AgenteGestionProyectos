"""Independent review of one exact corrective patch.

A review is bound to the content that would be applied: the patch identity is
the SHA-256 of the patched file together with the repository, commit and file
it applies to. A review of different content never authorizes anything, and a
review never turns into a permission on its own: a confirmed defect, a valid
syntax or approved tests are not a review of the correction.
"""

from enum import Enum
import hashlib
import re

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PatchReviewVerdict(str, Enum):
    """Outcome of an independent review of a corrective patch."""

    favorable = "favorable"
    unfavorable = "unfavorable"
    insufficient = "insufficient"


class PatchCandidate(BaseModel):
    """The exact patch a review would judge."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_id: StrictStr
    repository: StrictStr
    commit_sha: StrictStr
    file_path: StrictStr
    original_sha256: StrictStr
    patch_sha256: StrictStr
    original_excerpt: StrictStr
    suggested_code: StrictStr

    @field_validator("original_sha256", "patch_sha256")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Patch digests must be lowercase SHA-256 hex digests")
        return value


class PatchReviewOutcome(BaseModel):
    """The verdict of one review before it is recorded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: PatchReviewVerdict
    reason: StrictStr
    evidence_ids: list[StrictStr] = Field(default_factory=list)

    @field_validator("reason")
    @classmethod
    def nonempty_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A patch review requires a technical reason")
        return value


class PatchReview(BaseModel):
    """One recorded independent review of one exact patch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    review_id: StrictStr
    project_id: StrictStr
    run_id: StrictStr
    finding_id: StrictStr
    repository: StrictStr
    commit_sha: StrictStr
    file_path: StrictStr
    patch_sha256: StrictStr
    original_sha256: StrictStr
    verdict: PatchReviewVerdict
    reason: StrictStr
    reviewed_by: StrictStr
    evidence_ids: list[StrictStr] = Field(default_factory=list)
    reviewed_at: StrictStr

    @field_validator("patch_sha256", "original_sha256")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("Patch digests must be lowercase SHA-256 hex digests")
        return value

    @field_validator("reason", "reviewed_by")
    @classmethod
    def nonempty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A patch review requires a reason and a reviewer")
        return value


def effective_patch(
    source_content: str, original_excerpt: str, suggested_code: str,
) -> tuple[str, str]:
    """Return the patched content and its digest for one exact correction.

    The original fragment must occur exactly once: an ambiguous or stale
    fragment never produces an applicable patch, and the digest always
    describes the bytes that would be applied.
    """
    if not original_excerpt or source_content.count(original_excerpt) != 1:
        raise ValueError("The original fragment does not match exactly once")
    patched = source_content.replace(original_excerpt, suggested_code, 1)
    return patched, hashlib.sha256(patched.encode("utf-8")).hexdigest()


__all__ = [
    "PatchCandidate",
    "PatchReview",
    "PatchReviewOutcome",
    "PatchReviewVerdict",
    "effective_patch",
]
