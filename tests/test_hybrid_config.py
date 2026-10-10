"""Unit tests for hybrid provider configuration and secret protection."""

import os
import unittest
from unittest.mock import patch
from pydantic import ValidationError

from agente_gestion.application.config import Settings


class HybridConfigTestCase(unittest.TestCase):
    def test_default_quality_provider_is_ollama(self):
        settings = Settings.from_env_and_file(_env_file=None)
        self.assertEqual(settings.quality_provider, "ollama")
        self.assertIsNone(settings.groq_api_key)
        self.assertEqual(settings.groq_model, "llama-3.3-70b-versatile")
        self.assertIsNone(settings.mistral_api_key)
        self.assertEqual(settings.mistral_model, "mistral-large-latest")
        self.assertIsNone(settings.quality_context_window)
        self.assertIsNone(settings.quality_max_output_tokens)
        self.assertEqual(settings.quality_context_margin, 64)

    def test_quality_provider_groq_and_mistral_selection(self):
        with patch.dict(os.environ, {"QUALITY_PROVIDER": "groq"}, clear=True):
            settings = Settings.from_env_and_file(_env_file=None)
            self.assertEqual(settings.quality_provider, "groq")

        with patch.dict(os.environ, {"QUALITY_PROVIDER": "MISTRAL"}, clear=True):
            settings = Settings.from_env_and_file(_env_file=None)
            self.assertEqual(settings.quality_provider, "mistral")

    def test_invalid_quality_provider_rejected(self):
        with patch.dict(os.environ, {"QUALITY_PROVIDER": "unsupported_provider"}, clear=True):
            with self.assertRaises(ValidationError) as ctx:
                Settings.from_env_and_file(_env_file=None)
            self.assertIn("quality_provider", str(ctx.exception).lower())

    def test_secrets_never_exposed_in_repr_or_dumps(self):
        secret_groq = "gsk_test_secret_key_never_log_123"
        secret_mistral = "mistral_test_secret_key_never_log_456"

        with patch.dict(
            os.environ,
            {
                "GROQ_API_KEY": secret_groq,
                "MISTRAL_API_KEY": secret_mistral,
                "QUALITY_PROVIDER": "groq",
            },
            clear=True,
        ):
            settings = Settings.from_env_and_file(_env_file=None)

            # Repr protection
            repr_str = repr(settings)
            self.assertNotIn(secret_groq, repr_str)
            self.assertNotIn(secret_mistral, repr_str)

            # String conversion protection
            str_dump = str(settings)
            self.assertNotIn(secret_groq, str_dump)
            self.assertNotIn(secret_mistral, str_dump)

            # model_dump exclude protection
            dumped = settings.model_dump()
            self.assertNotIn("groq_api_key", dumped)
            self.assertNotIn("mistral_api_key", dumped)

            # Secret retrieval for redaction gate
            known = settings.get_known_secret_values()
            self.assertIn(secret_groq, known)
            self.assertIn(secret_mistral, known)

    def test_explicit_quality_context_budgets(self):
        with patch.dict(
            os.environ,
            {
                "QUALITY_CONTEXT_WINDOW": "65536",
                "QUALITY_MAX_OUTPUT_TOKENS": "4096",
                "QUALITY_CONTEXT_MARGIN": "128",
            },
            clear=True,
        ):
            settings = Settings.from_env_and_file(_env_file=None)
            self.assertEqual(settings.quality_context_window, 65536)
            self.assertEqual(settings.quality_max_output_tokens, 4096)
            self.assertEqual(settings.quality_context_margin, 128)

    def test_cli_overrides_precedence_for_quality_provider(self):
        with patch.dict(os.environ, {"QUALITY_PROVIDER": "ollama"}, clear=True):
            settings = Settings.from_env_and_file(
                _env_file=None,
                overrides={"quality_provider": "groq"},
            )
            self.assertEqual(settings.quality_provider, "groq")


if __name__ == "__main__":
    unittest.main()

