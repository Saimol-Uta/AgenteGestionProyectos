"""Exact stored capture receipts, without changing legacy snapshot saves."""
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
from tests import test_action_proposals, test_snapshot_history


class SnapshotCaptureReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'receipt.db'
        fixture = test_action_proposals.ActionProposalTests()
        fixture.setUp()
        self.snapshot = fixture.capture.snapshot

    def sql(self, statement, args=()):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            return conn.execute(statement, args).fetchall()

    def test_optional_nonabstract_port(self):
        self.assertNotIn('save_snapshot_with_capture_receipt', StoragePort.__abstractmethods__)
        legacy = type('Legacy', (StoragePort,), {
            name: lambda *a, **k: None for name in StoragePort.__abstractmethods__})()
        with self.assertRaises(NotImplementedError):
            legacy.save_snapshot_with_capture_receipt(self.snapshot)

    def test_exact_stored_receipt_retry_latest_and_single_serialization(self):
        store = SQLiteStorageAdapter(str(self.path))
        original = WeeklySnapshot.model_dump_json
        calls = []

        def serialize(value, *a, **k):
            calls.append(value.id)
            return original(value, *a, **k)

        with patch.object(WeeklySnapshot, 'model_dump_json', serialize):
            receipt = store.save_snapshot_with_capture_receipt(self.snapshot, 'legacy-hash')
        self.assertEqual(calls, [self.snapshot.id])
        raw, stored_hash = self.sql('SELECT data_json, content_hash FROM snapshot_captures')[0]
        self.assertEqual(receipt.capture_id, test_snapshot_history.digest(raw))
        self.assertEqual(receipt.content_hash, stored_hash)
        self.assertNotEqual(receipt.capture_id, self.snapshot.id)
        self.assertEqual(receipt.snapshot, self.snapshot)
        for field in ('id', 'project_id', 'captured_at', 'tasks'):
            self.assertEqual(getattr(receipt.snapshot, field), getattr(self.snapshot, field))
        self.assertEqual(store.get_snapshot_capture(receipt.capture_id), receipt)
        self.assertEqual(store.get_snapshot(self.snapshot.id), self.snapshot)
        self.assertEqual(self.sql('SELECT content_hash FROM snapshots'), [('legacy-hash',)])
        self.assertEqual(store.save_snapshot_with_capture_receipt(self.snapshot), receipt)
        self.assertEqual(self.sql('SELECT count(*) FROM snapshot_captures'), [(1,)])
        self.assertEqual(self.sql('SELECT content_hash FROM snapshots'), [(None,)])
        self.assertIsNone(store.save_snapshot(self.snapshot, 'old-api'))
        self.assertEqual(self.sql('SELECT content_hash FROM snapshots'), [('old-api',)])

    def test_legacy_raw_identity_is_not_parsed_default_identity(self):
        fixture = test_snapshot_history.SnapshotHistoryTests()
        fixture.path = self.path
        data = self.snapshot.model_dump(mode='json')
        del data['tasks_complete']
        del data['task_source_identity']
        raw = json.dumps(data, indent=2)
        fixture.legacy([raw])
        store = SQLiteStorageAdapter(str(self.path))
        old_id = test_snapshot_history.digest(raw)
        old = store.get_snapshot_capture(old_id)
        self.assertIsNone(old.snapshot.tasks_complete)
        receipt = store.save_snapshot_with_capture_receipt(old.snapshot)
        self.assertNotEqual(receipt.capture_id, old_id)
        self.assertEqual(store.get_snapshot_capture(old_id), old)
        self.assertEqual(self.sql('SELECT data_json FROM snapshot_captures WHERE capture_id = ?',
                                  (old_id,)), [(raw,)])
        new_raw = self.sql('SELECT data_json FROM snapshot_captures WHERE capture_id = ?',
                           (receipt.capture_id,))[0][0]
        self.assertEqual(receipt.capture_id, test_snapshot_history.digest(new_raw))

    def test_conflicting_or_tampered_capture_never_returns_proof(self):
        store = SQLiteStorageAdapter(str(self.path))
        receipt = store.save_snapshot_with_capture_receipt(self.snapshot)
        columns = ('snapshot_id', 'project_id', 'captured_at', 'cutoff_datetime',
                   'content_hash', 'data_json')
        for column in columns:
            old = self.sql(f'SELECT {column} FROM snapshot_captures')[0][0]
            self.sql(f'UPDATE snapshot_captures SET {column} = ?', ('forged',))
            with self.subTest(column=column), self.assertRaises(ValueError):
                store.save_snapshot_with_capture_receipt(self.snapshot, 'must-not-write')
            self.assertEqual(self.sql('SELECT content_hash FROM snapshots'), [(None,)])
            self.sql(f'UPDATE snapshot_captures SET {column} = ?', (old,))
        self.assertEqual(store.get_snapshot_capture(receipt.capture_id), receipt)

    def test_latest_write_and_commit_failures_rollback_without_receipt(self):
        store = SQLiteStorageAdapter(str(self.path))
        old = store.save_snapshot_with_capture_receipt(self.snapshot)
        changed = self.snapshot.model_copy(update={'incomplete_reason': 'changed'})
        self.sql("CREATE TRIGGER reject_latest BEFORE UPDATE ON snapshots "
                 "BEGIN SELECT RAISE(ABORT, 'injected latest'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'injected latest'):
            store.save_snapshot_with_capture_receipt(changed)
        self.assertEqual(store.list_snapshot_captures(self.snapshot.project_id), [old])
        self.assertEqual(store.get_snapshot(self.snapshot.id), self.snapshot)
        # A separate fixture avoids altering schema to remove the failure trigger.
        commit_path = Path(self.tmp.name) / 'commit.db'
        commit_store = SQLiteStorageAdapter(str(commit_path))
        commit_store.save_snapshot(self.snapshot)

        class FailCommit(sqlite3.Connection):
            def __exit__(self, exc_type, exc_value, traceback):
                if exc_type is None:
                    self.rollback()
                    raise sqlite3.OperationalError('injected commit')
                return super().__exit__(exc_type, exc_value, traceback)

        def connection():
            conn = sqlite3.connect(commit_path, factory=FailCommit)
            conn.row_factory = sqlite3.Row
            return conn

        with patch.object(commit_store, '_get_connection', connection):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'injected commit'):
                commit_store.save_snapshot_with_capture_receipt(changed)
        self.assertEqual(commit_store.get_snapshot(self.snapshot.id), self.snapshot)
        self.assertEqual(len(commit_store.list_snapshot_captures(self.snapshot.project_id)), 1)

    def test_receipt_is_read_after_commit_on_same_connection(self):
        store = SQLiteStorageAdapter(str(self.path))
        connections, parse_states = [], []
        original_connection = store._get_connection
        original_parse = store._parse_capture

        def connection():
            conn = original_connection()
            connections.append(conn)
            return conn

        def parse(row):
            parse_states.append(connections[-1].in_transaction)
            return original_parse(row)

        with patch.object(store, '_get_connection', connection), patch.object(store, '_parse_capture', parse):
            receipt = store.save_snapshot_with_capture_receipt(self.snapshot)
        self.assertEqual(len(connections), 1)
        self.assertTrue(parse_states[0])
        self.assertFalse(parse_states[-1])
        self.assertEqual(store.get_snapshot_capture(receipt.capture_id), receipt)

    def test_read_only_missing_current_and_legacy_never_mutate(self):
        missing = Path(self.tmp.name) / 'absent' / 'missing.db'
        with self.assertRaises(sqlite3.OperationalError):
            SQLiteStorageAdapter(str(missing), read_only=True)
        self.assertFalse(missing.parent.exists())
        fixture = test_snapshot_history.SnapshotHistoryTests()
        fixture.path = self.path
        fixture.legacy([self.snapshot.model_dump_json()])
        for current in (False, True):
            if current:
                SQLiteStorageAdapter(str(self.path))
            before = self.path.read_bytes()
            names = sorted(p.name for p in self.path.parent.iterdir())
            store = SQLiteStorageAdapter(str(self.path), read_only=True)
            for name in ('save_snapshot', 'save_snapshot_with_capture_receipt'):
                with self.subTest(current=current, method=name), self.assertRaises(sqlite3.OperationalError):
                    getattr(store, name)(self.snapshot)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), names)
