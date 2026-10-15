"""The fixture launcher accepts the documented frontend and local API origins."""
import unittest

from fastapi.testclient import TestClient

from agente_gestion.api.app import create_app
from scripts.serve_web_demo import demo_settings, FIXTURE_OPERATOR_TOKEN


class UnifiedDemoOriginTests(unittest.TestCase):
    def test_documented_frontend_origins_work_without_losing_api_origins(self):
        with demo_settings() as settings:
            with TestClient(create_app(settings)) as client:
                for origin in (
                    'http://127.0.0.1:8000', 'http://localhost:8000',
                    'http://127.0.0.1:5173', 'http://localhost:5173',
                ):
                    with self.subTest(origin=origin):
                        response = client.post('/api/v1/auth/login',
                            headers={'Origin': origin},
                            json={'token': FIXTURE_OPERATOR_TOKEN})
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertTrue(response.json()['authenticated'])
                        for capability in ('governance:proposal_execute', 'quality:decide', 'notifications:email_send'):
                            self.assertIn(capability, response.json()['capabilities'])
                rejected = client.post('/api/v1/auth/login',
                    headers={'Origin': 'https://attacker.invalid'},
                    json={'token': FIXTURE_OPERATOR_TOKEN})
                self.assertEqual(rejected.status_code, 403, rejected.text)
                self.assertEqual(rejected.json()['error']['code'], 'origen_no_permitido')
