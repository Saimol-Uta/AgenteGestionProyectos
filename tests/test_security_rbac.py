"""Unit and integration tests for RBAC, security policies, and cloud transfer gates (REQ-08, REQ-11)."""

from datetime import datetime, timezone
import os
import sqlite3
import tempfile
import unittest

from agente_gestion.domain.security_rbac import (
    Permission,
    PermissionDeniedSecurityError,
    SecurityAuditEntry,
    SecurityPrincipal,
    SystemRole,
    enforce_rbac,
    hash_token_fingerprint,
)
from agente_gestion.application.security import (
    ProjectSecurityPolicy,
    RemotePayloadSecurityError,
    RemoteProviderNotAllowedError,
    assert_cloud_transfer_policy,
    check_for_secrets,
)
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter


class SecurityRBACTests(unittest.TestCase):
    def setUp(self):
        self.dev_principal = SecurityPrincipal(
            actor_id="dev-alice",
            role=SystemRole.DEVELOPER,
            auth_mechanism="test_authority",
            auth_token="secret_dev_token_123",
        )
        self.mgr_principal = SecurityPrincipal(
            actor_id="mgr-bob",
            role=SystemRole.PROJECT_MANAGER,
            auth_mechanism="hmac_token",
            auth_token="secret_mgr_token_456",
        )
        self.monitoring_principal = SecurityPrincipal(
            actor_id="agent-monitor",
            role=SystemRole.MONITORING,
            auth_mechanism="test_authority",
            auth_token="secret_agent_token_789",
        )
        self.quality_principal = SecurityPrincipal(
            actor_id="agent-quality",
            role=SystemRole.QUALITY,
            auth_mechanism="test_authority",
            auth_token="secret_quality_token_101",
        )
        self.admin_principal = SecurityPrincipal(
            actor_id="admin-root",
            role=SystemRole.ADMIN,
            auth_mechanism="pki_signature",
            auth_token="secret_admin_token_999",
        )

    def test_developer_read_allowed_mutation_denied(self):
        # Developer can read tasks and code
        entry = enforce_rbac(self.dev_principal, Permission.READ_TASKS)
        self.assertEqual(entry.decision, "granted")

        entry_code = enforce_rbac(self.dev_principal, Permission.READ_CODE)
        self.assertEqual(entry_code.decision, "granted")

        # Developer CANNOT execute action on external systems
        with self.assertRaises(PermissionDeniedSecurityError):
            enforce_rbac(self.dev_principal, Permission.EXECUTE_ACTION)

        # Developer CANNOT decide weekly reports
        with self.assertRaises(PermissionDeniedSecurityError):
            enforce_rbac(self.dev_principal, Permission.DECIDE_REPORT)

    def test_monitoring_can_propose_but_cannot_execute_or_decide(self):
        # Monitoring agent can propose task actions
        entry = enforce_rbac(self.monitoring_principal, Permission.PROPOSE_TASK_ACTION)
        self.assertEqual(entry.decision, "granted")

        # Monitoring CANNOT authorize execution or execute actions
        with self.assertRaises(PermissionDeniedSecurityError):
            enforce_rbac(self.monitoring_principal, Permission.AUTHORIZE_ACTION_EXECUTION)
        with self.assertRaises(PermissionDeniedSecurityError):
            enforce_rbac(self.monitoring_principal, Permission.EXECUTE_ACTION)

    def test_quality_can_review_but_cannot_execute(self):
        # Quality agent can review proposals
        entry = enforce_rbac(self.quality_principal, Permission.REVIEW_TASK_PROPOSAL)
        self.assertEqual(entry.decision, "granted")

        # Quality agent CANNOT propose task actions or execute
        with self.assertRaises(PermissionDeniedSecurityError):
            enforce_rbac(self.quality_principal, Permission.PROPOSE_TASK_ACTION)
        with self.assertRaises(PermissionDeniedSecurityError):
            enforce_rbac(self.quality_principal, Permission.EXECUTE_ACTION)

    def test_project_manager_executive_privileges(self):
        # Project Manager can decide report, authorize execution, and execute action
        for perm in [
            Permission.DECIDE_REPORT,
            Permission.AUTHORIZE_ACTION_EXECUTION,
            Permission.EXECUTE_ACTION,
            Permission.TRIGGER_WEEKLY_SCHEDULE,
        ]:
            entry = enforce_rbac(self.mgr_principal, perm)
            self.assertEqual(entry.decision, "granted")

    def test_unverified_offline_assertion_rejected_for_privileged_operations(self):
        unverified_mgr = SecurityPrincipal(
            actor_id="mgr-unverified",
            role=SystemRole.PROJECT_MANAGER,
            auth_mechanism="offline_claim",
            auth_token="short",
        )
        # Allowed to read
        entry = enforce_rbac(unverified_mgr, Permission.READ_TASKS)
        self.assertEqual(entry.decision, "granted")

        # Privileged execution MUST be denied
        with self.assertRaises(PermissionDeniedSecurityError):
            enforce_rbac(unverified_mgr, Permission.EXECUTE_ACTION)
        with self.assertRaises(PermissionDeniedSecurityError):
            enforce_rbac(unverified_mgr, Permission.AUTHORIZE_ACTION_EXECUTION)

    def test_token_fingerprint_protection(self):
        token = "super_secret_raw_key_abcdef123456"
        fp = hash_token_fingerprint(token)
        self.assertTrue(fp.startswith("sha256:"))
        self.assertNotIn(token, fp)
        self.assertNotIn("super_secret", fp)

    def test_cloud_transfer_policy_enforcement(self):
        policy_disallowed = ProjectSecurityPolicy(
            project_id="PROJ-01",
            allow_remote_ai=False,
            allowed_remote_providers=["gemini"],
        )
        # Blocked when remote AI is disabled
        with self.assertRaises(RemoteProviderNotAllowedError):
            assert_cloud_transfer_policy(policy_disallowed, "gemini", {"query": "hello"})

        policy_allowed = ProjectSecurityPolicy(
            project_id="PROJ-01",
            allow_remote_ai=True,
            allowed_remote_providers=["gemini", "groq"],
        )
        # Blocked when provider is not in whitelist
        with self.assertRaises(RemoteProviderNotAllowedError):
            assert_cloud_transfer_policy(policy_allowed, "openai", {"query": "hello"})

        # Blocked when payload contains secrets
        secret_payload = {"code": "AKIA1234567890ABCDEF1234567890"}
        with self.assertRaises(RemotePayloadSecurityError):
            assert_cloud_transfer_policy(policy_allowed, "gemini", secret_payload)

        # Blocked when payload contains secret file path (.env)
        file_payload = {"diff_file": ".env.production"}
        with self.assertRaises(RemotePayloadSecurityError):
            assert_cloud_transfer_policy(policy_allowed, "gemini", file_payload)

        # Allowed when payload is clean
        clean_payload = {"diff_file": "src/main.py", "content": "def run(): pass"}
        assert_cloud_transfer_policy(policy_allowed, "gemini", clean_payload)

    def test_sqlite_security_audit_persistence_and_append_only(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            storage = SQLiteStorageAdapter(db_path=db_path)
            entry = SecurityAuditEntry(
                event_id="sec-evt-001",
                project_id="PROJ-01",
                actor_id="mgr-bob",
                role="project_manager",
                permission_checked="execute_action",
                resource_id="card-123",
                decision="granted",
                reason="Permission verified against role definition.",
                token_fingerprint="sha256:1234567890abcdef",
            )
            storage.append_security_audit_event(entry)

            events = storage.list_security_audit_events("PROJ-01")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].event_id, "sec-evt-001")
            self.assertEqual(events[0].decision, "granted")

            # Verify append-only trigger prevents updates
            conn = sqlite3.connect(db_path)
            with self.assertRaises((sqlite3.IntegrityError, sqlite3.OperationalError)):
                conn.execute("UPDATE security_audit_log SET decision = 'denied' WHERE event_id = 'sec-evt-001'")
            # Verify append-only trigger prevents deletes
            with self.assertRaises((sqlite3.IntegrityError, sqlite3.OperationalError)):
                conn.execute("DELETE FROM security_audit_log WHERE event_id = 'sec-evt-001'")
            conn.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)


if __name__ == "__main__":
    unittest.main()
