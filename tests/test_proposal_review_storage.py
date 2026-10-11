"""Verified offline ledger contracts; fixture modules do not duplicate discovery."""
import hashlib
import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier
from unittest.mock import patch

from tests import test_action_proposal_storage as fixtures
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.domain.proposal_review import DecisionEvent, OfflineActorClaim
from agente_gestion.ports.storage import StoragePort


class ProposalReviewStorageTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.ActionProposalStorageTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.path, self.store, self.sql = fixture.path, fixture.store, fixture.sql
        self.proposal, self.evidence = fixture.proposal, fixture.evidence
        self.store.save_action_proposal(self.proposal, [self.evidence])
        self.before = self.sql('SELECT * FROM action_proposals')
        self.digest = self.before[0][-1]

    def event(self, decision='approve_offline', version=0, **changes):
        return DecisionEvent(**(dict(event_id=f'event-{version}', project_id='project',
            proposal_id='p1', capture_id=self.proposal.observed_capture_id,
            proposal_body_sha256=self.digest, expected_version=version,
            actor=OfflineActorClaim(actor_id='human'), decision=decision) | changes))

    def state(self):
        return self.store.get_proposal_review_state('project', 'p1')

    def test_exact_hash_sequential_grants_replay_and_immutable_sources(self):
        digest, events, initial = self.state()
        self.assertEqual(digest, hashlib.sha256(self.before[0][-2].encode()).hexdigest())
        self.assertNotEqual(digest, hashlib.sha256(self.proposal.model_dump_json().encode()).hexdigest())
        self.assertEqual((events, initial.version), ([], 0))
        sources = self.sql('SELECT * FROM snapshot_captures'), self.sql('SELECT * FROM snapshots')
        approve = self.event()
        approved = self.store.append_proposal_decision(approve)
        self.assertEqual(approved.authorization_status, 'not_authorized')
        granted = self.store.append_proposal_decision(self.event('authorize_dry_run', 1))
        self.assertEqual(granted.permission_scope, 'bound_proposal_dry_run')
        self.assertFalse(granted.real_execution_allowed)
        self.assertEqual(granted.execution_status, 'not_executed')
        self.assertEqual(self.store.append_proposal_decision(approve), granted)
        self.assertEqual(len(self.state()[1]), 2)
        self.assertEqual(self.sql('SELECT * FROM action_proposals'), self.before)
        self.assertEqual((self.sql('SELECT * FROM snapshot_captures'), self.sql('SELECT * FROM snapshots')), sources)
        recorded = self.sql('SELECT recorded_at FROM proposal_decisions')[0][0]
        self.assertTrue(recorded.endswith('+00:00'))

    def test_rollback_binding_actor_transition_stale_and_global_conflicts(self):
        bad_actor = self.event().model_copy(deep=True)
        object.__setattr__(bad_actor.actor, 'authenticated', True)
        for event in [self.event('authorize_dry_run'), bad_actor,
                      self.event(project_id='other'), self.event(proposal_id='missing'),
                      self.event(capture_id='bad'), self.event(proposal_body_sha256='c' * 64),
                      self.event(version=1)]:
            with self.subTest(event=event), self.assertRaises(ValueError):
                self.store.append_proposal_decision(event)
            self.assertEqual(self.state()[2].version, 0)
        self.store.append_proposal_decision(self.event())
        for event in [self.event('reject'), self.event('deny', event_id='fresh')]:
            with self.assertRaises(ValueError):
                self.store.append_proposal_decision(event)
        other = self.proposal.model_copy(update={'proposal_id': 'p2'})
        self.store.save_action_proposal(other, [self.evidence])
        digest = self.store.get_proposal_review_state('project', 'p2')[0]
        with self.assertRaisesRegex(ValueError, 'event_id_conflict'):
            self.store.append_proposal_decision(self.event(proposal_id='p2', proposal_body_sha256=digest))
        snapshot = self.store.get_snapshot_capture(self.proposal.observed_capture_id).snapshot.model_copy(deep=True)
        snapshot.project_id = 'other'
        capture = self.store.save_snapshot_with_capture_receipt(snapshot)
        evidence = self.evidence.model_copy(deep=True)
        evidence.provenance.project_id = 'other'
        proposal = other.model_copy(update={'proposal_id': 'p3', 'project_id': 'other', 'observed_capture_id': capture.capture_id})
        self.store.save_action_proposal(proposal, [evidence])
        digest = self.store.get_proposal_review_state('other', 'p3')[0]
        with self.assertRaisesRegex(ValueError, 'event_id_conflict'):
            self.store.append_proposal_decision(self.event(project_id='other', proposal_id='p3', capture_id=capture.capture_id, proposal_body_sha256=digest))
        self.assertIsNone(self.store.get_proposal_review_state('other', 'p1'))
        self.assertIsNone(self.store.get_proposal_review_state('project', 'missing'))

    def test_catalogue_capture_and_frozen_evidence_fail_closed(self):
        for table, column, value in [('action_proposals', 'data_json', 'bad'),
                ('action_proposals', 'content_hash', 'bad'), ('action_proposals', 'capture_id', 'bad'),
                ('snapshot_captures', 'content_hash', 'bad')]:
            old = self.sql(f'SELECT {column} FROM {table}')[0][0]
            self.sql(f'UPDATE {table} SET {column}=?', (value,))
            with self.assertRaises(ValueError):
                self.state()
            with self.assertRaises(ValueError):
                self.store.append_proposal_decision(self.event())
            self.sql(f'UPDATE {table} SET {column}=?', (old,))
        data = json.loads(self.before[0][-2])
        data['evidence'][0]['provenance']['payload']['priority'] = 'low'
        raw = self.store._proposal_json(data)
        self.sql('UPDATE action_proposals SET data_json=?, content_hash=?', (raw, hashlib.sha256(raw.encode()).hexdigest()))
        with self.assertRaises(ValueError):
            self.state()

    def test_readonly_legacy_missing_and_optional_port(self):
        before = self.path.read_bytes()
        reader = SQLiteStorageAdapter(str(self.path), read_only=True)
        self.assertEqual(reader.get_proposal_review_state('project', 'p1'), self.state())
        with self.assertRaises(PermissionError):
            reader.append_proposal_decision(self.event())
        self.assertEqual(self.path.read_bytes(), before)
        old = self.path.parent / 'old.db'
        with closing(sqlite3.connect(old)) as conn, conn:
            conn.execute('CREATE TABLE legacy (id TEXT)')
        before = old.read_bytes()
        reader = SQLiteStorageAdapter(str(old), read_only=True)
        with self.assertRaisesRegex(NotImplementedError, 'ledger'):
            reader.get_proposal_review_state('project', 'p1')
        with self.assertRaises(PermissionError):
            reader.append_proposal_decision(self.event())
        self.assertEqual(old.read_bytes(), before)
        missing = self.path.parent / 'missing' / 'db'
        with self.assertRaises(sqlite3.OperationalError):
            SQLiteStorageAdapter(str(missing), read_only=True)
        self.assertFalse(missing.parent.exists())
        legacy = type('Legacy', (StoragePort,), {name: lambda *a: None for name in StoragePort.__abstractmethods__})()
        for method, args in [('get_proposal_review_state', ('project', 'p1')), ('append_proposal_decision', (self.event(),))]:
            with self.assertRaises(NotImplementedError):
                getattr(legacy, method)(*args)

    def test_ledger_integrity_metadata_gaps_and_append_only_schema(self):
        for changes in [{}, {'data_json': 'bad'}, {'content_hash': 'bad'}, {'project_id': 'other'},
                        {'capture_id': 'bad'}, {'ordered_version': 9}, {'recorded_at': 'bad'},
                        {'proposal_body_sha256': 'c' * 64}, {'event_id': 'forged'}]:
            with self.subTest(changes=changes):
                self.setUp()
                event = self.event('deny', 1 if not changes else 0)
                raw = self.store._proposal_json(event.model_dump(mode='json'))
                row = dict(event_id=event.event_id, project_id='project', proposal_id='p1',
                    capture_id=event.capture_id, proposal_body_sha256=self.digest,
                    ordered_version=event.expected_version + 1, data_json=raw, content_hash=hashlib.sha256(raw.encode()).hexdigest(),
                    recorded_at='2026-01-01T00:00:00+00:00') | changes
                self.sql(f"INSERT INTO proposal_decisions ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
                with self.assertRaises(ValueError):
                    self.state()
                with self.assertRaises(ValueError):
                    self.store.append_proposal_decision(self.event())
        triggers = self.sql("SELECT sql FROM sqlite_master WHERE type='trigger'")
        self.assertTrue(any('BEFORE DELETE' in row[0] for row in triggers))
        self.assertTrue(any('BEFORE UPDATE' in row[0] for row in triggers))

    def test_migration_separate_transaction_rolls_back_and_preserves_catalogue(self):
        connect = sqlite3.connect
        class FailLedger(sqlite3.Connection):
            def execute(self, sql, *args):
                if 'CREATE TRIGGER IF NOT EXISTS proposal_decisions' in sql:
                    raise sqlite3.OperationalError('injected ledger migration failure')
                return super().execute(sql, *args)
        path = self.path.parent / 'migration.db'
        with patch('agente_gestion.adapters.storage.sqlite.sqlite3.connect',
                   side_effect=lambda *a, **k: connect(*a, **(k | {'factory': FailLedger}))):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'injected'):
                SQLiteStorageAdapter(str(path))
        with closing(connect(path)) as conn:
            names = {row[0] for row in conn.execute('SELECT name FROM sqlite_master')}
        self.assertIn('action_proposals', names)
        self.assertNotIn('proposal_decisions', names)
        self.assertEqual(SQLiteStorageAdapter(str(path)).get_proposal_review_state('project', 'p1'), None)
        self.assertEqual(self.sql('SELECT * FROM action_proposals'), self.before)

    def test_concurrent_expected_version_zero_has_one_winner(self):
        stores = [SQLiteStorageAdapter(str(self.path)) for _ in range(2)]
        barrier = Barrier(2)
        def append(index):
            barrier.wait()
            try:
                return stores[index].append_proposal_decision(self.event(event_id=f'concurrent-{index}')).version
            except ValueError:
                return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(append, range(2))), [1, 'rejected'])
        self.assertEqual(self.state()[2].version, 1)
        self.assertEqual(self.sql('SELECT * FROM action_proposals'), self.before)
