"""Phase 2 manager email contracts; all SMTP transports are local test doubles."""

import hashlib
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agente_gestion.adapters.outbox.hybrid_outbox import (
    HybridNotificationOutboxAdapter,
    OutboxIdempotencyConflict,
)
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.api.app import create_app
from agente_gestion.application.weekly_scheduler import WeeklySchedulerService, get_default_schedule
from agente_gestion.domain.notification_outbox import OutboxMessage
from tests.web_fixtures import OPERATOR_TOKEN, PROJECT_ID, build_web_api_fixture


class FakeSMTP:
    def __init__(self, host, port, *, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.tls = False
        self.login_args = None
        self.messages = []
        self.closed = False
        self.failure = None

    def starttls(self, *, context=None):
        self.tls = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, message):
        if self.failure:
            raise self.failure
        self.messages.append(message)

    def quit(self):
        self.closed = True


class Phase2EmailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.storage = SQLiteStorageAdapter(str(self.root / "outbox.sqlite"))
        self.outbox_dir = self.root / "outbox"

    @staticmethod
    def _email_module():
        return importlib.import_module("agente_gestion.adapters.outbox.smtp")

    @staticmethod
    def _message(*, delivery_key="a" * 64, fingerprint="b" * 64, window_id="same-window", **changes):
        values = dict(
            message_id="msg-manager-001",
            project_id="EMAIL-TEST",
            window_id=window_id,
            report_id="rep-email-001",
            channel="all",
            subject="Informe del proyecto",
            summary="Avance registrado: 50%.",
            body_markdown="## Estado\n- Avance: 50%\n",
            metrics={"progress_percentage": 50.0, "completed_tasks": 1},
            risks=["Una tarea retrasada."],
            recipient="manager@example.test",
            body_html="<html><body><p>Avance: 50%</p></body></html>",
            delivery_key=delivery_key,
            delivery_fingerprint=fingerprint,
        )
        values.update(changes)
        return OutboxMessage(**values)

    def test_outbox_message_adds_optional_email_fields_without_breaking_legacy_contract(self):
        message = self._message()
        self.assertEqual(message.delivery_key, "a" * 64)
        self.assertIn("Avance", message.body_html)
        legacy = OutboxMessage(
            message_id="legacy-message", project_id="EMAIL-TEST", window_id="legacy-window",
            report_id="rep-legacy", channel="sqlite", subject="Legacy", summary="Resumen",
            body_markdown="Texto", metrics={},
        )
        self.assertIsNone(legacy.delivery_key)
        self.assertIsNone(legacy.body_html)

    def test_smtp_config_from_env_is_explicit_and_uses_documented_defaults(self):
        smtp = self._email_module().SMTPConfig
        values = {
            "MANAGER_EMAIL": "manager@example.test",
            "SMTP_HOST": "smtp.example.test",
            "SMTP_USER": "sender@example.test",
            "SMTP_PASSWORD": "test-password",
        }
        with patch.dict(os.environ, values, clear=True):
            config = smtp.from_env()
        self.assertEqual(config.port, 587)
        self.assertTrue(config.use_tls)
        self.assertTrue(config.is_configured)
        self.assertNotIn("test-password", repr(config))

    def test_valid_recipient_without_smtp_configuration_uses_local_html_without_explicit_key(self):
        adapter = HybridNotificationOutboxAdapter(
            self.storage, self.outbox_dir, smtp_config=self._email_module().SMTPConfig(),
        )
        message = self._message(delivery_key=None, fingerprint=None)
        receipt = adapter.deliver(message)
        self.assertEqual(receipt.delivery_channel, "local_outbox_simulated_email")
        self.assertTrue((self.outbox_dir / f"{receipt.delivery_id}.html").is_file())
        self.assertEqual(len(self.storage.list_outbox_receipts("EMAIL-TEST")), 1)

    def test_unconfigured_smtp_persists_html_and_append_only_receipt_with_local_channel(self):
        smtp = self._email_module().SMTPConfig()
        adapter = HybridNotificationOutboxAdapter(
            self.storage, self.outbox_dir, smtp_config=smtp,
        )
        receipt = adapter.deliver(self._message())
        self.assertEqual(receipt.status, "DELIVERED")
        self.assertEqual(receipt.delivery_channel, "local_outbox_simulated_email")
        self.assertTrue((self.outbox_dir / f"{receipt.delivery_id}.html").is_file())
        self.assertEqual(len(self.storage.list_outbox_receipts("EMAIL-TEST")), 1)
        self.assertTrue(any(p.suffix == ".json" for p in self.outbox_dir.iterdir()))
        self.assertTrue(any(p.suffix == ".md" for p in self.outbox_dir.iterdir()))

    def test_smtp_success_sends_plain_and_html_with_tls_and_records_relay_acceptance(self):
        smtp = self._email_module().SMTPConfig(
            manager_email="manager@example.test", host="smtp.example.test", port=587,
            user="sender@example.test", password="test-password", use_tls=True,
        )
        clients = []

        def factory(host, port, *, timeout=None):
            client = FakeSMTP(host, port, timeout=timeout)
            clients.append(client)
            return client

        adapter = HybridNotificationOutboxAdapter(
            self.storage, self.outbox_dir, smtp_config=smtp, smtp_factory=factory,
        )
        message = self._message(delivery_key=None, fingerprint=None)
        receipt = adapter.deliver(message)
        replay = adapter.deliver(message)
        self.assertEqual(receipt.status, "DELIVERED")
        self.assertEqual(replay.delivery_id, receipt.delivery_id)
        self.assertEqual(receipt.delivery_channel, "smtp_email_sent")
        self.assertEqual(len(clients), 1)
        self.assertTrue(clients[0].tls)
        self.assertEqual(clients[0].login_args, ("sender@example.test", "test-password"))
        self.assertEqual(clients[0].messages[0].get_content_type(), "multipart/alternative")
        self.assertEqual(clients[0].messages[0].get_body(preferencelist=("plain",)).get_content().strip(),
                         "## Estado\n- Avance: 50%")
        self.assertEqual(clients[0].messages[0].get_body(preferencelist=("html",)).get_content_type(), "text/html")
        self.assertTrue(clients[0].closed)
        self.assertTrue((self.outbox_dir / f"{receipt.delivery_id}.html").is_file())

    def test_smtp_failure_never_simulates_success_and_same_key_replay_never_resends(self):
        smtp = self._email_module().SMTPConfig(
            manager_email="manager@example.test", host="smtp.example.test",
            user="sender@example.test", password="top-secret", use_tls=False,
        )
        calls = []

        def factory(host, port, *, timeout=None):
            client = FakeSMTP(host, port, timeout=timeout)
            client.failure = RuntimeError("rejected top-secret")
            calls.append(client)
            return client

        adapter = HybridNotificationOutboxAdapter(
            self.storage, self.outbox_dir, smtp_config=smtp, smtp_factory=factory,
        )
        first = adapter.deliver(self._message())
        replay = adapter.deliver(self._message())
        self.assertEqual(first.status, "FAILED")
        self.assertEqual(first.delivery_channel, "smtp_email_failed")
        self.assertNotEqual(first.delivery_channel, "local_outbox_simulated_email")
        self.assertEqual(replay.delivery_id, first.delivery_id)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("top-secret", first.detail)
        self.assertEqual(len(self.storage.list_outbox_receipts("EMAIL-TEST")), 1)

    def test_crash_after_relay_acceptance_is_ambiguous_and_never_resends(self):
        smtp = self._email_module().SMTPConfig(
            manager_email="manager@example.test", host="smtp.example.test",
            user="sender@example.test", password="top-secret", use_tls=False,
        )
        clients = []

        def factory(host, port, *, timeout=None):
            client = FakeSMTP(host, port, timeout=timeout)
            clients.append(client)
            return client

        class CrashAfterAcceptance(HybridNotificationOutboxAdapter):
            @staticmethod
            def _save_state(path, state):
                if state.get("status") == "sent":
                    raise RuntimeError("simulated process crash after SMTP acceptance")
                return HybridNotificationOutboxAdapter._save_state(path, state)

        message = self._message()
        adapter = CrashAfterAcceptance(
            self.storage, self.outbox_dir, smtp_config=smtp, smtp_factory=factory,
        )
        with self.assertRaisesRegex(RuntimeError, "simulated process crash"):
            adapter.deliver(message)
        self.assertEqual(len(clients[0].messages), 1)
        recovered = HybridNotificationOutboxAdapter(
            self.storage, self.outbox_dir, smtp_config=smtp, smtp_factory=factory,
        ).deliver(message)
        self.assertEqual(recovered.status, "FAILED")
        self.assertIn("unknown", recovered.detail)
        self.assertEqual(len(clients), 1)

    def test_same_key_conflicts_and_new_manual_key_is_not_suppressed_by_window_dedupe(self):
        smtp = self._email_module().SMTPConfig()
        adapter = HybridNotificationOutboxAdapter(self.storage, self.outbox_dir, smtp_config=smtp)
        first = adapter.deliver(self._message(delivery_key="a" * 64, fingerprint="b" * 64))
        with self.assertRaises(OutboxIdempotencyConflict):
            adapter.deliver(self._message(delivery_key="a" * 64, fingerprint="c" * 64))
        second = adapter.deliver(self._message(
            delivery_key="d" * 64, fingerprint="e" * 64, message_id="msg-manager-002",
        ))
        self.assertNotEqual(first.delivery_id, second.delivery_id)
        self.assertEqual(first.window_id, second.window_id)
        self.assertEqual(len(self.storage.list_outbox_receipts("EMAIL-TEST")), 2)

    def test_header_injection_and_invalid_recipients_are_rejected_before_transport(self):
        module = self._email_module()
        for recipient in ("bad@example.test\r\nBcc: victim@example.test", "not-an-email"):
            with self.subTest(recipient=recipient):
                with self.assertRaises(ValueError):
                    module.validate_email_address(recipient)
        with self.assertRaises(ValueError):
            module.safe_header("Asunto\r\nBcc: victim@example.test")

    def test_manager_report_uses_persisted_metrics_proposals_and_quality_suggestions_and_escapes_html(self):
        fixture = build_web_api_fixture(self.root / "web", governance_enabled=True)
        settings = fixture.client.app.state.settings
        storage = SQLiteStorageAdapter(str(fixture.database_path))
        report = storage.get_latest_report(PROJECT_ID)
        malicious_report = report.model_copy(update={
            "recommendations": ["Ampliar la evidencia antes de confirmar.", "<script>alert('unsafe')</script>"],
        })
        with patch.object(storage, "get_latest_report", return_value=malicious_report):
            service = importlib.import_module("agente_gestion.application.manager_notifications").ManagerNotificationService(
                storage, settings=settings,
            )
            message = service.compose_message(
                PROJECT_ID,
                recipient_email="manager@example.test",
                window_id="manual-test-window",
                delivery_key="f" * 64,
                delivery_fingerprint="1" * 64,
            )
        self.assertEqual(message.metrics["total_tasks"], 1)
        self.assertIn("Progreso global", message.body_markdown)
        self.assertIn(fixture.proposal_id, message.body_markdown)
        self.assertIn("Ampliar la evidencia", message.body_markdown)
        self.assertIn("http://127.0.0.1:5173/", message.body_markdown)
        self.assertNotIn("<script>alert", message.body_html)
        self.assertIn("&lt;script&gt;alert(&#x27;unsafe&#x27;)&lt;/script&gt;", message.body_html)

    def test_finished_run_helper_uses_stable_idempotency_key_and_allows_a_new_manual_send(self):
        fixture = build_web_api_fixture(self.root / "helper", governance_enabled=True)
        storage = SQLiteStorageAdapter(str(fixture.database_path))
        smtp = self._email_module().SMTPConfig()
        outbox = HybridNotificationOutboxAdapter(
            storage, fixture.outbox_dir, smtp_config=smtp,
        )
        service = importlib.import_module("agente_gestion.application.manager_notifications").ManagerNotificationService(
            storage, outbox, smtp_config=smtp,
        )
        first = service.send_project_email(
            PROJECT_ID, recipient_email="manager@example.test", idempotency_key="run:run-current",
        )
        replay = service.send_project_email(
            PROJECT_ID, recipient_email="manager@example.test", idempotency_key="run:run-current",
        )
        new_send = service.send_project_email(
            PROJECT_ID, recipient_email="manager@example.test", idempotency_key="manual:second-send",
        )
        self.assertEqual(first.delivery_id, replay.delivery_id)
        self.assertNotEqual(first.delivery_id, new_send.delivery_id)
        self.assertEqual(first.delivery_channel, "local_outbox_simulated_email")

    def test_protected_route_enforces_session_csrf_idempotency_and_project_bound_preview(self):
        fixture = build_web_api_fixture(self.root / "api", governance_enabled=True)
        base_settings = fixture.client.app.state.settings.model_copy(update={
            "manager_email": "manager@example.test",
        })
        app = create_app(base_settings)
        email_router = importlib.import_module("agente_gestion.api.email_notifications").router
        app.include_router(email_router)
        client = TestClient(app)
        base = f"/api/v1/projects/{PROJECT_ID}/notifications/email"
        origin = {"Origin": "http://testserver"}

        self.assertEqual(client.post(base, json={}, headers={**origin, "Idempotency-Key": "email-key-001"}).status_code, 401)
        login = client.post("/api/v1/auth/login", json={"token": OPERATOR_TOKEN}, headers=origin)
        csrf = login.json()["csrf_token"]
        wrong_origin = client.post(base, json={}, headers={
            "Origin": "https://attacker.test", "X-CSRF-Token": csrf,
            "Idempotency-Key": "email-key-002",
        })
        self.assertEqual(wrong_origin.status_code, 403)
        self.assertEqual(client.post(
            "/api/v1/projects/OTHER/notifications/email", json={},
            headers={**origin, "X-CSRF-Token": csrf, "Idempotency-Key": "email-key-003"},
        ).status_code, 404)
        self.assertEqual(client.post(base, json={}, headers=origin).status_code, 422)
        no_csrf = client.post(base, json={}, headers={**origin, "Idempotency-Key": "email-key-001"})
        self.assertEqual(no_csrf.status_code, 403)

        headers = {**origin, "X-CSRF-Token": csrf, "Idempotency-Key": "email-key-001"}
        first = client.post(base, json={}, headers=headers)
        replay = client.post(base, json={}, headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(first.json()["delivery_id"], replay.json()["delivery_id"])
        self.assertEqual(first.json()["delivery_channel"], "local_outbox_simulated_email")
        self.assertEqual(first.json()["status"], "DELIVERED")
        self.assertEqual(TestClient(app).get(first.json()["html_preview_url"]).status_code, 401)
        preview = client.get(first.json()["html_preview_url"])
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.headers["content-type"].split(";")[0], "text/html")
        self.assertEqual(client.get(
            f"/api/v1/projects/OTHER/notifications/email/{first.json()['delivery_id']}/preview",
        ).status_code, 404)

        conflict = client.post(base, json={"recipient_email": "other@example.test"}, headers=headers)
        self.assertEqual(conflict.status_code, 409)
        distinct = client.post(
            base, json={"recipient_email": "other@example.test"},
            headers={**origin, "X-CSRF-Token": csrf, "Idempotency-Key": "email-key-004"},
        )
        self.assertEqual(distinct.status_code, 200)
        self.assertEqual(distinct.json()["recipient_email"], "other@example.test")
        self.assertNotEqual(distinct.json()["delivery_id"], first.json()["delivery_id"])

    def test_route_can_resend_only_a_receipt_bound_to_the_requested_project(self):
        fixture = build_web_api_fixture(self.root / "resend", governance_enabled=True)
        base_settings = fixture.client.app.state.settings.model_copy(update={
            "manager_email": "manager@example.test",
        })
        app = create_app(base_settings)
        app.include_router(importlib.import_module("agente_gestion.api.email_notifications").router)
        client = TestClient(app)
        origin = {"Origin": "http://testserver"}
        login = client.post("/api/v1/auth/login", json={"token": OPERATOR_TOKEN}, headers=origin)
        rows, _total, _availability = app.state.control_plane._outbox_rows(PROJECT_ID, page=1, page_size=100)
        old_delivery_id = rows[0][0].delivery_id
        response = client.post(
            f"/api/v1/projects/{PROJECT_ID}/notifications/email",
            json={"outbox_delivery_id": old_delivery_id},
            headers={**origin, "X-CSRF-Token": login.json()["csrf_token"], "Idempotency-Key": "resend-key-001"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["delivery_channel"], "local_outbox_simulated_email")

    def test_weekly_scheduler_emails_configured_manager_and_keeps_legacy_recipient_unconfigured(self):
        smtp_module = self._email_module()
        fixture = build_web_api_fixture(self.root / "weekly", governance_enabled=True)
        storage = SQLiteStorageAdapter(str(fixture.database_path))
        config = smtp_module.SMTPConfig(manager_email="manager@example.test")
        outbox = HybridNotificationOutboxAdapter(
            storage, fixture.outbox_dir, smtp_config=config,
        )
        service = WeeklySchedulerService(storage, outbox, smtp_config=config)
        result = service.run_scheduled_cycle(
            PROJECT_ID, get_default_schedule(PROJECT_ID), force=True,
        )
        self.assertEqual(result.status, "EXECUTED")
        self.assertEqual(result.message.recipient, "manager@example.test")
        self.assertEqual(result.receipt.delivery_channel, "local_outbox_simulated_email")
        self.assertIn(fixture.proposal_id, result.message.body_markdown)

        legacy_outbox = HybridNotificationOutboxAdapter(storage, self.root / "legacy-outbox")
        legacy = WeeklySchedulerService(storage, legacy_outbox, smtp_config=smtp_module.SMTPConfig())
        self.assertIsNone(legacy.manager_email)
        self.assertEqual(legacy.legacy_recipient, "gestor_proyecto")
        legacy_result = legacy.run_scheduled_cycle(
            PROJECT_ID, get_default_schedule(PROJECT_ID), force=True,
        )
        self.assertEqual(legacy_result.message.recipient, "gestor_proyecto")
        self.assertIsNone(legacy_result.message.delivery_key)
        self.assertIsNone(legacy_result.receipt.delivery_channel)


if __name__ == "__main__":
    unittest.main()
