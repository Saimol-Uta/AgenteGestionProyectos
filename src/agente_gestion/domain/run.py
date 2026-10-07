"""Execution run models, stage lifecycle tracking, and hashing."""

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    queued = "queued"
    collecting = "collecting"
    analyzing = "analyzing"
    awaiting_human = "awaiting_human"
    completed = "completed"
    needs_attention = "needs_attention"
    failed = "failed"
    cancelled = "cancelled"


class StageStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    skipped = "skipped"
    failed = "failed"
    needs_attention = "needs_attention"


class StageExecutionRecord(BaseModel):
    """Execution status and audit metadata for an individual pipeline stage."""
    stage_name: str = Field(description="Stage identifier e.g. monitoring, quality, verifier, report")
    status: StageStatus = Field(default=StageStatus.pending)
    duration_seconds: float = Field(default=0.0)
    output_summary: Optional[str] = None
    error_message: Optional[str] = None
    data_hash: Optional[str] = None
    completed_at: Optional[datetime] = None


class ExecutionRun(BaseModel):
    """Complete lifecycle state of a project analysis run."""
    run_id: str = Field(description="Unique run identifier")
    project_id: str
    status: RunStatus = Field(default=RunStatus.queued)
    created_at: datetime
    updated_at: datetime
    snapshot_id: str
    config_hash: str
    snapshot_hash: str
    policy_version: str = Field(default="v1")
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    schema_versions: dict[str, str] = Field(default_factory=dict)
    stages: dict[str, StageExecutionRecord] = Field(default_factory=dict)
    duration_seconds: float = Field(default=0.0)
    error_message: Optional[str] = None
    report_id: Optional[str] = None
    is_simulated: bool = Field(default=False)


def compute_sha256(data: Any) -> str:
    """Computes a stable deterministic SHA256 hex digest for any serializable payload."""
    if isinstance(data, str):
        payload_bytes = data.encode("utf-8")
    elif isinstance(data, (bytes, bytearray)):
        payload_bytes = bytes(data)
    elif hasattr(data, "model_dump_json"):
        payload_bytes = data.model_dump_json().encode("utf-8")
    elif isinstance(data, dict):
        payload_bytes = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    else:
        payload_bytes = str(data).encode("utf-8")
    return hashlib.sha256(payload_bytes).hexdigest()
