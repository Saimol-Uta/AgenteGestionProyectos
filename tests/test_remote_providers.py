"""Unit tests for GroqProvider and MistralProvider remote LLM adapters."""

import json
import unittest
from unittest.mock import MagicMock

from agente_gestion.domain.llm import (
    GenerationRequest,
    GenerationResult,
    GenerationStatus,
)
from agente_gestion.application.generation import is_auth_or_config_error, is_transient_error
from agente_gestion.application.security import (
    RemoteProviderNotAllowedError,
    RemotePayloadSecurityError,
)
from agente_gestion.adapters.llm.groq import GroqProvider
from agente_gestion.adapters.llm.mistral import MistralProvider


class RemoteProvidersTestCase(unittest.TestCase):
    def setUp(self):
        self.sample_request = GenerationRequest(
            role="revisor",
            instructions="Analiza el codigo y genera hallazgos.",
            evidences={"code": "def test(): pass", "file_path": "src/test.py"},
            schema_name="code_findings_v1",
            json_schema={"type": "object", "properties": {"findings": {"type": "array"}}},
            model="llama-3.3-70b-versatile",
            max_generation_tokens=512,
            timeout_seconds=30.0,
        )

    # =========================================================================
    # GROQ TESTS
    # =========================================================================

    def test_groq_successful_json_generation(self):
        mock_transport = MagicMock()
        mock_transport.return_value = {
            "status_code": 200,
            "body": {
                "id": "chatcmpl-groq-123",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"findings": [{"id": "CQ-001", "claim": "ok"}]}),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 42, "completion_tokens": 18, "total_tokens": 60},
            },
            "text": "ok",
        }
        provider = GroqProvider(
            api_key="gsk_test_groq_secret_key_1234567890",
            model="llama-3.3-70b-versatile",
            allow_remote_ai=True,
            transport=mock_transport,
        )

        result = provider.generate(self.sample_request)

        self.assertEqual(result.status, GenerationStatus.complete)
        self.assertEqual(result.provider, "groq")
        self.assertEqual(result.model, "llama-3.3-70b-versatile")
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.prompt_tokens, 42)
        self.assertEqual(result.completion_tokens, 18)
        self.assertEqual(result.structured_data, {"findings": [{"id": "CQ-001", "claim": "ok"}]})

        # Verify transport invocation details
        call_args = mock_transport.call_args
        endpoint = call_args[0][0]
        self.assertIn("/chat/completions", endpoint)
        headers = call_args[1]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer gsk_test_groq_secret_key_1234567890")
        sent_body = call_args[1]["json"]
        self.assertEqual(sent_body["model"], "llama-3.3-70b-versatile")
        self.assertEqual(sent_body["response_format"], {"type": "json_object"})
        self.assertEqual(sent_body["messages"][0]["role"], "system")
        self.assertEqual(sent_body["messages"][1]["role"], "user")

    def test_groq_remote_disabled_fails_fast(self):
        provider = GroqProvider(
            api_key="gsk_test_key",
            allow_remote_ai=False,
        )
        with self.assertRaises(RemoteProviderNotAllowedError):
            provider.generate(self.sample_request)

    def test_groq_missing_api_key_fails_cleanly_without_network_call(self):
        mock_transport = MagicMock()
        provider = GroqProvider(
            api_key="",
            allow_remote_ai=True,
            transport=mock_transport,
        )
        result = provider.generate(self.sample_request)
        self.assertEqual(result.status, GenerationStatus.error)
        self.assertIn("Groq API key is not configured", result.error_message)
        mock_transport.assert_not_called()
        self.assertTrue(is_auth_or_config_error(result.error_message))

    def test_groq_secret_bearing_payload_rejected_by_redaction_gate(self):
        provider = GroqProvider(
            api_key="gsk_valid_key_123456789",
            allow_remote_ai=True,
        )
        unsafe_request = GenerationRequest(
            role="revisor",
            instructions="Review code",
            evidences={"file_path": ".env", "content": "AWS_SECRET_KEY=AKIAIOSFODNN7EXAMPLE"},
            model="llama-3.3-70b-versatile",
        )
        with self.assertRaises(RemotePayloadSecurityError):
            provider.generate(unsafe_request)

    def test_groq_api_key_masked_in_error_message(self):
        secret_key = "gsk_super_secret_groq_key_to_mask_99999"
        mock_transport = MagicMock()
        mock_transport.return_value = {
            "status_code": 401,
            "body": {"error": {"message": f"Invalid API Key: {secret_key}"}},
            "text": f"Error with key {secret_key}",
        }
        provider = GroqProvider(
            api_key=secret_key,
            allow_remote_ai=True,
            transport=mock_transport,
        )
        result = provider.generate(self.sample_request)

        self.assertEqual(result.status, GenerationStatus.error)
        self.assertNotIn(secret_key, result.error_message)
        self.assertIn("[REDACTED_CREDENTIAL]", result.error_message)
        self.assertTrue(is_auth_or_config_error(result.error_message))

    def test_groq_repr_does_not_reveal_api_key(self):
        secret_key = "gsk_classified_secret_key_12345678"
        provider = GroqProvider(api_key=secret_key, model="llama-3.3-70b-versatile")
        repr_str = repr(provider)
        self.assertNotIn(secret_key, repr_str)
        self.assertIn("GroqProvider", repr_str)

    def test_groq_http_401_and_403_normalized(self):
        for code in (401, 403):
            mock_transport = MagicMock(return_value={
                "status_code": code,
                "body": {"error": {"message": f"Access denied ({code})"}},
                "text": "Unauthorized",
            })
            provider = GroqProvider(
                api_key="gsk_key",
                allow_remote_ai=True,
                transport=mock_transport,
            )
            result = provider.generate(self.sample_request)
            self.assertEqual(result.status, GenerationStatus.error)
            self.assertIn(str(code), result.error_message)
            self.assertTrue(is_auth_or_config_error(result.error_message))

    def test_groq_http_429_normalized_as_transient(self):
        mock_transport = MagicMock(return_value={
            "status_code": 429,
            "body": {"error": {"message": "Rate limit exceeded. Try again in 2s"}},
            "text": "Rate limited",
        })
        provider = GroqProvider(
            api_key="gsk_key",
            allow_remote_ai=True,
            transport=mock_transport,
        )
        result = provider.generate(self.sample_request)
        self.assertEqual(result.status, GenerationStatus.error)
        self.assertIn("429", result.error_message)
        self.assertTrue(is_transient_error(result.error_message))

    def test_groq_http_5xx_normalized(self):
        for code in (500, 502, 503, 504):
            mock_transport = MagicMock(return_value={
                "status_code": code,
                "body": {"error": {"message": f"Server failure {code}"}},
                "text": "Server Error",
            })
            provider = GroqProvider(
                api_key="gsk_key",
                allow_remote_ai=True,
                transport=mock_transport,
            )
            result = provider.generate(self.sample_request)
            self.assertEqual(result.status, GenerationStatus.error)
            self.assertIn(str(code), result.error_message)

    def test_groq_timeout_network_error_handled(self):
        def timeout_transport(*args, **kwargs):
            raise TimeoutError("Connection timed out after 30s")

        provider = GroqProvider(
            api_key="gsk_key",
            allow_remote_ai=True,
            transport=timeout_transport,
        )
        result = provider.generate(self.sample_request)
        self.assertEqual(result.status, GenerationStatus.error)
        self.assertIn("timed out", result.error_message.lower())
        self.assertTrue(is_transient_error(result.error_message))

    def test_groq_invalid_json_rejected(self):
        mock_transport = MagicMock(return_value={
            "status_code": 200,
            "body": {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "This is plain text, not JSON"},
                        "finish_reason": "stop",
                    }
                ],
            },
        })
        provider = GroqProvider(
            api_key="gsk_key",
            allow_remote_ai=True,
            transport=mock_transport,
        )
        result = provider.generate(self.sample_request)
        self.assertEqual(result.status, GenerationStatus.error)
        self.assertIn("Failed to parse structured JSON from Groq", result.error_message)

    def test_groq_length_truncation_reports_incomplete_status(self):
        mock_transport = MagicMock(return_value={
            "status_code": 200,
            "body": {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": '{"findings": [{"id": "CQ-"}]'},
                        "finish_reason": "length",
                    }
                ],
            },
        })
        provider = GroqProvider(
            api_key="gsk_key",
            allow_remote_ai=True,
            transport=mock_transport,
        )
        result = provider.generate(self.sample_request)
        self.assertEqual(result.status, GenerationStatus.incomplete)
        self.assertEqual(result.finish_reason, "length")
        self.assertIn("truncated", result.error_message.lower())

    def test_groq_capabilities(self):
        provider = GroqProvider(
            api_key="gsk_key",
            context_tokens=131072,
            max_output_tokens=8192,
        )
        caps = provider.get_capabilities()
        self.assertEqual(caps.context_tokens, 131072)
        self.assertEqual(caps.max_output_tokens, 8192)
        self.assertTrue(caps.supports_json_schema)

    # =========================================================================
    # MISTRAL TESTS
    # =========================================================================

    def test_mistral_successful_json_generation(self):
        mock_transport = MagicMock()
        mock_transport.return_value = {
            "status_code": 200,
            "body": {
                "id": "chatcmpl-mistral-999",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"findings": [{"id": "CQ-002", "claim": "valid defect"}]}),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75},
            },
            "text": "ok",
        }
        provider = MistralProvider(
            api_key="mistral_secret_token_123456789",
            model="mistral-large-latest",
            allow_remote_ai=True,
            transport=mock_transport,
        )

        request = self.sample_request.model_copy(update={"model": "mistral-large-latest"})
        result = provider.generate(request)

        self.assertEqual(result.status, GenerationStatus.complete)
        self.assertEqual(result.provider, "mistral")
        self.assertEqual(result.model, "mistral-large-latest")
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.prompt_tokens, 50)
        self.assertEqual(result.completion_tokens, 25)
        self.assertEqual(result.structured_data, {"findings": [{"id": "CQ-002", "claim": "valid defect"}]})

        # Verify transport invocation details
        call_args = mock_transport.call_args
        endpoint = call_args[0][0]
        self.assertIn("/chat/completions", endpoint)
        headers = call_args[1]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer mistral_secret_token_123456789")
        sent_body = call_args[1]["json"]
        self.assertEqual(sent_body["model"], "mistral-large-latest")
        self.assertEqual(sent_body["response_format"], {"type": "json_object"})

    def test_mistral_remote_disabled_fails_fast(self):
        provider = MistralProvider(
            api_key="mistral_key",
            allow_remote_ai=False,
        )
        with self.assertRaises(RemoteProviderNotAllowedError):
            provider.generate(self.sample_request)

    def test_mistral_missing_api_key_fails_cleanly(self):
        mock_transport = MagicMock()
        provider = MistralProvider(
            api_key="",
            allow_remote_ai=True,
            transport=mock_transport,
        )
        result = provider.generate(self.sample_request)
        self.assertEqual(result.status, GenerationStatus.error)
        self.assertIn("Mistral API key is not configured", result.error_message)
        mock_transport.assert_not_called()
        self.assertTrue(is_auth_or_config_error(result.error_message))

    def test_mistral_secret_bearing_payload_rejected(self):
        provider = MistralProvider(
            api_key="mistral_key",
            allow_remote_ai=True,
        )
        unsafe_request = GenerationRequest(
            role="revisor",
            instructions="Review code",
            evidences={"file_path": "id_rsa", "content": "-----BEGIN RSA PRIVATE KEY-----"},
            model="mistral-large-latest",
        )
        with self.assertRaises(RemotePayloadSecurityError):
            provider.generate(unsafe_request)

    def test_mistral_api_key_masked_in_error_message(self):
        secret_key = "mistral_super_confidential_key_abcdef"
        mock_transport = MagicMock(return_value={
            "status_code": 401,
            "body": {"message": f"Unauthorized access for token {secret_key}"},
            "text": f"Unauthorized token {secret_key}",
        })
        provider = MistralProvider(
            api_key=secret_key,
            allow_remote_ai=True,
            transport=mock_transport,
        )
        result = provider.generate(self.sample_request)

        self.assertEqual(result.status, GenerationStatus.error)
        self.assertNotIn(secret_key, result.error_message)
        self.assertIn("[REDACTED_CREDENTIAL]", result.error_message)
        self.assertTrue(is_auth_or_config_error(result.error_message))

    def test_mistral_repr_does_not_reveal_api_key(self):
        secret_key = "mistral_private_key_xyz987"
        provider = MistralProvider(api_key=secret_key, model="mistral-large-latest")
        repr_str = repr(provider)
        self.assertNotIn(secret_key, repr_str)
        self.assertIn("MistralProvider", repr_str)

    def test_mistral_http_401_and_403_normalized(self):
        for code in (401, 403):
            mock_transport = MagicMock(return_value={
                "status_code": code,
                "body": {"message": f"Forbidden {code}"},
                "text": "Forbidden",
            })
            provider = MistralProvider(
                api_key="mistral_key",
                allow_remote_ai=True,
                transport=mock_transport,
            )
            result = provider.generate(self.sample_request)
            self.assertEqual(result.status, GenerationStatus.error)
            self.assertIn(str(code), result.error_message)
            self.assertTrue(is_auth_or_config_error(result.error_message))

    def test_mistral_http_429_normalized_as_transient(self):
        mock_transport = MagicMock(return_value={
            "status_code": 429,
            "body": {"message": "Too many requests. Quota reached."},
            "text": "Rate limited",
        })
        provider = MistralProvider(
            api_key="mistral_key",
            allow_remote_ai=True,
            transport=mock_transport,
        )
        result = provider.generate(self.sample_request)
        self.assertEqual(result.status, GenerationStatus.error)
        self.assertIn("429", result.error_message)
        self.assertTrue(is_transient_error(result.error_message))

    def test_mistral_http_5xx_normalized(self):
        for code in (500, 503):
            mock_transport = MagicMock(return_value={
                "status_code": code,
                "body": {"message": f"Mistral server error {code}"},
                "text": "Server Error",
            })
            provider = MistralProvider(
                api_key="mistral_key",
                allow_remote_ai=True,
                transport=mock_transport,
            )
            result = provider.generate(self.sample_request)
            self.assertEqual(result.status, GenerationStatus.error)
            self.assertIn(str(code), result.error_message)

    def test_mistral_timeout_network_error_handled(self):
        def network_err_transport(*args, **kwargs):
            raise ConnectionError("Failed to establish a new connection: Connection refused")

        provider = MistralProvider(
            api_key="mistral_key",
            allow_remote_ai=True,
            transport=network_err_transport,
        )
        result = provider.generate(self.sample_request)
        self.assertEqual(result.status, GenerationStatus.error)
        self.assertIn("connection refused", result.error_message.lower())
        self.assertTrue(is_transient_error(result.error_message))

    def test_mistral_invalid_json_rejected(self):
        mock_transport = MagicMock(return_value={
            "status_code": 200,
            "body": {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "not a json string"},
                        "finish_reason": "stop",
                    }
                ],
            },
        })
        provider = MistralProvider(
            api_key="mistral_key",
            allow_remote_ai=True,
            transport=mock_transport,
        )
        result = provider.generate(self.sample_request)
        self.assertEqual(result.status, GenerationStatus.error)
        self.assertIn("Failed to parse structured JSON from Mistral", result.error_message)

    def test_mistral_length_truncation_reports_incomplete_status(self):
        mock_transport = MagicMock(return_value={
            "status_code": 200,
            "body": {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": '{"partial": true'},
                        "finish_reason": "length",
                    }
                ],
            },
        })
        provider = MistralProvider(
            api_key="mistral_key",
            allow_remote_ai=True,
            transport=mock_transport,
        )
        result = provider.generate(self.sample_request)
        self.assertEqual(result.status, GenerationStatus.incomplete)
        self.assertEqual(result.finish_reason, "length")
        self.assertIn("truncated", result.error_message.lower())

    def test_mistral_capabilities(self):
        provider = MistralProvider(
            api_key="mistral_key",
            context_tokens=131072,
            max_output_tokens=8192,
        )
        caps = provider.get_capabilities()
        self.assertEqual(caps.context_tokens, 131072)
        self.assertEqual(caps.max_output_tokens, 8192)
        self.assertTrue(caps.supports_json_schema)


if __name__ == "__main__":
    unittest.main()
