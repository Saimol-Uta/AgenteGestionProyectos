"""Offline contract tests for observed task planning metadata (H1A)."""

from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from agente_gestion.adapters.sources.fixtures import FixtureSourcesAdapter
from agente_gestion.adapters.sources.http_transport import HttpResponse
from agente_gestion.adapters.sources.trello import TrelloSourceAdapter
from agente_gestion.domain.tasks import TaskCard, TaskMappingConfig, TaskState, calculate_task_metrics
from agente_gestion.ports.source_errors import SourceError, SourceErrorCategory
from tests.test_trello import FakeTransport


METADATA = ("assignee_ids", "start_date", "due_complete", "is_archived",
            "priority", "last_activity_at", "observed_fields")


class TestTaskMetadata(unittest.TestCase):
    def fixture_tasks(self, raw):
        adapter = FixtureSourcesAdapter()
        with patch.object(adapter, "load_fixture", return_value=json.loads(json.dumps({"tasks": raw}))):
            return adapter.get_tasks("synthetic")

    def trello(self, raw, strategy="list_mapping", closed_list=False):
        transport = FakeTransport({
            "https://api.trello.com/1/boards/board/lists/all": HttpResponse(200, [
                {"id": "todo", "idBoard": "board", "name": "Todo", "closed": closed_list}]),
            "https://api.trello.com/1/boards/board/cards/all": HttpResponse(200, raw),
        })
        adapter = TrelloSourceAdapter(
            "board", "sentinel_key", "sentinel_token", transport=transport,
            mapping_config=TaskMappingConfig(pending_list_ids=["todo"]),
            state_strategy=strategy, clock=lambda: datetime(2026, 10, 10, tzinfo=timezone.utc))
        tasks = adapter.get_tasks("synthetic")
        evidence = adapter.get_evidence("synthetic")
        # One members read for the whole board, reused by every card of the collection.
        self.assertEqual(len(transport.calls), 3)
        return tasks, evidence

    def card(self, **metadata):
        return {"id": "card", "name": "Task", "idBoard": "board", "idList": "todo",
                "closed": False, "labels": ["high"], **metadata}

    def test_legacy_domain_and_fixture_json(self):
        raw = {"id": "old", "title": "Legacy", "state": "pending"}
        for task in (TaskCard.model_validate_json(json.dumps(raw)), self.fixture_tasks([raw])[0]):
            for field in METADATA:
                self.assertIsNone(getattr(task, field), field)
            restored = TaskCard.model_validate_json(task.model_dump_json())
            self.assertEqual(restored, task)
            self.assertTrue(task.is_included)

    def test_rich_fixture_and_serialized_observations(self):
        raw = {"id": "rich", "title": "Planning", "assignee_ids": ["b", "a", "b"],
               "start_date": "2026-10-01T10:00:00", "due_complete": False,
               "is_archived": True, "priority": "urgent", "last_activity_at": "2026-10-02T15:00:00Z"}
        task = self.fixture_tasks([raw])[0]
        self.assertEqual(task.assignee_ids, ["a", "b"])
        self.assertEqual(task.start_date.utcoffset().total_seconds(), -18000)
        self.assertEqual(task.last_activity_at.utcoffset().total_seconds(), -18000)
        self.assertEqual(task.priority, "urgent")
        self.assertFalse(task.due_complete)
        self.assertTrue(task.is_archived)
        self.assertEqual(set(task.observed_fields), set(raw))
        self.assertEqual(self.fixture_tasks([json.loads(task.model_dump_json())])[0], task)
        explicit = {**raw, "observed_fields": ["assignee_ids"]}
        self.assertEqual(self.fixture_tasks([explicit])[0].observed_fields, ["assignee_ids"])

    def test_fixture_missing_empty_and_null(self):
        base = {"id": "task", "title": "Task"}
        tasks = self.fixture_tasks([base, {**base, "assignee_ids": []}, {**base, "assignee_ids": None}])
        self.assertEqual([t.assignee_ids for t in tasks], [None, [], None])
        self.assertEqual([t.observed_fields for t in tasks], [None, ["assignee_ids", "id", "title"],
                                                             ["assignee_ids", "id", "title"]])
        for task in tasks:
            self.assertEqual(TaskCard.model_validate_json(task.model_dump_json()), task)

    def test_rich_trello_both_strategies_and_evidence(self):
        for strategy, expected in (("list_mapping", TaskState.pending), ("date_fields", TaskState.in_progress)):
            with self.subTest(strategy=strategy):
                tasks, evidence = self.trello([self.card(
                    idMembers=["member_b", "member_a", "member_b"], dueComplete=False,
                    start="2026-10-01T12:00:00+02:00", dateLastActivity="2026-10-02T15:00:00Z",
                    due="2026-10-20T15:00:00Z")], strategy)
                task, payload = tasks[0], evidence[0].provenance.payload
                self.assertEqual(task.state, expected)
                self.assertEqual(task.assignee_ids, ["member_a", "member_b"])
                self.assertIsNone(task.priority)  # 'high' label is not priority evidence.
                self.assertNotIn("priority", task.observed_fields)
                self.assertEqual(task.start_date.hour, 5)
                self.assertEqual(task.last_activity_at.hour, 10)
                serialized = json.loads(task.model_dump_json())
                for field in METADATA:
                    self.assertEqual(payload[field], serialized[field], field)
                self.assertEqual(payload["member_ids"], task.assignee_ids)
                self.assertEqual(TaskCard.model_validate_json(task.model_dump_json()), task)

    def test_trello_missing_empty_and_null_dates(self):
        for fields, expected_ids in (({}, None), ({"idMembers": []}, []),
                                     ({"start": None, "dateLastActivity": None}, None)):
            with self.subTest(fields=fields):
                tasks, _ = self.trello([self.card(**fields)])
                task = TaskCard.model_validate_json(tasks[0].model_dump_json())
                self.assertEqual(task.assignee_ids, expected_ids)
                self.assertEqual("assignee_ids" in task.observed_fields, "idMembers" in fields)
                self.assertEqual("start_date" in task.observed_fields, "start" in fields)
                self.assertEqual("last_activity_at" in task.observed_fields, "dateLastActivity" in fields)

    def test_closed_cards_counts_and_contradiction_preserved(self):
        for strategy in ("list_mapping", "date_fields"):
            with self.subTest(strategy=strategy):
                tasks, _ = self.trello([self.card(closed=True, dueComplete=True,
                    start="2026-11-01T00:00:00Z", labels=["blocked"])], strategy)
                task = tasks[0]
                self.assertTrue(task.is_archived)
                self.assertFalse(task.is_included)
                self.assertEqual(task.state, TaskState.completed if strategy == "date_fields" else TaskState.blocked)
                self.assertEqual(task.has_contradiction, strategy == "date_fields")
                metrics = calculate_task_metrics(tasks, datetime(2026, 10, 10, tzinfo=timezone.utc))
                self.assertEqual((metrics.total_cards, metrics.included_cards, metrics.completed_cards), (1, 0, 0))
                self.assertIsNone(metrics.progress_percentage)
        tasks, _ = self.trello([self.card()], closed_list=True)
        self.assertFalse(tasks[0].is_archived)  # List closure is not card archival.
        self.assertFalse(tasks[0].is_included)

    def test_domain_metadata_rejects_coercion(self):
        for fields in ({"assignee_ids": [1]}, {"assignee_ids": "a"}, {"due_complete": 1},
                       {"is_archived": "false"}, {"priority": 2}, {"observed_fields": [1]},
                       {"start_date": 123}, {"last_activity_at": True}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                TaskCard(id="task", title="Task", **fields)

    def test_trello_invalid_metadata_and_identity_echo_fail_closed(self):
        for fields in ({"idMembers": [1]}, {"idMembers": None}, {"idMembers": ["sentinel_key"]},
                       {"dueComplete": 1}, {"dueComplete": None}, {"start": "2026-10-01T10:00:00"},
                       {"dateLastActivity": "invalid"}, {"priority": 1}):
            with self.subTest(fields=fields):
                with self.assertRaises(SourceError) as error:
                    self.trello([self.card(**fields)])
                self.assertEqual(error.exception.category, SourceErrorCategory.malformed_payload)

    def test_explicit_priority_and_fixture_strict_types(self):
        tasks, evidence = self.trello([self.card(priority="urgent")])
        self.assertEqual(tasks[0].priority, "urgent")
        self.assertIn("priority", tasks[0].observed_fields)
        self.assertEqual(evidence[0].provenance.payload["priority"], "urgent")
        for fields in ({"due_complete": "false"}, {"assignee_ids": [1]}, {"priority": 3},
                       {"start_date": 123}, {"last_activity_at": True}):
            with self.subTest(fields=fields), self.assertRaises((ValueError, TypeError)):
                self.fixture_tasks([{"id": "task", "title": "Task", **fields}])


if __name__ == "__main__":
    unittest.main()
