"""Manager-login defaults and explicit environment loading."""

import os
import unittest
from unittest.mock import patch

from agente_gestion.api.settings import ApiSettings, load_api_settings


class ManagerLoginSettingsTests(unittest.TestCase):
    def test_manager_credentials_have_safe_defaults_and_password_is_not_serialized(self):
        settings = ApiSettings()
        self.assertEqual(settings.manager_login_email, "gestor@hormigestion.ec")
        self.assertTrue(settings.manager_login_password.get_secret_value())
        self.assertNotIn("manager_login_password", settings.model_dump())
        self.assertNotIn(settings.manager_login_password.get_secret_value(), repr(settings))

    def test_environment_overrides_manager_credentials_with_file_loading_disabled(self):
        values = {
            "AGENTE_CONFIG_PATH": "missing-manager-auth-settings.yaml",
            "MANAGER_LOGIN_EMAIL": "manager-override@example.test",
            "MANAGER_LOGIN_PASSWORD": "test-contraseña-🔐",
        }
        with patch.dict(os.environ, values, clear=True):
            settings = load_api_settings(env_file=None)
        self.assertEqual(settings.manager_login_email, values["MANAGER_LOGIN_EMAIL"])
        self.assertEqual(settings.manager_login_password.get_secret_value(), values["MANAGER_LOGIN_PASSWORD"])
        self.assertNotIn(values["MANAGER_LOGIN_PASSWORD"], repr(settings))

    def test_api_settings_and_environment_loader_share_the_same_manager_defaults(self):
        expected = ApiSettings()
        with patch.dict(os.environ, {"AGENTE_CONFIG_PATH": "missing-manager-auth-settings.yaml"}, clear=True):
            loaded = load_api_settings(env_file=None)
        self.assertEqual(loaded.manager_login_email, expected.manager_login_email)
        self.assertEqual(loaded.manager_login_password, expected.manager_login_password)


if __name__ == "__main__":
    unittest.main()
