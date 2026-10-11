"""Offline CLI contracts using genuine local capture and frozen evidence."""
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
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
        self.db = self.root / 'catalogue.db'
        store = SQLiteStorageAdapter(str(self.db))
        now = datetime(2026, 10, 10, 12)
        snapshot = WeeklySnapshot(id='snapshot', project_id='project',
            cutoff_datetime=now, captured_at=now, tasks=[TaskCard(
                id='task', external_id='external', title='PRIVATE-TITLE',
                priority=None, observed_fields=['priority'])])
        capture = store.save_snapshot_with_capture_receipt(snapshot)
        evidence = EvidenceItem(category='task', summary='PRIVATE-SUMMARY',
            provenance=EvidenceProvenance(id='e1', project_id='project', source='manual',
                timestamp=now, external_id='external', payload={'priority': None,
                'snippet': 'PRIVATE-SNIPPET', 'url': 'https://PRIVATE-URL',
                'path': '/PRIVATE-PATH'}))
        proposal = ActionProposal(proposal_id='p1', project_id='project', task_id='task',
            observed_capture_id=capture.capture_id, field='priority', current_value=None,
            proposed_value='high', evidence_ids=['e1'], rationale='PRIVATE-RATIONALE',
            expected_impact='PRIVATE-IMPACT')
        store.save_action_proposal(proposal, [evidence])
        self.digest = store.get_proposal_review_state('project', 'p1')[0]

    def fingerprint(self):
        with closing(sqlite3.connect(self.db)) as conn:
            schema = conn.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name').fetchall()
        return ({str(p.relative_to(self.root)): p.read_bytes()
                 for p in self.root.rglob('*') if p.is_file()}, schema,
                sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')))

    def args(self, decision='approve_offline', version=0, **changes):
        values = dict(subcommand='proposals', proposals_operation='decide', project='project',
            proposal_id='p1', db_path=str(self.db), decision=decision, actor='human-explicit',
            event_id=f'event-{version}', expected_version=version,
            proposal_body_sha256=self.digest)
        return cli.argparse.Namespace(**(values | changes))

    def invoke(self, args, unchanged=False):
        before = self.fingerprint()
        out, err = io.StringIO(), io.StringIO()
        actual = SQLiteStorageAdapter
        def guarded(*a, **kw):
            if unchanged:
                self.assertTrue(kw.get('read_only'), 'writer initialized')
            return actual(*a, **kw)
        with patch.object(cli, 'SQLiteStorageAdapter', side_effect=guarded), \
                patch.object(cli, '_build_orchestrator', side_effect=AssertionError), \
                patch.object(cli.Settings, 'from_env_and_file', side_effect=AssertionError), \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.proposals_command(args)
        if unchanged:
            self.assertEqual(self.fingerprint(), before)
        self.assertNotIn('PRIVATE', out.getvalue() + err.getvalue())
        self.assertNotIn(str(self.root), out.getvalue() + err.getvalue())
        return code, out.getvalue(), err.getvalue()

    def test_state_allowlist_and_readonly(self):
        code, out, err = self.invoke(self.args(proposals_operation='state'), True)
        self.assertEqual((code, err), (0, ''))
        data = json.loads(out)
        self.assertEqual(set(data), {'schema_version', 'project_id', 'proposal_id',
            'capture_id', 'proposal_body_sha256', 'version', 'review_status',
            'authorization_status', 'permission_scope', 'execution_status',
            'real_execution_allowed', 'identity_verified', 'actor_trust', 'events',
            'authorization_limit', 'assertion_notice'})
        self.assertEqual((data['version'], data['events']), (0, []))
        self.assertFalse(data['identity_verified'])
        self.assertFalse(data['real_execution_allowed'])

    def test_sequence_replay_current_and_event_allowlist(self):
        for version, decision, permission in [(0, 'approve_offline', 'not_authorized'),
                (1, 'authorize_dry_run', 'authorized_dry_run'), (2, 'deny', 'denied'),
                (3, 'authorize_dry_run', 'authorized_dry_run'), (4, 'revoke', 'revoked'),
                (5, 'reject', 'revoked')]:
            code, out, err = self.invoke(self.args(decision, version))
            self.assertEqual((code, err), (0, ''))
            data = json.loads(out)
            self.assertEqual(data['version'], version + 1)
            self.assertEqual(data['authorization_status'], permission)
            self.assertEqual(data['permission_scope'],
                'bound_proposal_dry_run' if permission == 'authorized_dry_run' else None)
            self.assertFalse(data['real_execution_allowed'])
        code, replay, err = self.invoke(self.args(), True)
        self.assertEqual((code, err, json.loads(replay)), (0, '', data))
        event = data['events'][0]
        self.assertEqual(set(event), {'event_id', 'project_id', 'proposal_id', 'capture_id',
            'proposal_body_sha256', 'expected_version', 'decision', 'actor'})
        self.assertEqual(event['actor'], {'actor_id': 'human-explicit', 'provenance': 'asserted_offline'})

    def test_preflight_failures_static_and_no_writer(self):
        bad = [dict(decision='authorize_dry_run'), dict(actor=' '), dict(actor=True),
            dict(event_id=''), dict(expected_version=True), dict(expected_version=-1),
            dict(proposal_body_sha256='A' * 64), dict(proposal_body_sha256='c' * 64),
            dict(project='foreign'), dict(proposal_id='missing'), dict(decision='execute'),
            dict(db_path=str(self.root / 'missing' / 'db'))]
        for changes in bad:
            with self.subTest(changes=changes):
                self.assertEqual(self.invoke(self.args(**changes), True),
                    (2, '', 'ERROR: Proposal review request failed.\n'))
        self.assertEqual(self.invoke(self.args())[0], 0)
        for changes in [dict(decision='reject'), dict(event_id='fresh'),
                        dict(actor='different'), dict(decision='approve_offline',
                        expected_version=1, event_id='new')]:
            with self.subTest(changes=changes):
                self.assertEqual(self.invoke(self.args(**changes), True)[0], 2)

    def test_legacy_corrupt_and_missing_catalogue(self):
        for mode in ('legacy', 'corrupt', 'missing_catalogue'):
            path = self.root / (mode + '.db')
            if mode == 'corrupt':
                path.write_bytes(b'PRIVATE-CORRUPT')
            else:
                with closing(sqlite3.connect(path)) as conn:
                    conn.execute('CREATE TABLE unrelated (id TEXT)')
                    if mode == 'missing_catalogue':
                        conn.execute('CREATE TABLE proposal_decisions (id TEXT)')
            with self.subTest(mode=mode):
                for operation in ('state', 'decide'):
                    self.assertEqual(self.invoke(self.args(db_path=str(path),
                        proposals_operation=operation), True),
                        (2, '', 'ERROR: Proposal review request failed.\n'))

    def test_existing_catalogue_missing_ledger_and_tamper(self):
        # Copy through SQLite's public backup API, never inspect the ledger directly.
        for mode in ('legacy', 'tamper'):
            path = self.root / (mode + '.db')
            with closing(sqlite3.connect(self.db)) as source, \
                    closing(sqlite3.connect(path)) as target:
                source.backup(target)
                if mode == 'legacy':
                    target.execute('ALTER TABLE proposal_decisions RENAME TO old_decisions')
                else:
                    target.execute("UPDATE action_proposals SET content_hash=?", ('b' * 64,))
                target.commit()
            for operation in ('state', 'decide'):
                with self.subTest(mode=mode, operation=operation):
                    self.assertEqual(self.invoke(self.args(db_path=str(path),
                        proposals_operation=operation), True),
                        (2, '', 'ERROR: Proposal review request failed.\n'))

    def test_main_dispatch(self):
        with patch('sys.argv', ['agente_gestion', 'proposals', 'state', 'project', 'p1',
                   '--db-path', str(self.db)]), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(), 0)

    def test_parser_required_and_no_forged_authority(self):
        base = ['proposals', 'decide', 'project', 'p1']
        flags = ['--decision', 'approve_offline', '--actor', 'human', '--event-id', 'id',
            '--expected-version', '0', '--proposal-body-sha256', self.digest,
            '--db-path', str(self.db)]
        parser = cli.build_parser()
        self.assertEqual(parser.parse_args(base + flags).expected_version, 0)
        for index in range(0, len(flags), 2):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                parser.parse_args(base + flags[:index] + flags[index + 2:])
            self.assertEqual(error.exception.code, 2)
        for flag in ('--live', '--execute', '--admin', '--authenticated', '--sources', '--env-file'):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(base + flags + [flag])
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            parser.parse_args(base[:2] + ['--help'])
        for phrase in ('identity unverified', 'dry-run only', 'not authorship proof', 'legal grant'):
            self.assertIn(phrase, ' '.join(out.getvalue().split()))
        with patch.object(cli, 'SQLiteStorageAdapter', side_effect=AssertionError), \
                redirect_stderr(io.StringIO()):
            self.assertEqual(cli.proposals_command(self.args(actor=' ')), 2)
