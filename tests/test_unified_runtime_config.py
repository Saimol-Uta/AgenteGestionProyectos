"""Unified project configuration, source aliases, and effective API credentials."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agente_gestion.api.analysis_jobs import load_analysis_plan
from agente_gestion.api.settings import load_api_settings
from agente_gestion.application import config as engine_config
from agente_gestion.application.config import Settings


class UnifiedRuntimeConfigTests(unittest.TestCase):
    def test_project_yaml_defaults_remote_gate_and_current_providers(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env_and_file(_env_file=None)
        self.assertTrue(settings.allow_remote_ai)
        self.assertEqual(settings.monitoring_provider, "gemini")
        self.assertEqual(settings.quality_provider, "mistral")
        self.assertEqual(settings.report_provider, "gemini")

    def test_yaml_layers_apply_base_then_project_then_explicit_before_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "config.yaml").write_text(
                "project:\n  id: base-project\n  allow_remote_ai: false\n"
                "execution:\n  due_soon_days: 3\n"
                "sources:\n  github:\n    owner: base-owner\n    repo: base-repo\n",
                encoding="utf-8",
            )
            (config_dir / "hormigestion.yaml").write_text(
                "project:\n  id: HORMIGESTION\n  allow_remote_ai: true\n"
                "execution:\n  due_soon_days: 7\n"
                "sources:\n  github:\n    owner: project-owner\n    repo: project-repo\n",
                encoding="utf-8",
            )
            explicit = root / "override.yaml"
            explicit.write_text(
                "sources:\n  github:\n    repo: explicit-repo\n",
                encoding="utf-8",
            )
            with patch.object(engine_config, "PROJECT_ROOT", root), patch.dict(
                os.environ, {"DUE_SOON_DAYS": "9"}, clear=True,
            ):
                settings = Settings.from_env_and_file(config_path=str(explicit), _env_file=None)
        self.assertEqual(settings.project_id, "HORMIGESTION")
        self.assertTrue(settings.allow_remote_ai)
        self.assertEqual(settings.due_soon_days, 9)
        self.assertEqual(settings.github_owner, "project-owner")
        self.assertEqual(settings.github_repo, "explicit-repo")

    def test_github_full_repo_aliases_and_same_layer_conflicts(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            engine_config, "PROJECT_ROOT", Path(temporary),
        ):
            with patch.dict(os.environ, {
                "GITHUB_REPOSITORY": "canonical-owner/canonical-repo",
            }, clear=True):
                canonical = Settings.from_env_and_file(_env_file=None)
            self.assertEqual(canonical.github_owner, "canonical-owner")
            self.assertEqual(canonical.github_repo, "canonical-repo")
            self.assertEqual(canonical.github_repository, "canonical-repo")

            with patch.dict(os.environ, {"GITHUB_REPO": "owner/full-repository"}, clear=True):
                full = Settings.from_env_and_file(_env_file=None)
            self.assertEqual(full.github_owner, "owner")
            self.assertEqual(full.github_repo, "full-repository")

            with patch.dict(os.environ, {
                "GITHUB_OWNER": "legacy-owner", "GITHUB_REPO": "legacy-repository",
            }, clear=True):
                split = Settings.from_env_and_file(_env_file=None)
            self.assertEqual(split.github_owner, "legacy-owner")
            self.assertEqual(split.github_repo, "legacy-repository")

            with patch.dict(os.environ, {
                "GITHUB_REPO": "legacy/repository", "GITHUB_REPOSITORY": "canonical/repository",
            }, clear=True), self.assertRaises(ValueError):
                Settings.from_env_and_file(_env_file=None)

    def test_explicit_project_scope_does_not_inherit_default_project_bindings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "config.yaml").write_text(
                "project:\n  id: base-project\n  allow_remote_ai: true\n"
                "external_writes_enabled: true\n",
                encoding="utf-8",
            )
            (config_dir / "hormigestion.yaml").write_text(
                "project:\n  id: HORMIGESTION\n  name: HormiGestion\n"
                "sources:\n  mode: real\n  trello:\n    board_id: pinned-board\n"
                "  github:\n    owner: pinned-owner\n    repo: pinned-repository\n"
                "    commit_sha: " + "a" * 40 + "\n",
                encoding="utf-8",
            )
            explicit = root / "client.yaml"
            explicit.write_text(
                "project:\n  id: CLIENT-PROJECT\n  name: Client Project\n"
                "sources:\n  mode: fixtures\n",
                encoding="utf-8",
            )
            with patch.object(engine_config, "PROJECT_ROOT", root), patch(
                "agente_gestion.api.settings.PROJECT_ROOT", root,
            ), patch.dict(os.environ, {"AGENTE_CONFIG_PATH": str(explicit)}, clear=True):
                engine = Settings.from_env_and_file(config_path=str(explicit), _env_file=None)
                api = load_api_settings(env_file=None)

        self.assertEqual(engine.project_id, "CLIENT-PROJECT")
        self.assertEqual(engine.source_mode, "fixtures")
        self.assertIsNone(engine.trello_board_id)
        self.assertIsNone(engine.github_owner)
        self.assertIsNone(engine.github_repo)
        self.assertIsNone(engine.github_commit_sha)
        self.assertEqual(api.projects[0].id, "CLIENT-PROJECT")
        self.assertEqual(api.trello_board_ids, {})
        self.assertIsNone(api.github_repository)

    def test_api_normalizes_either_github_repository_environment_alias(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(engine_config, "PROJECT_ROOT", root), patch(
                "agente_gestion.api.settings.PROJECT_ROOT", root,
            ):
                for alias in ("GITHUB_REPO", "GITHUB_REPOSITORY"):
                    with self.subTest(alias=alias), patch.dict(
                        os.environ, {alias: "owner/repository"}, clear=True,
                    ):
                        settings = load_api_settings(env_file=None)
                        self.assertEqual(settings.github_repository, "owner/repository")

                with patch.dict(os.environ, {
                    "GITHUB_REPO": "owner/legacy", "GITHUB_REPOSITORY": "owner/canonical",
                }, clear=True), self.assertRaises(ValueError):
                    load_api_settings(env_file=None)

    def test_legacy_owner_with_full_repository_alias_preserves_singleton_api_and_cli(self):
        import io
        import sys
        from contextlib import redirect_stderr, redirect_stdout

        from agente_gestion.cli import main as cli_main

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_dir = root / "config"
            config_dir.mkdir()
            config_path = config_dir / "hormigestion.yaml"
            config_path.write_text(
                "project:\n  id: probe-project\n"
                "sources:\n  mode: fixtures\n",
                encoding="utf-8",
            )
            environment = {
                "GITHUB_OWNER": "legacy-owner",
                "GITHUB_REPOSITORY": "encoded-owner/repository",
                "EXTERNAL_WRITES_ENABLED": "false",
            }
            with patch.object(engine_config, "PROJECT_ROOT", root), patch(
                "agente_gestion.api.settings.PROJECT_ROOT", root,
            ), patch.dict(os.environ, environment, clear=True):
                engine = Settings.from_env_and_file(_env_file=None)
                api_settings = load_api_settings(env_file=None)
                stdout, stderr = io.StringIO(), io.StringIO()
                arguments = [
                    "agente_gestion", "source-check", "github", "--sources", "fixtures",
                    "--project", "fixture_1_standard", "--config", str(config_path), "--env-file", "/dev/null",
                ]
                with patch.object(sys, "argv", arguments), redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = cli_main()

        self.assertEqual(engine.github_owner, "encoded-owner")
        self.assertEqual(engine.github_repo, "repository")
        self.assertIsNone(engine.github_repositories)
        self.assertEqual(api_settings.projects[0].id, "probe-project")
        self.assertEqual(api_settings.github_repository, "encoded-owner/repository")
        self.assertEqual(exit_code, 0)
        self.assertRegex(stdout.getvalue(), r"(?m)^github\s+success\s")
        self.assertEqual(stderr.getvalue(), "")

    def test_api_and_analysis_share_dotenv_credentials_and_auto_bind_single_board(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "config.yaml").write_text(
                "project:\n  id: base\n  allow_remote_ai: true\n"
                "providers:\n  quality:\n    provider: groq\n",
                encoding="utf-8",
            )
            (config_dir / "hormigestion.yaml").write_text(
                "project:\n  id: HORMIGESTION\n  name: HormiGestion\n"
                "sources:\n  mode: real\n  trello:\n    board_id: board-from-yaml\n"
                "  github:\n    owner: yaml-owner\n    repo: yaml-repository\n",
                encoding="utf-8",
            )
            (root / ".env").write_text(
                "GROQ_API_KEY=synthetic-dotenv-groq\n"
                "TRELLO_API_KEY=synthetic-dotenv-trello-key\n"
                "TRELLO_TOKEN=synthetic-dotenv-trello-token\n"
                "GITHUB_TOKEN=synthetic-dotenv-github\n",
                encoding="utf-8",
            )
            with patch.object(engine_config, "PROJECT_ROOT", root), patch(
                "agente_gestion.api.settings.PROJECT_ROOT", root,
            ), patch.dict(os.environ, {}, clear=True):
                api_settings = load_api_settings()
                plan = load_analysis_plan(api_settings, "HORMIGESTION")

        self.assertEqual(api_settings.projects[0].id, "HORMIGESTION")
        self.assertTrue(api_settings.external_writes_enabled)
        self.assertEqual(api_settings.trello_board_ids, {"HORMIGESTION": "board-from-yaml"})
        self.assertEqual(api_settings.github_repository, "yaml-owner/yaml-repository")
        self.assertIsNotNone(plan.engine_settings)
        self.assertEqual(plan.engine_settings.groq_api_key.get_secret_value(), "synthetic-dotenv-groq")
        self.assertEqual(plan.engine_settings.trello_api_key.get_secret_value(), "synthetic-dotenv-trello-key")
        self.assertEqual(plan.engine_settings.github_token.get_secret_value(), "synthetic-dotenv-github")


if __name__ == "__main__":
    unittest.main()
