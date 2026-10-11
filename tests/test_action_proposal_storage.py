"""Offline immutable submission catalogue contracts."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.domain.action_proposal import ActionProposal
from agente_gestion.ports.storage import StoragePort
from tests import test_action_proposals, test_snapshot_history


class ActionProposalStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'catalogue.db'
        fixture = test_action_proposals.ActionProposalTests()
        fixture.setUp()
        self.evidence = fixture.evidence
        self.snapshot = fixture.capture.snapshot
        self.store = SQLiteStorageAdapter(str(self.path))
        self.store.save_snapshot(self.snapshot)
        self.capture_id = self.store.list_snapshot_captures('project')[0].capture_id
        self.proposal = ActionProposal(**(fixture.data | {'observed_capture_id': self.capture_id}))

    def sql(self, statement, args=()):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            return conn.execute(statement, args).fetchall()

    def test_optional_port(self):
        legacy = type('Legacy', (StoragePort,), {
            name: lambda *a, **k: None for name in StoragePort.__abstractmethods__})()
        for name, args in [('save_action_proposal', (self.proposal, [self.evidence])),
                           ('get_action_proposal', ('p1',)), ('list_action_proposals', ('project',))]:
            self.assertNotIn(name, StoragePort.__abstractmethods__)
            with self.assertRaises(NotImplementedError):
                getattr(legacy, name)(*args)

    def test_roundtrip_freeze_retry_conflict_and_order(self):
        original = self.evidence.model_dump_json()
        self.store.save_action_proposal(self.proposal, [self.evidence])
        self.store.save_action_proposal(self.proposal, [self.evidence])
        self.assertEqual(self.evidence.model_dump_json(), original)
        self.assertEqual(self.store.get_action_proposal('p1'), (self.proposal, [self.evidence]))
        self.evidence.provenance.payload['priority'] = 'low'
        self.store.save_evidence('run1', [self.evidence])
        self.store.save_evidence('run1', [self.evidence])
        self.store.save_evidence('run2', [self.evidence])
        frozen = self.store.get_action_proposal('p1')[1][0]
        self.assertEqual(frozen.model_dump_json(), original)
        self.assertIsNone(frozen.provenance.repository)
        with self.assertRaises(ValueError):
            self.store.save_action_proposal(self.proposal.model_copy(update={'rationale': 'changed'}), [frozen])
        for id in ['z', 'a']:
            self.store.save_action_proposal(self.proposal.model_copy(update={'proposal_id': id}), [frozen])
        self.assertEqual([p.proposal_id for p, _ in self.store.list_action_proposals('project')], ['a', 'p1', 'z'])
        self.assertEqual(self.store.list_action_proposals("project' OR 1=1 --"), [])
        self.assertIsNone(self.store.get_action_proposal('missing'))
        self.assertEqual(len(self.sql('SELECT * FROM action_proposals')), 3)

    def test_bindings_forged_models_and_atomic_failure(self):
        for change in [{'observed_capture_id': self.snapshot.id}, {'project_id': 'wrong'},
                       {'current_value': 'low'}, {'task_id': 'missing'},
                       {'evidence_ids': ['absent']}, {'evidence_ids': ['e1', 'e1']},
                       {'authorization_status': 'authorized'}, {'actor': 'invented'}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store.save_action_proposal(self.proposal.model_copy(update=change), [self.evidence])
        for items in [[], [self.evidence, self.evidence],
                      [self.evidence.model_copy(update={'category': 'diff'})]]:
            with self.assertRaises(ValueError):
                self.store.save_action_proposal(self.proposal, items)
        for key, value in [('project_id', 'wrong'), ('external_id', 'wrong'),
                           ('payload', {'priority': 'low'})]:
            item = self.evidence.model_copy(deep=True)
            setattr(item.provenance, key, value)
            with self.assertRaises(ValueError):
                self.store.save_action_proposal(self.proposal, [item])
        self.assertEqual(self.sql('SELECT * FROM action_proposals'), [])
        self.sql("CREATE TRIGGER reject_proposal BEFORE INSERT ON action_proposals "
                 "BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save_action_proposal(self.proposal, [self.evidence])
        self.assertEqual(self.sql('SELECT * FROM action_proposals'), [])

    def test_exact_body_hash_metadata_and_capture_tamper(self):
        self.store.save_action_proposal(self.proposal, [self.evidence])
        raw, digest = self.sql('SELECT data_json, content_hash FROM action_proposals')[0]
        self.assertEqual(hashlib.sha256(raw.encode()).hexdigest(), digest)
        data = json.loads(raw)
        self.assertEqual(data['evidence'], [self.evidence.model_dump(mode='json')])
        self.assertEqual(raw, json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False))
        for column, value in [('data_json', raw + ' '), ('data_json', b'invalid'), ('content_hash', 'bad'),
                              ('project_id', 'wrong'), ('capture_id', 'bad'), ('schema_version', 'bad')]:
            old = self.sql(f'SELECT {column} FROM action_proposals')[0][0]
            self.sql(f'UPDATE action_proposals SET {column} = ?', (value,))
            with self.subTest(column=column):
                with self.assertRaises(ValueError):
                    self.store.get_action_proposal('p1')
                with self.assertRaises(ValueError):
                    self.store.list_action_proposals(value if column == 'project_id' else 'project')
            self.sql(f'UPDATE action_proposals SET {column} = ?', (old,))
        data['evidence'][0]['provenance']['payload']['priority'] = 'wrong'
        forged = json.dumps(data, sort_keys=True, separators=(',', ':'))
        self.sql('UPDATE action_proposals SET data_json=?, content_hash=?',
                 (forged, hashlib.sha256(forged.encode()).hexdigest()))
        with self.assertRaisesRegex(ValueError, 'evidence_fact_mismatch'):
            self.store.get_action_proposal('p1')
        data['evidence'][0]['provenance']['payload']['priority'] = None
        data['proposal']['review_status'] = 'approved'
        forged = json.dumps(data, sort_keys=True, separators=(',', ':'))
        self.sql('UPDATE action_proposals SET data_json=?, content_hash=?',
                 (forged, hashlib.sha256(forged.encode()).hexdigest()))
        with self.assertRaises(ValueError):
            self.store.get_action_proposal('p1')
        self.sql('UPDATE action_proposals SET data_json=?, content_hash=?', (raw, digest))
        self.sql("UPDATE snapshot_captures SET content_hash='tampered'")
        with self.assertRaises(ValueError):
            self.store.get_action_proposal('p1')
        with self.assertRaises(ValueError):
            self.store.save_action_proposal(self.proposal, [self.evidence])

    def test_legacy_migration_exact_hash_idempotent_and_ddl_rollback(self):
        legacy = test_snapshot_history.SnapshotHistoryTests()
        legacy.path = Path(self.tmp.name) / 'legacy.db'
        data = self.snapshot.model_dump(mode='json')
        del data['tasks_complete']
        raw = json.dumps(data, indent=2)
        legacy.legacy([raw])
        store = SQLiteStorageAdapter(str(legacy.path))
        p = self.proposal.model_copy(update={'observed_capture_id': hashlib.sha256(raw.encode()).hexdigest()})
        store.save_action_proposal(p, [self.evidence])
        self.assertEqual(SQLiteStorageAdapter(str(legacy.path)).get_action_proposal('p1')[0], p)
        with closing(sqlite3.connect(legacy.path)) as conn:
            self.assertEqual(conn.execute('SELECT data_json FROM snapshot_captures').fetchone()[0], raw)
        failing = Path(self.tmp.name) / 'failure.db'
        legacy.path = failing
        legacy.legacy([raw])
        class FailIndex(sqlite3.Connection):
            def execute(self, sql, *args):
                if 'CREATE INDEX IF NOT EXISTS idx_action_proposals' in sql:
                    raise sqlite3.OperationalError('injected catalogue migration failure')
                return super().execute(sql, *args)
        connect = sqlite3.connect
        with patch('agente_gestion.adapters.storage.sqlite.sqlite3.connect',
                   side_effect=lambda *a, **k: connect(*a, **(k | {'factory': FailIndex}))):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'injected'):
                SQLiteStorageAdapter(str(failing))
        with closing(connect(failing)) as conn:
            names = {r[0] for r in conn.execute('SELECT name FROM sqlite_master')}
            self.assertIn('snapshot_captures', names)
            self.assertNotIn('action_proposals', names)
            self.assertEqual(conn.execute('SELECT data_json FROM snapshot_captures').fetchone()[0], raw)
            self.assertEqual(conn.execute('SELECT count(*) FROM runs').fetchone()[0], 1)
        self.assertEqual(SQLiteStorageAdapter(str(failing)).list_action_proposals('project'), [])

    def test_selected_dtos_once_no_aliases_and_unobserved_fact(self):
        extra = self.evidence.model_copy(deep=True)
        extra.provenance.id = 'unreferenced'
        before = self.proposal.model_dump_json()
        dump = type(self.evidence).model_dump
        calls = []
        def serialize(item, *args, **kwargs):
            calls.append(item.provenance.id)
            return dump(item, *args, **kwargs)
        with patch.object(type(self.evidence), 'model_dump', serialize):
            self.store.save_action_proposal(self.proposal, [extra, self.evidence])
        self.assertEqual(calls, ['unreferenced', 'e1'])
        self.assertEqual(self.proposal.model_dump_json(), before)
        result = self.store.get_action_proposal('p1')
        self.assertEqual(result[1], [self.evidence])
        result[0].evidence_ids.append('mutated')
        result[1][0].provenance.payload['priority'] = 'mutated'
        self.assertEqual(self.store.get_action_proposal('p1'), (self.proposal, [self.evidence]))
        other = self.snapshot.model_copy(deep=True)
        other.project_id = 'other'
        self.store.save_snapshot(other)
        id = self.store.list_snapshot_captures('other')[0].capture_id
        item = self.evidence.model_copy(deep=True)
        item.provenance.project_id = 'other'
        p = self.proposal.model_copy(update={'proposal_id': 'other', 'project_id': 'other',
                                             'observed_capture_id': id})
        self.store.save_action_proposal(p, [item])
        self.assertEqual(self.store.list_action_proposals('other'), [(p, [item])])
        self.assertEqual(len(self.store.list_action_proposals('project')), 1)
        other.tasks[0].observed_fields = []
        self.store.save_snapshot(other)
        id = hashlib.sha256(other.model_dump_json().encode()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'field_not_observed'):
            self.store.save_action_proposal(p.model_copy(update={'observed_capture_id': id}), [item])

    def test_readonly_current_legacy_missing_no_mutation(self):
        self.store.save_action_proposal(self.proposal, [self.evidence])
        before = self.path.read_bytes()
        reader = SQLiteStorageAdapter(str(self.path), read_only=True)
        self.assertEqual(reader.get_action_proposal('p1'), (self.proposal, [self.evidence]))
        self.assertEqual(len(reader.list_action_proposals('project')), 1)
        with self.assertRaises(PermissionError):
            reader.save_action_proposal(self.proposal, [self.evidence])
        self.assertEqual(self.path.read_bytes(), before)
        legacy = test_snapshot_history.SnapshotHistoryTests()
        legacy.path = Path(self.tmp.name) / 'legacy.db'
        legacy.legacy([self.snapshot.model_dump_json()])
        before = legacy.path.read_bytes()
        reader = SQLiteStorageAdapter(str(legacy.path), read_only=True)
        for query in [lambda: reader.get_action_proposal('missing'), lambda: reader.list_action_proposals('project')]:
            with self.assertRaisesRegex(NotImplementedError, 'catalogue'):
                query()
        self.assertEqual(legacy.path.read_bytes(), before)
        missing = Path(self.tmp.name) / 'absent' / 'db'
        with self.assertRaises(sqlite3.OperationalError):
            SQLiteStorageAdapter(str(missing), read_only=True)
        self.assertFalse(missing.parent.exists())
