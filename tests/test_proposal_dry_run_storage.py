"""Transactional simulation receipts, never live execution authority."""
import hashlib
import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
from threading import Event
from unittest.mock import patch

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.domain.tasks import TaskState
from agente_gestion.ports.storage import StoragePort

from tests import test_action_proposal_storage as fixtures
from agente_gestion.domain.proposal_dry_run import DryRunRequest
from agente_gestion.domain.proposal_review import DecisionEvent, OfflineActorClaim


class ProposalDryRunStorageTests(unittest.TestCase):
    def setUp(self, complete=True):
        fixture = fixtures.ActionProposalStorageTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.path, self.store, self.sql = fixture.path, fixture.store, fixture.sql
        snapshot = fixture.snapshot
        snapshot.captured_at += timedelta(seconds=1)
        snapshot.tasks[0].state = TaskState.pending
        snapshot.tasks_complete = complete
        snapshot.task_source_identity = 'manual:mapper-v1'
        capture = self.store.save_snapshot_with_capture_receipt(snapshot)
        self.proposal = fixture.proposal.model_copy(update={'observed_capture_id': capture.capture_id})
        self.store.save_action_proposal(self.proposal, [fixture.evidence])
        self.digest = self.store.get_proposal_review_state('project', 'p1')[0]
        self.store.append_proposal_decision(self.event('approve_offline', 0))
        self.store.append_proposal_decision(self.event('authorize_dry_run', 1))
        self.request = DryRunRequest(request_id='r1', project_id='project', proposal_id='p1',
            proposal_body_sha256=self.digest, capture_id=capture.capture_id,
            permission_event_id='event-1', expected_version=2)

    def event(self, decision, version):
        return DecisionEvent(event_id=f'event-{version}', project_id='project', proposal_id='p1',
            capture_id=self.proposal.observed_capture_id, proposal_body_sha256=self.digest,
            expected_version=version, actor=OfflineActorClaim(actor_id='human'), decision=decision)

    def test_simulation_replay_and_immutable_sources(self):
        tables = ['snapshots', 'snapshot_captures', 'action_proposals', 'proposal_decisions',
                  'runs', 'stage_results', 'evidence', 'reports', 'human_decisions']
        before = [self.sql(f'SELECT * FROM {table}') for table in tables]
        receipt = self.store.append_proposal_dry_run(self.request)
        self.assertEqual(receipt[1].status, 'SIMULATED')
        self.assertEqual((receipt[1].before, receipt[1].after), (None, 'high'))
        self.assertFalse(receipt[3])
        self.assertFalse(receipt[1].live_state_changed)
        self.assertEqual(receipt[1].execution_status, 'not_executed')
        self.assertEqual(self.store.append_proposal_dry_run(self.request), (*receipt[:3], True))
        self.assertEqual(self.store.get_proposal_dry_run('project', 'p1', 'r1'), receipt[:3])
        self.assertEqual(len(self.sql('SELECT * FROM proposal_dry_runs')), 1)
        self.store.append_proposal_dry_run(self.request.model_copy(update={'request_id': 'r2'}))
        self.assertEqual(len(self.sql('SELECT * FROM proposal_dry_runs')), 2)
        self.assertEqual(before, [self.sql(f'SELECT * FROM {table}') for table in tables])

    def test_conflicts_and_strict_preconnect(self):
        self.store.append_proposal_dry_run(self.request)
        for changes in [{'permission_event_id': 'changed'}, {'project_id': 'foreign'},
                        {'proposal_id': 'missing'}, {'capture_id': 'b' * 64},
                        {'proposal_body_sha256': 'b' * 64}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.append_proposal_dry_run(self.request.model_copy(update=changes))
        for changes in [{'authorized': True}, {'expected_version': True}, {'request_id': 1}]:
            with patch.object(self.store, '_get_connection', side_effect=AssertionError('connected')):
                with self.assertRaises(ValueError):
                    self.store.append_proposal_dry_run(self.request.model_copy(update=changes))
        proposal, evidence = self.store.get_action_proposal('p1')
        other = proposal.model_copy(update={'proposal_id': 'p2'})
        self.store.save_action_proposal(other, evidence)
        digest = self.store.get_proposal_review_state('project', 'p2')[0]
        for version, decision in enumerate(['approve_offline', 'authorize_dry_run']):
            self.store.append_proposal_decision(self.event(decision, version).model_copy(update={
                'event_id': f'other-{version}', 'proposal_id': 'p2', 'proposal_body_sha256': digest}))
        with self.assertRaisesRegex(ValueError, 'request_id_conflict'):
            self.store.append_proposal_dry_run(self.request.model_copy(update={
                'proposal_id': 'p2', 'proposal_body_sha256': digest, 'permission_event_id': 'other-1'}))
        self.assertEqual(len(self.sql('SELECT * FROM proposal_dry_runs')), 1)

    def test_denials_and_replays_recheck_current_guards(self):
        self.setUp(False)
        denied = self.store.append_proposal_dry_run(self.request)
        self.assertIn('incomplete_task_source', denied[1].reasons)
        self.assertEqual(self.store.get_proposal_dry_run('project', 'p1', 'r1'), denied[:3])
        for decision in ['revoke', 'deny', 'reject', 'authorize_dry_run']:
            with self.subTest(decision=decision):
                self.setUp()
                receipt = self.store.append_proposal_dry_run(self.request)
                self.store.append_proposal_decision(self.event(decision, 2))
                with self.assertRaisesRegex(ValueError, 'retry_denied'):
                    self.store.append_proposal_dry_run(self.request)
                self.assertEqual(self.store.get_proposal_dry_run('project', 'p1', 'r1'), receipt[:3])
                denied = self.store.append_proposal_dry_run(self.request.model_copy(update={'request_id': 'denied'}))
                self.assertEqual(denied[1].status, 'denied')
                self.assertTrue(denied[1].reasons)
                with self.assertRaisesRegex(ValueError, 'retry_denied'):
                    self.store.append_proposal_dry_run(denied[0])
        for offset, reason in [(0, 'ambiguous_latest_capture'), (1, 'stale_capture')]:
            self.setUp()
            self.store.append_proposal_dry_run(self.request)
            snapshot = self.store.get_snapshot_capture(self.request.capture_id).snapshot
            snapshot.id = 'another'
            snapshot.captured_at += timedelta(seconds=offset)
            self.store.save_snapshot(snapshot)
            with self.assertRaises(ValueError):
                self.store.append_proposal_dry_run(self.request)
            denied = self.store.append_proposal_dry_run(self.request.model_copy(update={'request_id': 'fresh'}))
            self.assertIn(reason, denied[1].reasons)

    def test_audit_integrity_and_append_only(self):
        self.store.append_proposal_dry_run(self.request)
        for operation in ['UPDATE proposal_dry_runs SET status=\'denied\'', 'DELETE FROM proposal_dry_runs']:
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'append-only'):
                self.sql(operation)
        original = dict(zip([r[1] for r in self.sql('PRAGMA table_info(proposal_dry_runs)')],
                            self.sql('SELECT * FROM proposal_dry_runs')[0]))
        changes = [{'request_json': '{}'}, {'result_sha256': 'bad'}, {'status': 'denied'},
            {'recorded_at': 'bad'}, {'project_id': 'other'}, {'capture_id': 'bad'},
            {'proposal_body_sha256': 'b' * 64}, {'permission_event_id': 'wrong'}, {'expected_version': 9}]
        for index, change in enumerate(changes):
            request_id = f'tampered-{index}'
            request = self.request.model_copy(update={'request_id': request_id})
            raw = self.store._proposal_json(request.model_dump(mode='json'))
            row = original | dict(request_id=request_id, request_json=raw,
                                  request_sha256=hashlib.sha256(raw.encode()).hexdigest()) | change
            self.sql(f"INSERT INTO proposal_dry_runs ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store.get_proposal_dry_run('project', 'p1', request_id)
        result = json.loads(original['result_json']) | {'live_state_changed': True}
        raw = self.store._proposal_json(result)
        row = original | {'request_id': 'unsafe', 'result_json': raw,
                          'result_sha256': hashlib.sha256(raw.encode()).hexdigest()}
        self.sql(f"INSERT INTO proposal_dry_runs ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
        with self.assertRaises(ValueError):
            self.store.get_proposal_dry_run('project', 'p1', 'unsafe')

    def test_corrupt_context_rollback(self):
        for table, column in [('action_proposals', 'data_json'), ('snapshot_captures', 'content_hash')]:
            self.setUp()
            self.sql(f'UPDATE {table} SET {column}=?', ('bad',))
            with self.assertRaises(ValueError):
                self.store.append_proposal_dry_run(self.request)
            self.assertEqual(self.sql('SELECT * FROM proposal_dry_runs'), [])
        self.setUp()
        self.sql("INSERT INTO proposal_decisions VALUES ('bad','project','p1','bad','bad',3,'{}','bad','bad')")
        with self.assertRaises(ValueError):
            self.store.append_proposal_dry_run(self.request)
        self.assertEqual(self.sql('SELECT * FROM proposal_dry_runs'), [])

    def test_readonly_legacy_and_optional_port(self):
        receipt = self.store.append_proposal_dry_run(self.request)
        before = self.path.read_bytes()
        reader = SQLiteStorageAdapter(str(self.path), read_only=True)
        self.assertEqual(reader.get_proposal_dry_run('project', 'p1', 'r1'), receipt[:3])
        with self.assertRaises(PermissionError):
            reader.append_proposal_dry_run(self.request)
        self.assertEqual(self.path.read_bytes(), before)
        old = self.path.parent / 'old.db'
        with closing(sqlite3.connect(old)) as conn, conn:
            conn.execute('CREATE TABLE legacy (id TEXT)')
        reader = SQLiteStorageAdapter(str(old), read_only=True)
        before = old.read_bytes()
        with self.assertRaises(NotImplementedError):
            reader.get_proposal_dry_run('project', 'p1', 'r1')
        self.assertEqual(old.read_bytes(), before)
        missing = self.path.parent / 'missing' / 'db'
        with self.assertRaises(sqlite3.OperationalError):
            SQLiteStorageAdapter(str(missing), read_only=True)
        self.assertFalse(missing.parent.exists())
        legacy = type('Legacy', (StoragePort,), {name: lambda *a: None for name in StoragePort.__abstractmethods__})()
        for name, args in [('get_proposal_dry_run', ('project', 'p1', 'r1')), ('append_proposal_dry_run', (self.request,))]:
            with self.assertRaises(NotImplementedError):
                getattr(legacy, name)(*args)

    def test_serialized_version_guard_after_locked_ledger_write(self):
        ready = Event()
        connect = self.store._get_connection
        def connection():
            conn = connect()
            ready.set()
            return conn
        with closing(connect()) as lock, lock:
            lock.execute('BEGIN IMMEDIATE')
            event = self.event('authorize_dry_run', 2)
            raw = self.store._proposal_json(event.model_dump(mode='json'))
            lock.execute('INSERT INTO proposal_decisions VALUES (?,?,?,?,?,?,?,?,?)',
                (event.event_id, event.project_id, event.proposal_id, event.capture_id,
                 event.proposal_body_sha256, 3, raw, hashlib.sha256(raw.encode()).hexdigest(),
                 '2026-01-01T00:00:00+00:00'))
            with patch.object(self.store, '_get_connection', side_effect=connection), ThreadPoolExecutor(1) as pool:
                attempt = pool.submit(self.store.append_proposal_dry_run, self.request)
                self.assertTrue(ready.wait(2))
                lock.commit()
                receipt = attempt.result(timeout=5)
        self.assertIn('version_mismatch', receipt[1].reasons)
        self.assertEqual(self.store.get_proposal_review_state('project', 'p1')[2].version, 3)
