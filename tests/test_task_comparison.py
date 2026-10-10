"""Offline contracts for conservative planning observations."""
import unittest
from datetime import datetime

from agente_gestion.domain.evidence import EvidenceItem, EvidenceProvenance
from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.domain.tasks import TaskCard, TaskState
from agente_gestion.domain.task_comparison import compare_task_snapshots

A = datetime.fromisoformat("2026-10-10T12:00:00+00:00")
B = datetime.fromisoformat("2026-10-11T12:00:00+00:00")


def card(id="c", **facts):
    return TaskCard(id=id, title=facts.pop("title", "Task"),
                    observed_fields=facts.pop("observed_fields", list(facts)), **facts)


def snap(tasks, current=False, **metadata):
    return WeeklySnapshot(id="B" if current else "A", project_id="p",
                          cutoff_datetime=A, captured_at=B if current else A,
                          tasks=tasks, **metadata)


def evidence(id, payload, external_id="c", project="p", category="task"):
    return EvidenceItem(category=category, summary="synthetic",
                        provenance=EvidenceProvenance(id=id, source="synthetic",
                            project_id=project, external_id=external_id,
                            timestamp=A, payload=payload))


class TaskComparisonTests(unittest.TestCase):
    def compare(self, old, new, **kwargs):
        return compare_task_snapshots(snap(old), snap(new, True), **kwargs)

    def test_exact_four_planning_changes_and_context(self):
        old = card(state=TaskState.pending, assignee_ids=["A"],
                   due_date="2026-10-15T12:00:00Z", priority="normal")
        new = card(state=TaskState.in_progress, assignee_ids=["B"],
                   due_date="2026-10-19T12:00:00Z", priority="high")
        result = self.compare([old], [new])
        self.assertEqual([c.field for c in result.changes],
                         ["assignee_ids", "due_date", "priority", "state"])
        self.assertEqual(len(result.changes), 4)
        self.assertEqual((result.project_id, result.previous_snapshot_id,
                          result.current_snapshot_id), ("p", "A", "B"))
        self.assertEqual((result.previous_captured_at, result.current_captured_at), (A, B))
        self.assertEqual(result.changes[0].before, ["A"])
        self.assertEqual(result.changes[0].after, ["B"])
        self.assertTrue(all(c.kind == "field_changed" and c.actor is None for c in result.changes))

    def test_reject_project_chronology_source_and_duplicates(self):
        old, new = snap([card()]), snap([card()], True)
        for update in ({"project_id": "other"}, {"captured_at": A.replace(day=9)}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                compare_task_snapshots(old, new.model_copy(update=update))
        with self.assertRaises(ValueError):
            compare_task_snapshots(old.model_copy(update={"task_source_identity": "board/map1"}),
                                   new.model_copy(update={"task_source_identity": "board/map2"}))
        for previous, current in ((snap([card(), card()]), new), (old, snap([card(), card()], True))):
            with self.assertRaises(ValueError):
                compare_task_snapshots(previous, current)

    def test_missing_is_unknown_but_null_and_empty_are_clearing(self):
        old = card(priority="high", due_date=A, assignee_ids=["u"], labels=["x"])
        self.assertEqual(self.compare([old], [card()]).changes, [])
        new = card(priority=None, due_date=None, assignee_ids=[], labels=[])
        changes = self.compare([old], [new]).changes
        self.assertEqual([c.field for c in changes], ["assignee_ids", "due_date", "labels", "priority"])
        self.assertEqual([c.after for c in changes], [[], None, [], None])
        self.assertEqual(self.compare([card()], [old]).changes, [])

    def test_offsets_and_set_order_are_semantically_equal(self):
        old = card(due_date="2026-10-15T12:00:00Z", start_date=A,
                   labels=["z", "a", "a"], assignee_ids=["b", "a"], list_id="l")
        new = card(due_date="2026-10-15T07:00:00-05:00", start_date="2026-10-10T07:00:00-05:00",
                   labels=["a", "z"], assignee_ids=["a", "b"], list_id="l")
        self.assertEqual(self.compare([old], [new]).changes, [])

    def test_all_remaining_fields_and_excluded_archived_cards(self):
        old = card(title="old", observed_fields=["title", "list_id", "list_name", "due_complete",
                   "is_archived", "is_included", "has_contradiction"], list_id="l1", list_name="one",
                   due_complete=False, is_archived=False, is_included=False, has_contradiction=False)
        new = old.model_copy(update={"title": "new", "list_id": "l2", "list_name": "two",
                    "due_complete": True, "is_archived": True, "has_contradiction": True})
        changes = self.compare([old], [new]).changes
        self.assertEqual([c.field for c in changes], ["due_complete", "has_contradiction", "is_archived",
                                                    "list_id", "list_name", "title"])
        included = new.model_copy(update={"is_included": True})
        self.assertEqual(self.compare([new], [included]).changes[0].field, "is_included")

    def test_membership_is_not_creation_deletion_or_archive(self):
        result = self.compare([card("gone"), card("stay", priority="normal")],
                              [card("new"), card("stay", priority="high")])
        self.assertEqual([(c.task_id, c.kind, c.field) for c in result.changes],
                         [("gone", "not_observed", None), ("new", "first_observed", None),
                          ("stay", "field_changed", "priority")])
        self.assertIn("unknown_task_scope", result.warnings)
        self.assertIn("limited_task_enumeration", result.warnings)

    def test_task_completeness_independent_of_ci(self):
        old = snap([card(priority="normal")], tasks_complete=True,
                   task_source_identity="board/map", is_complete=False)
        new = snap([card(priority="high")], True, tasks_complete=True,
                   task_source_identity="board/map", is_complete=False)
        self.assertEqual(compare_task_snapshots(old, new).warnings, [])
        for completeness in (None, False):
            result = compare_task_snapshots(old, new.model_copy(update={"tasks_complete": completeness}))
            self.assertIn("limited_task_enumeration", result.warnings)
            self.assertEqual(len(result.changes), 1)
        self.assertIsNone(WeeklySnapshot.model_validate(old.model_dump(exclude={"tasks_complete", "task_source_identity"})).tasks_complete)

    def test_legacy_defaults_are_not_observations(self):
        old = TaskCard(id="c", title="Task", priority="normal", labels=["x"], state="pending")
        new = TaskCard(id="c", title="Task", priority=None, labels=[], state="en_curso")
        self.assertEqual(self.compare([old], [new]).changes, [])
        renamed = new.model_copy(update={"title": "Renamed"})
        self.assertEqual(self.compare([old], [renamed]).changes[0].field, "title")

    def test_evidence_ids_and_actual_legacy_facts_not_titles(self):
        old, new = TaskCard(id="local", external_id="remote", title="Task"), TaskCard(id="local", external_id="remote", title="Task")
        before = [evidence("old", {"card_id": "remote", "priority": "normal"}, external_id=None)]
        after = [evidence("new", {"priority": "high"}, external_id="remote"),
                 evidence("wrong-project", {"priority": "low"}, external_id="remote", project="q"),
                 evidence("wrong-category", {"priority": "low"}, external_id="remote", category="ci"),
                 evidence("wrong-card", {"title": "Task", "priority": "low"}, external_id="other")]
        result = self.compare([old], [new], previous_evidence=before, current_evidence=after)
        self.assertEqual(len(result.changes), 1)
        change = result.changes[0]
        self.assertEqual((change.before, change.after), ("normal", "high"))
        self.assertEqual(change.previous_evidence_ids, ["old"])
        self.assertEqual(change.current_evidence_ids, ["new"])

    def test_evidence_observation_mask_prevents_default_inference(self):
        old, new = TaskCard(id="c", title="Task"), TaskCard(id="c", title="Task")
        result = self.compare([old], [new],
            previous_evidence=[evidence("a", {"priority": "high", "observed_fields": []})],
            current_evidence=[evidence("b", {"priority": None, "observed_fields": ["priority"]})])
        self.assertEqual(result.changes, [])

    def test_stable_nonduplicated_ids_order_and_no_mutation(self):
        old = snap([card("z", priority="normal"), card("a", priority="normal")])
        new = snap([card("a", priority="high"), card("z", priority="high")], True)
        proofs = [evidence("p2", {"priority": "high"}, external_id="a"),
                  evidence("p1", {"priority": "high"}, external_id="a")]
        originals = (old.model_dump(mode="json"), new.model_dump(mode="json"), [e.model_dump() for e in proofs])
        first = compare_task_snapshots(old, new, current_evidence=proofs + proofs)
        second = compare_task_snapshots(old.model_copy(update={"tasks": list(reversed(old.tasks))}),
            new.model_copy(update={"tasks": list(reversed(new.tasks))}), current_evidence=list(reversed(proofs)))
        self.assertEqual(first, second)
        self.assertEqual(len({c.id for c in first.changes}), 2)
        self.assertEqual(first.changes[0].current_evidence_ids, ["p1", "p2"])
        self.assertEqual(originals, (old.model_dump(mode="json"), new.model_dump(mode="json"), [e.model_dump() for e in proofs]))
        self.assertTrue(all(c.actor is None for c in first.changes))

    def test_explicit_start_date_clearing_and_unobserved_archive(self):
        old = card(start_date=A, is_archived=False)
        new = card(start_date=None, is_archived=True, observed_fields=["start_date"])
        changes = self.compare([old], [new]).changes
        self.assertEqual([(c.field, c.before, c.after) for c in changes], [("start_date", A, None)])

    def test_complete_membership_and_equal_capture_offsets_remain_observations(self):
        old = snap([card("gone")], tasks_complete=True, task_source_identity="scope")
        new = snap([card("new")], True, tasks_complete=True, task_source_identity="scope")
        new = new.model_copy(update={"captured_at": datetime.fromisoformat("2026-10-10T07:00:00-05:00")})
        result = compare_task_snapshots(old, new)
        self.assertEqual([c.kind for c in result.changes], ["not_observed", "first_observed"])
        self.assertEqual(result.warnings, [])
        self.assertEqual([c.before for c in result.changes], [True, False])
        self.assertEqual([c.after for c in result.changes], [False, True])

    def test_conflicting_evidence_is_unknown_not_order_dependent(self):
        old, new = TaskCard(id="c", title="Task"), TaskCard(id="c", title="Task")
        before = [evidence("a", {"priority": "normal"}), evidence("b", {"priority": "low"})]
        after = [evidence("c", {"priority": "high"})]
        result = self.compare([old], [new], previous_evidence=before, current_evidence=after)
        self.assertEqual(result.changes, [])
        self.assertIn("conflicting_task_evidence", result.warnings)
