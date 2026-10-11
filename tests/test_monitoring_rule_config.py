"""Offline owner constraints: strict local JSON, separate settings, run-only CLI."""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from agente_gestion import cli
from agente_gestion.application.config import Settings
from agente_gestion.domain.report import DomainProposalRuleSet
from agente_gestion.domain.run import RunStatus
from agente_gestion.adapters.sources.local_monitoring_rules import (
    MonitoringRulesError, load_monitoring_rules,
)


def rule_data():
    return {
        "project_id": "owner-project",
        "rules": [{"rule_id": "owner-1", "task_ids": ["task-1"], "field": "due_date",
                   "allowed_values": ["2026-07-01T12:00:00+00:00", None],
                   "rationale": "Owner requested review", "expected_impact": "Review deadline"}],
    }


class MonitoringRuleConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "owner.json"
        self.path.write_text(json.dumps(rule_data()), encoding="utf-8")

    def test_loader_returns_canonical_typed_rules(self):
        rules = load_monitoring_rules(self.path, "owner-project")
        self.assertIsInstance(rules, DomainProposalRuleSet)
        self.assertEqual(rules, DomainProposalRuleSet.model_validate(rule_data()))
        self.assertIsNone(rules.rules[0].allowed_values[1])
        for field, values in (("start_date", [None, "2026-07-01T12:00:00"]),
                              ("priority", [None, "high"]),
                              ("assignee_ids", [None, [], ["member-1"]]),
                              ("state", ["completada"])):
            with self.subTest(field=field):
                data = rule_data()
                data["rules"][0].update(field=field, allowed_values=values)
                self.path.write_text(json.dumps(data), encoding="utf-8")
                self.assertEqual(load_monitoring_rules(self.path, "owner-project"),
                                 DomainProposalRuleSet.model_validate(data))

    def test_loader_rejects_invalid_or_trust_claiming_data(self):
        variants = [None, [], {"project_id": "foreign", "rules": []}]
        for key, value in (("project_id", ""), ("project_id", True),
                           ("authenticated", True), ("_actor", "private")):
            data = rule_data()
            data[key] = value
            variants.append(data)
        for key, value in (("field", "labels"), ("field", True),
                           ("task_ids", []), ("task_ids", [True]),
                           ("rationale", " "), ("expected_impact", ""),
                           ("allowed_values", []), ("allowed_values", [True]),
                           ("allowed_values", [123]), ("allowed_values", ["2026-07-01"]),
                           ("allowed_values", [{"actor": "private"}]),
                           ("authorized", True), ("_actor", "private")):
            data = rule_data()
            data["rules"][0][key] = value
            variants.append(data)
        for field, values in (("state", ["desconocido"]), ("state", [None]),
                              ("assignee_ids", [[True]]), ("priority", [False])):
            data = rule_data()
            data["rules"][0].update(field=field, allowed_values=values)
            variants.append(data)
        for data in variants:
            with self.subTest(data=data):
                self.path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(MonitoringRulesError) as caught:
                    load_monitoring_rules(self.path, "owner-project")
                self.assertEqual(str(caught.exception), "Monitoring rules input invalid.")

    def test_file_failures_are_static_and_bounded(self):
        for content in (b"{private-invalid", b"\xff", b" " * (1024 * 1024 + 1)):
            self.path.write_bytes(content)
            with self.assertRaises(MonitoringRulesError):
                load_monitoring_rules(self.path, "owner-project")
        for path in (self.path.parent / "missing-private", self.path.parent):
            with self.assertRaises(MonitoringRulesError):
                load_monitoring_rules(path, "owner-project")
        with patch("builtins.open", side_effect=PermissionError("private")):
            with self.assertRaises(MonitoringRulesError) as caught:
                load_monitoring_rules(self.path, "owner-project")
        self.assertEqual(str(caught.exception), "Monitoring rules input invalid.")

    def test_settings_default_and_yaml_env_override_precedence(self):
        config = self.path.parent / "config.yaml"
        config.write_text(f"monitoring_rules_path: {self.path}\n", encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(Settings(_env_file=None).monitoring_rules_path)
            self.assertEqual(Settings(_env_file=None, _yaml_file=str(config)).monitoring_rules_path,
                             str(self.path))
            with patch.dict(os.environ, {"MONITORING_RULES_PATH": "environment.json"}):
                self.assertEqual(Settings(_env_file=None, _yaml_file=str(config)).monitoring_rules_path,
                                 "environment.json")
                settings = Settings.from_env_and_file(
                    str(config), {"monitoring_rules_path": "explicit.json"}, _env_file=None)
                self.assertEqual(settings.monitoring_rules_path, "explicit.json")
                self.assertNotEqual(settings.policies_path, settings.monitoring_rules_path)

    def run_args(self, *extra):
        return cli.build_parser().parse_args(["run", "owner-project", "--fake", *extra])

    def test_cli_passes_exact_typed_object_and_flag_overrides_settings(self):
        settings = Settings(_env_file=None, monitoring_rules_path="missing.json", is_fake=True)
        rules = load_monitoring_rules(self.path, "owner-project")
        orchestrator, storage = MagicMock(), MagicMock()
        orchestrator.execute.return_value.status = RunStatus.completed
        orchestrator.execute.return_value.duration_seconds = 0.0
        storage.get_findings.return_value = []
        storage.get_verifications.return_value = []
        def load(path, project):
            self.assertEqual(path, str(self.path))
            self.assertEqual(project, "owner-project")
            return rules
        with patch.object(cli.Settings, "from_env_and_file", return_value=settings) as config_load, \
                patch.object(cli, "load_monitoring_rules", side_effect=load), \
                patch.object(cli, "_build_orchestrator", return_value=(
                    orchestrator, storage, None, None, None, None, None)), \
                patch("sys.stdout", io.StringIO()):
            self.assertEqual(cli.run_command(self.run_args("--monitoring-rules", str(self.path))), 0)
        self.assertEqual(config_load.call_args.kwargs["overrides"]["monitoring_rules_path"], str(self.path))
        self.assertIs(orchestrator.execute.call_args.kwargs["proposal_rules"], rules)

    def test_cli_invalid_settings_rule_file_precedes_all_composition(self):
        for content in (None, "{private", json.dumps({"project_id": "foreign", "rules": []})):
            if content is not None:
                self.path.write_text(content, encoding="utf-8")
            rule_path = self.path if content is not None else self.path.parent / "missing-private"
            settings = Settings(_env_file=None, monitoring_rules_path=str(rule_path))
            for extra in ((), ("--monitoring-rules", str(rule_path))):
                stderr = io.StringIO()
                with patch.object(cli.Settings, "from_env_and_file", return_value=settings), \
                        patch.object(cli, "_build_orchestrator") as build, patch("sys.stderr", stderr):
                    self.assertEqual(cli.run_command(self.run_args(*extra)), 2)
                build.assert_not_called()
                self.assertEqual(stderr.getvalue(), "ERROR: Monitoring rules input invalid.\n")
        with patch.object(cli.Settings, "from_env_and_file", side_effect=ValueError("private")), \
                patch.object(cli, "_build_orchestrator") as build, \
                patch("sys.stderr", io.StringIO()) as stderr:
            self.assertEqual(cli.run_command(self.run_args()), 2)
        build.assert_not_called()
        self.assertEqual(stderr.getvalue(), "ERROR: Configuration validation failed.\n")

    def test_real_fake_pipeline_keeps_sparse_fixture_inactive_and_legacy_cache(self):
        from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter

        settings = Settings(_env_file=None, is_fake=True,
                            db_path=str(self.path.parent / "run.db"),
                            runs_dir=str(self.path.parent / "runs"))
        args = self.run_args()
        with patch.object(cli.Settings, "from_env_and_file", return_value=settings), \
                patch("sys.stdout", io.StringIO()):
            self.assertEqual(cli.run_command(args), 0)
            self.assertEqual(cli.run_command(args), 0)
        storage = SQLiteStorageAdapter(settings.db_path)
        runs = storage.get_all_runs_for_project("owner-project")
        self.assertEqual(len(runs), 1)
        baseline = runs[0]
        self.assertIn("missing_rules", json.loads(baseline.stages["proposals"].output_summary)["limitations"])
        self.assertIsNotNone(storage.get_latest_report("owner-project"))
        settings.monitoring_rules_path = str(self.path)
        with patch.object(cli.Settings, "from_env_and_file", return_value=settings), \
                patch("sys.stdout", io.StringIO()):
            self.assertEqual(cli.run_command(args), 0)
        runs = storage.get_all_runs_for_project("owner-project")
        self.assertEqual(len(runs), 2)
        self.assertEqual(len({run.config_hash for run in runs}), 2)
        self.assertEqual(storage.list_action_proposals("owner-project"), [])
        for run in runs:
            self.assertEqual(json.loads(run.stages["proposals"].output_summary)["saved"], 0)

    def test_cli_without_input_preserves_legacy_call_and_read_only_flags(self):
        settings = Settings(_env_file=None, is_fake=True)
        orchestrator, storage = MagicMock(), MagicMock()
        orchestrator.execute.return_value.status = RunStatus.completed
        orchestrator.execute.return_value.duration_seconds = 0.0
        storage.get_findings.return_value = []
        with patch.object(cli.Settings, "from_env_and_file", return_value=settings), \
                patch.object(cli, "load_monitoring_rules") as load, \
                patch.object(cli, "_build_orchestrator", return_value=(
                    orchestrator, storage, None, None, None, None, None)), \
                patch("sys.stdout", io.StringIO()):
            self.assertEqual(cli.run_command(self.run_args()), 0)
        load.assert_not_called()
        orchestrator.execute.assert_called_once_with(project_id="owner-project", fixture_name=None)
        for command in ("history", "proposals"):
            with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as caught:
                cli.build_parser().parse_args([
                    command, "list", "owner-project", "--db-path", "unused",
                    "--monitoring-rules", str(self.path)])
            self.assertEqual(caught.exception.code, 2)
