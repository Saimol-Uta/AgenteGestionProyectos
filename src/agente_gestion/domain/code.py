"""Code diff snippets and CI run entities with exact commit matching."""

import ast
from datetime import datetime
import hashlib
import json
import re
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def validate_source_syntax(file_path: str, content: str) -> Literal["valid", "invalid", "unsupported"]:
    """Validate supported source syntax without executing project code."""
    suffix = file_path.rsplit(".", 1)[-1].lower() if "." in file_path.rsplit("/", 1)[-1] else ""
    if suffix in {"py", "pyw"}:
        try:
            ast.parse(content, filename=file_path)
        except (SyntaxError, ValueError):
            return "invalid"
        return "valid"
    if suffix == "json":
        try:
            json.loads(content, parse_constant=_reject_json_constant)
        except ValueError:
            return "invalid"
        return "valid"
    return "unsupported"


class CodeDiffSnippet(BaseModel):
    """Scoped snippet of code changes from a commit or PR."""
    repository: str = Field(description="Repository full name or ID")
    pull_request_id: Optional[str] = Field(default=None, description="PR ID if associated")
    commit_sha: str = Field(description="Exact commit SHA where snippet originates")
    file_path: str = Field(description="Path to modified file")
    diff_side: Optional[str] = Field(default="NEW", description="Diff side: NEW or OLD")
    lines: list[int] = Field(description="Line numbers included in the snippet")
    content: str = Field(description="Content lines of the snippet")
    evidence_scope: Literal["full_file", "snippet", "diff", "truncated"] = "snippet"
    is_complete: bool = Field(default=False, description="True only for a verified complete file")
    start_line: Optional[int] = Field(default=None, ge=1)
    end_line: Optional[int] = Field(default=None, ge=1)
    total_lines: Optional[int] = Field(default=None, ge=0)
    truncated: bool = False
    source_sha256: Optional[str] = Field(default=None, description="SHA-256 of the exact complete source bytes")
    source_blob_sha: Optional[str] = Field(default=None, description="Provider blob identifier at the pinned commit")

    @model_validator(mode="after")
    def validate_coverage(self):
        # Diff line sets may be disjoint: the range is only an envelope, not coverage.
        if any(line < 1 for line in self.lines) or self.lines != sorted(set(self.lines)):
            raise ValueError("Evidence lines must be positive, unique and ordered")
        first = min(self.lines) if self.lines else None
        last = max(self.lines) if self.lines else None
        if self.start_line is not None and self.start_line != first:
            raise ValueError("start_line must match visible lines")
        if self.end_line is not None and self.end_line != last:
            raise ValueError("end_line must match visible lines")
        self.start_line, self.end_line = first, last
        if self.total_lines is not None and last is not None and last > self.total_lines:
            raise ValueError("Visible lines exceed total_lines")
        if self.source_sha256 is not None:
            if (not _SHA256_RE.fullmatch(self.source_sha256)
                    or hashlib.sha256(self.content.encode("utf-8")).hexdigest() != self.source_sha256):
                raise ValueError("source_sha256 must match the exact content")
        if self.source_blob_sha is not None and not _SHA_RE.fullmatch(self.source_blob_sha):
            raise ValueError("source_blob_sha must be a full hexadecimal object identifier")
        if self.evidence_scope == "truncated":
            self.truncated = True
        if self.is_complete or self.evidence_scope == "full_file":
            if (self.evidence_scope != "full_file" or not self.is_complete or self.truncated
                    or self.total_lines is None
                    or self.lines != list(range(1, self.total_lines + 1))
                    or len(self.content.splitlines()) != self.total_lines):
                raise ValueError("Complete file requires untruncated content and every file line")
        return self



class CIRunResult(BaseModel):
    """Result of automated CI workflow run for a specific commit."""
    id: str = Field(description="CI workflow run identifier")
    workflow_name: str = Field(description="Name of CI workflow e.g. tests, lint")
    commit_sha: str = Field(description="Exact commit SHA evaluated by this CI run")
    status: str = Field(description="Status e.g. success, failure, neutral, cancelled")
    total_tests: Optional[int] = Field(default=None, description="Total tests run if reported")
    failed_tests: Optional[int] = Field(default=None, description="Failed tests count if reported")
    coverage_percentage: Optional[float] = Field(default=None, description="Reported test coverage percentage")
    timestamp: Optional[datetime] = Field(default=None, description="Completion timestamp")


def match_ci_for_commit(ci_runs: list[CIRunResult], commit_sha: str) -> Optional[CIRunResult]:
    """
    Returns the CI run result strictly matching the target commit SHA.
    Enforces invariant: a CI run for a different SHA never certifies the current change.
    """
    for run in ci_runs:
        if run.commit_sha == commit_sha:
            return run
    return None
