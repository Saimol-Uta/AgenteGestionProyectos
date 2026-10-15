"""Offline browse-only catalogue contracts; never open a real project DB."""
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, closing, redirect_stdout, redirect_stderr
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agente_gestion import cli
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.domain.action_proposal import ActionProposal
from agente_gestion.domain.tasks import TaskState
from tests import test_action_proposals


LIST_KEYS = {"proposal_id", "schema_version", "project_id", "task_id",
             "observed_capture_id", "field", "review_status", "authorization_status",
             "execution_status", "supporting_evidence_count"}
SHOW_KEYS = (LIST_KEYS - {"supporting_evidence_count"}) | {
    "current_value", "proposed_value", "rationale", "expected_impact", "evidence_ids"}


class CLIProposalsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "catalogue.db"
        artifacts = self.root / "artifacts"
        artifacts.mkdir()
        (artifacts / "sentinel").write_text("unchanged")
        fixture = test_action_proposals.ActionProposalTests()
        fixture.setUp()
        self.fixture = fixture
        self.writer = SQLiteStorageAdapter(str(self.db))
        self.proposal = self.persist()

    def persist(self, id="p1", project="project", field="priority", current=None, proposed="high"):
        snapshot = self.fixture.capture.snapshot.model_copy(deep=True)
        snapshot.project_id = project
        snapshot.tasks[0].observed_fields = [field]
        setattr(snapshot.tasks[0], field, current)
        snapshot.tasks[0].detail = "PRIVATE-SNAPSHOT"
        self.writer.save_snapshot(snapshot)
        capture = hashlib.sha256(snapshot.model_dump_json().encode()).hexdigest()
        evidence = self.fixture.evidence.model_copy(deep=True)
        evidence.summary = "PRIVATE-SUMMARY"
        evidence.provenance.project_id = project
        evidence.provenance.payload = {field: current, "snippet": "PRIVATE-SNIPPET",
            "url": "https://PRIVATE-URL", "other": "PRIVATE-PAYLOAD"}
        proposal = ActionProposal(**(self.fixture.data | dict(proposal_id=id,
            project_id=project, observed_capture_id=capture, field=field,
            current_value=current, proposed_value=proposed)))
        self.writer.save_action_proposal(proposal, [evidence])
        return proposal

    def fingerprint(self):
        files = {str(p.relative_to(self.root)): p.read_bytes()
                 for p in self.root.rglob("*") if p.is_file()}
        with closing(sqlite3.connect(self.db)) as conn:
            schema = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
        return files, schema, sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))

    def invoke(self, operation, *ids, project="project", db=None, failure=None):
        before = self.fingerprint()
        out, err = io.StringIO(), io.StringIO()
        with ExitStack() as stack:
            denied = []
            for name in ("_build_orchestrator", "ArtifactStorageAdapter", "Settings",
                         "FakeProvider", "GeminiProvider", "GroqProvider",
                         "create_analysis_composite_source", "create_task_source",
                         "create_code_source", "create_ci_source", "create_policy_source"):
                denied.append(stack.enter_context(patch.object(cli, name, side_effect=AssertionError(name))))
            denied.append(stack.enter_context(patch.object(SQLiteStorageAdapter, "_ensure_dir_and_schema",
                                                           side_effect=AssertionError("writer initialized"))))
            adapter = stack.enter_context(patch.object(cli, "SQLiteStorageAdapter", wraps=SQLiteStorageAdapter))
            if failure:
                stack.enter_context(patch.object(SQLiteStorageAdapter,
                    "list_action_proposals" if operation == "list" else "get_action_proposal",
                    side_effect=failure))
            stack.enter_context(patch("sys.argv", ["agente_gestion", "proposals", operation,
                project, *ids, "--db-path", str(db or self.db)]))
            stack.enter_context(redirect_stdout(out))
            stack.enter_context(redirect_stderr(err))
            code = cli.main()
            adapter.assert_called_once_with(db_path=str(db or self.db), read_only=True)
            for mock in denied:
                mock.assert_not_called()
        self.assertEqual(self.fingerprint(), before, "CLI mutated DB/schema/directories/artifacts")
        self.assertNotIn("PRIVATE", out.getvalue() + err.getvalue())
        return code, out.getvalue(), err.getvalue()

    def assert_failure(self, *args, **kwargs):
        result = self.invoke(*args, **kwargs)
        self.assertEqual(result[0:2], (1, ""))
        self.assertIn(result[2], {
            "ERROR: Proposal not found.\n",
            "ERROR: Proposal catalogue unavailable; no migration performed.\n",
            "ERROR: Proposal read failed; check database integrity.\n"})
        return result

    def test_list_exact_projection_order_status_and_project_filter(self):
        self.persist(id="a")
        self.persist(id="foreign", project="other")
        code, out, err = self.invoke("list")
        self.assertEqual((code, err), (0, ""))
        rows = json.loads(out)
        self.assertEqual([row["proposal_id"] for row in rows], ["a", "p1"])
        for row in rows:
            self.assertEqual(set(row), LIST_KEYS)
            expected = self.proposal.model_dump(mode="json") | {"supporting_evidence_count": 1}
            expected["proposal_id"] = row["proposal_id"]
            self.assertEqual(row, {key: expected[key] for key in LIST_KEYS})
            self.assertIs(type(row["supporting_evidence_count"]), int)
            for key in LIST_KEYS - {"supporting_evidence_count"}:
                self.assertIs(type(row[key]), str)
        self.assertEqual(json.loads(self.invoke("list", project="other")[1])[0]["proposal_id"], "foreign")
        self.assertEqual(json.loads(self.invoke("list", project="unknown")[1]), [])

    def test_show_all_known_fields_and_json_value_types(self):
        for index, (field, current, proposed) in enumerate([
            ("priority", None, "high"), ("state", TaskState.unknown, TaskState.pending),
            ("assignee_ids", None, []), ("assignee_ids", [], ["u"]),
            ("start_date", None, datetime(2026, 10, 11, 12)),
            ("due_date", datetime(2026, 10, 10, 12), None),
        ]):
            with self.subTest(field=field, current=current):
                proposal = self.persist(id=str(index), field=field, current=current, proposed=proposed)
                code, out, err = self.invoke("show", proposal.proposal_id)
                self.assertEqual((code, err), (0, ""))
                result = json.loads(out)
                self.assertEqual(set(result), SHOW_KEYS)
                self.assertEqual(result, proposal.model_dump(mode="json"))
                for key, value in result.items():
                    self.assertIs(type(value), type(proposal.model_dump(mode="json")[key]))
                self.assertEqual([result[k] for k in ("review_status", "authorization_status",
                    "execution_status")], ["pending", "not_authorized", "not_executed"])

    def test_empty_catalogue_and_foreign_notfound_indistinguishable(self):
        empty = self.root / "empty.db"
        SQLiteStorageAdapter(str(empty))
        self.assertEqual(self.invoke("list", db=empty), (0, "[]\n", ""))
        missing = self.assert_failure("show", "missing")
        foreign = self.assert_failure("show", "p1", project="other")
        self.assertEqual(missing, foreign)

    def test_missing_legacy_and_private_exception_never_create_or_migrate(self):
        missing = self.root / "PRIVATE-directory" / "missing.db"
        legacy = self.root / "legacy.db"
        with closing(sqlite3.connect(legacy)) as conn, conn:
            conn.execute("CREATE TABLE snapshots (data_json TEXT)")
        for operation, ids in [("list", ()), ("show", ("p1",))]:
            for db in (missing, legacy):
                self.assert_failure(operation, *ids, db=db)
            self.assert_failure(operation, *ids, failure=ValueError("PRIVATE-exception"))
        self.assertFalse(missing.parent.exists())

    def test_malformed_record_and_invalid_model_errors_are_static(self):
        for raw, update_hash in [("PRIVATE broken JSON", False),
                                 ('{"proposal":{"rationale":"PRIVATE"},"evidence":[]}', True)]:
            with closing(sqlite3.connect(self.db)) as conn, conn:
                conn.execute("UPDATE action_proposals SET data_json=?, content_hash=?",
                    (raw, hashlib.sha256(raw.encode()).hexdigest() if update_hash else "bad"))
            for operation, ids in [("list", ()), ("show", ("p1",))]:
                self.assert_failure(operation, *ids)
