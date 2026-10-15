"""Security regression cases for local operator sessions and API boundaries."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.api.app import ApiSettings, create_app
from agente_gestion.api.settings import load_api_settings
from tests.web_fixtures import OPERATOR_TOKEN, PROJECT_ID, build_web_api_fixture


class WebApiSessionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = build_web_api_fixture(Path(self.temp.name), session_ttl_seconds=60)
        self.manager_email = "gestor-ñ@example.test"
        self.manager_password = "contraseña-🔐"
        self.manager_settings = self.fixture.client.app.state.settings.model_copy(update={
            "manager_login_email": self.manager_email,
            "manager_login_password": SecretStr(self.manager_password),
        })
        self.manager_client = TestClient(create_app(self.manager_settings))

    def _failed_auth_audits(self):
        storage = SQLiteStorageAdapter(str(self.fixture.database_path), read_only=True)
        return [
            event for event in storage.list_security_audit_events("GLOBAL")
            if event.permission_checked == "manager_login" and event.decision == "denied"
        ]

    def test_manager_login_returns_full_authoritative_session_and_csrf_cookie(self):
        response = self.manager_client.post(
            "/api/v1/auth/login",
            json={
                "email": self.manager_email,
                "password": self.manager_password,
                "token": "not-the-operator-token",
            },
            headers={"Origin": "http://testserver"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["authenticated"])
        self.assertEqual(body["email"], self.manager_email)
        self.assertEqual(body["notification_email"], self.manager_email)
        self.assertEqual(body["role"], "PROJECT_MANAGER")
        self.assertEqual(body["actor"], self.manager_email)
        self.assertTrue(body["csrf_token"])
        self.assertTrue(response.cookies.get("h5_operator_session"))
        self.assertIn("httponly", response.headers["set-cookie"].lower())
        session = self.manager_client.get("/api/v1/auth/session").json()
        self.assertEqual(session["email"], self.manager_email)
        self.assertEqual(session["notification_email"], self.manager_email)
        self.assertEqual(session["role"], "PROJECT_MANAGER")

    def test_unknown_email_and_wrong_password_are_audited_401_problems_without_sessions(self):
        before = len(self._failed_auth_audits())
        for credentials in (
            {"email": "cualquiera@test.com", "password": self.manager_password},
            {"email": self.manager_email, "password": "contraseña-incorrecta"},
        ):
            with self.subTest(email=credentials["email"]):
                response = self.manager_client.post(
                    "/api/v1/auth/login", json=credentials,
                    headers={"Origin": "http://testserver"},
                )
                self.assertEqual(response.status_code, 401)
                self.assertEqual(
                    response.json()["error"]["message"],
                    "Correo electrónico o contraseña incorrectos.",
                )
                self.assertEqual(response.headers["content-type"], "application/problem+json")
                self.assertEqual(response.json()["type"], "about:blank")
                self.assertEqual(response.json()["title"], "Unauthorized")
                self.assertEqual(response.json()["status"], 401)
                self.assertEqual(response.json()["detail"], "Correo electrónico o contraseña incorrectos.")
                self.assertFalse(response.cookies.get("h5_operator_session"))
                self.assertFalse(self.manager_client.get("/api/v1/auth/session").json()["authenticated"])
        events = self._failed_auth_audits()[before:]
        self.assertEqual(len(events), 2)
        self.assertTrue(all(event.project_id == "GLOBAL" for event in events))
        self.assertNotIn(self.manager_email, repr(events))
        self.assertNotIn(self.manager_password, repr(events))

    def test_presence_of_either_manager_field_cannot_fall_back_to_a_valid_token(self):
        requests = (
            {"token": OPERATOR_TOKEN, "email": self.manager_email},
            {"token": OPERATOR_TOKEN, "password": self.manager_password},
            {"token": OPERATOR_TOKEN, "email": None, "password": self.manager_password},
            {"token": OPERATOR_TOKEN, "email": self.manager_email, "password": None},
            {"token": OPERATOR_TOKEN, "email": "", "password": self.manager_password},
            {"token": OPERATOR_TOKEN, "email": self.manager_email, "password": ""},
        )
        for body in requests:
            with self.subTest(body=body):
                response = self.manager_client.post(
                    "/api/v1/auth/login", json=body,
                    headers={"Origin": "http://testserver"},
                )
                self.assertEqual(response.status_code, 401)
                self.assertEqual(
                    response.json()["error"]["message"],
                    "Correo electrónico o contraseña incorrectos.",
                )
                self.assertFalse(self.manager_client.get("/api/v1/auth/session").json()["authenticated"])

    def test_client_identity_claims_are_forbidden_for_manager_login(self):
        response = self.manager_client.post("/api/v1/auth/login", json={
            "email": self.manager_email,
            "password": self.manager_password,
            "actor": "administrador",
            "role": "ADMIN",
            "project_ids": ["OTHER"],
        }, headers={"Origin": "http://testserver"})
        self.assertEqual(response.status_code, 422)
        self.assertFalse(self.manager_client.get("/api/v1/auth/session").json()["authenticated"])

    def test_unicode_credentials_are_compared_as_utf8_bytes(self):
        response = self.manager_client.post("/api/v1/auth/login", json={
            "email": self.manager_email, "password": self.manager_password,
        }, headers={"Origin": "http://testserver"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["email"], self.manager_email)

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
        self.assertTrue(public["login_available"])
        response = absent.client.post("/api/v1/auth/login", json={"token": OPERATOR_TOKEN},
                                      headers={"Origin": "http://testserver"})
        self.assertEqual(response.status_code, 401)

        forged = self.fixture.client.post("/api/v1/auth/login", json={
            "token": OPERATOR_TOKEN, "actor": "administrador", "project_ids": ["OTHER"],
        }, headers={"Origin": "http://testserver"})
        self.assertEqual(forged.status_code, 422)
        self.assertFalse(self.fixture.client.get("/api/v1/auth/session").json()["authenticated"])

    def test_dotenv_config_path_is_never_opened(self):
        dotenv = Path(self.temp.name) / ".env"
        original_open = Path.open

        def guarded_open(path, *args, **kwargs):
            if path.resolve(strict=False) == dotenv.resolve(strict=False):
                raise AssertionError("dotenv must not be read as YAML")
            return original_open(path, *args, **kwargs)

        with patch.dict(os.environ, {"AGENTE_CONFIG_PATH": str(dotenv)}):
            with patch.object(Path, "open", guarded_open):
                settings = load_api_settings(env_file=None)
        self.assertEqual(settings.projects[0].id, "HORMIGESTION")

    def test_public_local_observer_is_anonymous_and_queries_remain_open(self):
        session = self.fixture.client.get("/api/v1/auth/session")
        self.assertEqual(session.status_code, 200)
        self.assertEqual(session.json(), {
            "authenticated": False,
            "actor": None,
            "csrf_token": None,
            "capabilities": [],
            "login_available": True,
            "email": None,
            "notification_email": None,
            "role": None,
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
        self.assertIsNone(session["email"])
        self.assertEqual(session["notification_email"], "gestor@hormigestion.ec")
        self.assertIsNone(session["role"])
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
