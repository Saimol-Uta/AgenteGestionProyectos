"""Coverage for the verified attention reasons exposed with every run.

The projection only reads persisted records, so the tests write those records
directly and assert what the API then reports. The cases mirror the states a
manager can meet: a clean run, a run that needs attention, a failed run, a
stage that never wrote a record and a capability that was not configured.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.web_fixtures import PROJECT_ID, build_web_api_fixture


PIPELINE_STAGES = (
    "source_collection", "monitoring", "proposals", "quality", "verifier", "report",
)


class RunAttentionReasonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = build_web_api_fixture(Path(self.temp.name))
        self.client = self.fixture.client

    # -- helpers ---------------------------------------------------------

    def _write_run(self, run_id: str, status: str, error_message: str | None = None) -> None:
        connection = sqlite3.connect(self.fixture.database_path)
        with connection:
            connection.execute(
                "INSERT OR REPLACE INTO runs (run_id, project_id, status, created_at, updated_at, "
                "snapshot_id, config_hash, snapshot_hash, policy_version, duration_seconds, "
                "error_message, is_simulated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    run_id, PROJECT_ID, status,
                    "2026-10-19T19:17:43.884152-05:00", "2026-10-19T19:18:22.529126-05:00",
                    "snap-attention", "cfg", "hash", "v1", 12.5, error_message,
                ),
            )
        connection.close()

    def _write_stage(
        self, run_id: str, stage_name: str, status: str,
        output_summary: str | None = None, error_message: str | None = None,
    ) -> None:
        connection = sqlite3.connect(self.fixture.database_path)
        with connection:
            connection.execute(
                "INSERT OR REPLACE INTO stage_results (run_id, stage_name, status, duration_seconds, "
                "output_summary, error_message, data_hash, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id, stage_name, status, 1.0, output_summary, error_message, "hash",
                    "2026-10-19T19:18:22.529126-05:00",
                ),
            )
        connection.close()

    def _write_pipeline(self, run_id: str, status: str = "completed") -> None:
        for stage in PIPELINE_STAGES:
            self._write_stage(run_id, stage, "completed", f"{stage} completed.")

    def _notices(self, run_id: str) -> list[dict]:
        response = self.client.get(f"/api/v1/runs/{run_id}")
        self.assertEqual(response.status_code, 200)
        return response.json()["attention"]

    # -- cases -----------------------------------------------------------

    def test_completed_run_without_problems_reports_no_attention_reason(self):
        run_id = "run-attention-clean"
        self._write_run(run_id, "completed")
        self._write_pipeline(run_id)

        self.assertEqual(self._notices(run_id), [])

    def test_repository_reason_replaces_the_aggregate_stage_summary(self):
        run_id = "run-attention-repository"
        self._write_run(run_id, "needs_attention")
        self._write_pipeline(run_id)
        self._write_stage(
            run_id, "quality", "needs_attention",
            "Repository stage outcomes: backend:needs_attention, frontend:completed",
        )
        self._write_stage(
            run_id, "quality:backend", "needs_attention",
            "Repository source or model output was incomplete; retained only identity-matched findings.",
        )

        notices = self._notices(run_id)
        repository = [item for item in notices if item["scope"] == "repository"]
        self.assertEqual(len(repository), 1)
        self.assertEqual(repository[0]["reason_code"], "repository_needs_attention")
        self.assertEqual(repository[0]["kind"], "attention")
        self.assertEqual(repository[0]["stage_name"], "quality")
        self.assertEqual(repository[0]["repository_id"], "backend")
        self.assertEqual(
            repository[0]["message"],
            "Repository source or model output was incomplete; retained only identity-matched findings.",
        )
        # The aggregate stage summary is not repeated: the specific reason wins.
        self.assertNotIn("quality", [item.get("stage_name") for item in notices if item["scope"] == "stage"])

    def test_failed_run_reports_the_persisted_reason_attributed_to_its_stage(self):
        run_id = "run-attention-failed"
        reason = "Monitoring stage failed: Provider failed in stage 'monitoring'."
        self._write_run(run_id, "failed", reason)
        self._write_stage(run_id, "monitoring", "failed", None, reason)

        notices = self._notices(run_id)
        failures = [item for item in notices if item["kind"] == "failure"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["reason_code"], "run_failed")
        self.assertEqual(failures[0]["scope"], "run")
        self.assertEqual(failures[0]["stage_name"], "monitoring")
        self.assertEqual(failures[0]["message"], reason)

    def test_stage_without_record_is_reported_as_unknown_information(self):
        run_id = "run-attention-unknown"
        self._write_run(run_id, "needs_attention")
        for stage in PIPELINE_STAGES:
            if stage != "verifier":
                self._write_stage(run_id, stage, "completed", f"{stage} completed.")

        notices = self._notices(run_id)
        unknown = [item for item in notices if item["kind"] == "unknown"]
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0]["reason_code"], "stage_not_recorded")
        self.assertEqual(unknown[0]["stage_name"], "verifier")
        self.assertIn("desconoce", unknown[0]["message"])

    def test_missing_proposal_rules_are_reported_as_a_capability_limitation(self):
        run_id = "run-attention-limitation"
        self._write_run(run_id, "needs_attention")
        self._write_pipeline(run_id)
        self._write_stage(
            run_id, "proposals", "skipped",
            '{"failures": [], "limitations": ["missing_rules"], "offered": 0, "saved": 0}',
        )

        notices = self._notices(run_id)
        limitations = [item for item in notices if item["kind"] == "limitation"]
        self.assertEqual(len(limitations), 1)
        self.assertEqual(limitations[0]["reason_code"], "proposal_rules_not_configured")
        self.assertEqual(limitations[0]["stage_name"], "proposals")
        self.assertIn("no están configuradas", limitations[0]["message"])

    def test_run_list_carries_the_same_verified_reasons(self):
        run_id = "run-attention-list"
        self._write_run(run_id, "needs_attention")
        self._write_pipeline(run_id)
        self._write_stage(run_id, "quality", "needs_attention", "Quality could not conclude.")
        self._write_stage(run_id, "quality:backend", "needs_attention", "Model output was incomplete.")

        response = self.client.get("/api/v1/runs", params={"project_id": PROJECT_ID})
        self.assertEqual(response.status_code, 200)
        listed = {item["run_id"]: item for item in response.json()["items"]}
        self.assertIn(run_id, listed)
        reasons = [item["reason_code"] for item in listed[run_id]["attention"]]
        self.assertIn("repository_needs_attention", reasons)

    def test_attention_reasons_do_not_leak_between_projects(self):
        run_id = "run-attention-other-project"
        connection = sqlite3.connect(self.fixture.database_path)
        with connection:
            connection.execute(
                "INSERT OR REPLACE INTO runs (run_id, project_id, status, created_at, updated_at, "
                "snapshot_id, config_hash, snapshot_hash, policy_version, duration_seconds, "
                "error_message, is_simulated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    run_id, "STORED-ONLY", "needs_attention",
                    "2026-10-19T19:17:43.884152-05:00", "2026-10-19T19:18:22.529126-05:00",
                    "snap-other", "cfg", "hash", "v1", 1.0, None,
                ),
            )
        connection.close()
        self._write_stage(run_id, "quality", "needs_attention", "Quality could not conclude.")

        response = self.client.get("/api/v1/runs", params={"project_id": PROJECT_ID})
        listed = {item["run_id"] for item in response.json()["items"]}
        self.assertNotIn(run_id, listed)


if __name__ == "__main__":
    unittest.main()
