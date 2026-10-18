"""Configuration tests for the current Gemini/Groq provider set."""

import os
import unittest
from unittest.mock import patch
from pydantic import ValidationError

from agente_gestion.application.config import Settings


class UnifiedProviderConfigTests(unittest.TestCase):
    def test_project_defaults_enable_remote_and_select_gemini(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env_and_file(_env_file=None)
        self.assertTrue(settings.allow_remote_ai)
        self.assertEqual(settings.monitoring_provider, "gemini")
        self.assertEqual(settings.quality_provider, "gemini")
        self.assertEqual(settings.report_provider, "gemini")
        self.assertEqual(settings.gemini_quality_model, "gemini-3.5-flash-lite")
        self.assertEqual(settings.quality_thinking_level, "medium")
        self.assertEqual(settings.quality_context_window, 128000)
        self.assertEqual(settings.quality_max_output_tokens, 4096)
        self.assertEqual(settings.quality_context_margin, 64)

    def test_gemini_groq_and_mistral_providers_are_supported_and_retired_provider_is_rejected(self) -> None:
        with patch.dict(os.environ, {"QUALITY_PROVIDER": "gemini"}, clear=True):
            settings = Settings.from_env_and_file(_env_file=None)
            self.assertEqual(settings.quality_provider, "gemini")
        with patch.dict(os.environ, {"QUALITY_PROVIDER": "groq"}, clear=True):
            settings = Settings.from_env_and_file(_env_file=None)
            self.assertEqual(settings.quality_provider, "groq")
        with patch.dict(os.environ, {"QUALITY_PROVIDER": "mistral"}, clear=True):
            settings = Settings.from_env_and_file(_env_file=None)
            self.assertEqual(settings.quality_provider, "mistral")
        with patch.dict(os.environ, {"QUALITY_PROVIDER": "unsupported_provider"}, clear=True):
            with self.assertRaises(ValidationError):
                Settings.from_env_and_file(_env_file=None)

    def test_secrets_are_excluded_and_available_for_redaction(self) -> None:
        secret_groq = "gsk_test_secret_key_never_log_123"
        secret_mistral = "mis_test_secret_key_never_log_456"
        secret_gemini = "AIza_synthetic_secret_key_123456"
        with patch.dict(os.environ, {
            "GROQ_API_KEY": secret_groq,
            "MISTRAL_API_KEY": secret_mistral,
            "GEMINI_API_KEY": secret_gemini,
        }, clear=True):
            settings = Settings.from_env_and_file(_env_file=None)

        self.assertNotIn(secret_groq, repr(settings))
        self.assertNotIn(secret_mistral, repr(settings))
        self.assertNotIn(secret_gemini, str(settings))
        self.assertNotIn("groq_api_key", settings.model_dump())
        self.assertNotIn("mistral_api_key", settings.model_dump())
        self.assertNotIn("gemini_api_key", settings.model_dump())
        self.assertIn(secret_groq, settings.get_known_secret_values())
        self.assertIn(secret_mistral, settings.get_known_secret_values())
        self.assertIn(secret_gemini, settings.get_known_secret_values())

    def test_explicit_quality_budgets_and_override_precedence(self) -> None:
        with patch.dict(os.environ, {
            "QUALITY_CONTEXT_WINDOW": "65536",
            "QUALITY_MAX_OUTPUT_TOKENS": "4096",
            "QUALITY_CONTEXT_MARGIN": "128",
        }, clear=True):
            settings = Settings.from_env_and_file(
                _env_file=None,
                overrides={"quality_provider": "groq"},
            )
        self.assertEqual(settings.quality_provider, "groq")
        self.assertEqual(settings.quality_context_window, 65536)
        self.assertEqual(settings.quality_max_output_tokens, 4096)
        self.assertEqual(settings.quality_context_margin, 128)


if __name__ == "__main__":
    unittest.main()
