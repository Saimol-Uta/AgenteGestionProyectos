"""Reproducible offline history example; no project database or providers used."""
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, closing, redirect_stdout, redirect_stderr
from datetime import datetime
from unittest.mock import patch

from agente_gestion import cli
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.domain.tasks import TaskCard


def planning_example():
    """A/B reuse the latest snapshot ID but have distinct content identities."""
    snapshots = []
    for day, state, assignee, due, priority in (
        (10, "pending", "A", 15, "normal"),
        (11, "en_curso", "B", 19, "high"),
    ):
        snapshots.append(WeeklySnapshot(
            id="snap-p", project_id="p",
            cutoff_datetime=datetime.fromisoformat("2026-10-10T12:00:00+00:00"),
            captured_at=datetime.fromisoformat(f"2026-10-{day}T12:00:00+00:00"),
            task_source_identity="synthetic/board/map", tasks_complete=True,
            tasks=[TaskCard(id="c", title="Synthetic", state=state,
                assignee_ids=[assignee], due_date=f"2026-10-{due}T12:00:00Z",
                priority=priority, detail="PRIVATE-SNAPSHOT-DETAIL",
                observed_fields=["state", "assignee_ids", "due_date", "priority"])],
        ))
    return snapshots


class CLIHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "history.db"
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        (self.artifacts / "sentinel").write_text("unchanged")
        self.snapshots = planning_example()
        self.writer = SQLiteStorageAdapter(str(self.db))
        for snapshot in self.snapshots:
            self.writer.save_snapshot(snapshot)
        self.ids = [hashlib.sha256(s.model_dump_json().encode()).hexdigest()
                    for s in self.snapshots]

    def fingerprint(self):
        files = {str(p.relative_to(self.root)): p.read_bytes()
                 for p in self.root.rglob("*") if p.is_file()}
        with closing(sqlite3.connect(self.db)) as conn:
            schema = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
        return files, schema, sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))

    def invoke(self, operation, *ids, project="p", db=None):
        before = self.fingerprint()
        out, err = io.StringIO(), io.StringIO()
        with ExitStack() as stack:
            for name in ("_build_orchestrator", "ArtifactStorageAdapter", "Settings",
                         "FakeProvider", "OllamaProvider", "GeminiProvider", "GroqProvider",
                         "MistralProvider", "create_composite_source", "create_task_source",
                         "create_code_source", "create_ci_source", "create_policy_source"):
                stack.enter_context(patch.object(cli, name, side_effect=AssertionError(name)))
            stack.enter_context(patch.object(SQLiteStorageAdapter, "_ensure_dir_and_schema",
                                            side_effect=AssertionError("writer initialized")))
            adapter = stack.enter_context(patch.object(cli, "SQLiteStorageAdapter", wraps=SQLiteStorageAdapter))
            stack.enter_context(patch("sys.argv", ["agente_gestion", "history", operation,
                project, *ids, "--db-path", str(db or self.db)]))
            stack.enter_context(redirect_stdout(out))
            stack.enter_context(redirect_stderr(err))
            code = cli.main()
            adapter.assert_called_once_with(db_path=str(db or self.db), read_only=True)
        self.assertEqual(self.fingerprint(), before, "CLI mutated DB/schema/artifacts/files")
        self.assertNotIn("PRIVATE-SNAPSHOT-DETAIL", out.getvalue() + err.getvalue())
        return code, out.getvalue(), err.getvalue()

    def test_list_exact_order_and_metadata_only(self):
        code, out, err = self.invoke("list")
        self.assertEqual((code, err), (0, ""))
        captures = json.loads(out)
        self.assertEqual([c["capture_id"] for c in captures], self.ids)
        self.assertEqual([c["snapshot_id"] for c in captures], ["snap-p", "snap-p"])
        for capture, snapshot in zip(captures, self.snapshots):
            self.assertEqual(set(capture), {"capture_id", "content_hash", "snapshot_id",
                "project_id", "captured_at", "cutoff_datetime", "task_source_identity",
                "tasks_complete", "is_complete", "task_count"})
            self.assertEqual(capture["content_hash"], capture["capture_id"])
            self.assertEqual(capture["project_id"], "p")
            self.assertEqual(capture["captured_at"], snapshot.captured_at.isoformat())
            self.assertEqual(capture["cutoff_datetime"], snapshot.cutoff_datetime.isoformat())
            self.assertEqual(capture["task_source_identity"], "synthetic/board/map")
            self.assertTrue(capture["tasks_complete"])
            self.assertEqual(capture["task_count"], 1)

    def test_compare_all_four_values_and_original_context(self):
        code, out, err = self.invoke("compare", *self.ids)
        self.assertEqual((code, err), (0, ""))
        result = json.loads(out)
        self.assertEqual([result[k] for k in ("previous_capture_id", "current_capture_id")], self.ids)
        self.assertNotEqual(*self.ids)
        self.assertEqual(self.ids, [
            "0e9de74b139db73b50db7615b0162b809cc4c18a19c765f4d52d2c27f333947b",
            "1df793912b546e0dd4fbc488136c29e8dc0ac423a69512b7f3e44712ce0b218b",
        ])
        self.assertEqual([result[k] for k in ("previous_snapshot_id", "current_snapshot_id")], ["snap-p"] * 2)
        self.assertEqual(result["project_id"], "p")
        self.assertEqual([result[k] for k in ("previous_captured_at", "current_captured_at")],
                         [s.model_dump(mode="json")["captured_at"] for s in self.snapshots])
        self.assertEqual(result["warnings"], [])
        self.assertEqual([(c["field"], c["before"], c["after"]) for c in result["changes"]], [
            ("assignee_ids", ["A"], ["B"]),
            ("due_date", "2026-10-15T12:00:00Z", "2026-10-19T12:00:00Z"),
            ("priority", "normal", "high"), ("state", "pending", "en_curso")])
        for change in result["changes"]:
            self.assertEqual(change["kind"], "field_changed")
            self.assertIsNone(change["actor"])
            self.assertEqual(change["previous_evidence_ids"], [])
            self.assertEqual(change["current_evidence_ids"], [])

    def test_identical_capture_and_empty_project(self):
        code, out, err = self.invoke("compare", self.ids[0], self.ids[0])
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["changes"], [])
        self.assertEqual(json.loads(self.invoke("list", project="unknown")[1]), [])

    def assert_failure(self, *args, **kwargs):
        code, out, err = self.invoke(*args, **kwargs)
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("ERROR: History"))
        self.assertNotIn("Traceback", err)
        self.assertNotIn("PRIVATE", err)

    def test_missing_db_and_legacy_never_create_or_migrate(self):
        missing = self.root / "nonexistent" / "missing.db"
        legacy = self.root / "legacy.db"
        with closing(sqlite3.connect(legacy)) as conn, conn:
            conn.execute("CREATE TABLE snapshots (data_json TEXT)")
        for db in (missing, legacy):
            for operation, ids in (("list", ()), ("compare", tuple(self.ids))):
                with self.subTest(db=db, operation=operation):
                    self.assert_failure(operation, *ids, db=db)
        self.assertFalse(missing.parent.exists())

    def test_missing_foreign_reversed_and_incompatible(self):
        self.assert_failure("compare", self.ids[0], "missing")
        self.assert_failure("compare", *self.ids, project="foreign")
        self.assert_failure("compare", *reversed(self.ids))
        other = self.snapshots[1].model_copy(update={"task_source_identity": "other"})
        self.writer.save_snapshot(other)
        other_id = hashlib.sha256(other.model_dump_json().encode()).hexdigest()
        self.assert_failure("compare", self.ids[0], other_id)
        foreign = self.snapshots[1].model_copy(update={"project_id": "foreign"})
        self.writer.save_snapshot(foreign)
        foreign_id = hashlib.sha256(foreign.model_dump_json().encode()).hexdigest()
        self.assert_failure("compare", self.ids[0], foreign_id)
        self.assert_failure("compare", foreign_id, self.ids[1])

    def test_corrupt_json_and_model_validation_do_not_dump_snapshot(self):
        for raw in ("PRIVATE broken JSON", '{"id":"PRIVATE"}'):
            with closing(sqlite3.connect(self.db)) as conn, conn:
                conn.execute("UPDATE snapshot_captures SET data_json=? WHERE capture_id=?", (raw, self.ids[0]))
            self.assert_failure("list")
            self.assert_failure("compare", *self.ids)
        data = self.snapshots[0].model_dump(mode="json")
        data["tasks"][0]["state"] = "PRIVATE invalid state"
        raw = json.dumps(data)
        capture_id = hashlib.sha256(raw.encode()).hexdigest()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE snapshot_captures SET data_json=?, capture_id=?, content_hash=? "
                         "WHERE capture_id=?", (raw, capture_id, capture_id, self.ids[0]))
        self.assert_failure("list")
        self.assert_failure("compare", capture_id, self.ids[1])

    def test_unknown_scope_warnings_and_membership_not_deletion(self):
        current = self.snapshots[1].model_copy(update={"tasks": [],
            "tasks_complete": False, "task_source_identity": None})
        self.writer.save_snapshot(current)
        capture_id = hashlib.sha256(current.model_dump_json().encode()).hexdigest()
        code, out, err = self.invoke("compare", self.ids[0], capture_id)
        self.assertEqual((code, err), (0, ""))
        result = json.loads(out)
        self.assertEqual(result["warnings"], ["limited_task_enumeration", "unknown_task_scope"])
        self.assertEqual([c["kind"] for c in result["changes"]], ["not_observed"])
        self.assertIsNone(result["changes"][0]["actor"])
