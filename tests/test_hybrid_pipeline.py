"""Unit and integration tests for hybrid LLM provider wiring in the pipeline."""

import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from agente_gestion.cli import initialize_pipeline, main
from agente_gestion.application.config import Settings
from agente_gestion.adapters.llm.ollama import OllamaProvider
from agente_gestion.adapters.llm.groq import GroqProvider
from agente_gestion.adapters.llm.mistral import MistralProvider
from agente_gestion.adapters.llm.gemini import GeminiProvider
from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.domain.run import RunStatus


class HybridPipelineTestCase(unittest.TestCase):
    def test_requirement_a_quality_provider_ollama_maintains_existing_behavior(self):
        config = Settings(
            quality_provider="ollama",
            allow_remote_ai=False,
            is_fake=False,
        )
        _, _, _, verificador, elaborador, revisor, aprobador = initialize_pipeline(config)

        self.assertIsInstance(elaborador, OllamaProvider)
        self.assertIsInstance(revisor, OllamaProvider)
        self.assertIsInstance(aprobador, OllamaProvider)
        self.assertIsNone(verificador)

    def test_requirement_b_quality_provider_groq_wires_groq_provider(self):
        config = Settings(
            quality_provider="groq",
            groq_api_key="gsk_synthetic_test_key_12345",
            groq_model="llama-3.3-70b-versatile",
            allow_remote_ai=True,
            is_fake=False,
        )
        _, _, _, verificador, elaborador, revisor, aprobador = initialize_pipeline(config)

        self.assertIsInstance(revisor, GroqProvider)
        self.assertEqual(revisor.model, "llama-3.3-70b-versatile")
        self.assertEqual(revisor.context_tokens, 131072)
        # Monitoring and report remain Ollama
        self.assertIsInstance(elaborador, OllamaProvider)
        self.assertIsInstance(aprobador, OllamaProvider)

    def test_requirement_c_quality_provider_mistral_wires_mistral_provider(self):
        config = Settings(
            quality_provider="mistral",
            mistral_api_key="mistral_synthetic_test_key_67890",
            mistral_model="mistral-large-latest",
            allow_remote_ai=True,
            is_fake=False,
        )
        _, _, _, verificador, elaborador, revisor, aprobador = initialize_pipeline(config)

        self.assertIsInstance(revisor, MistralProvider)
        self.assertEqual(revisor.model, "mistral-large-latest")
        self.assertEqual(revisor.context_tokens, 131072)
        # Monitoring and report remain Ollama
        self.assertIsInstance(elaborador, OllamaProvider)
        self.assertIsInstance(aprobador, OllamaProvider)

    def test_requirement_d_monitoring_and_report_always_use_ollama_independent_of_quality(self):
        for provider_name in ("ollama", "groq", "mistral"):
            config = Settings(
                quality_provider=provider_name,
                groq_api_key="gsk_key",
                mistral_api_key="mistral_key",
                allow_remote_ai=True,
                is_fake=False,
            )
            _, _, _, _, elaborador, _, aprobador = initialize_pipeline(config)

            self.assertIsInstance(elaborador, OllamaProvider, f"Monitoring failed for {provider_name}")
            self.assertIsInstance(aprobador, OllamaProvider, f"Report failed for {provider_name}")

    def test_requirement_e_verifier_always_uses_gemini_when_enabled(self):
        for provider_name in ("ollama", "groq", "mistral"):
            config = Settings(
                quality_provider=provider_name,
                gemini_api_key="synthetic-gemini-key",
                gemini_model="gemini-2.5-flash",
                allow_remote_ai=True,
                is_fake=False,
            )
            _, _, _, verificador, _, _, _ = initialize_pipeline(config)

            self.assertIsInstance(verificador, GeminiProvider, f"Verifier failed for {provider_name}")
            self.assertEqual(verificador.model, "gemini-2.5-flash")

    def test_fake_mode_preserves_fake_provider_for_all_agents(self):
        for provider_name in ("ollama", "groq", "mistral"):
            config = Settings(
                quality_provider=provider_name,
                is_fake=True,
            )
            _, _, _, verificador, elaborador, revisor, aprobador = initialize_pipeline(config)

            self.assertIsInstance(elaborador, FakeProvider)
            self.assertIsInstance(revisor, FakeProvider)
            self.assertIsInstance(verificador, FakeProvider)
            self.assertIsInstance(aprobador, FakeProvider)

    def test_execution_summary_displays_correct_provider_and_models(self):
        cmd = [
            "run",
            "fixture_1_standard",
            "--fixture", "fixture_1_standard",
            "--allow-remote-ai",
        ]
        env_vars = {
            "QUALITY_PROVIDER": "groq",
            "GROQ_API_KEY": "gsk_synthetic_test_key_12345",
            "GEMINI_API_KEY": "gemini_synthetic_test_key_67890",
            "ALLOW_REMOTE_AI": "true",
        }

        stdout_capture = io.StringIO()
        with patch.dict(os.environ, env_vars, clear=True), \
             patch.object(sys, "argv", ["agente_gestion"] + cmd), \
             patch("sys.stdout", stdout_capture), \
             patch("agente_gestion.cli.ProjectOrchestrator") as mock_orch_cls:

            mock_run = MagicMock()
            mock_run.run_id = "run-test-hybrid"
            mock_run.status = RunStatus.completed
            mock_run.duration_seconds = 2.5
            mock_orch_instance = MagicMock()
            mock_orch_instance.execute.return_value = mock_run
            mock_orch_cls.return_value = mock_orch_instance

            exit_code = main()

        self.assertEqual(exit_code, 0)
        output = stdout_capture.getvalue()
        self.assertIn("Elaborador=ollama/", output)
        self.assertIn("Revisor=groq/llama-3.3-70b-versatile", output)
        self.assertIn("Verificador=gemini/", output)
        self.assertIn("Aprobador=ollama/", output)

    def test_cli_quality_provider_flag_override(self):
        cmd = [
            "run",
            "fixture_1_standard",
            "--fixture", "fixture_1_standard",
            "--quality-provider", "mistral",
            "--allow-remote-ai",
        ]
        env_vars = {
            "QUALITY_PROVIDER": "ollama",
            "MISTRAL_API_KEY": "mistral_test_key",
            "ALLOW_REMOTE_AI": "true",
        }

        stdout_capture = io.StringIO()
        with patch.dict(os.environ, env_vars, clear=True), \
             patch.object(sys, "argv", ["agente_gestion"] + cmd), \
             patch("sys.stdout", stdout_capture), \
             patch("agente_gestion.cli.ProjectOrchestrator") as mock_orch_cls:

            mock_run = MagicMock()
            mock_run.run_id = "run-test-flag-override"
            mock_run.status = RunStatus.completed
            mock_run.duration_seconds = 1.0
            mock_orch_instance = MagicMock()
            mock_orch_instance.execute.return_value = mock_run
            mock_orch_cls.return_value = mock_orch_instance

            exit_code = main()

        self.assertEqual(exit_code, 0)
        output = stdout_capture.getvalue()
        self.assertIn("Revisor=mistral/mistral-large-latest", output)


if __name__ == "__main__":
    unittest.main()
