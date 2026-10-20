"""Independent diagnostic probes: consent agrees with the real provider factory."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agente_gestion.api.analysis_jobs import load_analysis_plan
from agente_gestion.api.settings import ApiSettings, ProjectConfig
from agente_gestion.cli import _build_orchestrator


SECRET = "SYNTHETIC-CREDENTIAL-not-a-real-key"
PROVIDERS = (
    ("gemini", "gemini_quality_model", "GeminiProvider"),
    ("mistral", "mistral_model", "MistralProvider"),
    ("groq", "groq_model", "GroqProvider"),
)


class PreflightFactoryAgreementTests(unittest.TestCase):
    def _selection(self, root, provider, model_field, credentialed):
        config = root / "analysis.yaml"
        config.write_text(
            "allow_remote_ai: true\n"
            f"quality_provider: {provider}\n{model_field}: diagnostic-quality-model\n"
            + (f"{provider}_api_key: {SECRET}\n" if credentialed else ""),
            encoding="utf-8",
        )
        settings = ApiSettings(
            analysis_config_path=str(config), analysis_env_file=None,
            db_path=str(root / "isolated.sqlite"), runs_dir=str(root / "runs"),
            projects=(ProjectConfig(id="P", name="Diagnostic", source_mode="fixtures"),),
        )
        with patch.dict(os.environ, {}, clear=True):
            plan = load_analysis_plan(settings, "P")
            quality = next(p for p in plan.remote.providers if p.stage == "quality")
            effective = plan.engine_settings.model_copy(update={
                "db_path": settings.db_path, "runs_dir": settings.runs_dir,
            })
            _, _, _, _, _, reviewer, _ = _build_orchestrator(effective, effective_mode="fixtures")
        return plan, quality, reviewer

    def test_configured_and_missing_credentials_match_actual_factory(self):
        for provider, model_field, expected_class in PROVIDERS:
            for credentialed in (True, False):
                with self.subTest(provider=provider, credentialed=credentialed), tempfile.TemporaryDirectory() as root:
                    _, quality, reviewer = self._selection(Path(root), provider, model_field, credentialed)
                    self.assertEqual(quality.model, reviewer.model)
                    if credentialed:
                        self.assertEqual(quality.provider, reviewer.provider_name)
                        self.assertEqual(quality.transport, "remote")
                        self.assertEqual(quality.credential_status, "configured")
                        self.assertEqual(type(reviewer).__name__, expected_class)
                    else:
                        self.assertEqual(quality.provider, "fake_simulated")
                        self.assertEqual(quality.transport, "local")
                        self.assertEqual(type(reviewer).__name__, "FakeProvider")

    def test_public_preflight_never_exposes_credentials(self):
        for provider, model_field, _ in PROVIDERS:
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as root:
                plan, _, _ = self._selection(Path(root), provider, model_field, True)
                public = json.dumps(plan.remote.model_dump(mode="json"))
                public += json.dumps(plan.preflight.model_dump(mode="json"))
                public += plan.fingerprint + str(plan.fallback_reasons)
                self.assertNotIn(SECRET, public)
