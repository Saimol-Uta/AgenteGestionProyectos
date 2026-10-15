"""Operator-only configuration for governed external actions and SMTP."""
import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from agente_gestion.api.settings import ApiSettings, load_api_settings


class Phase2SettingsTests(unittest.TestCase):
    def test_external_write_permission_defaults_on_without_credentials(self):
        settings = ApiSettings()
        self.assertTrue(settings.external_writes_enabled)
        self.assertIsNone(settings.github_token)
        self.assertIsNone(settings.trello_token)
        self.assertIsNone(settings.manager_email)
        self.assertTrue(settings.smtp_use_tls)
        self.assertEqual(settings.smtp_port, 587)
        self.assertEqual(settings.trello_priority_labels, {})

    def test_environment_loads_live_actions_priority_mapping_and_smtp(self):
        env = {
            "AGENTE_CONFIG_PATH": "does-not-exist.yaml",
            "EXTERNAL_WRITES_ENABLED": "true",
            "GITHUB_TOKEN": "github-secret",
            "GITHUB_REPOSITORY": "owner/repository",
            "GITHUB_BASE_BRANCH": "develop",
            "TRELLO_API_KEY": "trello-api-secret",
            "TRELLO_TOKEN": "trello-secret",
            "TRELLO_PRIORITY_LABELS": '{"project":{"alta":"abc123","media":"def456","baja":"ghi789"}}',
            "MANAGER_EMAIL": "manager@example.org",
            "SMTP_HOST": "smtp.gmail.com", "SMTP_PORT": "587",
            "SMTP_USER": "sender@example.org", "SMTP_PASSWORD": "app-password",
            "SMTP_USE_TLS": "true",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = load_api_settings(env_file=None)
        self.assertTrue(settings.external_writes_enabled)
        self.assertEqual(settings.github_repository, "owner/repository")
        self.assertEqual(settings.github_base_branch, "develop")
        self.assertEqual(settings.trello_priority_labels["project"]["alta"], "abc123")
        self.assertEqual(settings.manager_email, "manager@example.org")
        self.assertEqual(settings.smtp_password.get_secret_value(), "app-password")
        serialized = settings.model_dump_json()
        for secret in ("github-secret", "trello-api-secret", "trello-secret", "app-password"):
            self.assertNotIn(secret, serialized)
        self.assertNotIn("app-password", repr(settings))

    def test_invalid_configuration_fails_closed(self):
        for values in (
            {"smtp_port": 0}, {"smtp_port": 65536},
            {"manager_email": "user@example.org\r\nBcc: victim@example.org"},
            {"manager_email": "not-an-email"},
            {"github_repository": "../outside"},
            {"trello_priority_labels": {"project": {"alta": ""}}},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                ApiSettings(**values)
        with patch.dict(os.environ, {"TRELLO_PRIORITY_LABELS": "not-json"}, clear=True):
            with self.assertRaises(ValueError):
                load_api_settings(env_file=None)

    def test_explicit_dotenv_config_path_uses_yaml_defaults_without_external_write_override(self):
        with patch.dict(os.environ, {"AGENTE_CONFIG_PATH": ".env"}, clear=True):
            settings = load_api_settings(env_file=None)
        self.assertTrue(settings.governance_enabled)
        self.assertTrue(settings.analysis_enabled)
        self.assertTrue(settings.external_writes_enabled)
        self.assertEqual(settings.projects[0].id, "HORMIGESTION")
        self.assertEqual(settings.allowed_origins[0], "http://localhost:5173")
