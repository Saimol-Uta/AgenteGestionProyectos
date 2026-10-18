"""Structured stage invocation, budget management, bounded retries, and attempt tracing."""

import time
import uuid
from typing import Any, Callable, Optional

from agente_gestion.domain.llm import (
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
    LLMCallAttempt,
)
from agente_gestion.ports.llm import LLMProviderPort
from agente_gestion.application.validation import ValidationError


class GlobalBudgetExceededError(Exception):
    """Raised when the maximum LLM call budget across all stages in a run is exhausted."""
    pass


class NonRetryableError(Exception):
    """Raised when an unrecoverable auth, configuration, or security failure occurs."""
    pass


class ProviderError(RuntimeError):
    """Base exception for provider invocation failures."""
    pass


class PayloadTooLargeError(ProviderError):
    """HTTP 413 or prompt exceeding operational token limit."""
    pass


class RateLimitExceededError(ProviderError):
    """HTTP 429 or operational rate limit exhausted."""
    pass


class AuthenticationError(ProviderError, NonRetryableError):
    """Authentication or access credentials defect."""
    pass


class FormatValidationError(ProviderError):
    """Failed to produce valid JSON adhering to domain schema."""
    pass


class ProviderInternalError(ProviderError):
    """5xx internal provider error."""
    pass


class GlobalBudgetTracker:
    """Tracks and bounds cumulative LLM call count across all stages of an execution run."""

    def __init__(self, max_calls: int = 8):
        self.max_calls = max_calls
        self.total_calls_made = 0

    def can_call(self) -> bool:
        return self.total_calls_made < self.max_calls

    def consume_call(self) -> None:
        if self.total_calls_made >= self.max_calls:
            raise GlobalBudgetExceededError(
                f"Global LLM budget exhausted: {self.total_calls_made}/{self.max_calls} calls consumed. "
                "Halting execution to prevent unbounded resource drain."
            )
        self.total_calls_made += 1


def is_transient_error(error_msg: Optional[str]) -> bool:
    """Determines if a failure message indicates a temporary network/server issue."""
    if not error_msg:
        return False
    msg = error_msg.lower()
    transient_indicators = [
        "503", "502", "504", "429", "timeout", "timed out",
        "connection reset", "connection refused", "service unavailable",
        "temporary", "overloaded",
    ]
    return any(ind in msg for ind in transient_indicators)


def is_auth_or_config_error(error_msg: Optional[str]) -> bool:
    """Determines if a failure message indicates an unrecoverable credential or config defect."""
    if not error_msg:
        return False
    msg = error_msg.lower()
    indicators = [
        "401", "403", "unauthorized", "forbidden", "invalid api key",
        "missing api key", "not configured", "not allowed", "security policy",
        "rejected",
    ]
    return any(ind in msg for ind in indicators)


