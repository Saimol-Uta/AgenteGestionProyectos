"""Default web capabilities and isolated project dotenv startup regressions."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agente_gestion.api.app import create_app
from agente_gestion.api.settings import ApiSettings, load_api_settings
from agente_gestion.application import config as engine_config
from tests.web_fixtures import PROJECT_ID, build_web_api_fixture


FLAGS = (
    "H5_ANALYSIS_ENABLED", "H5_GOVERNANCE_ENABLED",
    "H5_REPORT_DECISIONS_ENABLED", "H5_WEEKLY_CYCLE_ENABLED",
)
FIELDS = (
    "analysis_enabled", "governance_enabled",
    "report_decisions_enabled", "weekly_cycle_enabled",
)


class WebStartupSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=".runtime")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_capabilities_and_external_writes_default_enabled(self):
        with patch.dict(os.environ, {}, clear=True):
            loaded = load_api_settings(env_file=None)
        for field in FIELDS:
            with self.subTest(field=field):
                self.assertTrue(getattr(ApiSettings(), field))
                self.assertTrue(getattr(loaded, field))
        self.assertTrue(ApiSettings().external_writes_enabled)
        self.assertTrue(loaded.external_writes_enabled)
        self.assertIsNone(loaded.operator_token)

    def test_explicit_false_environment_preserves_disabled_capabilities(self):
        with patch.dict(os.environ, {
            **{name: "false" for name in FLAGS},
            "EXTERNAL_WRITES_ENABLED": "false",
        }, clear=True):
            settings = load_api_settings(env_file=None)
        self.assertTrue(all(getattr(settings, field) is False for field in FIELDS))
        self.assertFalse(settings.external_writes_enabled)

    def test_root_dotenv_loads_automatically_without_mutating_process_environment(self):
        (self.root / ".env").write_text(
            'MANAGER_LOGIN_EMAIL="file-manager@example.test"\n'
            'MANAGER_LOGIN_PASSWORD="isolated-file-password"\n'
            'H5_GOVERNANCE_ENABLED=false\nSMTP_PORT=2525\n'
            'AGENTE_CONFIG_PATH="missing-startup-settings.yaml"\n',
            encoding="utf-8",
        )
        with patch("agente_gestion.api.settings.PROJECT_ROOT", self.root), patch.object(
            engine_config, "PROJECT_ROOT", self.root,
        ):
            with patch.dict(os.environ, {}, clear=True):
                settings = load_api_settings()
                self.assertEqual(dict(os.environ), {})
        self.assertEqual(settings.manager_login_email, "file-manager@example.test")
        self.assertEqual(settings.manager_login_password.get_secret_value(), "isolated-file-password")
        self.assertFalse(settings.governance_enabled)
        self.assertTrue(settings.analysis_enabled)
        self.assertEqual(settings.smtp_port, 2525)
        self.assertTrue(settings.external_writes_enabled)

    def test_process_environment_wins_and_dotenv_can_be_disabled(self):
        (self.root / ".env").write_text(
            "MANAGER_LOGIN_EMAIL=file-manager@example.test\nH5_ANALYSIS_ENABLED=false\n",
            encoding="utf-8",
        )
        with patch("agente_gestion.api.settings.PROJECT_ROOT", self.root), patch.object(
            engine_config, "PROJECT_ROOT", self.root,
        ):
            with patch.dict(os.environ, {"MANAGER_LOGIN_EMAIL": "process-manager@example.test"}, clear=True):
                settings = load_api_settings()
                disabled = load_api_settings(env_file=None)
        self.assertEqual(settings.manager_login_email, "process-manager@example.test")
        self.assertFalse(settings.analysis_enabled)
        self.assertEqual(disabled.manager_login_email, "process-manager@example.test")
        self.assertTrue(disabled.analysis_enabled)

    def test_missing_dotenv_uses_defaults_and_invalid_flags_are_rejected(self):
        with patch("agente_gestion.api.settings.PROJECT_ROOT", self.root), patch.object(
            engine_config, "PROJECT_ROOT", self.root,
        ):
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(load_api_settings().manager_login_email, "gestor@hormigestion.ec")
                (self.root / ".env").write_text("H5_ANALYSIS_ENABLED=not-a-boolean\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "H5_ANALYSIS_ENABLED"):
                    load_api_settings()

    def test_default_capabilities_do_not_bypass_session_csrf_or_origin(self):
        fixture = build_web_api_fixture(self.root)
        settings = ApiSettings(
            db_path=str(fixture.database_path),
            projects=fixture.client.app.state.settings.projects,
            allowed_origins=["http://testserver"], allowed_hosts=["testserver"],
            job_ledger_path=str(self.root / "jobs.sqlite"),
        )
        with TestClient(create_app(settings)) as client:
            endpoint = f"/api/v1/projects/{PROJECT_ID}/notifications/email"
            rejected = client.post(endpoint, json={}, headers={"Origin": "http://testserver", "Idempotency-Key": "startup-check-001"})
            self.assertEqual(rejected.status_code, 401)
            login = client.post("/api/v1/auth/login", json={
                "email": "gestor@hormigestion.ec", "password": "HormiGestion2026!",
            }, headers={"Origin": "http://testserver"})
            self.assertEqual(login.status_code, 200)
            self.assertIn("notifications:email_send", login.json()["capabilities"])
            no_csrf = client.post(endpoint, json={}, headers={"Origin": "http://testserver", "Idempotency-Key": "startup-check-002"})
            self.assertEqual(no_csrf.status_code, 403)
            no_origin = client.post(endpoint, json={}, headers={"X-CSRF-Token": login.json()["csrf_token"], "Idempotency-Key": "startup-check-003"})
            self.assertEqual(no_origin.status_code, 403)
            self.assertTrue(settings.external_writes_enabled)


if __name__ == "__main__":
    unittest.main()
