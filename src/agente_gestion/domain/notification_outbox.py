"""Domain models for scheduled report notification outbox and delivery receipts (H3)."""

from datetime import datetime, timezone
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


class OutboxMessage(BaseModel):
    """Structured notification message representing a weekly report ready for delivery."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    message_id: StrictStr
    project_id: StrictStr
    window_id: StrictStr
    report_id: StrictStr
    channel: Literal["file", "sqlite", "console", "all"]
    subject: StrictStr
    summary: StrictStr
    body_markdown: StrictStr
    metrics: dict[str, Any]
    risks: list[StrictStr] = Field(default_factory=list)
    action_ledger_summary: dict[str, Any] = Field(default_factory=dict)
    recipient: Optional[StrictStr] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("message_id", "project_id", "window_id", "report_id", "subject")
    @classmethod
    def nonblank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field must be non-blank")
        return v.strip()


class OutboxReceipt(BaseModel):
    """Receipt documenting delivery attempt, idempotency status, and stored artifact location."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    delivery_id: StrictStr
    message_id: StrictStr
    project_id: StrictStr
    window_id: StrictStr
    channel: StrictStr
    status: Literal["DELIVERED", "SKIPPED_DUPLICATE", "FAILED"]
    file_path: Optional[StrictStr] = None
    dispatched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    detail: StrictStr = ""

