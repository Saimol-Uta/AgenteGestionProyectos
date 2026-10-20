"""Contracts for human-approved, verifiable code branch actions."""

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agente_gestion.domain.repositories import RepositoryIdentity


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+$")


class FindingActionOrigin(BaseModel):
    """Stored finding origin bound to one run, repository, commit, and source context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    finding_id: str = Field(min_length=1, max_length=128)
    repository_identity: RepositoryIdentity | None = None
    repository: str | None = None
    commit_sha: str | None = None
    file_path: str | None = None
    source_sha256: str | None = None
    excerpt_sha256: str | None = None
    source_blob_sha: str | None = None
    base_branch: str | None = None
    configured_for_code_action: bool = False
    multi_repository: bool = False

    @model_validator(mode="after")
    def validate_origin(self) -> "FindingActionOrigin":
        if self.repository is not None:
            if not _REPOSITORY_RE.fullmatch(self.repository):
                raise ValueError("Finding action repository must be a full owner/repository name.")
            owner, name = self.repository.split("/", 1)
            if owner in {".", ".."} or name in {".", ".."}:
                raise ValueError("Finding action repository is invalid.")
            if self.repository_identity is not None and self.repository.casefold() != (
                f"{self.repository_identity.owner}/{self.repository_identity.repo}".casefold()
            ):
                raise ValueError("Finding action repository does not match its stored identity.")
        if self.commit_sha is not None and not _COMMIT_SHA_RE.fullmatch(self.commit_sha):
            raise ValueError("Finding action commit must be a full 40- or 64-character SHA.")
        for value, label in (
            (self.source_sha256, "source SHA-256"),
            (self.excerpt_sha256, "excerpt SHA-256"),
        ):
            if value is not None and not _SHA256_RE.fullmatch(value):
                raise ValueError(f"Finding action {label} is invalid.")
        if self.source_blob_sha is not None and not _COMMIT_SHA_RE.fullmatch(self.source_blob_sha):
            raise ValueError("Finding action source blob identifier is invalid.")
        if self.base_branch is not None and (
            not self.base_branch or any(character.isspace() for character in self.base_branch)
        ):
            raise ValueError("Finding action base branch is invalid.")
        if self.configured_for_code_action and not all((
            self.repository, self.commit_sha, self.file_path, self.base_branch,
        )):
            raise ValueError("A configured finding action requires repository, commit, file, and base branch.")
        return self


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


class PullRequestExecutionResult(BaseModel):
    """Observable result of one controlled pull request publication.

    A sandbox publication is always explicitly simulated: it carries the
    ``simulated`` state and a ``sandbox://`` URL, so it can never be read as a
    real pull request.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_mode: Literal["live", "sandbox"]
    pull_request_number: int = Field(ge=1)
    pull_request_url: str = Field(min_length=1, max_length=512)
    state: Literal["open", "simulated"]
    repository: str = Field(min_length=1)
    base_branch: str = Field(min_length=1)
    head_branch: str = Field(min_length=1)
    head_commit_sha: str = Field(min_length=1)
    recovered: bool = False

    @model_validator(mode="after")
    def validate_publication(self) -> "PullRequestExecutionResult":
        if not _REPOSITORY_RE.fullmatch(self.repository):
            raise ValueError("Pull request repository must be a full owner/repository name.")
        if not _COMMIT_SHA_RE.fullmatch(self.head_commit_sha):
            raise ValueError("Pull request head commit must be a full 40- or 64-character SHA.")
        for value, label in ((self.base_branch, "base"), (self.head_branch, "head")):
            if (not value or len(value) > 240 or any(character.isspace() for character in value)
                    or value.startswith("/") or value.endswith(".") or ".." in value):
                raise ValueError(f"Pull request {label} branch is invalid.")
        if self.execution_mode == "sandbox":
            if self.state != "simulated" or not self.pull_request_url.startswith("sandbox://"):
                raise ValueError("A sandbox pull request must be explicitly simulated.")
        elif self.state != "open" or not self.pull_request_url.startswith("https://"):
            raise ValueError("A live pull request must be open and carry an https URL.")
        return self
