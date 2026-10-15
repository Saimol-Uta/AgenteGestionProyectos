"""Provider wiring tests for live, mixed, and deterministic analysis modes."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agente_gestion.cli import build_parser, initialize_pipeline
from agente_gestion.application.config import Settings
from agente_gestion.adapters.llm.groq import GroqProvider
from agente_gestion.adapters.llm.gemini import GeminiProvider
from agente_gestion.adapters.llm.fake import FakeProvider
import agente_gestion.adapters.llm as llm_adapters


class UnifiedPipelineProviderTests(unittest.TestCase):
    def settings(self, **updates) -> Settings:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        values = {
            "_env_file": None,
            "db_path": str(root / "analysis.sqlite"),
            "runs_dir": str(root / "runs"),
            "source_mode": "fixtures",
            "allow_remote_ai": True,
        }
        values.update(updates)
        with patch.dict("os.environ", {}, clear=True):
            return Settings(**values)

    def providers(self, settings: Settings):
        with patch.dict("os.environ", {}, clear=True):
            return initialize_pipeline(settings)[3:]

    def test_missing_credentials_wire_deterministic_provider_for_each_component(self) -> None:
        verifier, monitoring, quality, report = self.providers(self.settings())
        self.assertIsInstance(monitoring, FakeProvider)
        self.assertIsInstance(quality, FakeProvider)
        self.assertIsInstance(verifier, FakeProvider)
        self.assertIsInstance(report, FakeProvider)

    def test_partial_credentials_only_enable_the_bound_remote_model(self) -> None:
        verifier, monitoring, quality, report = self.providers(self.settings(
            groq_api_key="synthetic-groq-key",
        ))
        self.assertIsInstance(quality, GroqProvider)
        self.assertIsInstance(monitoring, FakeProvider)
        self.assertIsInstance(verifier, FakeProvider)
        self.assertIsInstance(report, FakeProvider)

    def test_gemini_key_enables_monitoring_verifier_and_report(self) -> None:
        verifier, monitoring, quality, report = self.providers(self.settings(
            gemini_api_key="synthetic-gemini-key",
        ))
        self.assertIsInstance(monitoring, GeminiProvider)
        self.assertIsInstance(verifier, GeminiProvider)
        self.assertIsInstance(report, GeminiProvider)
        self.assertIsInstance(quality, FakeProvider)

    def test_explicit_remote_gate_forces_sandbox_even_with_synthetic_keys(self) -> None:
        verifier, monitoring, quality, report = self.providers(self.settings(
            allow_remote_ai=False,
            gemini_api_key="synthetic-gemini-key",
            groq_api_key="synthetic-groq-key",
        ))
        self.assertTrue(all(isinstance(item, FakeProvider)
                            for item in (verifier, monitoring, quality, report)))

    def test_explicit_fake_mode_never_constructs_remote_providers(self) -> None:
        verifier, monitoring, quality, report = self.providers(self.settings(
            is_fake=True,
            gemini_api_key="synthetic-gemini-key",
            groq_api_key="synthetic-groq-key",
        ))
        self.assertTrue(all(isinstance(item, FakeProvider)
                            for item in (verifier, monitoring, quality, report)))

    def test_all_credentials_bind_each_remote_role_to_its_provider(self) -> None:
        verifier, monitoring, quality, report = self.providers(self.settings(
            gemini_api_key="synthetic-gemini-key",
            groq_api_key="synthetic-groq-key",
            quality_context_window=65536,
            quality_max_output_tokens=4096,
        ))
        self.assertIsInstance(monitoring, GeminiProvider)
        self.assertIsInstance(verifier, GeminiProvider)
        self.assertIsInstance(report, GeminiProvider)
        self.assertIsInstance(quality, GroqProvider)
        self.assertEqual(monitoring.model, "gemini-3.5-flash-lite")
        self.assertEqual(verifier.model, "gemini-2.5-flash")
        self.assertEqual(report.model, "gemini-3.5-flash-lite")
        self.assertEqual(quality.context_tokens, 65536)
        self.assertEqual(quality.max_output_tokens, 4096)

    def test_provider_exports_and_cli_choices_exclude_retired_adapters(self) -> None:
        self.assertEqual(set(llm_adapters.__all__), {"FakeProvider", "GeminiProvider", "GroqProvider"})
        for retired in ("OllamaProvider", "MistralProvider"):
            self.assertFalse(hasattr(llm_adapters, retired))

        parser = build_parser()
        for arguments in (
            ["run", "--monitoring-provider", "ollama"],
            ["run", "--quality-provider", "mistral"],
            ["run", "--report-provider", "ollama"],
        ):
            with self.subTest(arguments=arguments), redirect_stderr(StringIO()), \
                    self.assertRaises(SystemExit) as error:
                parser.parse_args(arguments)
            self.assertEqual(error.exception.code, 2)

        help_output = StringIO()
        with redirect_stdout(help_output), self.assertRaises(SystemExit) as error:
            parser.parse_args(["run", "--help"])
        self.assertEqual(error.exception.code, 0)
        self.assertNotIn("ollama", help_output.getvalue().lower())
        self.assertNotIn("mistral", help_output.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
