"""Retired manual proposal simulation commands are explicitly refused."""
import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from agente_gestion import cli


class CLIProposalDryRunRetirementTests(unittest.TestCase):
    def test_manual_state_decision_and_dry_run_commands_are_refused_before_database_access(self):
        parser = cli.build_parser()
        with patch.object(cli, "SQLiteStorageAdapter", side_effect=AssertionError("database opened")) as storage:
            for operation, tail in (
                ("state", ["project", "proposal-1", "--db-path", "unused.sqlite"]),
                ("decide", ["project", "proposal-1", "--db-path", "unused.sqlite"]),
                ("dry-run", ["project", "proposal-1", "--db-path", "unused.sqlite"]),
                ("audit", ["project", "proposal-1", "request-1", "--db-path", "unused.sqlite"]),
            ):
                with self.subTest(operation=operation), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as stopped:
                        parser.parse_args(["proposals", operation, *tail])
                    self.assertEqual(stopped.exception.code, 2)
            storage.assert_not_called()

        listed = parser.parse_args(["proposals", "list", "project", "--db-path", "existing.sqlite"])
        shown = parser.parse_args([
            "proposals", "show", "project", "proposal-1", "--db-path", "existing.sqlite",
        ])
        self.assertEqual((listed.proposals_operation, shown.proposals_operation), ("list", "show"))
