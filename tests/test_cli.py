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
from agente_gestion.application.config import Settings
from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.adapters.llm.gemini import GeminiProvider
from agente_gestion.adapters.llm.groq import GroqProvider
from agente_gestion.domain.report import HumanDecisionState
from agente_gestion.domain.run import RunStatus


class CLITestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "cli_test.db")
        self.runs_dir = os.path.join(self.temp_dir, "runs")

        # CLI tests use explicit synthetic dotenvs only; never read the real project .env.
        original_loader = Settings.from_env_and_file.__func__
        def isolated_loader(cls, *args, **kwargs):
            if kwargs.get("env_file") is None and "_env_file" not in kwargs:
                kwargs["_env_file"] = None
            return original_loader(cls, *args, **kwargs)
        env_guard = patch.object(Settings, "from_env_and_file", classmethod(isolated_loader))
        env_guard.start()
        self.addCleanup(env_guard.stop)

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

    def test_cli_remote_disabled_uses_deterministic_verifier_without_remote_constructors(self):
        cmd = [
            "run",
            "fixture_1_standard",
            "--fixture", "fixture_1_standard",
            "--db-path", self.db_path,
            "--output-dir", self.runs_dir,
        ]

        stdout_capture = io.StringIO()
        orig_loader = Settings.from_env_and_file
        with patch.dict(os.environ, {"ALLOW_REMOTE_AI": "false"}, clear=True), \
             patch(
                 "agente_gestion.cli.Settings.from_env_and_file",
                 side_effect=lambda *a, **kw: orig_loader(*a, **{**kw, "_env_file": None}),
             ), \
             patch.object(sys, "argv", ["agente_gestion"] + cmd), \
             patch("sys.stdout", stdout_capture), \
             patch.object(GeminiProvider, "__init__", autospec=True,
                          side_effect=AssertionError("Gemini constructor must stay idle")) as gemini_constructor, \
             patch.object(GroqProvider, "__init__", autospec=True,
                          side_effect=AssertionError("Groq constructor must stay idle")) as groq_constructor, \
             patch("agente_gestion.cli.ProjectOrchestrator") as mock_orch_cls:

            mock_run = MagicMock()
            mock_run.run_id = "run-test-123"
            mock_run.status = RunStatus.awaiting_human
            mock_run.duration_seconds = 1.23
            mock_orch_instance = MagicMock()
            mock_orch_instance.execute.return_value = mock_run
            mock_orch_cls.return_value = mock_orch_instance

            exit_code = main()

        self.assertEqual(exit_code, 0)
        verifier = mock_orch_cls.call_args[1].get("verificador_provider")
        self.assertIsInstance(verifier, FakeProvider)
        self.assertTrue(verifier.is_simulated)
        self.assertEqual(verifier.model, "sandbox-verifier")
        gemini_constructor.assert_not_called()
        groq_constructor.assert_not_called()

        output = stdout_capture.getvalue()
        self.assertIn("Verificador=sandbox-verifier", output)

    def test_cli_remote_disabled_scenario_immune_to_ambient_host_dotenv_and_os_flags(self):
        """A host dotenv cannot override explicit remote-off policy or construct remote providers."""
        from agente_gestion.application.config import Settings
        orig_loader = Settings.from_env_and_file
        host_env_path = os.path.join(self.temp_dir, "host.env")
        with open(host_env_path, "w", encoding="utf-8") as f:
            f.write(
                "ALLOW_REMOTE_AI=true\n"
                "GEMINI_API_KEY=synthetic-host-key\n"
            )

        cmd = [
            "run",
            "fixture_1_standard",
            "--fixture", "fixture_1_standard",
            "--db-path", self.db_path,
            "--output-dir", self.runs_dir,
        ]

        # Case 1: Scoped Settings factory using _env_file=None ignores ambient host dotenv
        with patch.dict(os.environ, {"ALLOW_REMOTE_AI": "false"}, clear=True), \
             patch(
                 "agente_gestion.cli.Settings.from_env_and_file",
                 side_effect=lambda *a, **kw: orig_loader(*a, **{**kw, "_env_file": None}),
             ), \
             patch.object(sys, "argv", ["agente_gestion"] + cmd), \
             patch("sys.stdout", io.StringIO()), \
             patch("agente_gestion.cli.ProjectOrchestrator") as mock_orch_cls:

            mock_run = MagicMock(run_id="run-test", duration_seconds=1.0)
            mock_run.status.value = "needs_attention"
            mock_orch_cls.return_value.execute.return_value = mock_run

            main()
            call_kwargs = mock_orch_cls.call_args[1]
            self.assertIsInstance(call_kwargs.get("verificador_provider"), FakeProvider)

        # Case 2: OS environment ALLOW_REMOTE_AI=false strictly beats host dotenv ALLOW_REMOTE_AI=true
        cmd_with_host_env = cmd + ["--env-file", host_env_path]
        with patch.dict(os.environ, {"ALLOW_REMOTE_AI": "false"}, clear=True), \
             patch.object(sys, "argv", ["agente_gestion"] + cmd_with_host_env), \
             patch("sys.stdout", io.StringIO()), \
             patch("agente_gestion.cli.ProjectOrchestrator") as mock_orch_cls_2:

            mock_run = MagicMock(run_id="run-test-2", duration_seconds=1.0)
            mock_run.status.value = "needs_attention"
            mock_orch_cls_2.return_value.execute.return_value = mock_run

            main()
            call_kwargs_2 = mock_orch_cls_2.call_args[1]
            self.assertIsInstance(call_kwargs_2.get("verificador_provider"), FakeProvider)

    def test_cli_wires_verifier_timeout_from_config_to_orchestrator(self):
        """CLI must wire verifier_timeout_seconds from Settings into OrchestratorConfig."""
        from agente_gestion.application.config import Settings
        orig_loader = Settings.from_env_and_file
        cmd = [
            "run",
            "fixture_1_standard",
            "--fake",
            "--fixture", "fixture_1_standard",
            "--db-path", self.db_path,
            "--output-dir", self.runs_dir,
        ]

        with patch.dict(os.environ, {"VERIFIER_TIMEOUT_SECONDS": "150.0"}, clear=True), \
             patch(
                 "agente_gestion.cli.Settings.from_env_and_file",
                 side_effect=lambda *a, **kw: orig_loader(*a, **{**kw, "_env_file": None}),
             ), \
             patch.object(sys, "argv", ["agente_gestion"] + cmd), \
             patch("sys.stdout", io.StringIO()), \
             patch("agente_gestion.cli.ProjectOrchestrator") as mock_orch_cls:

            mock_run = MagicMock(run_id="run-wires-timeout", duration_seconds=1.0)
            mock_run.status.value = "awaiting_human"
            mock_orch_cls.return_value.execute.return_value = mock_run

            main()
            call_kwargs = mock_orch_cls.call_args[1]
            orch_config = call_kwargs.get("config")
            self.assertIsNotNone(orch_config)
            self.assertEqual(orch_config.verifier_timeout_seconds, 150.0)
            self.assertEqual(orch_config.quality_timeout_seconds, 120.0)

    def test_cli_env_file_option_loads_and_cli_overrides_beat_it(self):
        env_custom_path = os.path.join(self.temp_dir, "custom.env")
        with open(env_custom_path, "w", encoding="utf-8") as f:
            f.write(
                "PROJECT_ID=env-project\n"
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
             patch("agente_gestion.cli.ProjectOrchestrator") as mock_orch_cls:

            mock_run = MagicMock()
            mock_run.run_id = "run-test-gemini"
            mock_run.status = RunStatus.completed
            mock_run.duration_seconds = 1.0
            mock_orch_instance = MagicMock()
            mock_orch_instance.execute.return_value = mock_run
            mock_orch_cls.return_value = mock_orch_instance

            exit_code = main()
            wired = mock_orch_cls.call_args.kwargs

        self.assertEqual(exit_code, 0)
        # Gemini owns monitoring, verification, and reporting; Groq is local without its key.
        gemini_roles = [wired[name] for name in
                        ("elaborador_provider", "verificador_provider", "aprobador_provider")]
        self.assertTrue(all(isinstance(provider, GeminiProvider) for provider in gemini_roles))
        self.assertTrue(all(provider.api_key == "synthetic-cli-key" for provider in gemini_roles))
        self.assertTrue(all(provider.allow_remote_ai for provider in gemini_roles))
        self.assertIsInstance(wired["revisor_provider"], FakeProvider)


if __name__ == "__main__":
    unittest.main()

