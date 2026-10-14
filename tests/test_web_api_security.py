"""Security regression cases for local operator sessions and API boundaries."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agente_gestion.api.app import ApiSettings, create_app
from agente_gestion.api.settings import load_api_settings
from tests.web_fixtures import OPERATOR_TOKEN, PROJECT_ID, build_web_api_fixture


class WebApiSessionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = build_web_api_fixture(Path(self.temp.name), session_ttl_seconds=60)

    def test_session_expires_from_server_clock_and_scope_is_server_derived(self):
        now = [100.0]
        app = create_app(self.fixture.client.app.state.settings, clock=lambda: now[0])
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"token": OPERATOR_TOKEN},
                            headers={"Origin": "http://testserver"})
        self.assertEqual(login.status_code, 200)
        cookie = login.cookies.get("h5_operator_session")
        session = app.state.sessions.get(cookie)
        self.assertEqual(session.actor, "operador-local-de-prueba")
        self.assertEqual(session.project_ids, (PROJECT_ID,))

        now[0] += 61
        self.assertFalse(client.get("/api/v1/auth/session").json()["authenticated"])

    def test_unprovisioned_token_and_client_claims_never_create_operator_identity(self):
        absent = build_web_api_fixture(Path(self.temp.name), database_name="no-token.sqlite",
                                       operator_token=None)
        public = absent.client.get("/api/v1/auth/session").json()
        self.assertFalse(public["authenticated"])
        self.assertFalse(public["login_available"])
        response = absent.client.post("/api/v1/auth/login", json={"token": OPERATOR_TOKEN},
                                      headers={"Origin": "http://testserver"})
        self.assertEqual(response.status_code, 503)

        forged = self.fixture.client.post("/api/v1/auth/login", json={
            "token": OPERATOR_TOKEN, "actor": "administrador", "project_ids": ["OTHER"],
        }, headers={"Origin": "http://testserver"})
        self.assertEqual(forged.status_code, 422)
        self.assertFalse(self.fixture.client.get("/api/v1/auth/session").json()["authenticated"])

    def test_dotenv_config_path_is_never_opened(self):
        dotenv = Path(self.temp.name) / ".env"
        with patch.dict(os.environ, {"AGENTE_CONFIG_PATH": str(dotenv)}):
            with patch.object(Path, "open", side_effect=AssertionError("dotenv must not be read")):
                settings = load_api_settings()
        self.assertEqual(settings.projects, ())

    def test_public_local_observer_is_anonymous_and_queries_remain_open(self):
        session = self.fixture.client.get("/api/v1/auth/session")
        self.assertEqual(session.status_code, 200)
        self.assertEqual(session.json(), {
            "authenticated": False,
            "actor": None,
            "csrf_token": None,
            "capabilities": [],
            "login_available": True,
        })
        self.assertEqual(self.fixture.client.get(
            f"/api/v1/projects/{PROJECT_ID}/tasks",
        ).status_code, 200)

    def test_login_origin_cookie_csrf_and_logout_revocation(self):
        client = self.fixture.client
        path = "/api/v1/auth/login"
        self.assertEqual(client.post(path, json={"token": OPERATOR_TOKEN}).status_code, 403)
        wrong_origin = client.post(path, json={"token": OPERATOR_TOKEN},
                                   headers={"Origin": "https://attacker.test"})
        self.assertEqual(wrong_origin.status_code, 403)
        denied = client.post(path, json={"token": "wrong-token"},
                             headers={"Origin": "http://testserver"})
        self.assertEqual(denied.status_code, 401)
        self.assertNotIn("wrong-token", denied.text)

        login = client.post(path, json={"token": OPERATOR_TOKEN},
                            headers={"Origin": "http://testserver"})
        self.assertEqual(login.status_code, 200)
        self.assertNotIn(OPERATOR_TOKEN, login.text)
        self.assertTrue(login.cookies.get("h5_operator_session"))
        cookie_header = login.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie_header)
        self.assertIn("samesite=strict", cookie_header)
        self.assertIn("path=/api/v1", cookie_header)
        session = client.get("/api/v1/auth/session").json()
        self.assertTrue(session["authenticated"])
        self.assertEqual(session["actor"], "operador-local-de-prueba")
        self.assertEqual(session["capabilities"], [
            "operator_session", "audit:read", "configuration:read",
        ])
        csrf = session["csrf_token"]
        self.assertTrue(csrf)

        self.assertEqual(client.post(
            "/api/v1/auth/logout", headers={"Origin": "http://testserver"},
        ).status_code, 403)
        logout = client.post("/api/v1/auth/logout", headers={
            "Origin": "http://testserver", "X-CSRF-Token": csrf,
        })
        self.assertEqual(logout.status_code, 204)
        self.assertFalse(client.get("/api/v1/auth/session").json()["authenticated"])

    def test_trusted_hosts_and_cors_are_restricted(self):
        hostile = TestClient(self.fixture.client.app, base_url="http://attacker.test")
        self.assertEqual(hostile.get("/api/v1/system/health").status_code, 400)
        preflight = self.fixture.client.options("/api/v1/projects", headers={
            "Origin": "https://attacker.test", "Access-Control-Request-Method": "GET",
        })
        self.assertNotEqual(preflight.headers.get("access-control-allow-origin"), "https://attacker.test")
        allowed = self.fixture.client.get("/api/v1/system/health", headers={
            "Origin": "http://testserver",
        })
        self.assertEqual(allowed.headers.get("access-control-allow-origin"), "http://testserver")

    def test_encoded_traversal_identifiers_cannot_select_other_resources(self):
        for path in (
            "/api/v1/projects/%2E%2E%2Foutside",
            f"/api/v1/projects/{PROJECT_ID}/tasks/%2E%2E%2Foutside",
        ):
            response = self.fixture.client.get(path)
            self.assertIn(response.status_code, (404, 422))
            self.assertNotEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
