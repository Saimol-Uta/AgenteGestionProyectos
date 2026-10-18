"""Mistral AI chat completions API provider adapter with structured output and security gating."""

import json
import os
import re
import time
from typing import Any, Callable, Optional
import requests

from agente_gestion.domain.llm import (
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
    ModelCapabilities,
)
from agente_gestion.ports.llm import LLMProviderPort
from agente_gestion.application.security import (
    assert_remote_allowed,
    assert_safe_remote_payload,
    mask_secret,
)
from agente_gestion.adapters.llm.groq import RollingWindowTokenLimiter
from agente_gestion.agents.context import estimate_tokens_from_data

_BEARER_RE = re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]+", re.IGNORECASE)


def sanitize_mistral_error(err: str, secret: Optional[Any] = None) -> str:
    """Mask credentials and internal authorization tokens from error strings."""
    cleaned = err
    if secret:
        sec_str = secret.get_secret_value() if hasattr(secret, "get_secret_value") else str(secret)
        cleaned = mask_secret(cleaned, sec_str)
    cleaned = _BEARER_RE.sub("Bearer [REDACTED]", cleaned)
    return cleaned


class MistralProvider(LLMProviderPort):
    """Adapter for Mistral AI chat completions API with schema enforcement and remote gating."""
    provider_name: str = "mistral"

    def __init__(
        self,
        api_key: Optional[Any] = None,
        model: str = "mistral-large-2512",
        base_url: str = "https://api.mistral.ai/v1",
        allow_remote_ai: bool = False,
        context_tokens: int = 128000,
        max_output_tokens: int = 4096,
        transport: Optional[Callable[..., dict[str, Any]]] = None,
        rate_limiter: Optional[RollingWindowTokenLimiter] = None,
    ):
        self.provider_name = "mistral"
        if hasattr(api_key, "get_secret_value"):
            self.api_key = api_key.get_secret_value()
        else:
            self.api_key = api_key if api_key is not None else os.environ.get("MISTRAL_API_KEY", "")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.allow_remote_ai = allow_remote_ai
        self.context_tokens = context_tokens
        self.max_output_tokens = max_output_tokens
        self.transport = transport
        self.rate_limiter = rate_limiter

    def __repr__(self) -> str:
        return f"<MistralProvider model='{self.model}' provider='{self.provider_name}'>"

    def get_capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            context_tokens=self.context_tokens,
            max_output_tokens=self.max_output_tokens,
            supports_json_schema=True,
            supports_tools=False,
            supports_streaming=False,
            reasoning_options=False,
        )

    def _default_transport(
        self,
        url: str,
        headers: dict[str, str],
        payload: Optional[dict[str, Any]] = None,
        timeout: float = 30.0,
        json: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        body = payload if payload is not None else (json if json is not None else kwargs.get("json", {}))
        resp = requests.post(url, headers=headers, json=body, timeout=timeout)
        resp_json = resp.json() if resp.text else {}
        return {
            "status_code": resp.status_code,
            "body": resp_json,
            "text": resp.text,
            "headers": dict(resp.headers),
        }

    def generate(self, request: GenerationRequest) -> GenerationResult:
        # 1. Enforce explicit remote opt-in outside the provider
        assert_remote_allowed(self.allow_remote_ai)

        # 2. Enforce remote redaction & secret-bearing payload gating
        assert_safe_remote_payload(request.evidences)
        assert_safe_remote_payload(request.instructions)

        target_model = request.model or self.model

        # 3. Check credentials: fail fast without making network call if missing
        if not self.api_key:
            return GenerationResult(
                status=GenerationStatus.error,
                error_message="Missing API key: Mistral API key is not configured or empty.",
                provider="mistral",
                model=target_model,
                duration_seconds=0.0,
                request_ref=request.schema_name,
            )

        # 4. Check operational rate limits if configured
        estimated_prompt_tokens = (
            estimate_tokens_from_data(request.instructions)
            + estimate_tokens_from_data(request.evidences)
        )
        elapsed_pacing = 0.0
        if self.rate_limiter is not None:
            t0 = time.monotonic()
            acquired = self.rate_limiter.acquire(
                estimated_prompt_tokens,
                timeout=request.timeout_seconds,
            )
            elapsed_pacing = time.monotonic() - t0
            if not acquired:
                if estimated_prompt_tokens > self.rate_limiter.limit_tokens:
                    return GenerationResult(
                        status=GenerationStatus.error,
                        error_message=(
                            f"Local rejection: Payload estimated at {estimated_prompt_tokens} tokens "
                            f"exceeds operational limit of {self.rate_limiter.limit_tokens} tokens."
                        ),
                        provider="mistral",
                        model=target_model,
                        duration_seconds=0.0,
                        request_ref=request.schema_name,
                    )
                else:
                    return GenerationResult(
                        status=GenerationStatus.error,
                        error_message=(
                            f"Operational rate limit (ITPM) exhausted across window. "
                            f"Could not allocate {estimated_prompt_tokens} tokens within timeout."
                        ),
                        provider="mistral",
                        model=target_model,
                        duration_seconds=0.0,
                        request_ref=request.schema_name,
                    )

        endpoint = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        messages = [
            {"role": "system", "content": request.instructions},
            {"role": "user", "content": json.dumps(request.evidences, default=str)},
        ]

        body: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "max_tokens": request.max_generation_tokens or self.max_output_tokens,
        }

        if request.json_schema or request.schema_name:
            body["response_format"] = {"type": "json_object"}

        start_time = time.monotonic()
        call_transport = self.transport or self._default_transport
        transport_timeout = max(5.0, request.timeout_seconds - elapsed_pacing)

        try:
            try:
                res = call_transport(
                    endpoint,
                    headers=headers,
                    payload=body,
                    json=body,
                    timeout=transport_timeout,
                )
            except TypeError:
                try:
                    res = call_transport(
                        endpoint,
                        headers=headers,
                        payload=body,
                        timeout=transport_timeout,
                    )
                except TypeError:
                    res = call_transport(
                        endpoint,
                        headers=headers,
                        json=body,
                        timeout=transport_timeout,
                    )
        except Exception as e:
            duration = time.monotonic() - start_time
            sanitized_err = sanitize_mistral_error(str(e), self.api_key)
            return GenerationResult(
                status=GenerationStatus.error,
                error_message=f"Mistral transport error: {sanitized_err}",
                provider="mistral",
                model=target_model,
                duration_seconds=duration,
                request_ref=request.schema_name,
            )

        duration = time.monotonic() - start_time
        status_code = res.get("status_code", 200)
        data = res.get("body", {})

        if status_code >= 400:
            raw_err = (
                data.get("message")
                or data.get("error", {}).get("message")
                or res.get("text")
                or f"HTTP {status_code}"
            )
            sanitized_err = sanitize_mistral_error(str(raw_err), self.api_key)
            return GenerationResult(
                status=GenerationStatus.error,
                error_message=f"Mistral API error {status_code}: {sanitized_err}",
                provider="mistral",
                model=target_model,
                duration_seconds=duration,
                request_ref=request.schema_name,
            )

        choices = data.get("choices", [])
        if not choices:
            return GenerationResult(
                status=GenerationStatus.error,
                provider="mistral",
                model=target_model,
                duration_seconds=duration,
                request_ref=request.schema_name,
                error_message="Mistral returned empty choices list.",
            )

        choice = choices[0]
        finish_reason = choice.get("finish_reason")
        raw_text = choice.get("message", {}).get("content", "")

        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")

        if finish_reason == "length":
            return GenerationResult(
                status=GenerationStatus.incomplete,
                finish_reason="length",
                raw_text=raw_text,
                provider="mistral",
                model=target_model,
                duration_seconds=duration,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                request_ref=request.schema_name,
                error_message="Mistral generation truncated by token limit.",
            )

        try:
            parsed_data = json.loads(raw_text) if raw_text else {}
        except json.JSONDecodeError as exc:
            return GenerationResult(
                status=GenerationStatus.error,
                finish_reason=finish_reason,
                raw_text=raw_text,
                provider="mistral",
                model=target_model,
                duration_seconds=duration,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                request_ref=request.schema_name,
                error_message=f"Failed to parse structured JSON from Mistral: {str(exc)}",
            )

        return GenerationResult(
            status=GenerationStatus.complete,
            structured_data=parsed_data,
            raw_text=raw_text,
            finish_reason=finish_reason or "stop",
            provider="mistral",
            model=target_model,
            duration_seconds=duration,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            request_ref=request.schema_name,
        )
