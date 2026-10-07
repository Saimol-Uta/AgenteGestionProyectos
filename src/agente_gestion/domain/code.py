"""Code diff snippets and CI run entities with exact commit matching."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class CodeDiffSnippet(BaseModel):
    """Scoped snippet of code changes from a commit or PR."""
    repository: str = Field(description="Repository full name or ID")
    pull_request_id: Optional[str] = Field(default=None, description="PR ID if associated")
    commit_sha: str = Field(description="Exact commit SHA where snippet originates")
    file_path: str = Field(description="Path to modified file")
    diff_side: Optional[str] = Field(default="NEW", description="Diff side: NEW or OLD")
    lines: list[int] = Field(description="Line numbers included in the snippet")
    content: str = Field(description="Content lines of the snippet")


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
