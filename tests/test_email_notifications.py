"""Human-session recipient defaults preserve legacy token-only behavior."""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr

from agente_gestion.api.app import create_app
from tests.web_fixtures import OPERATOR_TOKEN, PROJECT_ID, build_web_api_fixture


class SessionEmailRecipientTests(unittest.TestCase):
    def test_human_session_email_is_default_but_token_only_uses_configured_manager_email(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = build_web_api_fixture(Path(temp), governance_enabled=True)
            human_email = "human-manager@example.test"
            configured_default = "notifications@example.test"
            settings = fixture.client.app.state.settings.model_copy(update={
                "manager_email": configured_default,
                "manager_login_email": human_email,
                "manager_login_password": SecretStr("test-manager-password"),
            })
            app = create_app(settings)
            human_client = TestClient(app)
            origin = {"Origin": "http://testserver"}
            base = f"/api/v1/projects/{PROJECT_ID}/notifications/email"

            human_login = human_client.post("/api/v1/auth/login", json={
                "email": human_email,
                "password": "test-manager-password",
            }, headers=origin)
            self.assertEqual(human_login.status_code, 200)
            human_response = human_client.post(base, json={}, headers={
                **origin,
                "X-CSRF-Token": human_login.json()["csrf_token"],
                "Idempotency-Key": "human-email-001",
            })
            self.assertEqual(human_response.status_code, 200)
            self.assertEqual(human_response.json()["recipient_email"], human_email)

            token_client = TestClient(app)
            token_login = token_client.post("/api/v1/auth/login", json={"token": OPERATOR_TOKEN}, headers=origin)
            self.assertEqual(token_login.status_code, 200)
            token_response = token_client.post(base, json={}, headers={
                **origin,
                "X-CSRF-Token": token_login.json()["csrf_token"],
                "Idempotency-Key": "token-email-001",
            })
            self.assertEqual(token_response.status_code, 200)
            self.assertEqual(token_response.json()["recipient_email"], configured_default)


if __name__ == "__main__":
    unittest.main()
