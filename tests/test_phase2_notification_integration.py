"""Real application wiring for email; disposable state and no SMTP/network."""
import tempfile
import unittest
from pathlib import Path

from agente_gestion.api.app import create_app
from agente_gestion.application.orchestrator import ProjectOrchestrator, OrchestratorConfig
from agente_gestion.adapters.outbox.smtp import SMTPConfig
from agente_gestion.adapters.outbox.hybrid_outbox import HybridNotificationOutboxAdapter
from agente_gestion.application.manager_notifications import ManagerNotificationService
from agente_gestion.domain.tasks import TaskState
from tests import test_orchestrator as pipeline_fixture
from tests.web_fixtures import build_web_api_fixture, OPERATOR_TOKEN, PROJECT_ID


class Phase2NotificationIntegrationTests(unittest.TestCase):
    def test_factory_email_endpoint_and_outbox_expose_actual_delivery_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = build_web_api_fixture(Path(tmp), governance_enabled=True)
            client = fixture.client
            login = client.post('/api/v1/auth/login', json={'token': OPERATOR_TOKEN},
                                headers={'Origin': 'http://testserver'})
            self.assertEqual(login.status_code, 200, login.text)
            capabilities = login.json()['capabilities']
            self.assertIn('notifications:email_send', capabilities)
            self.assertNotIn('governance:proposal_review', capabilities)
            response = client.post(f'/api/v1/projects/{PROJECT_ID}/notifications/email',
                json={'recipient_email': 'manager@example.test'},
                headers={'Origin': 'http://testserver', 'X-CSRF-Token': login.json()['csrf_token'],
                         'Idempotency-Key': 'phase2-integration-email'})
            self.assertEqual(response.status_code, 200, response.text)
            delivery = response.json()
            self.assertEqual(delivery['delivery_channel'], 'local_outbox_simulated_email')
            preview = client.get(delivery['html_preview_url'])
            self.assertEqual(preview.status_code, 200, preview.text)
            records = client.get(f'/api/v1/projects/{PROJECT_ID}/schedule/outbox').json()
            matching = [item for item in records.get('items', []) if item['delivery_id'] == delivery['delivery_id']]
            self.assertEqual(len(matching), 1, records)
            self.assertEqual(matching[0]['delivery_channel'], delivery['delivery_channel'])

    def test_disabled_governance_never_grants_email_capability_or_writes_outbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = build_web_api_fixture(Path(tmp), governance_enabled=False)
            client = fixture.client
            login = client.post('/api/v1/auth/login', json={'token': OPERATOR_TOKEN},
                                headers={'Origin': 'http://testserver'})
            self.assertEqual(login.status_code, 200, login.text)
            self.assertNotIn('notifications:email_send', login.json()['capabilities'])
            endpoint = f'/api/v1/projects/{PROJECT_ID}/schedule/outbox'
            before = client.get(endpoint)
            self.assertEqual(before.status_code, 200, before.text)
            rejected = client.post(f'/api/v1/projects/{PROJECT_ID}/notifications/email',
                json={'recipient_email': 'manager@example.test'},
                headers={'Origin': 'http://testserver', 'X-CSRF-Token': login.json()['csrf_token'],
                         'Idempotency-Key': 'disabled-governance-email'})
            self.assertEqual(rejected.status_code, 503, rejected.text)
            self.assertEqual(rejected.json(), {'error': {
                'code': 'gobernanza_no_habilitada',
                'message': 'El servidor no habilitó las operaciones de gobernanza.',
            }})
            self.assertEqual(client.get(endpoint).json(), before.json())

    def test_manager_email_audit_is_idempotent_and_server_attributed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = build_web_api_fixture(Path(tmp), governance_enabled=True)
            client = fixture.client
            login = client.post("/api/v1/auth/login", json={"token": OPERATOR_TOKEN},
                                headers={"Origin": "http://testserver"})
            headers = {"Origin": "http://testserver", "X-CSRF-Token": login.json()["csrf_token"],
                       "Idempotency-Key": "email-audit-integration"}
            endpoint = f"/api/v1/projects/{PROJECT_ID}/notifications/email"
            first = client.post(endpoint, json={"recipient_email": "manager@example.test"}, headers=headers)
            replay = client.post(endpoint, json={"recipient_email": "manager@example.test"}, headers=headers)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json(), replay.json())
            audit = client.get(f"/api/v1/projects/{PROJECT_ID}/audit").json()
            events = [event for event in audit.get("items", [])
                      if event["resource_id"] == first.json()["delivery_id"] and event["event_type"] == "security"]
            self.assertEqual(len(events), 1, audit)
            self.assertEqual(events[0]["actor_verification"], "verified_local_session")

    def test_orchestrator_sends_once_after_risk_run_is_persisted_and_not_on_cache_replay(self):
        base = pipeline_fixture.OrchestratorTestCase()
        base.setUp()
        self.addCleanup(base.tearDown)
        smtp = SMTPConfig(manager_email='manager@example.test')
        service = ManagerNotificationService(base.sqlite,
            HybridNotificationOutboxAdapter(base.sqlite, Path(base.temp_dir) / 'outbox', smtp_config=smtp),
            smtp_config=smtp)
        pipeline = ProjectOrchestrator(base.sqlite, base.artifacts, base.fake_provider,
            base.fake_provider, aprobador_provider=base.fake_provider,
            config=OrchestratorConfig(), manager_notifications=service,
            manager_email='manager@example.test')
        snapshot = base._sample_snapshot_no_code()
        snapshot.tasks[0].state = TaskState.blocked
        run = pipeline.execute(snapshot=snapshot)
        self.assertIsNotNone(base.sqlite.get_report(run.report_id))
        receipts = base.sqlite.list_outbox_receipts(snapshot.project_id)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0].delivery_channel, 'local_outbox_simulated_email')
        self.assertEqual(pipeline.execute(snapshot=snapshot).run_id, run.run_id)
        self.assertEqual(len(base.sqlite.list_outbox_receipts(snapshot.project_id)), 1)
