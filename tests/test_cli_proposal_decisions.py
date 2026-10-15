"""The proposals CLI exposes read-only list/show commands only."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agente_gestion import cli
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.domain.action_proposal import ActionProposal
from agente_gestion.domain.evidence import EvidenceItem, EvidenceProvenance
from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.domain.tasks import TaskCard


class CLIProposalDecisionsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / "catalogue.db"
        store = SQLiteStorageAdapter(str(self.db))
        now = datetime(2026, 10, 10, 12)
        snapshot = WeeklySnapshot(id="snapshot", project_id="project",
            cutoff_datetime=now, captured_at=now, tasks=[TaskCard(
                id="task", external_id="external", title="PRIVATE-TITLE",
                priority=None, observed_fields=["priority"])])
        capture = store.save_snapshot_with_capture_receipt(snapshot)
        evidence = EvidenceItem(category="task", summary="PRIVATE-SUMMARY",
            provenance=EvidenceProvenance(id="e1", project_id="project", source="manual",
                timestamp=now, external_id="external", payload={"priority": None,
                "snippet": "PRIVATE-SNIPPET", "url": "https://PRIVATE-URL",
                "path": "/PRIVATE-PATH"}))
        proposal = ActionProposal(proposal_id="p1", project_id="project", task_id="task",
            observed_capture_id=capture.capture_id, field="priority", current_value=None,
            proposed_value="high", evidence_ids=["e1"], rationale="PRIVATE-RATIONALE",
            expected_impact="PRIVATE-IMPACT")
        store.save_action_proposal(proposal, [evidence])

    def _invoke(self, operation):
        args = cli.argparse.Namespace(
            subcommand="proposals", proposals_operation=operation, project="project",
            proposal_id="p1", db_path=str(self.db),
        )
        out, err = io.StringIO(), io.StringIO()
        real_storage = SQLiteStorageAdapter

        def read_only_storage(*args, **kwargs):
            self.assertTrue(kwargs.get("read_only"), "proposal reads must not open a writer")
            return real_storage(*args, **kwargs)

        with patch.object(cli, "SQLiteStorageAdapter", side_effect=read_only_storage), \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.proposals_command(args)
        return code, out.getvalue(), err.getvalue()

    def test_only_read_only_proposal_cli_entrypoints_remain(self):
        parser = cli.build_parser()
        for operation in ("state", "decide", "dry-run", "audit"):
            with self.subTest(operation=operation), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(["proposals", operation, "project", "p1", "--db-path", str(self.db)])
        listed = parser.parse_args(["proposals", "list", "project", "--db-path", str(self.db)])
        shown = parser.parse_args(["proposals", "show", "project", "p1", "--db-path", str(self.db)])
        self.assertEqual((listed.proposals_operation, shown.proposals_operation), ("list", "show"))

    def test_list_and_show_are_allowlisted_read_only_projections(self):
        original = self.db.read_bytes()
        list_code, list_output, list_error = self._invoke("list")
        show_code, show_output, show_error = self._invoke("show")
        self.assertEqual((list_code, list_error), (0, ""))
        self.assertEqual((show_code, show_error), (0, ""))
        listed = json.loads(list_output)
        shown = json.loads(show_output)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["proposal_id"], "p1")
        self.assertEqual(set(shown), {
            "proposal_id", "schema_version", "project_id", "task_id", "observed_capture_id",
            "field", "review_status", "authorization_status", "execution_status",
            "current_value", "proposed_value", "rationale", "expected_impact", "evidence_ids",
        })
        self.assertEqual(self.db.read_bytes(), original)
        self.assertEqual(shown["rationale"], "PRIVATE-RATIONALE")
        self.assertEqual(shown["expected_impact"], "PRIVATE-IMPACT")
        for private_evidence in (
            "PRIVATE-TITLE", "PRIVATE-SUMMARY", "PRIVATE-SNIPPET", "PRIVATE-URL", "PRIVATE-PATH",
        ):
            self.assertNotIn(private_evidence, list_output + show_output)
