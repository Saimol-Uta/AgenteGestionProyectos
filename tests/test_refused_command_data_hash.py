"""Independent diagnostic probes: refused actions leave persisted bytes unchanged."""

import hashlib
from pathlib import Path
import tempfile
import unittest

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.agents.context import ContextBudgetExceededError
from agente_gestion.agents.verifier import VerifierAgent
from tests import test_multirepo_finding_actions as harness
from tests import test_verifier_context_budgets as budget_harness


class RefusedCommandDataHashTests(unittest.TestCase):
    def test_refused_approval_keeps_database_identical(self):
        with tempfile.TemporaryDirectory() as root:
            client, port, _, findings = harness.MultiRepoFindingActionTests()._client(Path(root))
            self.addCleanup(client.close)
            headers = {
                **harness.MultiRepoFindingActionTests._authenticate(client),
                "Idempotency-Key": "diagnostic-refused-hash-1",
            }
            database = Path(client.app.state.settings.db_path)
            before = hashlib.sha256(database.read_bytes()).hexdigest()
            response = client.post(
                f"/api/v1/runs/{harness.RUN_ID}/findings/{findings[0].id}/decision",
                headers=headers, json={"decision": "approve"},
            )
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["error"]["code"], "revision_independiente_requerida")
            self.assertEqual(port.calls, [])
            self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)

    def test_refused_context_never_calls_provider_or_changes_database(self):
        with tempfile.TemporaryDirectory() as root:
            database = Path(root) / "isolated.sqlite"
            SQLiteStorageAdapter(str(database))
            before = hashlib.sha256(database.read_bytes()).hexdigest()
            fixture = budget_harness.VerifierContextBudgetTests()
            fixture.setUp()
            provider = budget_harness.RecordingVerifierProvider(context_tokens=4096)
            with self.assertRaises(ContextBudgetExceededError):
                VerifierAgent(provider).run(
                    "P", [fixture.finding], [fixture.source], evidence_ids=[fixture.sha],
                )
            self.assertEqual(provider.requests, [])
            self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)
