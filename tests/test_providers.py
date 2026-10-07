"""Tests for LLM provider adapters: Ollama, Gemini, and Fake."""

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from agente_gestion.domain.llm import (
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
)
from agente_gestion.adapters.llm.ollama import OllamaProvider
from agente_gestion.adapters.llm.gemini import GeminiProvider
from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.application.security import (
    RemoteProviderNotAllowedError,
    RemotePayloadSecurityError,
)


class LLMProvidersTestCase(unittest.TestCase):
    def setUp(self):
        self.sample_request = GenerationRequest(
            role="elaborador",
            instructions="Draft weekly project monitoring summary",
            evidences={"completed_cards": 2, "total_cards": 5},
            schema_name="draft_schema",
            json_schema={
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "risks": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["summary", "risks"],
            },
            model="qwen3.5:2b-q4_K_M",
            max_generation_tokens=256,
        )

    # --- Ollama Adapter Tests ---

    def test_ollama_successful_structured_generation(self):
        # Mock transport returning successful Ollama response
        mock_transport = MagicMock()
        mock_transport.return_value = {
            "status_code": 200,
            "body": {
                "model": "qwen3.5:2b-q4_K_M",
                "message": {
                    "role": "assistant",
                    "content": json.dumps({"summary": "Progress is steady", "risks": ["Auth delay"]}),
                },
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 42,
                "eval_count": 18,
                "total_duration": 1500000000,  # 1.5s
            },
        }

        provider = OllamaProvider(
            base_url="http://127.0.0.1:11435",
            model="qwen3.5:2b-q4_K_M",
            transport=mock_transport,
            unload_after_request=True,
        )
        result = provider.generate(self.sample_request)

        self.assertEqual(result.status, GenerationStatus.complete)
        self.assertEqual(result.structured_data, {"summary": "Progress is steady", "risks": ["Auth delay"]})
        self.assertEqual(result.prompt_tokens, 42)
        self.assertEqual(result.completion_tokens, 18)
        self.assertEqual(result.finish_reason, "stop")
        self.assertAlmostEqual(result.duration_seconds, 1.5)

        # Verify Ollama request arguments: think=False, keep_alive=0, format=schema
        call_args = mock_transport.call_args
        sent_body = call_args[1]["json"]
        self.assertFalse(sent_body["think"])
        self.assertEqual(sent_body["keep_alive"], 0)
        self.assertEqual(sent_body["format"], self.sample_request.json_schema)

    def test_ollama_truncation_reports_incomplete_status(self):
        mock_transport = MagicMock()
        mock_transport.return_value = {
            "status_code": 200,
            "body": {
                "model": "qwen3.5:2b-q4_K_M",
                "message": {"role": "assistant", "content": '{"summary": "Incomplete text'},
                "done": True,
                "done_reason": "length",
                "prompt_eval_count": 50,
                "eval_count": 256,
            },
        }
        provider = OllamaProvider(transport=mock_transport)
        result = provider.generate(self.sample_request)

        self.assertEqual(result.status, GenerationStatus.incomplete)
        self.assertEqual(result.finish_reason, "length")

    def test_ollama_invalid_json_reports_error_status(self):
        mock_transport = MagicMock()
        mock_transport.return_value = {
            "status_code": 200,
            "body": {
                "model": "qwen3.5:2b-q4_K_M",
                "message": {"role": "assistant", "content": "NOT JSON AT ALL"},
                "done": True,
                "done_reason": "stop",
            },
        }
        provider = OllamaProvider(transport=mock_transport)
        result = provider.generate(self.sample_request)

        self.assertEqual(result.status, GenerationStatus.error)
        self.assertIn("JSON", result.error_message)

    def test_ollama_http_failure_reports_error_status(self):
        mock_transport = MagicMock()
        mock_transport.side_effect = ConnectionError("Failed to connect to 127.0.0.1:11435")

        provider = OllamaProvider(transport=mock_transport)
        result = provider.generate(self.sample_request)

        self.assertEqual(result.status, GenerationStatus.error)
        self.assertIn("Failed to connect", result.error_message)

    def test_ollama_nullable_tokens_when_unreported(self):
        mock_transport = MagicMock()
        mock_transport.return_value = {
            "status_code": 200,
            "body": {
                "model": "qwen3.5:2b-q4_K_M",
                "message": {"role": "assistant", "content": '{"summary": "ok", "risks": []}'},
                "done": True,
                "done_reason": "stop",
                # prompt_eval_count and eval_count omitted
            },
        }
        provider = OllamaProvider(transport=mock_transport)
        result = provider.generate(self.sample_request)

        self.assertEqual(result.status, GenerationStatus.complete)
        self.assertIsNone(result.prompt_tokens)
        self.assertIsNone(result.completion_tokens)

    # --- Gemini Adapter Tests ---

    def test_gemini_remote_disabled_fails_fast_outside_provider(self):
        # When allow_remote_ai is False, invoking Gemini must fail explicitly
        provider = GeminiProvider(
            api_key="fake-secret-key-12345",
            allow_remote_ai=False,
        )
        with self.assertRaises(RemoteProviderNotAllowedError):
            provider.generate(self.sample_request)

    def test_gemini_credentials_never_logged_or_in_prompts(self):
        mock_transport = MagicMock()
        mock_transport.return_value = {
            "status_code": 200,
            "body": {
                "candidates": [
                    {
                        "content": {"parts": [{"text": '{"summary": "Verified", "risks": []}'}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 35, "candidatesTokenCount": 12},
            },
        }
        secret_key = "AIzaSyFakeSecretGeminiKey123456789"
        provider = GeminiProvider(
            api_key=secret_key,
            allow_remote_ai=True,
            transport=mock_transport,
        )
        result = provider.generate(self.sample_request)

        self.assertEqual(result.status, GenerationStatus.complete)
        call_args = mock_transport.call_args
        # Header or URL check: API key must NOT be inside the prompt/content body!
        sent_body = call_args[1]["json"]
        body_str = json.dumps(sent_body)
        self.assertNotIn(secret_key, body_str)

    def test_gemini_secret_bearing_payload_rejected_by_redaction_gate(self):
        provider = GeminiProvider(
            api_key="fake-key",
            allow_remote_ai=True,
        )
        # Attempting to send a payload referencing a secret file or containing private keys
        unsafe_request = GenerationRequest(
            role="revisor",
            instructions="Review code",
            evidences={"file_path": ".env", "content": "AWS_SECRET_KEY=AKIAIOSFODNN7EXAMPLE"},
            model="gemini-2.5-flash",
        )
        with self.assertRaises(RemotePayloadSecurityError):
            provider.generate(unsafe_request)

    def test_gemini_safety_refusal_reports_refused_status(self):
        mock_transport = MagicMock()
        mock_transport.return_value = {
            "status_code": 200,
            "body": {
                "candidates": [
                    {
                        "finishReason": "SAFETY",
                    }
                ],
                "promptFeedback": {"blockReason": "SAFETY"},
            },
        }
        provider = GeminiProvider(
            api_key="fake-key",
            allow_remote_ai=True,
            transport=mock_transport,
        )
        result = provider.generate(self.sample_request)
        self.assertEqual(result.status, GenerationStatus.refused)
        self.assertEqual(result.finish_reason, "SAFETY")

    def test_gemini_max_tokens_reports_incomplete_status(self):
        mock_transport = MagicMock()
        mock_transport.return_value = {
            "status_code": 200,
            "body": {
                "candidates": [
                    {
                        "content": {"parts": [{"text": '{"summary": "incomplete...'}]},
                        "finishReason": "MAX_TOKENS",
                    }
                ],
            },
        }
        provider = GeminiProvider(
            api_key="fake-key",
            allow_remote_ai=True,
            transport=mock_transport,
        )
        result = provider.generate(self.sample_request)
        self.assertEqual(result.status, GenerationStatus.incomplete)
        self.assertEqual(result.finish_reason, "MAX_TOKENS")

    # --- Fake Provider Tests ---

    def test_fake_provider_deterministic_simulated_e2e(self):
        fake = FakeProvider()
        self.assertTrue(fake.is_simulated)
        self.assertEqual(fake.provider_name, "fake_simulated")

        result = fake.generate(self.sample_request)
        self.assertEqual(result.status, GenerationStatus.complete)
        self.assertIsNotNone(result.structured_data)

    # --- Real Default Transport & Packaging Regression Tests ---

    @patch("requests.post")
    def test_ollama_real_default_transport_signature_and_call(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps({
            "message": {"content": json.dumps({"summary": "Ok", "risks": []})},
            "done": True,
            "done_reason": "stop",
        })
        mock_resp.json.return_value = {
            "message": {"content": json.dumps({"summary": "Ok", "risks": []})},
            "done": True,
            "done_reason": "stop",
        }
        mock_post.return_value = mock_resp

        # Instantiated with REAL default transport (no transport passed)
        provider = OllamaProvider()
        result = provider.generate(self.sample_request)

        self.assertEqual(result.status, GenerationStatus.complete)
        self.assertTrue(mock_post.called)
        call_kwargs = mock_post.call_args[1]
        self.assertIn("json", call_kwargs)
        self.assertEqual(call_kwargs["json"]["model"], "qwen3.5:2b-q4_K_M")

    @patch("requests.post")
    def test_gemini_real_default_transport_signature_and_call(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps({
            "candidates": [
                {
                    "content": {"parts": [{"text": json.dumps({"summary": "Ok", "risks": []})}]},
                    "finishReason": "STOP",
                }
            ]
        })
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {"parts": [{"text": json.dumps({"summary": "Ok", "risks": []})}]},
                    "finishReason": "STOP",
                }
            ]
        }
        mock_post.return_value = mock_resp

        # Instantiated with REAL default transport (no transport passed)
        provider = GeminiProvider(api_key="fake-gemini-key", allow_remote_ai=True)
        result = provider.generate(self.sample_request)

        self.assertEqual(result.status, GenerationStatus.complete)
        self.assertTrue(mock_post.called)
        call_kwargs = mock_post.call_args[1]
        self.assertIn("json", call_kwargs)

    def test_bounded_no_credential_local_behavior(self):
        # Local ollama execution does not need or leak remote credentials
        provider = OllamaProvider()
        caps = provider.get_capabilities()
        self.assertTrue(caps.supports_json_schema)
        self.assertEqual(provider.model, "qwen3.5:2b-q4_K_M")

    def test_packaging_dependencies_declared(self):
        pyproject_path = os.path.join(os.path.dirname(__file__), "..", "pyproject.toml")
        with open(pyproject_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("pyyaml", content.lower())
        self.assertIn("requests", content.lower())
        self.assertIn("pydantic", content.lower())


if __name__ == "__main__":
    unittest.main()