class StageInvocationCoordinator:
    """
    Coordinates invocation of a single agent stage with:
    - Shared global budget tracking (max 8 calls across all stages)
    - At most ONE repair attempt for invalid JSON or semantic validation failure
    - Only transient retries for temporary transport errors
    - Immediate failure on authentication, configuration, or security violations
    - Strict avoidance of silent fallback to cloud providers
    - Complete recording of EVERY attempt for T3 storage handoff
    """

    def __init__(
        self,
        budget_tracker: Optional[GlobalBudgetTracker] = None,
        max_transient_retries: int = 1,
        max_repair_attempts: int = 1,
    ):
        self.budget_tracker = budget_tracker or GlobalBudgetTracker(max_calls=8)
        self.max_transient_retries = max_transient_retries
        self.max_repair_attempts = max_repair_attempts
        self.recorded_attempts: list[LLMCallAttempt] = []

    def invoke_stage(
        self,
        stage: str,
        provider: LLMProviderPort,
        request: GenerationRequest,
        validator: Optional[Callable[[dict[str, Any]], list[str]]] = None,
    ) -> tuple[GenerationResult, dict[str, Any]]:
        """
        Executes an agent stage invocation loop with strict budget and retry invariants.
        Returns the final GenerationResult and valid structured data.
        Raises ValidationError or RuntimeError on unrecoverable failure.
        """
        stage_attempts = 0
        repair_attempts_made = 0
        transient_retries_made = 0
        current_request = request

        while True:
            # 1. Budget enforcement
            self.budget_tracker.consume_call()
            stage_attempts += 1
            attempt_id = f"attempt-{uuid.uuid4().hex[:8]}"

            # 2. Invoke provider
            result = provider.generate(current_request)

            # 3. Assess result
            if result.status == GenerationStatus.error:
                err_msg = result.error_message or "Unknown provider error"

                # Check unrecoverable errors (auth, config, security) -> abort immediately
                if is_auth_or_config_error(err_msg):
                    self.recorded_attempts.append(
                        LLMCallAttempt(
                            attempt_id=attempt_id,
                            request_ref=current_request.schema_name,
                            stage=stage,
                            attempt_number=stage_attempts,
                            provider=result.provider,
                            model=result.model,
                            status=result.status,
                            duration_seconds=result.duration_seconds,
                            prompt_tokens=result.prompt_tokens,
                            completion_tokens=result.completion_tokens,
                            validation_passed=False,
                            error_message=err_msg,
                        )
                    )
                    raise AuthenticationError(f"Non-retryable provider error in stage '{stage}': {err_msg}")

                # Check transient errors
                if is_transient_error(err_msg) and transient_retries_made < self.max_transient_retries:
                    transient_retries_made += 1
                    self.recorded_attempts.append(
                        LLMCallAttempt(
                            attempt_id=attempt_id,
                            request_ref=current_request.schema_name,
                            stage=stage,
                            attempt_number=stage_attempts,
                            provider=result.provider,
                            model=result.model,
                            status=result.status,
                            duration_seconds=result.duration_seconds,
                            prompt_tokens=result.prompt_tokens,
                            completion_tokens=result.completion_tokens,
                            validation_passed=False,
                            error_message=f"Transient failure (retry {transient_retries_made}): {err_msg}",
                        )
                    )
                    time.sleep(0.05)  # brief backoff
                    continue

                # Unrecoverable or retry limit reached
                self.recorded_attempts.append(
                    LLMCallAttempt(
                        attempt_id=attempt_id,
                        request_ref=current_request.schema_name,
                        stage=stage,
                        attempt_number=stage_attempts,
                        provider=result.provider,
                        model=result.model,
                        status=result.status,
                        duration_seconds=result.duration_seconds,
                        prompt_tokens=result.prompt_tokens,
                        completion_tokens=result.completion_tokens,
                        validation_passed=False,
                        error_message=err_msg,
                    )
                )
                msg_lower = err_msg.lower()
                if "413" in err_msg or "request too large" in msg_lower or "payload too large" in msg_lower or "local rejection" in msg_lower:
                    raise PayloadTooLargeError(f"Payload too large in stage '{stage}': {err_msg}")
                if "429" in err_msg or "rate limit" in msg_lower or "itpm" in msg_lower:
                    raise RateLimitExceededError(f"Rate limit exceeded in stage '{stage}': {err_msg}")
                if any(ind in err_msg for ind in ["500", "502", "503", "504"]) or "internal" in msg_lower or "overloaded" in msg_lower:
                    raise ProviderInternalError(f"Provider internal error in stage '{stage}': {err_msg}")
                raise ProviderError(f"Provider failed in stage '{stage}': {err_msg}")

            if result.status == GenerationStatus.refused:
                self.recorded_attempts.append(
                    LLMCallAttempt(
                        attempt_id=attempt_id,
                        request_ref=current_request.schema_name,
                        stage=stage,
                        attempt_number=stage_attempts,
                        provider=result.provider,
                        model=result.model,
                        status=result.status,
                        finish_reason=result.finish_reason,
                        duration_seconds=result.duration_seconds,
                        prompt_tokens=result.prompt_tokens,
                        completion_tokens=result.completion_tokens,
                        validation_passed=False,
                        error_message=result.error_message or "Model refused request",
                    )
                )
                raise RuntimeError(f"Generation refused by model in stage '{stage}': {result.error_message}")

            if result.status == GenerationStatus.incomplete:
                self.recorded_attempts.append(
                    LLMCallAttempt(
                        attempt_id=attempt_id,
                        request_ref=current_request.schema_name,
                        stage=stage,
                        attempt_number=stage_attempts,
                        provider=result.provider,
                        model=result.model,
                        status=result.status,
                        finish_reason=result.finish_reason,
                        duration_seconds=result.duration_seconds,
                        prompt_tokens=result.prompt_tokens,
                        completion_tokens=result.completion_tokens,
                        validation_passed=False,
                        error_message=result.error_message or "Generation incomplete/truncated",
                    )
                )
                raise RuntimeError(f"Generation incomplete in stage '{stage}': {result.error_message}")

            # Result is GenerationStatus.complete
            parsed_data = result.structured_data or {}
            val_errors: list[str] = []
            if validator:
                val_errors = validator(parsed_data)

            if val_errors:
                # Validation failure: record attempt
                self.recorded_attempts.append(
                    LLMCallAttempt(
                        attempt_id=attempt_id,
                        request_ref=current_request.schema_name,
                        stage=stage,
                        attempt_number=stage_attempts,
                        provider=result.provider,
                        model=result.model,
                        status=result.status,
                        finish_reason=result.finish_reason,
                        duration_seconds=result.duration_seconds,
                        prompt_tokens=result.prompt_tokens,
                        completion_tokens=result.completion_tokens,
                        validation_passed=False,
                        validation_errors=val_errors,
                        structured_data=parsed_data,
                    )
                )

                # Attempt at most one repair
                if repair_attempts_made < self.max_repair_attempts:
                    repair_attempts_made += 1
                    err_summary = "; ".join(val_errors)
                    repair_instructions = (
                        f"{request.instructions}\n\n"
                        f"IMPORTANT CORRECTION REQUIRED: Your previous output failed domain validation with:\n"
                        f"{err_summary}\n"
                        f"Regenerate the response fixing these specific issues strictly adhering to given facts."
                    )
                    current_request = GenerationRequest(
                        role=request.role,
                        instructions=repair_instructions,
                        evidences=request.evidences,
                        schema_name=request.schema_name,
                        json_schema=request.json_schema,
                        model=request.model,
                        max_generation_tokens=request.max_generation_tokens,
                        timeout_seconds=request.timeout_seconds,
                        options=request.options,
                    )
                    continue
                else:
                    raise ValidationError(
                        f"Stage '{stage}' output failed validation after {repair_attempts_made} repair attempt(s): "
                        f"{'; '.join(val_errors)}"
                    )

            # Success! Record successful attempt
            self.recorded_attempts.append(
                LLMCallAttempt(
                    attempt_id=attempt_id,
                    request_ref=current_request.schema_name,
                    stage=stage,
                    attempt_number=stage_attempts,
                    provider=result.provider,
                    model=result.model,
                    status=result.status,
                    finish_reason=result.finish_reason,
                    duration_seconds=result.duration_seconds,
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    validation_passed=True,
                    structured_data=parsed_data,
                )
            )
            return result, parsed_data
