"""Contracts for source collection results, audit records, and composition port."""

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
import math
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agente_gestion.domain.evidence import EvidenceItem
from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.ports.source_errors import (
    SourceError,
    SourceErrorCategory,
    SourceProvider,
)


class SourceExecutionStatus(str, Enum):
    """Execution status of an individual source retrieval."""
    success = "success"
    failed = "failed"


class SourceErrorInfo(BaseModel):
    """Sanitized structured error information for an execution record."""
    model_config = ConfigDict(frozen=True)

    provider: SourceProvider
    category: SourceErrorCategory
    message: str = Field(default="")

    @model_validator(mode="before")
    @classmethod
    def _sanitize_input(cls, data: Any) -> Any:
        if isinstance(data, dict) and "message" in data:
            data = dict(data)
            data["message"] = ""
        return data

    @model_validator(mode="after")
    def _set_canonical_message(self) -> "SourceErrorInfo":
        canonical = f"Source '{self.provider.value}' failed with error category '{self.category.value}'."
        object.__setattr__(self, "message", canonical)
        return self


class SourceExecutionRecord(BaseModel):
    """Audit record for a single source provider execution."""
    provider: SourceProvider
    status: SourceExecutionStatus
    duration_seconds: float = Field(
        description="Execution duration in seconds; must be finite and non-negative."
    )
    attempt_count: int = Field(
        default=1,
        ge=1,
        description="Number of attempts made (must be >= 1 for executed operations).",
    )
    error: Optional[SourceErrorInfo] = Field(
        default=None,
        description="Sanitized error information if the source execution failed.",
    )

    @field_validator("duration_seconds")
    @classmethod
    def validate_duration(cls, v: float) -> float:
        if not math.isfinite(v) or v < 0.0:
            raise ValueError("duration_seconds must be a finite non-negative number")
        return v

    @field_validator("error", mode="before")
    @classmethod
    def coerce_error(cls, v: Any) -> Any:
        if isinstance(v, SourceError):
            return SourceErrorInfo(
                provider=v.provider,
                category=v.category,
            )
        return v

    @model_validator(mode="after")
    def validate_consistency(self) -> "SourceExecutionRecord":
        if self.status == SourceExecutionStatus.success:
            if self.error is not None:
                raise ValueError("Successful source execution cannot have an error attached.")
        elif self.status == SourceExecutionStatus.failed:
            if self.error is None:
                raise ValueError("Failed source execution must have an error attached.")
            if self.error.provider != self.provider:
                raise ValueError("Execution record provider and error provider must match.")
        return self


class SourceCollectionResult(BaseModel):
    """Composite outcome of collecting data across all configured sources."""
    snapshot: WeeklySnapshot
    evidence: list[EvidenceItem] = Field(default_factory=list)
    records: list[SourceExecutionRecord] = Field(default_factory=list)
    target_commit_sha: Optional[str] = Field(
        default=None,
        description="Optional pinned target commit SHA across source queries.",
    )

    @model_validator(mode="after")
    def validate_completeness(self) -> "SourceCollectionResult":
        if self.snapshot.is_complete:
            for record in self.records:
                if record.status != SourceExecutionStatus.success or record.error is not None:
                    raise ValueError(
                        f"A complete snapshot cannot accompany an incomplete or failed record: "
                        f"provider={record.provider.value}, status={record.status.value}"
                    )
        return self


class SourceCollectionPort(ABC):
    """Port interface for orchestrating snapshot and evidence collection across sources."""

    @abstractmethod
    def collect_snapshot(
        self,
        project_id: str,
        cutoff_datetime: datetime,
        commit_sha: Optional[str] = None,
    ) -> SourceCollectionResult:
        """Retrieve complete or partial snapshot, evidence items, and execution audit records."""
        raise NotImplementedError
