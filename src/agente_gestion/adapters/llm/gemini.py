"""Google Gemini REST API provider adapter with structured output and security gating."""

import json
import os
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


class GeminiProvider(LLMProviderPort):
    """Adapter for Google Gemini REST API with schema enforcement and remote gating."""
    provider_name: str = "gemini"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-2.5-flash",
        allow_remote_ai: bool = False,
        context_tokens: int = 1048576,
        max_output_tokens: int = 8192,
        transport: Optional[Callable[..., dict[str, Any]]] = None,
    ):
        self.provider_name = "gemini"
        self.api_key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", "")
        self.model = model
        self.allow_remote_ai = allow_remote_ai
        self.context_tokens = context_tokens
        self.max_output_tokens = max_output_tokens
        self.transport = transport

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
                error_message="Gemini API key is missing or empty.",
                provider="gemini",
                model=target_model,
                duration_seconds=0.0,
                request_ref=request.schema_name,
            )
        endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent"
        )
        # Authentication via header so key is never logged in query strings or prompts
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        # Prompt content: instructions + evidences as user turn
        user_prompt_text = (
            f"{request.instructions}\n\n"
            f"Evidence Context:\n{json.dumps(request.evidences, default=str)}"
        )

        body: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_prompt_text}],
                }
            ],
            "generationConfig": {
                "maxOutputTokens": request.max_generation_tokens or self.max_output_tokens,
            },
        }

        if request.json_schema:
            body["generationConfig"]["responseFormat"] = {
                "text": {
                    "mimeType": "APPLICATION_JSON",
                    "schema": request.json_schema,
                }
            }

        start_time = time.monotonic()
        call_transport = self.transport or self._default_transport

        try:
            try:
                res = call_transport(
                    endpoint,
                    headers=headers,
                    payload=body,
                    json=body,
                    timeout=request.timeout_seconds,
                )
            except TypeError:
                try:
                    res = call_transport(
                        endpoint,
                        headers=headers,
                        payload=body,
                        timeout=request.timeout_seconds,
                    )
                except TypeError:
                    res = call_transport(
                        endpoint,
                        headers=headers,
                        json=body,
                        timeout=request.timeout_seconds,
                    )
        except Exception as e:
            duration = time.monotonic() - start_time
            sanitized_err = mask_secret(str(e), self.api_key)
            return GenerationResult(
                status=GenerationStatus.error,
                error_message=f"Gemini transport error: {sanitized_err}",
                provider="gemini",
                model=target_model,
                duration_seconds=duration,
                request_ref=request.schema_name,
            )

        duration = time.monotonic() - start_time
        status_code = res.get("status_code", 200)
        data = res.get("body", {})

        if status_code >= 400:
            raw_err = data.get("error", {}).get("message") or res.get("text") or f"HTTP {status_code}"
            sanitized_err = mask_secret(str(raw_err), self.api_key)
            return GenerationResult(
                status=GenerationStatus.error,
                error_message=f"Gemini API error {status_code}: {sanitized_err}",
                provider="gemini",
                model=target_model,
                duration_seconds=duration,
                request_ref=request.schema_name,
            )

        # Check safety or refusal
        prompt_feedback = data.get("promptFeedback", {})
        if prompt_feedback.get("blockReason"):
            return GenerationResult(
                status=GenerationStatus.refused,
                finish_reason=prompt_feedback.get("blockReason"),
                provider="gemini",
                model=target_model,
                duration_seconds=duration,
                request_ref=request.schema_name,
                error_message=f"Gemini blocked prompt: {prompt_feedback.get('blockReason')}",
            )

        candidates = data.get("candidates", [])
        if not candidates:
            return GenerationResult(
                status=GenerationStatus.error,
                provider="gemini",
                model=target_model,
                duration_seconds=duration,
                request_ref=request.schema_name,
                error_message="Gemini returned empty candidates list.",
            )

        candidate = candidates[0]
        finish_reason = candidate.get("finishReason")

        if finish_reason in ("SAFETY", "RECITATION", "BLOCKLIST"):
            return GenerationResult(
                status=GenerationStatus.refused,
                finish_reason=finish_reason,
                provider="gemini",
                model=target_model,
                duration_seconds=duration,
                request_ref=request.schema_name,
                error_message=f"Gemini generation refused: finishReason={finish_reason}",
            )

        parts = candidate.get("content", {}).get("parts", [])
        raw_text = parts[0].get("text", "") if parts else ""

        # Usage metadata
        usage = data.get("usageMetadata", {})
        prompt_tokens = usage.get("promptTokenCount")
        completion_tokens = usage.get("candidatesTokenCount")

        # Check truncation
        if finish_reason == "MAX_TOKENS":
            return GenerationResult(
                status=GenerationStatus.incomplete,
                finish_reason="MAX_TOKENS",
                raw_text=raw_text,
                provider="gemini",
                model=target_model,
                duration_seconds=duration,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                request_ref=request.schema_name,
                error_message="Gemini generation truncated by max tokens limit.",
            )

        try:
            parsed_data = json.loads(raw_text) if raw_text else {}
        except json.JSONDecodeError as exc:
            return GenerationResult(
                status=GenerationStatus.error,
                finish_reason=finish_reason,
                raw_text=raw_text,
                provider="gemini",
                model=target_model,
                duration_seconds=duration,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                request_ref=request.schema_name,
                error_message=f"Failed to parse structured JSON from Gemini: {str(exc)}",
            )

        return GenerationResult(
            status=GenerationStatus.complete,
            structured_data=parsed_data,
            raw_text=raw_text,
            finish_reason=finish_reason or "STOP",
            provider="gemini",
            model=target_model,
            duration_seconds=duration,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            request_ref=request.schema_name,
        )
