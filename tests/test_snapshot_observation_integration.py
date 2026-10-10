"""Offline two-capture contract through real Trello, factory and composite adapters."""
import json
import unittest
from datetime import datetime, timezone
from hashlib import sha256
from unittest.mock import Mock, patch

from agente_gestion.adapters.sources.composite import CompositeSourcesAdapter
from agente_gestion.adapters.sources.factory import create_composite_source
from agente_gestion.adapters.sources.http_transport import HttpResponse
from agente_gestion.application.config import Settings
from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.domain.task_comparison import compare_task_snapshots
from agente_gestion.ports.source_errors import SourceError, SourceErrorCategory, SourceProvider


CUTOFF = datetime(2026, 10, 6, tzinfo=timezone.utc)
SHA = "1" * 40


class BoardTransport:
    """Only in-memory Trello responses; unexpected requests fail closed."""
    def __init__(self, board, list_id, list_name, cards, fail=False):
        self.board, self.list_id, self.list_name = board, list_id, list_name
        self.cards, self.fail = cards, fail

    def get(self, url, **kwargs):
        if self.fail:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.timeout)
        if url == f"https://api.trello.com/1/boards/{self.board}/lists/all":
            return HttpResponse(status_code=200, body=[{
                "id": self.list_id, "idBoard": self.board,
                "name": self.list_name, "closed": False,
            }])
        if url == f"https://api.trello.com/1/boards/{self.board}/cards/all":
            return HttpResponse(status_code=200, body=self.cards)
        raise AssertionError("Unexpected request")


