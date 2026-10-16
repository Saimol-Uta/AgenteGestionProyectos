"""Bounded source-collection limits and honest budget receipts."""

import math
from pydantic import BaseModel, ConfigDict, Field, field_validator


MIB = 1024 * 1024
DEFAULT_MULTIREPO_BUDGETS = {
    "project_max_http_calls": 128,
    "repository_max_http_calls": 48,
    "project_max_files": 6000,
    "repository_max_files": 3000,
    "project_max_response_bytes": 64 * MIB,
    "repository_max_response_bytes": 30 * MIB,
    "project_timeout_seconds": 300.0,
    "repository_timeout_seconds": 180.0,
    "project_max_estimated_tokens": 100_000,
    "repository_max_estimated_tokens": 50_000,
}


class SourceBudgetReceipt(BaseModel):
    """Observed source-collection usage; token units are estimates, not model telemetry."""

    model_config = ConfigDict(frozen=True)

    http_calls: int = Field(default=0, ge=0)
    files: int = Field(default=0, ge=0)
    response_bytes: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0.0, ge=0.0)
    estimated_tokens: int = Field(default=0, ge=0)
    exhausted: bool = False
    token_estimate_note: str = (
        "Estimated from serialized source data at approximately three characters per token; "
        "not measured model usage."
    )

    @field_validator("elapsed_seconds")
    @classmethod
    def finite_elapsed(cls, value: float) -> float:
        if not math.isfinite(value) or value < 0.0:
            raise ValueError("elapsed_seconds must be finite and non-negative.")
        return value


__all__ = ["DEFAULT_MULTIREPO_BUDGETS", "MIB", "SourceBudgetReceipt"]
