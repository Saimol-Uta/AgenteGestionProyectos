"""LLM provider contracts, request/response models, and capabilities."""

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class GenerationStatus(str, Enum):
    complete = "complete"
    incomplete = "incomplete"
    refused = "refused"
    error = "error"


class GenerationRequest(BaseModel):
    """Normalized request passed to any LLM provider adapter."""
    role: str = Field(description="Role name e.g. elaborador, revisor, aprobador")
    instructions: str = Field(description="System instructions and specific task guidelines")
    evidences: dict[str, Any] = Field(default_factory=dict, description="Fact and evidence payload")
    schema_name: Optional[str] = Field(default=None, description="Schema identifier or name")
    json_schema: Optional[dict[str, Any]] = Field(default=None, description="Expected JSON Schema for structured output")
    model: str = Field(description="Model identifier")
    max_generation_tokens: int = Field(default=768, description="Output token generation limit")
    timeout_seconds: float = Field(default=60.0, description="Per-request timeout")
    options: dict[str, Any] = Field(default_factory=dict, description="Provider-specific tuning options")


class GenerationResult(BaseModel):
    """Normalized response produced by an LLM provider adapter."""
    structured_data: Optional[dict[str, Any]] = Field(
        default=None,
        description="Parsed and validated structured output, or None on failure"
    )
    status: GenerationStatus = Field(description="Lifecycle status of generation")
    finish_reason: Optional[str] = Field(default=None, description="stop, length, content_filter, etc.")
    provider: str = Field(description="Provider adapter name e.g. gemini, groq, fake_simulated")
    model: str = Field(description="Model identifier actually used")
    duration_seconds: float = Field(description="Request execution latency in seconds")
    prompt_tokens: Optional[int] = Field(
        default=None,
        description="Prompt token count; None if unreported (never zero unless explicitly reported as 0)"
    )
    completion_tokens: Optional[int] = Field(
        default=None,
        description="Completion token count; None if unreported"
    )
    cost_estimate: Optional[float] = Field(
        default=None,
        description="Estimated monetary cost; None if pricing not configured"
    )
    request_ref: Optional[str] = Field(default=None, description="Correlation identifier")
    error_message: Optional[str] = Field(default=None, description="Error or refusal details if status is not complete")
    raw_text: Optional[str] = Field(default=None, description="Raw model text before parsing")


class LLMCallAttempt(BaseModel):
    """Execution record for each LLM call or repair attempt for audit and T3 storage."""
    attempt_id: str = Field(description="Unique identifier of this call attempt")
    request_ref: Optional[str] = Field(default=None, description="Correlation identifier")
    stage: str = Field(description="Stage or role being executed e.g. monitoring, quality, verifier, report")
    attempt_number: int = Field(default=1, description="Sequential attempt count for this stage invocation")
    provider: str = Field(description="Provider adapter name e.g. gemini, groq, fake_simulated")
    model: str = Field(description="Model used")
    status: GenerationStatus = Field(description="Generation result status")
    finish_reason: Optional[str] = Field(default=None, description="Finish reason e.g. stop, length, SAFETY")
    duration_seconds: float = Field(default=0.0, description="Latency in seconds")
    prompt_tokens: Optional[int] = Field(default=None, description="Prompt tokens if available")
    completion_tokens: Optional[int] = Field(default=None, description="Completion tokens if available")
    validation_passed: bool = Field(default=True, description="Whether semantic domain validation succeeded")
    validation_errors: list[str] = Field(default_factory=list, description="Validation error messages if failed")
    error_message: Optional[str] = Field(default=None, description="Provider or system error message")
    raw_response: Optional[str] = Field(default=None, description="Raw text response")
    structured_data: Optional[dict[str, Any]] = Field(default=None, description="Structured parsed data if present")


class ModelCapabilities(BaseModel):
    """Advertised capabilities for a given provider model."""
    context_tokens: int = Field(description="Maximum context window size in tokens")
    max_output_tokens: int = Field(description="Maximum generation limit in tokens")
    supports_json_schema: bool = Field(default=True, description="Whether structured JSON output is supported natively")
    supports_tools: bool = Field(default=False, description="Whether tool/function calling is supported")
    supports_streaming: bool = Field(default=False, description="Whether streaming responses are supported")
    reasoning_options: bool = Field(default=False, description="Whether reasoning/thinking parameters can be toggled")