class SnapshotObservationIntegrationTests(unittest.TestCase):
    def settings(self, **overrides):
        return Settings(_env_file=None, **{
            "source_mode": "real", "trello_board_id": "board-1",
            "trello_api_key": "synthetic-key", "trello_token": "synthetic-token",
            "trello_pending_list_ids": ["todo"],
            "trello_in_progress_list_ids": ["doing"],
            "github_owner": "owner", "github_repo": "repo",
            "github_commit_sha": SHA, **overrides,
        })

    def capture(self, settings=None, *, list_id="todo", list_name="Todo",
                empty=False, fail=False, completed=False, clock=None,
                task_evidence_project=None, project="project-1"):
        settings = settings or self.settings()
        card = {
            "id": "card-1", "idBoard": settings.trello_board_id,
            "idList": list_id, "name": "Task", "desc": "Detail",
            "closed": False, "due": None, "idMembers": [],
            "labels": [{"name": "high"}], "dueComplete": completed,
            "start": "2026-10-06T12:00:00Z",
        }
        transport = BoardTransport(settings.trello_board_id, list_id, list_name,
                                   [] if empty else [card], fail=fail)
        code = Mock(last_resolved_commit_sha=SHA, last_collection_complete=True)
        code.get_diffs.return_value = []
        code.get_evidence.return_value = []
        ci = Mock()
        ci.get_ci_results.return_value = []  # Missing CI makes the overall cut incomplete.
        ci.get_evidence.return_value = []
        policy = Mock()
        policy.get_policies.return_value = []
        with patch("agente_gestion.adapters.sources.factory.create_code_source", return_value=code), \
             patch("agente_gestion.adapters.sources.factory.GitHubActionsSourceAdapter", return_value=ci), \
             patch("agente_gestion.adapters.sources.factory.create_policy_source", return_value=policy):
            adapter = create_composite_source(settings, transport=transport)
        # Clock injection exercises the real strategy without wall-clock dependencies.
        if clock is not None:
            adapter._task_source._clock = lambda: clock
        if task_evidence_project is not None:
            real_get_evidence = adapter._task_source.get_evidence
            def foreign_evidence(project_id):
                items = real_get_evidence(project_id)
                for item in items:
                    item.provenance.project_id = task_evidence_project
                return items
            adapter._task_source.get_evidence = foreign_evidence
        return adapter.collect_snapshot(project, CUTOFF)

    def compare(self, old, new):
        return compare_task_snapshots(old.snapshot, new.snapshot,
                                      previous_evidence=old.evidence,
                                      current_evidence=new.evidence)

    def test_both_strategies_emit_derived_deltas_with_provenance(self):
        for strategy in ("list_mapping", "date_fields"):
            with self.subTest(strategy=strategy):
                settings = self.settings(trello_state_strategy=strategy)
                old = self.capture(settings, clock=CUTOFF)
                new = self.capture(settings, list_id="doing", list_name="Doing",
                                   completed=strategy == "date_fields", clock=CUTOFF)
                self.assertIsNotNone(old.snapshot.task_source_identity)
                self.assertEqual(old.snapshot.task_source_identity, new.snapshot.task_source_identity)
                self.assertTrue(old.snapshot.tasks_complete)
                self.assertTrue(new.snapshot.tasks_complete)
                self.assertFalse(old.snapshot.is_complete)
                comparison = self.compare(old, new)
                self.assertEqual(comparison.warnings, [])
                changes = {change.field: change for change in comparison.changes}
                self.assertTrue({"state", "list_name", "list_id"} <= changes.keys())
                for field in ("state", "list_name"):
                    change = changes[field]
                    self.assertEqual(change.previous_evidence_ids, ["trello-task-card-1"])
                    self.assertEqual(change.current_evidence_ids, ["trello-task-card-1"])
                    self.assertIsNone(change.actor)
                    self.assertEqual(change.kind, "field_changed")
                for result in (old, new):
                    task = result.snapshot.tasks[0]
                    payload = result.evidence[0].provenance.payload
                    self.assertTrue({"state", "list_name"} <= set(task.observed_fields))
                    self.assertEqual(payload["observed_fields"], task.observed_fields)
                    self.assertEqual(payload["state"], task.state.value)
                    self.assertEqual(payload["list_name"], task.list_name)
                    self.assertIsNone(task.priority)
                    self.assertNotIn("priority", task.observed_fields)

    def test_date_state_can_change_only_because_capture_time_changes(self):
        settings = self.settings(trello_state_strategy="date_fields")
        old = self.capture(settings, clock=CUTOFF)
        new = self.capture(settings, clock=datetime(2026, 10, 7, tzinfo=timezone.utc))
        comparison = self.compare(old, new)
        self.assertEqual([change.field for change in comparison.changes], ["state"])
        self.assertIsNone(comparison.changes[0].actor)
        self.assertEqual(old.snapshot.task_source_identity, new.snapshot.task_source_identity)

    def test_identity_is_whitelisted_canonical_sha256(self):
        settings = self.settings()
        mapping = settings.get_trello_mapping()
        expected = {
            "provider": "trello", "board_id": "board-1",
            "state_strategy": "list_mapping",
            "mapping": {key: sorted(set(value)) if value is not None else None
                        for key, value in mapping.model_dump(mode="json").items()},
        }
        encoded = json.dumps(expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        identity = self.capture(settings).snapshot.task_source_identity
        self.assertEqual(identity, "trello-sha256:" + sha256(encoded.encode("utf-8")).hexdigest())
        different_credentials = self.settings(trello_api_key="other-key", trello_token="other-token")
        self.assertEqual(identity, self.capture(different_credentials).snapshot.task_source_identity)
        self.assertEqual(identity, self.capture(settings, list_name="Renamed", empty=True).snapshot.task_source_identity)

    def test_effectively_equivalent_mapping_has_same_identity(self):
        first = self.settings(trello_pending_list_ids=["todo", "later"],
                              trello_blocked_labels=["blocked", "bloqueada"])
        second = self.settings(trello_pending_list_ids=["later", "todo", "todo"],
                               trello_blocked_labels=[" BLOQUEADA ", "blocked", ""])
        self.assertEqual(self.capture(first).snapshot.task_source_identity,
                         self.capture(second).snapshot.task_source_identity)

    def test_known_board_strategy_and_each_mapping_change_refuse_comparison(self):
        old = self.capture()
        variants = [
            {"trello_board_id": "board-2"},
            {"trello_state_strategy": "date_fields"},
            {"trello_pending_list_ids": ["different"]},
            {"trello_in_progress_list_ids": ["different"]},
            {"trello_completed_list_ids": ["different"]},
            {"trello_blocked_labels": ["different"]},
            {"trello_included_list_ids": ["todo"]},
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                new = self.capture(self.settings(**variant))
                self.assertNotEqual(old.snapshot.task_source_identity, new.snapshot.task_source_identity)
                with self.assertRaisesRegex(ValueError, "identities differ"):
                    self.compare(old, new)

    def test_successful_empty_tasks_are_complete_but_failed_collection_is_not(self):
        empty = self.capture(empty=True)
        failed = self.capture(fail=True)
        self.assertEqual(empty.snapshot.tasks, [])
        self.assertTrue(empty.snapshot.tasks_complete)
        self.assertFalse(empty.snapshot.is_complete)
        self.assertEqual(failed.snapshot.tasks, [])
        self.assertIs(failed.snapshot.tasks_complete, False)
        self.assertIn("limited_task_enumeration", self.compare(empty, failed).warnings)

    def test_task_evidence_validation_failure_is_not_complete(self):
        result = self.capture(task_evidence_project="foreign-project")
        self.assertIs(result.snapshot.tasks_complete, False)
        self.assertEqual(result.snapshot.tasks, [])
        self.assertEqual(result.evidence, [])

    def test_cross_project_comparison_guard_survives_collection(self):
        old = self.capture()
        new = self.capture(project="another-project")
        with self.assertRaisesRegex(ValueError, "different projects"):
            self.compare(old, new)

    def test_custom_composite_and_legacy_identity_remain_unknown(self):
        source = Mock()
        source.get_tasks.return_value = []
        source.get_policies.return_value = []
        source.get_evidence.return_value = []
        source.get_diffs.side_effect = ValueError("unavailable")
        adapter = CompositeSourcesAdapter(source, source, source, source)
        snapshot = adapter.collect_snapshot("project-1", CUTOFF).snapshot
        self.assertIsNone(snapshot.task_source_identity)
        self.assertTrue(snapshot.tasks_complete)
        legacy = WeeklySnapshot(id="legacy", project_id="project-1",
                                cutoff_datetime=CUTOFF, captured_at=CUTOFF)
        self.assertIsNone(legacy.task_source_identity)
        self.assertIsNone(legacy.tasks_complete)
        with patch("agente_gestion.adapters.sources.factory.create_task_source", return_value=source), \
             patch("agente_gestion.adapters.sources.factory.create_code_source", return_value=source), \
             patch("agente_gestion.adapters.sources.factory.GitHubActionsSourceAdapter", return_value=source), \
             patch("agente_gestion.adapters.sources.factory.create_policy_source", return_value=source):
            custom = create_composite_source(self.settings()).collect_snapshot("project-1", CUTOFF)
        self.assertIsNone(custom.snapshot.task_source_identity)
        self.assertTrue(custom.snapshot.tasks_complete)
