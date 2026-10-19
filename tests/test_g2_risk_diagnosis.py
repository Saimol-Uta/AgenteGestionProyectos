"""G2 coverage: deterministic task risks and safe proposal-stage diagnostics."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from agente_gestion.application.tasks_query import build_task_plan_summary
from agente_gestion.domain.snapshot import WeeklySnapshot
from agente_gestion.domain.tasks import DEFAULT_TIMEZONE, TaskCard, TaskState
from tests.web_fixtures import PROJECT_ID, TASK_ID, build_web_api_fixture

NARRATIVE_RISK = "Riesgo potencial de sincronizacion"
DETERMINISTIC_KINDS = {"blocked", "overdue", "due_soon", "contradictory_state", "unknown_state"}


def _task(card_id: str, state: TaskState, cutoff: datetime, *, due_in_days: int | None = None,
          included: bool = True, contradiction: bool = False) -> TaskCard:
    due = cutoff + timedelta(days=due_in_days) if due_in_days is not None else None
    return TaskCard(
        id=card_id,
        title=f"Tarea {card_id}",
        state=state,
        list_name="Fase",
        is_included=included,
        due_complete=False,
        due_date=due,
        has_contradiction=contradiction,
    )


class DeterministicRiskModelTests(unittest.TestCase):
    def setUp(self):
        self.cutoff = datetime(2026, 10, 12, 12, 0, tzinfo=DEFAULT_TIMEZONE)
        self.snapshot = WeeklySnapshot(
            id="snap-g2",
            project_id="PROJ-G2",
            cutoff_datetime=self.cutoff,
            captured_at=self.cutoff,
            tasks=[
                _task("task-blocked", TaskState.blocked, self.cutoff, due_in_days=-1),
                _task("task-due-soon", TaskState.in_progress, self.cutoff, due_in_days=2),
                _task("task-clean", TaskState.pending, self.cutoff, due_in_days=30),
                _task("task-contradictory", TaskState.unknown, self.cutoff, included=False, contradiction=True),
            ],
        )
        self.summary = build_task_plan_summary(self.snapshot, capture_id="cap-g2")

    def test_risks_are_structured_and_preserve_existing_strings(self):
        self.assertEqual(
            [risk.kind.value for risk in self.summary.risks],
            ["blocked", "overdue", "due_soon", "contradictory_state", "unknown_state"],
        )
        self.assertEqual(self.summary.identified_risks, [
            "1 tarea(s) bloqueada(s) activa(s).",
            "1 tarea(s) vencida(s) no completada(s).",
            "1 tarea(s) próxima(s) a vencer en la ventana de 7 días.",
            "1 tarea(s) con señales contradictorias de estado.",
            "1 tarea(s) con estado desconocido (falta mapeo o evidencia).",
        ])
        by_kind = {risk.kind.value: risk for risk in self.summary.risks}
        self.assertEqual(by_kind["blocked"].task_ids, ["task-blocked"])
        self.assertEqual(by_kind["overdue"].task_ids, ["task-blocked"])
        self.assertEqual(by_kind["due_soon"].task_ids, ["task-due-soon"])
        self.assertEqual(by_kind["contradictory_state"].task_ids, ["task-contradictory"])
        self.assertEqual(by_kind["unknown_state"].task_ids, ["task-contradictory"])
        for risk in self.summary.risks:
            self.assertEqual(risk.project_id, "PROJ-G2")
            self.assertEqual(risk.capture_id, "cap-g2")
            self.assertEqual(risk.source, "deterministic")
            self.assertEqual(risk.task_count, len(risk.task_ids))
            self.assertTrue(risk.summary)
            self.assertTrue(risk.basis)
            self.assertTrue(risk.risk_id.startswith("risk-"))

    def test_risk_ids_are_stable_and_capture_scoped(self):
        again = build_task_plan_summary(self.snapshot, capture_id="cap-g2")
        self.assertEqual([risk.risk_id for risk in self.summary.risks],
                         [risk.risk_id for risk in again.risks])
        identifiers = [risk.risk_id for risk in self.summary.risks]
        self.assertEqual(len(set(identifiers)), len(identifiers))
        other_capture = build_task_plan_summary(self.snapshot, capture_id="cap-other")
        self.assertTrue(set(identifiers).isdisjoint({risk.risk_id for risk in other_capture.risks}))

    def test_clean_snapshot_reports_no_risks(self):
        clean = WeeklySnapshot(
            id="snap-clean",
            project_id="PROJ-G2",
            cutoff_datetime=self.cutoff,
            captured_at=self.cutoff,
            tasks=[_task("task-clean", TaskState.pending, self.cutoff, due_in_days=30)],
        )
        summary = build_task_plan_summary(clean, capture_id="cap-clean")
        self.assertEqual(summary.risks, [])
        self.assertEqual(summary.identified_risks, [])
        self.assertEqual(summary.overdue_count, 0)
        self.assertEqual(summary.due_soon_count, 0)
        self.assertEqual(summary.blocked_count, 0)
        self.assertEqual(summary.unknown_count, 0)
        self.assertEqual(summary.contradictory_count, 0)

    def test_partial_metrics_produce_only_observed_risks(self):
        snapshot = WeeklySnapshot(
            id="snap-partial",
            project_id="PROJ-G2",
            cutoff_datetime=self.cutoff,
            captured_at=self.cutoff,
            tasks=[_task("task-due", TaskState.pending, self.cutoff, due_in_days=3)],
        )
        summary = build_task_plan_summary(snapshot, capture_id="cap-partial")
        self.assertEqual([risk.kind.value for risk in summary.risks], ["due_soon"])
        self.assertEqual(summary.risks[0].task_ids, ["task-due"])
        self.assertEqual(len(summary.identified_risks), 1)

    def test_risk_ids_are_unique_across_captures_of_the_same_project(self):
        older = build_task_plan_summary(self.snapshot, capture_id="cap-older")
        current = build_task_plan_summary(self.snapshot, capture_id="cap-current")
        kinds = {risk.kind.value: risk for risk in older.risks}
        current_kinds = {risk.kind.value: risk for risk in current.risks}
        self.assertEqual(set(kinds), set(current_kinds))
        for kind, risk in kinds.items():
            self.assertNotEqual(risk.risk_id, current_kinds[kind].risk_id)


class RiskApiContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = build_web_api_fixture(Path(self.temp.name))
        self.client = self.fixture.client

    def _summary(self, **params) -> dict:
        response = self.client.get(f"/api/v1/projects/{PROJECT_ID}/summary", params=params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_summary_exposes_deterministic_risks_for_the_selected_capture(self):
        body = self._summary()
        self.assertEqual(body["capture_id"], self.fixture.current_capture_id)
        self.assertTrue(body["risks"])
        self.assertTrue({risk["kind"] for risk in body["risks"]} <= DETERMINISTIC_KINDS)
        for risk in body["risks"]:
            self.assertEqual(risk["project_id"], PROJECT_ID)
            self.assertEqual(risk["capture_id"], self.fixture.current_capture_id)
            self.assertEqual(risk["source"], "deterministic")
            self.assertEqual(risk["task_count"], len(risk["task_ids"]))
            self.assertTrue(risk["basis"])
        due_soon = next(risk for risk in body["risks"] if risk["kind"] == "due_soon")
        self.assertEqual(due_soon["task_ids"], [TASK_ID])
        self.assertEqual(body["due_soon_count"], 1)
        self.assertEqual(body["due_soon_task_ids"], [TASK_ID])
        self.assertEqual(len(body["identified_risks"]), 1)

    def test_task_detail_binds_risk_classification_and_capture(self):
        response = self.client.get(f"/api/v1/projects/{PROJECT_ID}/tasks/{TASK_ID}")
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertTrue(detail["risks"])
        for risk in detail["risks"]:
            self.assertIn(TASK_ID, risk["task_ids"])
            self.assertEqual(risk["capture_id"], self.fixture.current_capture_id)
            self.assertEqual(risk["project_id"], PROJECT_ID)
            self.assertTrue(risk["basis"])
        self.assertTrue(detail["evidence"])

    def test_task_detail_without_risks_declares_absence(self):
        from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter

        clean = WeeklySnapshot(
            id="snap-clean-api",
            project_id=PROJECT_ID,
            cutoff_datetime=datetime(2026, 1, 22, 18, 0, tzinfo=DEFAULT_TIMEZONE),
            captured_at=datetime(2026, 1, 22, 18, 0, tzinfo=DEFAULT_TIMEZONE),
            tasks=[TaskCard(
                id=TASK_ID,
                title="Tarea sin riesgo determinista",
                state=TaskState.pending,
                list_name="En curso",
                due_complete=False,
                due_date=datetime(2026, 2, 20, 18, 0, tzinfo=DEFAULT_TIMEZONE),
            )],
        )
        capture = SQLiteStorageAdapter(self.fixture.database_path).save_snapshot_with_capture_receipt(clean)
        self.assertIsNotNone(capture)
        response = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/tasks/{TASK_ID}",
            params={"capture_id": capture.capture_id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["risks"], [])
        summary = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/summary",
            params={"capture_id": capture.capture_id},
        ).json()
        self.assertEqual(summary["risks"], [])
        self.assertEqual(summary["identified_risks"], [])

    def test_report_risk_task_ids_match_deterministic_risks(self):
        body = self._summary()
        report = self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/reports/{self.fixture.report_id}",
        ).json()
        expected = sorted({task_id for risk in body["risks"] for task_id in risk["task_ids"]})
        self.assertEqual(sorted(report["risk_task_ids"]), expected)

    def test_narrative_text_never_becomes_a_risk(self):
        body = self._summary()
        serialized = json.dumps(body["risks"], ensure_ascii=False)
        self.assertNotIn(NARRATIVE_RISK, serialized)
        self.assertNotIn(NARRATIVE_RISK, json.dumps(body["identified_risks"], ensure_ascii=False))
        detail = self.client.get(f"/api/v1/projects/{PROJECT_ID}/tasks/{TASK_ID}").json()
        self.assertNotIn(NARRATIVE_RISK, json.dumps(detail["risks"], ensure_ascii=False))

    def test_unknown_project_capture_and_task_do_not_leak_risks(self):
        self.assertEqual(self.client.get("/api/v1/projects/PROYECTO-INEXISTENTE/summary").status_code, 404)
        self.assertEqual(self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/summary", params={"capture_id": "captura-ajena"},
        ).status_code, 404)
        self.assertEqual(self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/tasks/tarea-inexistente",
        ).status_code, 404)
        self.assertEqual(self.client.get(
            f"/api/v1/projects/{PROJECT_ID}/tasks/{TASK_ID}", params={"capture_id": "captura-ajena"},
        ).status_code, 404)


class ProposalStageDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = build_web_api_fixture(Path(self.temp.name))
        self.client = self.fixture.client
        self.run_id = self.fixture.current_run_id

    def _write_summary(self, summary: str) -> None:
        with closing(sqlite3.connect(self.fixture.database_path)) as connection:
            connection.execute(
                "UPDATE stage_results SET output_summary = ? WHERE run_id = ? AND stage_name = 'proposals'",
                (summary, self.run_id),
            )
            connection.commit()

    def _delete_stage(self) -> None:
        with closing(sqlite3.connect(self.fixture.database_path)) as connection:
            connection.execute(
                "DELETE FROM stage_results WHERE run_id = ? AND stage_name = 'proposals'",
                (self.run_id,),
            )
            connection.commit()

    def _stages(self) -> dict:
        response = self.client.get(f"/api/v1/runs/{self.run_id}/stages")
        self.assertEqual(response.status_code, 200)
        return {stage["stage_name"]: stage for stage in response.json()["items"]}

    def _diagnostic(self) -> dict:
        stage = self._stages()["proposals"]
        self.assertTrue(stage["recorded"])
        self.assertIsNotNone(stage["proposals"])
        return stage["proposals"]

    def test_proposals_stage_is_exposed_with_a_structured_diagnostic(self):
        stages = self._stages()
        self.assertIn("proposals", stages)
        stage = stages["proposals"]
        self.assertTrue(stage["recorded"])
        self.assertIn(stage["status"], {"completed", "skipped"})
        self.assertIsNone(stage["result_summary"])
        self.assertEqual(stage["proposals"]["outcome"], "not_configured")
        self.assertEqual(stage["proposals"]["limitation_codes"], ["missing_rules"])
        self.assertEqual(stage["proposals"]["offered"], 0)
        self.assertEqual(stage["proposals"]["selected"], 0)
        self.assertEqual(stage["proposals"]["saved"], 0)

    def test_no_recorded_stage_reports_absence_without_guessing(self):
        self._delete_stage()
        stage = self._stages()["proposals"]
        self.assertFalse(stage["recorded"])
        self.assertIsNone(stage["proposals"])

    def test_outcome_classification_covers_every_zero_proposal_case(self):
        cases = {
            "not_configured": {"offered": 0, "limitations": ["missing_rules"], "failures": []},
            "rules_invalid": {"offered": 0, "limitations": ["invalid_rules"], "failures": []},
            "no_candidates": {"offered": 0, "limitations": ["unknown_task"], "failures": []},
            "candidates_discarded": {
                "offered": 2, "selected": 1, "failures": [{"choice_index": 0, "codes": ["invalid_choice"]}],
            },
            "not_selected": {"offered": 2, "selected": 0, "failures": []},
            "saved": {"offered": 2, "selected": 1, "saved": 1, "failures": []},
            "unknown": {"selected": 0, "saved": 0, "failures": []},
        }
        for outcome, extra in cases.items():
            with self.subTest(outcome=outcome):
                payload = {"selected": 0, "saved": 0, "technical_ready": 0, "technical_blocked": 0,
                           "limitations": [], "failures": []}
                payload.update(extra)
                self._write_summary(json.dumps(payload, sort_keys=True))
                self.assertEqual(self._diagnostic()["outcome"], outcome)

    def test_rules_invalid_with_project_mismatch_is_not_configured(self):
        self._write_summary(json.dumps({
            "offered": 0, "selected": 0, "saved": 0, "technical_ready": 0, "technical_blocked": 0,
            "limitations": ["project_mismatch"], "failures": [],
        }, sort_keys=True))
        diagnostic = self._diagnostic()
        self.assertEqual(diagnostic["outcome"], "rules_invalid")
        self.assertEqual(diagnostic["limitation_codes"], ["project_mismatch"])

    def test_discarded_candidates_expose_reasons(self):
        self._write_summary(json.dumps({
            "offered": 3, "selected": 2, "saved": 0, "technical_ready": 0, "technical_blocked": 0,
            "limitations": ["missing_evidence"],
            "failures": [{"choice_index": 0, "codes": ["invalid_choice"]},
                         {"choice_index": 1, "codes": ["unknown_task"]}],
        }, sort_keys=True))
        diagnostic = self._diagnostic()
        self.assertEqual(diagnostic["outcome"], "candidates_discarded")
        self.assertEqual(diagnostic["failure_codes"], ["invalid_choice", "unknown_task"])
        self.assertEqual(diagnostic["discarded"], 2)
        self.assertEqual(diagnostic["limitation_codes"], ["missing_evidence"])

    def test_saved_proposals_are_not_reported_as_zero(self):
        self._write_summary(json.dumps({
            "offered": 2, "selected": 1, "saved": 1, "technical_ready": 1, "technical_blocked": 0,
            "limitations": [], "failures": [],
        }, sort_keys=True))
        diagnostic = self._diagnostic()
        self.assertEqual(diagnostic["outcome"], "saved")
        self.assertEqual(diagnostic["saved"], 1)
        self.assertEqual(diagnostic["technical_ready"], 1)

    def test_unknown_codes_and_raw_payloads_are_never_exposed(self):
        self._write_summary(json.dumps({
            "offered": 0, "selected": 0, "saved": 0, "technical_ready": 0, "technical_blocked": 0,
            "limitations": ["unknown_task", "private-token-123", "/home/gestor/privado"],
            "failures": [{"choice_index": 0, "codes": ["invalid_choice", "raw-secret"]}],
        }, sort_keys=True))
        diagnostic = self._diagnostic()
        self.assertEqual(diagnostic["limitation_codes"], ["unknown_task"])
        self.assertEqual(diagnostic["failure_codes"], ["invalid_choice"])
        serialized = json.dumps(diagnostic, ensure_ascii=False)
        self.assertNotIn("private-token-123", serialized)
        self.assertNotIn("/home/gestor/privado", serialized)
        self.assertNotIn("raw-secret", serialized)

    def test_malformed_and_partial_payloads_fail_safe(self):
        for raw in ("not-json", "[]", "", "{\"offered\": \"dos\"}", None):
            with self.subTest(raw=raw):
                self._write_summary(raw)
                diagnostic = self._diagnostic()
                self.assertEqual(diagnostic["outcome"], "unknown")
                self.assertEqual(diagnostic["limitation_codes"], [])
                self.assertEqual(diagnostic["failure_codes"], [])
                self.assertEqual(diagnostic["discarded"], 0)

    def test_unknown_run_reports_not_found(self):
        self.assertEqual(self.client.get("/api/v1/runs/run-inexistente/stages").status_code, 404)


if __name__ == "__main__":
    unittest.main()
