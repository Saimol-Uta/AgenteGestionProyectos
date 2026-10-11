"""Offline simulation CLI against real SQLite; no fixture TestCase reuse."""
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from agente_gestion import cli
from tests import test_cli_proposal_decisions as fixtures
from agente_gestion.domain.proposal_review import DecisionEvent, OfflineActorClaim
from agente_gestion.domain.tasks import TaskState


class CLIProposalDryRunTests(unittest.TestCase):
    def setUp(self, complete=True):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / 'catalogue.db'
        self.store = fixtures.SQLiteStorageAdapter(str(self.db))
        now = datetime(2026, 10, 10, 12)
        snapshot = fixtures.WeeklySnapshot(id='snapshot', project_id='project',
            cutoff_datetime=now, captured_at=now, tasks_complete=complete,
            task_source_identity='manual:mapper-v1', tasks=[fixtures.TaskCard(
                id='task', external_id='external', title='PRIVATE-TITLE',
                state=TaskState.pending, priority=None, observed_fields=['priority'])])
        self.capture = self.store.save_snapshot_with_capture_receipt(snapshot)
        evidence = fixtures.EvidenceItem(category='task', summary='PRIVATE-SUMMARY',
            provenance=fixtures.EvidenceProvenance(id='e1', project_id='project',
                source='manual', timestamp=now, external_id='external',
                payload={'priority': None, 'snippet': 'PRIVATE', 'url': 'PRIVATE'}))
        proposal = fixtures.ActionProposal(proposal_id='p1', project_id='project',
            task_id='task', observed_capture_id=self.capture.capture_id, field='priority',
            current_value=None, proposed_value='high', evidence_ids=['e1'],
            rationale='PRIVATE-RATIONALE', expected_impact='PRIVATE-IMPACT')
        self.store.save_action_proposal(proposal, [evidence])
        self.digest = self.store.get_proposal_review_state('project', 'p1')[0]
        self.decision('approve_offline', 0)
        self.decision('authorize_dry_run', 1)

    def decision(self, decision, version):
        self.store.append_proposal_decision(DecisionEvent(event_id=f'event-{version}',
            project_id='project', proposal_id='p1', capture_id=self.capture.capture_id,
            proposal_body_sha256=self.digest, expected_version=version,
            actor=OfflineActorClaim(actor_id='grant-assertion'), decision=decision))

    def args(self, **changes):
        return cli.argparse.Namespace(**(dict(proposals_operation='dry-run',
            project='project', proposal_id='p1', request_id='r1', db_path=str(self.db),
            capture_id=self.capture.capture_id, permission_event_id='event-1',
            proposal_body_sha256=self.digest, expected_version=2) | changes))

    def fingerprint(self):
        return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob('*') if p.is_file()}

    def sql(self, query):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute(query).fetchall()

    def invoke(self, unchanged=False, preconnect=False, **changes):
        before = self.fingerprint()
        out, err = io.StringIO(), io.StringIO()
        def guarded(*a, **kw):
            self.assertFalse(preconnect, 'database opened for invalid local input')
            if unchanged:
                self.assertTrue(kw.get('read_only'), 'writer initialized')
            return fixtures.SQLiteStorageAdapter(*a, **kw)
        with patch.object(cli, 'SQLiteStorageAdapter', side_effect=guarded), \
                patch.object(cli, '_build_orchestrator', side_effect=AssertionError), \
                patch.object(cli.Settings, 'from_env_and_file', side_effect=AssertionError), \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.proposals_command(self.args(**changes))
        if unchanged:
            self.assertEqual(before, self.fingerprint())
        self.assertNotIn('PRIVATE', out.getvalue() + err.getvalue())
        self.assertNotIn(str(self.root), out.getvalue() + err.getvalue())
        return code, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue()

    def test_success_replay_and_immutable_core(self):
        tables = ['snapshots', 'snapshot_captures', 'action_proposals', 'proposal_decisions',
                  'runs', 'stage_results', 'evidence', 'reports', 'human_decisions']
        before = [self.sql(f'SELECT * FROM {t}') for t in tables]
        code, data, err = self.invoke()
        self.assertEqual((code, err, data['status']), (0, '', 'SIMULATED'))
        self.assertEqual((data['before'], data['after']), (None, 'high'))
        self.assertTrue(data['is_new_simulation'])
        code, replay, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertTrue(replay['replayed'] and replay['historical'])
        self.assertFalse(replay['is_new_simulation'])
        self.assertEqual(data['recorded_at'], replay['recorded_at'])
        self.assertTrue(data['recorded_at'].endswith('+00:00'))
        self.assertEqual(len(self.sql('SELECT * FROM proposal_dry_runs')), 1)
        self.assertEqual(before, [self.sql(f'SELECT * FROM {t}') for t in tables])
        self.assertEqual(set(data), {'schema_version', 'status', 'reasons', 'field', 'before',
            'after', 'request_id', 'project_id', 'proposal_id', 'capture_id',
            'proposal_body_sha256', 'permission_event_id', 'expected_version', 'recorded_at',
            'replayed', 'historical', 'is_new_simulation', 'execution_status',
            'live_state_changed', 'real_execution_allowed', 'identity_verified',
            'actor_trust', 'request_initiator'})
        self.assertEqual(data['request_initiator'], 'unknown')
        self.assertFalse(data['identity_verified'] or data['live_state_changed'] or
                         data['real_execution_allowed'])

    def test_first_denials_recorded(self):
        for changes, reason in [({'expected_version': 1}, 'version_mismatch'),
                                ({'permission_event_id': 'other'}, 'permission_event_mismatch')]:
            with self.subTest(changes=changes):
                code, data, _ = self.invoke(request_id=reason, **changes)
                self.assertEqual((code, data['status']), (0, 'denied'))
                self.assertIn(reason, data['reasons'])
                self.assertFalse(data['historical'] or data['is_new_simulation'])
                self.assertEqual(self.invoke(True, proposals_operation='audit',
                    request_id=reason)[1]['status'], 'denied')
        self.decision('revoke', 2)
        self.assertEqual(self.invoke(request_id='revoked')[1]['status'], 'denied')
        proposal, evidence = self.store.get_action_proposal('p1')
        self.store.save_action_proposal(proposal.model_copy(update={'proposal_id': 'pending'}), evidence)
        digest = self.store.get_proposal_review_state('project', 'pending')[0]
        self.assertEqual(self.invoke(proposal_id='pending', request_id='pending',
            proposal_body_sha256=digest, expected_version=0)[1]['status'], 'denied')
        self.setUp(False)
        data = self.invoke()[1]
        self.assertIn('incomplete_task_source', data['reasons'])
        self.assertEqual(len(self.sql('SELECT * FROM proposal_dry_runs')), 1)

    def test_retry_guards_and_historical_audit(self):
        for guard in ('revoke', 'version', 'capture'):
            with self.subTest(guard=guard):
                self.setUp()
                original = self.invoke()[1]
                if guard == 'revoke':
                    self.decision('revoke', 2)
                elif guard == 'version':
                    self.decision('authorize_dry_run', 2)
                else:
                    snapshot = self.capture.snapshot.model_copy(deep=True)
                    snapshot.id = 'new'
                    snapshot.captured_at += timedelta(seconds=1)
                    self.store.save_snapshot(snapshot)
                self.assertEqual(self.invoke()[0], 2)
                with patch.object(cli, 'simulate_proposal', side_effect=AssertionError):
                    code, data, _ = self.invoke(True, proposals_operation='audit')
                self.assertEqual(code, 0)
                self.assertTrue(data['historical'])
                self.assertFalse(data['is_new_simulation'])
                self.assertEqual(data['recorded_at'], original['recorded_at'])

    def test_invalid_bindings_no_writer(self):
        for changes in ({'capture_id': 'b' * 64}, {'proposal_body_sha256': 'b' * 64},
                        {'project': 'foreign'}, {'proposal_id': 'missing'},
                        {'request_id': ' '}, {'expected_version': True},
                        {'proposal_body_sha256': 'bad'}):
            with self.subTest(changes=changes):
                self.assertEqual(self.invoke(True, **changes),
                    (2, None, 'ERROR: Proposal dry-run request failed.\n'))
        for changes in ({'request_id': 42}, {'expected_version': True}, {'project': ' '}):
            self.assertEqual(self.invoke(True, preconnect=True, **changes)[0], 2)
        self.assertEqual(self.invoke(True, preconnect=True, proposals_operation='audit',
            request_id=' ')[0], 2)

    def test_conflict_and_missing_foreign_audit(self):
        self.invoke()
        self.assertEqual(self.invoke(permission_event_id='conflict')[0], 2)
        errors = [self.invoke(True, proposals_operation='audit', **changes)
                  for changes in ({'request_id': 'missing'}, {'project': 'foreign'},
                                  {'proposal_id': 'foreign'})]
        self.assertEqual(errors, [(2, None, 'ERROR: Proposal dry-run request failed.\n')] * 3)

    def test_missing_legacy_and_corrupt_readonly(self):
        self.assertEqual(self.invoke(True, db_path=str(self.root / 'absent' / 'db'))[0], 2)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('ALTER TABLE proposal_dry_runs RENAME TO legacy_audit')
            conn.commit()
        self.assertEqual(self.invoke(True)[0], 2)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('ALTER TABLE legacy_audit RENAME TO proposal_dry_runs')
            conn.execute("UPDATE action_proposals SET data_json='PRIVATE-corruption'")
            conn.commit()
        self.assertEqual(self.invoke(True)[0], 2)
        for table in ('action_proposals', 'proposal_decisions', 'snapshot_captures'):
            with self.subTest(missing=table):
                self.setUp()
                with closing(sqlite3.connect(self.db)) as conn:
                    conn.execute(f'ALTER TABLE {table} RENAME TO unavailable')
                    conn.commit()
                self.assertEqual(self.invoke(True)[0], 2)
        for table in ('proposal_decisions', 'snapshot_captures', 'proposal_dry_runs'):
            with self.subTest(corrupt=table):
                self.setUp()
                self.invoke()
                column = 'request_json' if table == 'proposal_dry_runs' else 'data_json'
                with closing(sqlite3.connect(self.db)) as conn:
                    # Preserve the real append-only table; inject an unprotected
                    # corrupt fixture copy rather than defeating its triggers.
                    conn.execute(f'ALTER TABLE {table} RENAME TO original_ledger')
                    conn.execute(f'CREATE TABLE {table} AS SELECT * FROM original_ledger')
                    conn.execute(f"UPDATE {table} SET {column}='PRIVATE-corruption'")
                    conn.commit()
                self.assertEqual(self.invoke(True)[0], 2)

    def test_parser_explicit_only(self):
        base = ['proposals', 'dry-run', 'project', 'p1']
        flags = ['--request-id', 'r1', '--capture-id', self.capture.capture_id,
                 '--permission-event-id', 'event-1', '--expected-version', '2',
                 '--proposal-body-sha256', self.digest, '--db-path', str(self.db)]
        parser = cli.build_parser()
        self.assertEqual(parser.parse_args(base + flags).expected_version, 2)
        with redirect_stderr(io.StringIO()):
            for index in range(0, len(flags), 2):
                with self.subTest(flag=flags[index]), self.assertRaises(SystemExit):
                    parser.parse_args(base + flags[:index] + flags[index + 2:])
            for flag in ('--actor', '--execute', '--live', '--sources', '--field', '--value'):
                with self.subTest(flag=flag), self.assertRaises(SystemExit):
                    parser.parse_args(base + flags + [flag, 'x'])
        self.assertEqual(parser.parse_args(['proposals', 'audit', 'project', 'p1', 'r1',
            '--db-path', str(self.db)]).request_id, 'r1')
