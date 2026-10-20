"""Consent must describe the Quality provider the pipeline will actually invoke."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agente_gestion.api.analysis_jobs import load_analysis_plan
from agente_gestion.api.settings import ApiSettings, ProjectConfig


class DiagnosticPreflightTests(unittest.TestCase):
    def test_quality_preflight_matches_configured_provider_and_model(self):
        cases = (
            ("gemini", "gemini_quality_model", "gemini_api_key"),
            ("mistral", "mistral_model", "mistral_api_key"),
            ("groq", "groq_model", "groq_api_key"),
        )
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {}, clear=True):
            config = Path(root) / "analysis.yaml"
            settings = ApiSettings(
                analysis_config_path=str(config), analysis_env_file=None,
                db_path=str(Path(root) / "isolated.sqlite"),
                projects=(ProjectConfig(id="P", name="Diagnostic", source_mode="fixtures"),),
            )
            for provider, model_field, secret_field in cases:
                with self.subTest(provider=provider):
                    config.write_text(
                        "allow_remote_ai: true\n"
                        f"quality_provider: {provider}\n"
                        f"{model_field}: diagnostic-quality-model\n"
                        f"{secret_field}: synthetic-test-credential-not-real\n",
                        encoding="utf-8",
                    )
                    plan = load_analysis_plan(settings, "P")
                    quality = next(p for p in plan.remote.providers if p.stage == "quality")
                    self.assertEqual(quality.provider, provider)
                    self.assertEqual(quality.model, "diagnostic-quality-model")
                    self.assertEqual(quality.transport, "remote")
                    self.assertEqual(quality.credential_status, "configured")
                    self.assertFalse(Path(settings.db_path).exists())


if __name__ == "__main__":
    unittest.main()
