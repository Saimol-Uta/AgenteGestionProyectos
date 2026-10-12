"""Unit and integration tests for tasks planning query service and CLI commands."""

from datetime import datetime, timezone
import json
import os
import tempfile
import unittest
from zoneinfo import ZoneInfo

from agente_gestion.domain.tasks import DEFAULT_TIMEZONE, TaskCard, TaskState
from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.application.tasks_query import (
    TaskFilter,
    build_task_plan_summary,
    extract_explicit_dependencies,
    format_plan_summary,
    format_task_detail,
    format_tasks_table,
    query_tasks_view,
)
from agente_gestion.cli import build_parser, main


class TestTasksQueryService(unittest.TestCase):
    def setUp(self):
        self.cutoff = datetime(2026, 10, 12, 12, 0, tzinfo=DEFAULT_TIMEZONE)
        self.c1 = TaskCard(
            id="task-001",
            title="Setup DB schema",
            state=TaskState.completed,
            list_name="Fase 1",
            is_included=True,
            due_complete=True,
            due_date=datetime(2026, 10, 10, 18, 0, tzinfo=DEFAULT_TIMEZONE),
        )
        self.c2 = TaskCard(
            id="task-002",
            title="Implement API endpoint",
            state=TaskState.in_progress,
            list_name="Fase 2",
            is_included=True,
            due_complete=False,
            due_date=datetime(2026, 10, 14, 18, 0, tzinfo=DEFAULT_TIMEZONE),
            detail="Depende de: task-001",
            assignee_ids=["dev-1"],
        )
        self.c3 = TaskCard(
            id="task-003",
            title="Configure CI pipeline",
            state=TaskState.blocked,
            list_name="Fase 2",
            is_included=True,
            due_complete=False,
            due_date=datetime(2026, 10, 11, 18, 0, tzinfo=DEFAULT_TIMEZONE),
            assignee_ids=["dev-2"],
        )
        self.c4 = TaskCard(
            id="task-004",
            title="Frontend Dashboard",
            state=TaskState.pending,
            list_name="Fase 3",
            is_included=True,
            due_complete=False,
            due_date=datetime(2026, 10, 25, 18, 0, tzinfo=DEFAULT_TIMEZONE),
            detail="blocked by: task-003",
            assignee_ids=["dev-3"],
        )
        self.c5 = TaskCard(
            id="task-005",
            title="Legacy card",
            state=TaskState.unknown,
            list_name="Backlog",
            is_included=False,
            has_contradiction=True,
        )

        self.snapshot = WeeklySnapshot(
            id="snap-test",
            project_id="PROJ-TEST",
            cutoff_datetime=self.cutoff,
            captured_at=self.cutoff,
            tasks=[self.c1, self.c2, self.c3, self.c4, self.c5],
        )

    def test_build_task_plan_summary(self):
        summary = build_task_plan_summary(self.snapshot, capture_id="cap-test")
        self.assertEqual(summary.project_id, "PROJ-TEST")
        self.assertEqual(summary.total_tasks, 5)
        self.assertEqual(summary.included_tasks, 4)
        self.assertEqual(summary.completed_tasks, 1)
        self.assertEqual(summary.progress_percentage, 25.0)
        self.assertEqual(summary.overdue_count, 1)  # task-003 is overdue
        self.assertEqual(summary.due_soon_count, 1)  # task-002 is due soon (Oct 14 within 7 days)
        self.assertEqual(summary.blocked_count, 1)  # task-003 is blocked
        self.assertEqual(summary.unknown_count, 1)  # task-005 is unknown
        self.assertEqual(summary.contradictory_count, 1)  # task-005

    def test_extract_explicit_dependencies(self):
        cards_map = {c.id: c for c in [self.c1, self.c2, self.c3, self.c4, self.c5]}
        
        # c2 depends on c1 (c1 is completed)
        deps2, impacts2 = extract_explicit_dependencies(self.c2, cards_map)
        self.assertEqual(deps2, ["task-001"])
        self.assertEqual(impacts2, [])  # completed dependency has no blocking impact

        # c4 depends on c3 (c3 is blocked)
        deps4, impacts4 = extract_explicit_dependencies(self.c4, cards_map)
        self.assertEqual(deps4, ["task-003"])
        self.assertEqual(len(impacts4), 1)
        self.assertIn("está bloqueada", impacts4[0])

        # c1 has no dependencies
        deps1, impacts1 = extract_explicit_dependencies(self.c1, cards_map)
        self.assertEqual(deps1, [])
        self.assertEqual(impacts1, [])

    def test_query_tasks_view_filters(self):
        summary, all_views = query_tasks_view(self.snapshot, capture_id="cap-test")
        self.assertEqual(len(all_views), 5)

        # Filter by state
        _, completed_views = query_tasks_view(
            self.snapshot, capture_id="cap-test", filters=TaskFilter(state=TaskState.completed)
        )
        self.assertEqual(len(completed_views), 1)
        self.assertEqual(completed_views[0].id, "task-001")

        # Filter critical only
        _, critical_views = query_tasks_view(
            self.snapshot, capture_id="cap-test", filters=TaskFilter(critical_only=True)
        )
        critical_ids = {v.id for v in critical_views}
        # task-002 is due soon, task-003 is overdue and blocked, task-005 has contradiction
        self.assertIn("task-002", critical_ids)
        self.assertIn("task-003", critical_ids)
        self.assertIn("task-005", critical_ids)
        self.assertNotIn("task-001", critical_ids)

        # Filter by assignee
        _, dev1_views = query_tasks_view(
            self.snapshot, capture_id="cap-test", filters=TaskFilter(assignee_id="dev-1")
        )
        self.assertEqual(len(dev1_views), 1)
        self.assertEqual(dev1_views[0].id, "task-002")

        # Filter by list name
        _, f2_views = query_tasks_view(
            self.snapshot, capture_id="cap-test", filters=TaskFilter(list_name="Fase 2")
        )
        self.assertEqual(len(f2_views), 2)
        self.assertEqual({v.id for v in f2_views}, {"task-002", "task-003"})

    def test_formatters(self):
        summary, views = query_tasks_view(self.snapshot, capture_id="cap-test")
        summary_text = format_plan_summary(summary)
        self.assertIn("PLANIFICACIÓN Y ESTADO DE TAREAS", summary_text)
        self.assertIn("PROJ-TEST", summary_text)

        table_text = format_tasks_table(views)
        self.assertIn("task-001", table_text)
        self.assertIn("PRÓXIMA", table_text)
        self.assertIn("BLOQUEADA", table_text)

        detail_text = format_task_detail(views[1])  # task-002
        self.assertIn("task-002", detail_text)
        self.assertIn("task-001", detail_text)


class TestTasksCliIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_tasks.db")
        self.storage = SQLiteStorageAdapter(db_path=self.db_path)

        cutoff = datetime(2026, 10, 12, 12, 0, tzinfo=DEFAULT_TIMEZONE)
        cards = [
            TaskCard(
                id="c-1",
                title="Card 1",
                state=TaskState.completed,
                is_included=True,
                due_complete=True,
            ),
            TaskCard(
                id="c-2",
                title="Card 2",
                state=TaskState.in_progress,
                is_included=True,
                due_complete=False,
                due_date=datetime(2026, 10, 15, 12, 0, tzinfo=DEFAULT_TIMEZONE),
                assignee_ids=["user-a"],
            ),
        ]
        self.snapshot = WeeklySnapshot(
            id="snap-cli",
            project_id="CLI-PROJ",
            cutoff_datetime=cutoff,
            captured_at=cutoff,
            tasks=cards,
        )
        self.capture_id = self.storage.save_snapshot_with_capture_receipt(self.snapshot)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cli_summary_plain_and_json(self):
        parser = build_parser()
        args = parser.parse_args(["tasks", "summary", "CLI-PROJ", "--db-path", self.db_path])
        from agente_gestion.cli import tasks_command
        rc = tasks_command(args)
        self.assertEqual(rc, 0)

        # JSON
        args_json = parser.parse_args(["tasks", "summary", "CLI-PROJ", "--db-path", self.db_path, "--json"])
        rc_json = tasks_command(args_json)
        self.assertEqual(rc_json, 0)

    def test_cli_list_plain_and_filter(self):
        parser = build_parser()
        args = parser.parse_args(["tasks", "list", "CLI-PROJ", "--db-path", self.db_path, "--state", "en_curso"])
        from agente_gestion.cli import tasks_command
        rc = tasks_command(args)
        self.assertEqual(rc, 0)

    def test_cli_show_plain_and_not_found(self):
        parser = build_parser()
        args = parser.parse_args(["tasks", "show", "CLI-PROJ", "c-2", "--db-path", self.db_path])
        from agente_gestion.cli import tasks_command
        rc = tasks_command(args)
        self.assertEqual(rc, 0)

        args_nf = parser.parse_args(["tasks", "show", "CLI-PROJ", "c-999", "--db-path", self.db_path])
        rc_nf = tasks_command(args_nf)
        self.assertEqual(rc_nf, 1)

    def test_cli_unknown_project(self):
        parser = build_parser()
        args = parser.parse_args(["tasks", "summary", "NON_EXISTENT", "--db-path", self.db_path])
        from agente_gestion.cli import tasks_command
        rc = tasks_command(args)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()

