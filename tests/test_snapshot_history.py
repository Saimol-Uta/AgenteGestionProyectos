"""Offline immutable capture, migration and read-only contracts."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.ports.storage import StoragePort


def digest(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SnapshotHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "history.db"

    def snapshot(self, **changes):
        values = dict(id="snap-p", project_id="p", cutoff_datetime="2026-01-02T00:00:00Z",
                      captured_at="2026-01-02T00:00:00Z", tasks_complete=True,
                      task_source_identity="trello:board:mapping-v1")
        values.update(changes)
        return WeeklySnapshot(**values)

    def legacy(self, raws):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("CREATE TABLE snapshots (snapshot_id TEXT PRIMARY KEY, project_id TEXT, "
                         "cutoff_datetime TEXT, captured_at TEXT, is_complete INTEGER, "
                         "incomplete_reason TEXT, data_json TEXT, content_hash TEXT)")
            conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, snapshot_hash TEXT, "
                         "config_hash TEXT, project_id TEXT)")
            conn.execute("INSERT INTO runs VALUES ('run-old', 'hash-old', 'cfg', 'p')")
            for raw in raws:
                data = json.loads(raw)
                conn.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?, 1, NULL, ?, 'caller-hash')",
                             (data['id'], data['project_id'], data['cutoff_datetime'],
                              data['captured_at'], raw))

    def test_immutable_replay_exact_hash_latest_and_fresh_values(self):
        store = SQLiteStorageAdapter(str(self.path))
        snap = self.snapshot(incomplete_reason="observación")
        original = snap.model_dump_json()
        store.save_snapshot(snap, "forged")
        store.save_snapshot(snap, "second-caller-hash")
        snap.incomplete_reason = "changed"
        store.save_snapshot(snap)
        captures = store.list_snapshot_captures("p")
        self.assertEqual(len(captures), 2)
        old = store.get_snapshot_capture(digest(original))
        self.assertEqual(old.capture_id, digest(original))
        self.assertEqual(old.content_hash, digest(original))
        self.assertEqual(old.snapshot.incomplete_reason, "observación")
        self.assertEqual(store.get_snapshot(snap.id).incomplete_reason, "changed")
        old.snapshot.incomplete_reason = "mutated"
        self.assertEqual(store.get_snapshot_capture(old.capture_id).snapshot.incomplete_reason, "observación")
        with closing(sqlite3.connect(self.path)) as conn, conn:
            raw, actual = conn.execute("SELECT data_json, content_hash FROM snapshot_captures "
                                       "WHERE capture_id = ?", (digest(original),)).fetchone()
            self.assertEqual((raw, actual), (original, digest(original)))
        self.assertIsNone(store.get_snapshot_capture("missing"))

    def test_offsets_naive_order_ties_and_project_isolation(self):
        store = SQLiteStorageAdapter(str(self.path))
        for name, timestamp, project in [
            ("later", "2026-01-02T01:00:00Z", "p"),
            ("offset", "2026-01-01T19:00:00-05:00", "p"),
            ("naive", "2026-01-01T19:00:00", "p"),
            ("earlier", "2026-01-02T01:00:00+02:00", "p"),
            ("other", "2026-01-01T00:00:00Z", "q"),
        ]:
            store.save_snapshot(self.snapshot(id=name, captured_at=timestamp, project_id=project))
        captures = store.list_snapshot_captures("p")
        self.assertEqual([c.snapshot.id for c in captures][::3], ["earlier", "later"])
        self.assertEqual([c.capture_id for c in captures[1:3]], sorted(c.capture_id for c in captures[1:3]))
        self.assertEqual(len(store.list_snapshot_captures("q")), 1)
        self.assertEqual(store.list_snapshot_captures("absent"), [])
        with closing(sqlite3.connect(self.path)) as conn, conn:
            times = [r[0] for r in conn.execute("SELECT captured_at FROM snapshot_captures")]
        self.assertTrue(all(t.endswith("+00:00") for t in times))

    def test_save_serializes_exactly_once(self):
        store = SQLiteStorageAdapter(str(self.path))
        snap = self.snapshot()
        original = WeeklySnapshot.model_dump_json
        calls = []

        def serialize(value, *args, **kwargs):
            calls.append(value.id)
            return original(value, *args, **kwargs)

        with patch.object(WeeklySnapshot, 'model_dump_json', serialize):
            store.save_snapshot(snap)
        self.assertEqual(calls, [snap.id])
        self.assertEqual(store.list_snapshot_captures('p')[0].snapshot, snap)

    def test_roundtrip_metadata_and_caller_hash_contract(self):
        store = SQLiteStorageAdapter(str(self.path))
        snap = self.snapshot(tasks_complete=False, is_complete=False, incomplete_reason="ci failed")
        store.save_snapshot(snap, "caller-hash")
        self.assertEqual(store.list_snapshot_captures("p")[0].snapshot, snap)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            self.assertEqual(conn.execute("SELECT content_hash FROM snapshots").fetchone()[0], "caller-hash")
        self.assertEqual(SQLiteStorageAdapter(str(self.path)).list_snapshot_captures("p")[0].snapshot, snap)

    def test_backfill_exact_legacy_json_preserves_rows_and_runs_on_reopen(self):
        data = self.snapshot().model_dump(mode="json")
        del data['tasks_complete']
        del data['task_source_identity']
        raw = json.dumps(data, indent=2)
        self.legacy([raw])
        before = self.path.read_bytes()
        for _ in range(2):
            store = SQLiteStorageAdapter(str(self.path))
            captures = store.list_snapshot_captures("p")
            self.assertEqual(len(captures), 1)
            self.assertEqual(captures[0].capture_id, digest(raw))
            self.assertIsNone(captures[0].snapshot.tasks_complete)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            self.assertEqual(conn.execute("SELECT data_json, content_hash FROM snapshots").fetchone(),
                             (raw, "caller-hash"))
            self.assertEqual(conn.execute("SELECT * FROM runs").fetchone(), ("run-old", "hash-old", "cfg", "p"))
            self.assertEqual(conn.execute("SELECT data_json FROM snapshot_captures").fetchone()[0], raw)
        self.assertNotEqual(self.path.read_bytes(), before)

    def test_failed_backfill_rolls_back_ddl_indexes_and_partial_inserts(self):
        raws = [self.snapshot(id=f"s{i}").model_dump_json() for i in range(2)]
        self.legacy(raws)
        original = SQLiteStorageAdapter._insert_capture
        calls = []

        def fail_second(adapter, conn, raw):
            calls.append(raw)
            if len(calls) == 2:
                raise RuntimeError("injected migration failure")
            return original(adapter, conn, raw)

        with patch.object(SQLiteStorageAdapter, '_insert_capture', fail_second):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                SQLiteStorageAdapter(str(self.path))
        with closing(sqlite3.connect(self.path)) as conn, conn:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
            self.assertNotIn("snapshot_captures", names)
            self.assertNotIn("idx_snapshot_captures_project_order", names)
            self.assertEqual([r[0] for r in conn.execute("SELECT data_json FROM snapshots")], raws)
            self.assertEqual(conn.execute("SELECT count(*) FROM runs").fetchone()[0], 1)
        self.assertEqual(len(SQLiteStorageAdapter(str(self.path)).list_snapshot_captures("p")), 2)

    def test_capture_and_latest_write_rollback_together(self):
        store = SQLiteStorageAdapter(str(self.path))
        snap = self.snapshot()
        store.save_snapshot(snap)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("CREATE TRIGGER reject_latest BEFORE UPDATE ON snapshots "
                         "BEGIN SELECT RAISE(ABORT, 'injected'); END")
        snap.incomplete_reason = "new"
        with self.assertRaises(sqlite3.IntegrityError):
            store.save_snapshot(snap)
        self.assertEqual(len(store.list_snapshot_captures("p")), 1)
        self.assertIsNone(store.get_snapshot(snap.id).incomplete_reason)

    def test_tamper_hash_and_metadata_rejected_on_get_and_list(self):
        for column, value in [("data_json", "{}"), ("content_hash", "forged"),
                              ("snapshot_id", "wrong"), ("project_id", "wrong"),
                              ("captured_at", "wrong"), ("cutoff_datetime", "wrong")]:
            with self.subTest(column=column):
                path = Path(self.tmp.name) / f"{column}.db"
                store = SQLiteStorageAdapter(str(path))
                snap = self.snapshot()
                store.save_snapshot(snap)
                capture_id = digest(snap.model_dump_json())
                with closing(sqlite3.connect(path)) as conn, conn:
                    conn.execute(f"UPDATE snapshot_captures SET {column} = ?", (value,))
                with self.assertRaises(ValueError):
                    store.get_snapshot_capture(capture_id)
                with self.assertRaises(ValueError):
                    store.list_snapshot_captures(value if column == 'project_id' else 'p')

    def test_read_only_missing_database_does_not_create_parent_or_file(self):
        missing = Path(self.tmp.name) / "absent" / "db.sqlite"
        with self.assertRaises(sqlite3.OperationalError):
            SQLiteStorageAdapter(str(missing), read_only=True)
        self.assertFalse(missing.parent.exists())

    def test_read_only_legacy_history_unavailable_no_migration(self):
        self.legacy([self.snapshot().model_dump_json()])
        before = self.path.read_bytes()
        store = SQLiteStorageAdapter(str(self.path), read_only=True)
        self.assertEqual(store.get_snapshot("snap-p"), self.snapshot())
        for query in [lambda: store.list_snapshot_captures('p'), lambda: store.get_snapshot_capture('x')]:
            with self.assertRaises(NotImplementedError):
                query()
        with self.assertRaises(sqlite3.OperationalError):
            store.save_snapshot(self.snapshot())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ['history.db'])

    def test_read_only_current_database_queries_and_write_protection(self):
        SQLiteStorageAdapter(str(self.path)).save_snapshot(self.snapshot())
        before = self.path.read_bytes()
        store = SQLiteStorageAdapter(str(self.path), read_only=True)
        captures = store.list_snapshot_captures('p')
        self.assertEqual(store.get_snapshot_capture(captures[0].capture_id), captures[0])
        with closing(store._get_connection()) as conn, conn:
            self.assertEqual(conn.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM runs")
        with self.assertRaises(sqlite3.OperationalError):
            store.save_snapshot(self.snapshot(id='new'))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ['history.db'])

    def test_new_port_capabilities_are_not_abstract(self):
        self.assertNotIn('list_snapshot_captures', StoragePort.__abstractmethods__)
        self.assertNotIn('get_snapshot_capture', StoragePort.__abstractmethods__)
        legacy_type = type('LegacyStorage', (StoragePort,), {
            name: lambda *args, **kwargs: None for name in StoragePort.__abstractmethods__
        })
        legacy = legacy_type()
        with self.assertRaises(NotImplementedError):
            legacy.list_snapshot_captures('p')
        with self.assertRaises(NotImplementedError):
            legacy.get_snapshot_capture('missing')
