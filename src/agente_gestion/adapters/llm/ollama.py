"""Ollama native API (/api/chat) provider adapter."""

import json
import threading
import time
from typing import Any, Callable, Optional, Union
import requests

from agente_gestion.domain.llm import (
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
    ModelCapabilities,
)
from agente_gestion.ports.llm import LLMProviderPort

# Sequential request lock to guarantee one active local inference at a time
_OLLAMA_SEQUENTIAL_LOCK = threading.Lock()


class OllamaProvider(LLMProviderPort):
    """Adapter for local Ollama instances via native /api/chat."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11435",
        model: str = "qwen3.5:2b-q4_K_M",
        context_tokens: int = 4096,
        max_output_tokens: int = 768,
        unload_after_request: bool = True,
        keep_alive: Union[int, str] = 0,
        transport: Optional[Callable[..., dict[str, Any]]] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.context_tokens = context_tokens
        self.max_output_tokens = max_output_tokens
        self.unload_after_request = unload_after_request
        self.keep_alive = keep_alive
        self.transport = transport

    def get_capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            context_tokens=self.context_tokens,
            max_output_tokens=self.max_output_tokens,
            supports_json_schema=True,
            supports_tools=False,
            supports_streaming=False,
            reasoning_options=True,
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
        endpoint = f"{self.base_url}/api/chat"
        headers = {"Content-Type": "application/json"}

        # Prepare messages
        messages = [
            {"role": "system", "content": request.instructions},
            {"role": "user", "content": json.dumps(request.evidences, default=str)},
        ]

        # Configure keep_alive
        chosen_keep_alive = request.options.get(
            "keep_alive",
            0 if self.unload_after_request else self.keep_alive,
        )

        body: dict[str, Any] = {
            "model": request.model or self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "keep_alive": chosen_keep_alive,
        }

        # Structured schema enforcement
        if request.json_schema:
            body["format"] = request.json_schema
        elif request.schema_name:
            body["format"] = "json"

        # Token limit option
        num_predict = request.max_generation_tokens or self.max_output_tokens
        body["options"] = {"num_predict": num_predict}

        start_time = time.monotonic()
        call_transport = self.transport or self._default_transport

        with _OLLAMA_SEQUENTIAL_LOCK:
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
                return GenerationResult(
                    status=GenerationStatus.error,
                    error_message=f"Transport error connecting to Ollama: {str(e)}",
                    provider="ollama",
                    model=request.model or self.model,
                    duration_seconds=duration,
                    request_ref=request.schema_name,
                )

        duration = time.monotonic() - start_time
        status_code = res.get("status_code", 200)
        data = res.get("body", {})

        if status_code >= 400:
            err_msg = data.get("error") or res.get("text") or f"HTTP {status_code}"
            return GenerationResult(
                status=GenerationStatus.error,
                error_message=f"Ollama server returned HTTP {status_code}: {err_msg}",
                provider="ollama",
                model=request.model or self.model,
                duration_seconds=duration,
                request_ref=request.schema_name,
            )

        msg = data.get("message", {})
        raw_content = msg.get("content", "")
        done_reason = data.get("done_reason") or ("stop" if data.get("done") else None)

        # Tokens
        prompt_tokens = data.get("prompt_eval_count")
        completion_tokens = data.get("eval_count")

        # Total duration reported by Ollama is in nanoseconds
        if "total_duration" in data and data["total_duration"] is not None:
            total_duration_sec = data["total_duration"] / 1e9
        else:
            total_duration_sec = duration

        # Check truncation
        if done_reason == "length":
            return GenerationResult(
                status=GenerationStatus.incomplete,
                finish_reason="length",
                raw_text=raw_content,
                provider="ollama",
                model=request.model or self.model,
                duration_seconds=total_duration_sec,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                request_ref=request.schema_name,
                error_message="Ollama generation truncated by token limit.",
            )

        # Parse structured JSON output
        try:
            parsed_data = json.loads(raw_content) if raw_content else {}
        except json.JSONDecodeError as exc:
            return GenerationResult(
                status=GenerationStatus.error,
                finish_reason=done_reason,
                raw_text=raw_content,
                provider="ollama",
                model=request.model or self.model,
                duration_seconds=total_duration_sec,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                request_ref=request.schema_name,
                error_message=f"Failed to parse structured JSON from Ollama output: {str(exc)}",
            )

        return GenerationResult(
            status=GenerationStatus.complete,
            structured_data=parsed_data,
            raw_text=raw_content,
            finish_reason=done_reason or "stop",
            provider="ollama",
            model=request.model or self.model,
            duration_seconds=total_duration_sec,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            request_ref=request.schema_name,
        )
