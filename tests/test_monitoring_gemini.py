"""Offline Monitoring-only Gemini composition and request contract."""
import json
import os
import unittest
from datetime import datetime, timezone
from contextlib import ExitStack
from unittest.mock import Mock, patch

from pydantic import ValidationError as ConfigError

from agente_gestion.application.config import Settings
from agente_gestion.application.generation import GlobalBudgetTracker, StageInvocationCoordinator
from agente_gestion.application.validation import ValidationError
from agente_gestion.agents.context import ContextBudgetExceededError
from agente_gestion.agents.monitoring import ProjectMonitoringAgent
from agente_gestion.adapters.llm.gemini import GeminiProvider
from agente_gestion.adapters.llm.groq import GroqProvider
from agente_gestion.cli import _build_orchestrator
from agente_gestion.domain.tasks import calculate_task_metrics


class MonitoringGeminiTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.metrics = calculate_task_metrics([], datetime(2026, 1, 1, tzinfo=timezone.utc))

    def settings(self, **kwargs):
        return Settings(_env_file=None, **kwargs)

    def compose(self, settings):
        with ExitStack() as stack:
            for name in ("SQLiteStorageAdapter", "ArtifactStorageAdapter", "FixtureSourcesAdapter",
                         "create_analysis_composite_source"):
                stack.enter_context(patch("agente_gestion.cli." + name))
            return _build_orchestrator(settings)

    def test_defaults_and_environment_precedence_are_role_specific(self):
        defaults = self.settings()
        self.assertEqual(defaults.monitoring_provider, "gemini")
        self.assertEqual(defaults.quality_provider, "groq")
        self.assertEqual(defaults.report_provider, "gemini")
        self.assertIsNone(defaults.monitoring_max_generation_tokens)
        self.assertTrue(defaults.allow_remote_ai)
        with patch.dict(os.environ, {"MONITORING_PROVIDER": "gemini", "GEMINI_MODEL": "gemini-3.8-flash",
                                     "MONITORING_MAX_GENERATION_TOKENS": "4096"}):
            settings = self.settings()
            self.assertEqual(settings.monitoring_provider, "gemini")
            self.assertEqual(settings.gemini_monitoring_model, "gemini-3.5-flash-lite")
            self.assertEqual(settings.gemini_model, "gemini-3.8-flash")
            self.assertEqual(settings.monitoring_max_generation_tokens, 4096)
            self.assertEqual(settings.quality_provider, "groq")
            self.assertEqual(self.settings(monitoring_provider="gemini").monitoring_provider, "gemini")
        for override in ({"monitoring_provider": "groq"}, {"monitoring_max_generation_tokens": 0},
                         {"monitoring_max_generation_tokens": 8193}):
            with self.assertRaises(ConfigError):
                self.settings(**override)

    def test_mocked_yaml_dotenv_environment_and_init_precedence(self):
        field = "monitoring_max_generation_tokens"
        with patch("agente_gestion.application.config.YamlConfigSettingsSource.__call__",
                   return_value={field: 512}), patch(
                   "pydantic_settings.sources.DotEnvSettingsSource.__call__", return_value={}):
            self.assertEqual(self.settings().monitoring_max_generation_tokens, 512)
            with patch("pydantic_settings.sources.DotEnvSettingsSource.__call__",
                       return_value={field: 1024}):
                self.assertEqual(self.settings().monitoring_max_generation_tokens, 1024)
                with patch.dict(os.environ, {"MONITORING_MAX_GENERATION_TOKENS": "2048"}):
                    self.assertEqual(self.settings().monitoring_max_generation_tokens, 2048)
                    self.assertEqual(self.settings(monitoring_max_generation_tokens=4096)
                                     .monitoring_max_generation_tokens, 4096)

    def test_invalid_monitoring_reservation_is_rejected_before_storage(self):
        with patch("agente_gestion.cli.SQLiteStorageAdapter") as storage:
            with self.assertRaises(ConfigError):
                self.settings(monitoring_max_generation_tokens=8193)
            storage.assert_not_called()

    def test_composition_models_and_budgets_are_role_specific(self):
        for explicit, expected in ((None, 2048), (4096, 4096)):
            with self.subTest(output=explicit):
                config = self.settings(
                    allow_remote_ai=True,
                    gemini_api_key="dummy-gemini-key",
                    groq_api_key="dummy-groq-key",
                    gemini_model="gemini-3.8-flash",
                    groq_model="qwen/qwen3.8-27b",
                    quality_context_window=65536,
                    quality_max_output_tokens=4096,
                    monitoring_max_generation_tokens=explicit,
                )
                orchestrator, _, _, verifier, monitoring, quality, report = self.compose(config)
                self.assertEqual(monitoring.provider_name, "gemini")
                self.assertEqual(monitoring.model, config.gemini_monitoring_model)
                self.assertEqual(orchestrator.config.monitoring_max_generation_tokens, expected)
                self.assertGreaterEqual(monitoring.get_capabilities().max_output_tokens, expected)
                self.assertEqual(verifier.model, "gemini-3.8-flash")
                self.assertEqual(quality.provider_name, "groq")
                self.assertEqual(quality.model, "qwen/qwen3.8-27b")
                self.assertEqual(quality.get_capabilities().context_tokens, 65536)
                self.assertEqual(quality.get_capabilities().max_output_tokens, 4096)
                self.assertEqual(report.provider_name, "gemini")
                self.assertEqual(report.model, config.gemini_report_model)
                self.assertEqual(report.get_capabilities().max_output_tokens, 768)

    def test_remote_off_preflight_selects_honestly_labeled_sandbox_without_remote_constructors(self):
        with ExitStack() as stack:
            remote_constructors = [
                stack.enter_context(patch.object(provider, "__init__", autospec=True,
                                                 side_effect=AssertionError("remote constructor called")))
                for provider in (GeminiProvider, GroqProvider)
            ]
            orchestrator, _, _, verifier, monitoring, quality, report = self.compose(
                self.settings(allow_remote_ai=False, gemini_api_key="dummy-gemini-key",
                              groq_api_key="dummy-groq-key"))
            for constructor in remote_constructors:
                constructor.assert_not_called()
            for role, provider in (("verifier", verifier), ("monitoring", monitoring),
                                   ("quality", quality), ("report", report)):
                self.assertEqual(provider.provider_name, "fake_simulated", role)
                self.assertTrue(provider.is_simulated, role)
                self.assertIn("sandbox", provider.model, role)
            self.assertTrue(orchestrator.elaborador_provider.is_simulated)

    def test_fake_does_not_construct_any_real_llm_even_without_key_or_optin(self):
        with ExitStack() as stack:
            mocks = [
                stack.enter_context(patch.object(provider, "__init__", autospec=True,
                                                 side_effect=AssertionError("remote constructor called")))
                for provider in (GeminiProvider, GroqProvider)
            ]
            result = self.compose(self.settings(is_fake=True, monitoring_provider="gemini"))
            for constructor in mocks:
                constructor.assert_not_called()
            self.assertEqual(result[4].provider_name, "fake_simulated")
            self.assertIn("sandbox", result[4].model)

    def run_monitoring(self, output=2048, data=None, finish="STOP", max_output=8192):
        response = {"summary": "Sin tareas.", "referenced_task_ids": [], "evidence_ids": []}
        transport = Mock(return_value={"status_code": 200, "body": {
            "candidates": [{"finishReason": finish, "content": {"parts": [
                {"text": json.dumps(response if data is None else data)}]}}]}})
        provider = GeminiProvider(api_key="dummy", model="gemini-3.5-flash-lite", allow_remote_ai=True,
                                  context_tokens=8192, max_output_tokens=max_output, transport=transport)
        coordinator = StageInvocationCoordinator(GlobalBudgetTracker(1), 0, 0)
        agent = ProjectMonitoringAgent(provider, model=provider.model, coordinator=coordinator,
                                       max_generation_tokens=output)
        return agent, transport, coordinator

    def test_actual_body_required_nested_schema_and_reserved_output(self):
        for output in (2048, 4096):
            agent, transport, coordinator = self.run_monitoring(output)
            draft = agent.run("p", self.metrics, [])
            self.assertEqual(draft.referenced_task_ids, [])
            body = transport.call_args.kwargs["payload"]
            config = body["generationConfig"]
            self.assertEqual(config["maxOutputTokens"], output)
            schema = config["responseFormat"]["text"]["schema"]
            self.assertEqual(config["responseFormat"]["text"]["mimeType"], "APPLICATION_JSON")
            self.assertTrue({"referenced_task_ids", "evidence_ids"} <= set(schema["required"]))
            self.assertIn("$ref", schema["properties"]["task_state_claims"]["items"])
            self.assertIn("TaskStateClaim", schema["$defs"])
            self.assertIn("gemini-3.5-flash-lite:generateContent", transport.call_args.args[0])
            prompt = body["contents"][0]["parts"][0]["text"]
            metadata = json.loads(prompt.split("Evidence Context:\n", 1)[1])["context_metadata"]
            self.assertEqual(metadata["reserved_output_tokens"], output)
            self.assertLessEqual(metadata["budget_tokens"], 4096)
            self.assertLessEqual(metadata["estimated_prompt_tokens"] + output +
                                 metadata["safety_margin_tokens"], 8192)
            self.assertEqual(len(coordinator.recorded_attempts), 1)

    def test_omissions_and_unknown_ids_fail_validation_without_repair(self):
        for data in ({"summary": "Sin tareas."},
                     {"summary": "Sin tareas.", "referenced_task_ids": ["unknown"], "evidence_ids": ["unknown"]}):
            agent, transport, coordinator = self.run_monitoring(data=data)
            with self.assertRaises(ValidationError):
                agent.run("p", self.metrics, [])
            self.assertEqual(transport.call_count, 1)
            self.assertFalse(coordinator.recorded_attempts[0].validation_passed)

    def test_max_tokens_is_incomplete_and_consumes_one_call(self):
        agent, transport, coordinator = self.run_monitoring(finish="MAX_TOKENS")
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            agent.run("p", self.metrics, [])
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(coordinator.budget_tracker.total_calls_made, 1)
        self.assertEqual(coordinator.recorded_attempts[0].finish_reason, "MAX_TOKENS")

    def test_unsupported_output_fails_before_transport(self):
        agent, transport, _ = self.run_monitoring(output=4096, max_output=2048)
        with self.assertRaises(ContextBudgetExceededError):
            agent.run("p", self.metrics, [])
        transport.assert_not_called()
