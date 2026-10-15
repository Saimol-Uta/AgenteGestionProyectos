"""Contracts for human-approved, verifiable code branch actions."""

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class CodeSourceSnapshot(BaseModel):
    """Captured file content used only as a bounded patch base."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: str
    is_complete: bool = False
    repository: str | None = None
    commit_sha: str | None = None
    file_path: str | None = None
    source_sha256: str | None = None
    source_blob_sha: str | None = None
    total_lines: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_complete_source(self):
        if not self.is_complete:
            return self
        if (not self.repository or not self.file_path
                or not self.commit_sha or not _COMMIT_SHA_RE.fullmatch(self.commit_sha)
                or not self.source_sha256 or not _SHA256_RE.fullmatch(self.source_sha256)
                or hashlib.sha256(self.content.encode("utf-8")).hexdigest() != self.source_sha256
                or self.total_lines != len(self.content.splitlines())):
            raise ValueError("Complete source requires matching provenance, line boundaries and SHA-256")
        if self.source_blob_sha is not None and not _COMMIT_SHA_RE.fullmatch(self.source_blob_sha):
            raise ValueError("Source blob identifier is invalid")
        return self


class BranchExecutionResult(BaseModel):
    """Observable result of a live GitHub or explicitly local sandbox branch action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_mode: Literal["live", "sandbox"]
    branch_name: str = Field(min_length=1)
    branch_url: str = Field(min_length=1)
    commit_sha_applied: str = Field(min_length=1)
