"""Offline weekly planning; fixture choices are not production defaults."""

import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from agente_gestion.domain.weekly_schedule import WeeklySchedule, plan_weekly_window


LOCAL = ZoneInfo("America/Guayaquil")
EXPLICIT = dict(project_id="owner-project", weekday=4, local_time="17:30",
                timezone="America/Guayaquil", window_rule="previous_scheduled_week")


class WeeklyScheduleTests(unittest.TestCase):
    def schedule(self, **changes):
        return WeeklySchedule(**(EXPLICIT | changes))

    def test_required_strict_configuration(self):
        for field in EXPLICIT:
            with self.subTest(missing=field), self.assertRaises(ValueError):
                WeeklySchedule(**{k: v for k, v in EXPLICIT.items() if k != field})
        invalid = {
            "project_id": ["", " \t", 4, None],
            "weekday": [True, "4", 4.0, -1, 7, None],
            "local_time": ["7:30", "24:00", "17:60", "17:30:00", "17:30-05:00", " 17:30", 1730],
            "timezone": ["UTC", "Unknown/Zone", None],
            "window_rule": ["calendar_week", None],
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.schedule(**{field: value})
        for field in ("executed", "notified", "job_claimed", "unknown"):
            with self.subTest(extra=field), self.assertRaises(ValueError):
                self.schedule(**{field: True})

    def test_unsafe_models_revalidated_without_mutation(self):
        now = datetime(2026, 1, 2, 17, 30, tzinfo=LOCAL)
        unsafe = [WeeklySchedule.model_construct(**(EXPLICIT | {"weekday": True})),
                  WeeklySchedule.model_construct(**(EXPLICIT | {"executed": True})),
                  WeeklySchedule.model_construct(project_id="owner-project"),
                  self.schedule().model_copy(update={"timezone": "UTC"}),
                  self.schedule().model_copy(update={"local_time": "25:00"}),
                  self.schedule().model_copy(update={"window_rule": "unknown"}),
                  self.schedule().model_copy(update={"notified": True})]
        mutated = self.schedule()
        object.__setattr__(mutated, "weekday", "4")
        unsafe.append(mutated)
        collision = self.schedule()
        object.__setattr__(collision, "__pydantic_extra__", {"weekday": 4})
        unsafe.append(collision)
        for schedule in unsafe:
            before = dict(schedule.__dict__)
            with self.subTest(raw=before), self.assertRaisesRegex(ValueError, "^Invalid weekly schedule$"):
                plan_weekly_window(schedule, now)
            self.assertEqual(schedule.__dict__, before)
        for schedule in (EXPLICIT, None):
            with self.assertRaisesRegex(ValueError, "^Invalid weekly schedule$"):
                plan_weekly_window(schedule, now)

    def test_evaluation_requires_actual_aware_datetime(self):
        for now in (datetime(2026, 1, 2), "2026-01-02T17:30:00-05:00", 0, True, None):
            with self.subTest(now=now), self.assertRaisesRegex(ValueError, "^Invalid evaluation time$"):
                plan_weekly_window(self.schedule(), now)
        for now in (datetime.min.replace(tzinfo=LOCAL), datetime.max.replace(tzinfo=LOCAL)):
            with self.assertRaisesRegex(ValueError, "^Invalid evaluation time$"):
                plan_weekly_window(self.schedule(), now)

    def test_before_exact_after_explicit_friday(self):
        boundary = datetime(2026, 1, 2, 17, 30, tzinfo=LOCAL)
        for delta, elapsed in ((-1, boundary - timedelta(days=7)), (0, boundary), (1, boundary)):
            with self.subTest(seconds=delta):
                plan = plan_weekly_window(self.schedule(), boundary + timedelta(seconds=delta))
                self.assertEqual(plan.elapsed_boundary_local, elapsed)
                self.assertEqual(plan.next_boundary_local, elapsed + timedelta(days=7))
                self.assertEqual(plan.window_start_local, elapsed - timedelta(days=7))
                self.assertEqual(plan.window_end_local, elapsed)
                self.assertEqual(plan.cutoff_local, elapsed)
                self.assertEqual(plan.window_end_local - plan.window_start_local, timedelta(days=7))

    def test_monday_weekend_year_and_leap_rollovers(self):
        cases = [
            (0, "2026-01-05T17:29:00", "2025-12-29T17:30:00"),
            (5, "2026-01-04T12:00:00", "2026-01-03T17:30:00"),
            (6, "2026-01-04T17:30:00", "2026-01-04T17:30:00"),
            (4, "2024-03-01T17:29:00", "2024-02-23T17:30:00"),
            (3, "2024-02-29T17:30:00", "2024-02-29T17:30:00"),
        ]
        for day, evaluation, expected in cases:
            with self.subTest(day=day, evaluation=evaluation):
                plan = plan_weekly_window(self.schedule(weekday=day), datetime.fromisoformat(evaluation).replace(tzinfo=LOCAL))
                self.assertEqual(plan.elapsed_boundary_local, datetime.fromisoformat(expected).replace(tzinfo=LOCAL))
                self.assertEqual(plan.next_boundary_local - plan.elapsed_boundary_local, timedelta(days=7))

    def test_utc_equivalence_local_date_and_serialization(self):
        schedule = self.schedule(local_time="23:30")
        local = datetime(2026, 1, 2, 23, 30, tzinfo=LOCAL)
        plan = plan_weekly_window(schedule, local)
        self.assertEqual(plan, plan_weekly_window(schedule, local.astimezone(timezone.utc)))
        self.assertEqual(plan.elapsed_boundary_local.date().isoformat(), "2026-01-02")
        self.assertEqual(plan.elapsed_boundary_utc.date().isoformat(), "2026-01-03")
        serialized = plan.model_dump(mode="json")
        for boundary in ("elapsed_boundary", "next_boundary", "window_start", "window_end", "cutoff"):
            local_value = getattr(plan, boundary + "_local")
            utc_value = getattr(plan, boundary + "_utc")
            self.assertEqual(local_value.tzinfo.key, "America/Guayaquil")
            self.assertEqual(utc_value.utcoffset(), timedelta(0))
            self.assertEqual(local_value, utc_value)
            self.assertTrue(serialized[boundary + "_local"].endswith("-05:00"))
            self.assertTrue(serialized[boundary + "_utc"].endswith("Z"))

    def test_identity_planning_only_and_immutability(self):
        schedule = self.schedule()
        before = schedule.model_dump()
        now = datetime(2026, 1, 2, 17, 30, tzinfo=LOCAL)
        plan = plan_weekly_window(schedule, now)
        self.assertEqual(plan, plan_weekly_window(schedule, now + timedelta(hours=1)))
        payload = {"schedule": before, "window_start_utc": plan.window_start_utc.isoformat(),
                   "window_end_utc": plan.window_end_utc.isoformat()}
        expected = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
        self.assertEqual(plan.window_id, expected)
        for change in ({"project_id": "other-project"}, {"weekday": 3}, {"local_time": "17:29"}):
            self.assertNotEqual(plan.window_id, plan_weekly_window(self.schedule(**change), now).window_id)
        self.assertNotEqual(plan.window_id, plan_weekly_window(schedule, now + timedelta(days=7)).window_id)
        with self.assertRaises(ValueError):
            self.schedule(window_rule="other-policy")
        self.assertEqual(schedule.model_dump(), before)
        for model, field, value in ((schedule, "weekday", 0), (plan, "window_id", "changed"), (plan, "planning_only", False)):
            with self.assertRaises((ValueError, AttributeError)):
                setattr(model, field, value)
        self.assertTrue(plan.planning_only)
        self.assertTrue(type(plan).model_construct(planning_only=False).planning_only)
        self.assertFalse({"executed", "notified", "job_claimed"} & plan.model_dump().keys())


if __name__ == "__main__":
    unittest.main()
