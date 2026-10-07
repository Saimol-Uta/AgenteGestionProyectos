"""Unit tests for the CLI commands: run, decide, and status."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import io
import sys

from agente_gestion.cli import main
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.domain.report import HumanDecisionState
from agente_gestion.domain.run import RunStatus


class CLITestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "cli_test.db")
        self.runs_dir = os.path.join(self.temp_dir, "runs")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cli_fake_run_generates_report_and_summary(self):
        cmd = [
            "run",
            "fixture_1_standard",
            "--fake",
            "--fixture", "fixture_1_standard",
            "--allow-remote-ai",
            "--db-path", self.db_path,
            "--output-dir", self.runs_dir,
        ]

        stdout_capture = io.StringIO()
        with patch.object(sys, "argv", ["agente_gestion"] + cmd), patch("sys.stdout", stdout_capture):
            exit_code = main()

        output = stdout_capture.getvalue()
        self.assertEqual(exit_code, 0, f"CLI exited with {exit_code}. Output: {output}")

        # Summary must include: Run ID, status, agents, models, findings, escalations, verifications, report path, duration
        self.assertIn("Run ID", output)
        self.assertIn("status", output.lower())
        self.assertIn("findings", output.lower())
        self.assertIn("report", output.lower())

        # Verify SQLite has the report
        sqlite = SQLiteStorageAdapter(db_path=self.db_path)
        report = sqlite.get_latest_report("fixture_1_standard")
        self.assertIsNotNone(report)
        self.assertEqual(report.human_decision, HumanDecisionState.pending)

    def test_cli_human_decision_registration(self):
        # 1. Run first
        cmd_run = [
            "run",
            "fixture_1_standard",
            "--fake",
            "--fixture", "fixture_1_standard",
            "--db-path", self.db_path,
            "--output-dir", self.runs_dir,
        ]
        with patch.object(sys, "argv", ["agente_gestion"] + cmd_run), patch("sys.stdout", io.StringIO()):
            main()

        sqlite = SQLiteStorageAdapter(db_path=self.db_path)
        report = sqlite.get_latest_report("fixture_1_standard")
        self.assertIsNotNone(report)

        # Retrieve run_id
        runs = sqlite.get_all_runs_for_project("fixture_1_standard")
        self.assertTrue(len(runs) > 0)
        run_id = runs[0].run_id

        # 2. Register human decision
        cmd_decide = [
            "decide",
            run_id,
            "--decision", "approved",
            "--reason", "Aprobado por el gestor técnico en reunión de sprint",
            "--db-path", self.db_path,
        ]

        stdout_capture = io.StringIO()
        with patch.object(sys, "argv", ["agente_gestion"] + cmd_decide), patch("sys.stdout", stdout_capture):
            exit_code = main()

        output = stdout_capture.getvalue()
        self.assertEqual(exit_code, 0, f"Decide exited with {exit_code}. Output: {output}")
        self.assertIn("approved", output)

        # 3. Check updated report
        updated_report = sqlite.get_latest_report("fixture_1_standard")
        self.assertEqual(updated_report.human_decision, HumanDecisionState.approved)

    def test_cli_decide_rejects_invalid_decision_choice(self):
        cmd_invalid = [
            "decide",
            "nonexistent-run",
            "--decision", "invalid_status",
            "--db-path", self.db_path,
        ]
        with patch.object(sys, "argv", ["agente_gestion"] + cmd_invalid):
            with self.assertRaises(SystemExit):
                main()

    def test_cli_default_run_without_fixture_uses_default_fixture_and_preserves_project_id(self):
        # Default command without positional project or --fixture flag
        cmd = [
            "run",
            "--fake",
            "--db-path", self.db_path,
            "--output-dir", self.runs_dir,
        ]
        stdout_capture = io.StringIO()
        with patch.object(sys, "argv", ["agente_gestion"] + cmd), patch("sys.stdout", stdout_capture):
            exit_code = main()

        output = stdout_capture.getvalue()
        self.assertEqual(exit_code, 0, f"Default CLI run failed with {exit_code}. Output: {output}")

        # Check SQLite report preserved requested project ID 'proyecto-demo'
        sqlite = SQLiteStorageAdapter(db_path=self.db_path)
        report = sqlite.get_latest_report("proyecto-demo")
        self.assertIsNotNone(report, "Expected report for 'proyecto-demo' to exist in database")
        self.assertEqual(report.project_id, "proyecto-demo")

    def test_cli_explicit_fixture_flag_preserves_custom_project_id(self):
        cmd = [
            "run",
            "custom-project-x",
            "--fixture", "fixture_1_standard",
            "--fake",
            "--db-path", self.db_path,
            "--output-dir", self.runs_dir,
        ]
        stdout_capture = io.StringIO()
        with patch.object(sys, "argv", ["agente_gestion"] + cmd), patch("sys.stdout", stdout_capture):
            exit_code = main()

        output = stdout_capture.getvalue()
        self.assertEqual(exit_code, 0, f"CLI with --fixture failed with {exit_code}. Output: {output}")

        sqlite = SQLiteStorageAdapter(db_path=self.db_path)
        report = sqlite.get_latest_report("custom-project-x")
        self.assertIsNotNone(report)
        self.assertEqual(report.project_id, "custom-project-x")

    def test_cli_real_mode_default_disabled_verificador_is_unavailable_and_no_ollama_fallback(self):
        cmd = [
            "run",
            "fixture_1_standard",
            "--fixture", "fixture_1_standard",
            "--db-path", self.db_path,
            "--output-dir", self.runs_dir,
        ]

        stdout_capture = io.StringIO()
        with patch.object(sys, "argv", ["agente_gestion"] + cmd), \
             patch("sys.stdout", stdout_capture), \
             patch("agente_gestion.cli.ProjectOrchestrator") as mock_orch_cls:

            mock_run = MagicMock()
            mock_run.run_id = "run-test-123"
            mock_run.status.value = "needs_attention"
            mock_run.duration_seconds = 1.23
            mock_orch_instance = MagicMock()
            mock_orch_instance.execute.return_value = mock_run
            mock_orch_cls.return_value = mock_orch_instance

            exit_code = main()

        # Orchestrator should have received verificador_provider=None, NOT OllamaProvider!
        call_kwargs = mock_orch_cls.call_args[1]
        self.assertIsNone(
            call_kwargs.get("verificador_provider"),
            "Expected verificador_provider to be None when remote AI is disabled (no Ollama fallback allowed)",
        )

        output = stdout_capture.getvalue()
        self.assertIn("Verificador=unavailable", output)

    def test_cli_env_file_option_loads_and_cli_overrides_beat_it(self):
        env_custom_path = os.path.join(self.temp_dir, "custom.env")
        with open(env_custom_path, "w", encoding="utf-8") as f:
            f.write(
                "PROJECT_ID=env-project\n"
                "OLLAMA_BASE_URL=http://env-host:11435\n"
            )

        cmd = [
            "run",
            "cli-project-override",
            "--fake",
            "--fixture", "fixture_1_standard",
            "--env-file", env_custom_path,
            "--db-path", self.db_path,
            "--output-dir", self.runs_dir,
        ]

        stdout_capture = io.StringIO()
        with patch.object(sys, "argv", ["agente_gestion"] + cmd), patch("sys.stdout", stdout_capture):
            exit_code = main()

        self.assertEqual(exit_code, 0)
        sqlite = SQLiteStorageAdapter(db_path=self.db_path)
        # CLI override 'cli-project-override' beats env file 'env-project'
        report = sqlite.get_latest_report("cli-project-override")
        self.assertIsNotNone(report)
        self.assertEqual(report.project_id, "cli-project-override")

    def test_cli_gemini_secret_key_passed_unmasked_only_to_gemini_provider(self):
        cmd = [
            "run",
            "fixture_1_standard",
            "--fixture", "fixture_1_standard",
            "--allow-remote-ai",
            "--db-path", self.db_path,
            "--output-dir", self.runs_dir,
        ]

        with patch.dict(os.environ, {"GEMINI_API_KEY": "synthetic-cli-key", "ALLOW_REMOTE_AI": "true"}, clear=True), \
             patch.object(sys, "argv", ["agente_gestion"] + cmd), \
             patch("sys.stdout", io.StringIO()), \
             patch("agente_gestion.cli.GeminiProvider") as mock_gemini_cls, \
             patch("agente_gestion.cli.ProjectOrchestrator") as mock_orch_cls:

            mock_run = MagicMock()
            mock_run.run_id = "run-test-gemini"
            mock_run.status = RunStatus.completed
            mock_run.duration_seconds = 1.0
            mock_orch_instance = MagicMock()
            mock_orch_instance.execute.return_value = mock_run
            mock_orch_cls.return_value = mock_orch_instance

            mock_gemini_instance = MagicMock()
            mock_gemini_instance.model = "gemini-2.5-flash"
            mock_gemini_cls.return_value = mock_gemini_instance

            exit_code = main()

        self.assertEqual(exit_code, 0)
        # Verify GeminiProvider was instantiated with the string key extracted from SecretStr
        mock_gemini_cls.assert_called_once()
        self.assertEqual(mock_gemini_cls.call_args[1]["api_key"], "synthetic-cli-key")
        self.assertTrue(mock_gemini_cls.call_args[1]["allow_remote_ai"])


if __name__ == "__main__":
    unittest.main()

