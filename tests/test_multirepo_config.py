"""Public configuration contracts for additive MultiRepo repository settings."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agente_gestion.api.settings import ApiSettings, load_api_settings
from agente_gestion.application import config as engine_config
from agente_gestion.application.config import Settings


class MultiRepoSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config_dir = self.root / "config"
        self.config_dir.mkdir()
        self.yaml_path = self.config_dir / "project.yaml"
        self.root_patch = patch.object(engine_config, "PROJECT_ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.api_root_patch = patch("agente_gestion.api.settings.PROJECT_ROOT", self.root)
        self.api_root_patch.start()
        self.addCleanup(self.api_root_patch.stop)

    @staticmethod
    def repository(
        repository_id="backend",
        owner="Josu-F1",
        repo="hormigestion-backend",
        branch="develop",
        commit_sha=None,
        quality_workflow_path=".github/workflows/ci.yml",
    ):
        value = {
            "id": repository_id,
            "owner": owner,
            "repo": repo,
            "branch": branch,
            "quality_workflow_path": quality_workflow_path,
        }
        if commit_sha is not None:
            value["commit_sha"] = commit_sha
        return value

    def write_yaml(self, repositories, *, legacy=None, project_id="MULTIREPO"):
        legacy = legacy or {}
        github_fields = "".join(f"    {key}: {value}\n" for key, value in legacy.items())
        repositories_yaml = "\n".join(
            "    - id: {id}\n"
            "      owner: {owner}\n"
            "      repo: {repo}\n"
            "      branch: {branch}\n"
            "      quality_workflow_path: {quality_workflow_path}\n".format(**item)
            + (f"      commit_sha: {item['commit_sha']}\n" if item.get("commit_sha") else "")
            for item in repositories
        )
        self.yaml_path.write_text(
            f"project:\n  id: {project_id}\n"
            "sources:\n  github:\n"
            f"{github_fields}"
            "    repositories:\n"
            f"{repositories_yaml}",
            encoding="utf-8",
        )
        return self.yaml_path

    def load_engine(self, path=None):
        return Settings.from_env_and_file(
            config_path=str(path or self.yaml_path),
            _env_file=None,
        )

    def test_legacy_single_repository_configuration_still_loads(self):
        with patch.dict(os.environ, {
            "GITHUB_OWNER": "legacy-owner",
            "GITHUB_REPO": "legacy-repository",
            "GITHUB_BRANCH": "release/1.x",
            "GITHUB_COMMIT_SHA": "a" * 40,
        }, clear=True):
            settings = self.load_engine()

        self.assertEqual(settings.github_owner, "legacy-owner")
        self.assertEqual(settings.github_repo, "legacy-repository")
        self.assertEqual(settings.github_branch, "release/1.x")
        self.assertEqual(settings.github_commit_sha, "a" * 40)
        self.assertIsNone(settings.github_repositories)

    def test_yaml_collection_and_hormigestion_config_have_two_unpinned_repositories(self):
        repositories = [
            self.repository(),
            self.repository(
                repository_id="frontend",
                repo="hormigestion-frontend",
                quality_workflow_path=".github/workflows/ci-quality.yml",
            ),
        ]
        self.write_yaml(repositories)
        with patch.dict(os.environ, {}, clear=True):
            settings = self.load_engine()

        self.assertEqual([item.id for item in settings.github_repositories], ["backend", "frontend"])
        self.assertEqual([item.branch for item in settings.github_repositories], ["develop", "develop"])
        self.assertEqual(
            [item.quality_workflow_path for item in settings.github_repositories],
            [".github/workflows/ci.yml", ".github/workflows/ci-quality.yml"],
        )
        self.assertTrue(all(item.commit_sha is None for item in settings.github_repositories))

        repository_root = Path(__file__).resolve().parents[1]
        with patch.object(engine_config, "PROJECT_ROOT", repository_root), patch.dict(os.environ, {}, clear=True):
            project_settings = Settings.from_env_and_file(_env_file=None)
        self.assertEqual([item.id for item in project_settings.github_repositories], ["backend", "frontend"])
        self.assertIsNone(project_settings.github_repositories[0].commit_sha)

    def test_each_repository_keeps_its_own_explicit_sha_in_engine_and_api_settings(self):
        backend_sha = "a" * 40
        frontend_sha = "b" * 40
        self.write_yaml([
            self.repository(commit_sha=backend_sha),
            self.repository(
                repository_id="frontend",
                repo="hormigestion-frontend",
                commit_sha=frontend_sha,
                quality_workflow_path=".github/workflows/ci-quality.yml",
            ),
        ])

        with patch.dict(os.environ, {
            "AGENTE_CONFIG_PATH": str(self.yaml_path),
            "EXTERNAL_WRITES_ENABLED": "false",
        }, clear=True):
            engine = self.load_engine()
            api_settings = load_api_settings(env_file=None)

        self.assertEqual(
            [(repository.id, repository.commit_sha) for repository in engine.github_repositories],
            [("backend", backend_sha), ("frontend", frontend_sha)],
        )
        self.assertEqual(
            [(repository.id, repository.commit_sha) for repository in api_settings.projects[0].repositories],
            [("backend", backend_sha), ("frontend", frontend_sha)],
        )

    def test_duplicate_and_invalid_repository_ids_are_rejected(self):
        duplicate = [self.repository(), self.repository(repo="another-backend")]
        invalid = [self.repository(repository_id="../backend")]
        for repositories in (duplicate, invalid):
            with self.subTest(repositories=repositories), patch.dict(os.environ, {}, clear=True):
                self.write_yaml(repositories)
                with self.assertRaises(ValueError):
                    self.load_engine()

    def test_repository_owner_name_branch_sha_and_workflow_are_validated(self):
        invalid_entries = [
            self.repository(owner="bad/owner"),
            self.repository(repo="../repo"),
            self.repository(branch="  "),
            self.repository(commit_sha="short"),
            self.repository(quality_workflow_path="../../outside.yml"),
        ]
        for entry in invalid_entries:
            with self.subTest(entry=entry), patch.dict(os.environ, {}, clear=True):
                self.write_yaml([entry])
                with self.assertRaises(ValueError):
                    self.load_engine()

    def test_legacy_fields_must_match_one_entry_without_overriding_it(self):
        sha = "b" * 40
        entries = [self.repository(commit_sha=sha)]
        self.write_yaml(entries)
        with patch.dict(os.environ, {
            "GITHUB_OWNER": "Josu-F1",
            "GITHUB_REPO": "hormigestion-backend",
            "GITHUB_BRANCH": "develop",
            "GITHUB_COMMIT_SHA": sha,
        }, clear=True):
            settings = self.load_engine()
        self.assertEqual(settings.github_repositories[0].branch, "develop")
        self.assertEqual(settings.github_repositories[0].commit_sha, sha)

        contradictions = [
            {"GITHUB_BRANCH": "main"},
            {"GITHUB_COMMIT_SHA": "c" * 40},
            {"GITHUB_OWNER": "different-owner", "GITHUB_REPO": "hormigestion-backend"},
            {"GITHUB_OWNER": "Josu-F1", "GITHUB_REPO": "not-configured"},
        ]
        for legacy in contradictions:
            with self.subTest(legacy=legacy), patch.dict(os.environ, legacy, clear=True):
                with self.assertRaisesRegex(ValueError, "(?i)(legacy|contradict|match|repository)"):
                    self.load_engine()

    def test_legacy_sha_cannot_be_distributed_and_ambiguous_match_is_rejected(self):
        self.write_yaml([
            self.repository(),
            self.repository(repository_id="frontend", repo="hormigestion-frontend"),
        ])
        for legacy in (
            {"GITHUB_COMMIT_SHA": "d" * 40},
            {"GITHUB_BRANCH": "develop"},
            {"GITHUB_OWNER": "Josu-F1"},
        ):
            with self.subTest(legacy=legacy), patch.dict(os.environ, legacy, clear=True):
                with self.assertRaises(ValueError):
                    self.load_engine()

    def test_malformed_or_empty_json_collection_is_rejected(self):
        for raw in ("not-json", "[]", '[{"id":"bad"}]'):
            with self.subTest(raw=raw), patch.dict(os.environ, {"GITHUB_REPOSITORIES": raw}, clear=True):
                with self.assertRaises(ValueError):
                    self.load_engine()

    def test_json_environment_collection_precedes_yaml_and_api_exposes_all_entries(self):
        self.write_yaml([self.repository()])
        environment_repositories = [
            self.repository(repository_id="service-a", repo="service-a"),
            self.repository(
                repository_id="service-b",
                repo="service-b",
                quality_workflow_path=".github/workflows/ci-quality.yml",
            ),
        ]
        with patch.dict(os.environ, {
            "AGENTE_CONFIG_PATH": str(self.yaml_path),
            "GITHUB_REPOSITORIES": json.dumps(environment_repositories),
            "EXTERNAL_WRITES_ENABLED": "false",
        }, clear=True):
            engine = self.load_engine()
            api_settings = load_api_settings(env_file=None)

        self.assertEqual([item.id for item in engine.github_repositories], ["service-a", "service-b"])
        self.assertEqual([item.id for item in api_settings.projects[0].repositories], ["service-a", "service-b"])
        self.assertIsNone(api_settings.github_repository)
        serialized = api_settings.model_dump(mode="json")
        loaded_api = ApiSettings.model_validate(serialized)
        self.assertEqual(loaded_api.projects[0].repositories, api_settings.projects[0].repositories)
        engine_serialized = json.loads(engine.model_dump_json())
        self.assertEqual([item["id"] for item in engine_serialized["github_repositories"]], ["service-a", "service-b"])

    def test_explicit_collection_override_remains_highest_precedence(self):
        self.write_yaml([self.repository(repository_id="yaml", repo="yaml-repo")])
        environment_repositories = [self.repository(repository_id="environment", repo="environment-repo")]
        override_repositories = [self.repository(repository_id="explicit", repo="explicit-repo")]
        with patch.dict(os.environ, {
            "GITHUB_REPOSITORIES": json.dumps(environment_repositories),
        }, clear=True):
            settings = Settings.from_env_and_file(
                config_path=str(self.yaml_path),
                overrides={"github_repositories": override_repositories},
                _env_file=None,
            )
        self.assertEqual([item.id for item in settings.github_repositories], ["explicit"])

    def test_api_loader_rejects_invalid_collection_instead_of_silently_dropping_it(self):
        self.write_yaml([self.repository()])
        with patch.dict(os.environ, {
            "AGENTE_CONFIG_PATH": str(self.yaml_path),
            "GITHUB_REPOSITORIES": "not-json",
            "EXTERNAL_WRITES_ENABLED": "false",
        }, clear=True):
            with self.assertRaises(ValueError):
                load_api_settings(env_file=None)

    def test_api_loader_rejects_non_list_yaml_collection_instead_of_dropping_project(self):
        self.yaml_path.write_text(
            "project:\n  id: probe-project\n"
            "sources:\n  github:\n    repositories:\n      invalid: collection\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {
            "AGENTE_CONFIG_PATH": str(self.yaml_path),
            "EXTERNAL_WRITES_ENABLED": "false",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "(?i)(repository|github|configuration)"):
                load_api_settings(env_file=None)

    def test_collection_still_rejects_owner_conflicting_with_full_repository_alias(self):
        self.write_yaml([self.repository()])
        with patch.dict(os.environ, {
            "GITHUB_OWNER": "legacy-owner",
            "GITHUB_REPOSITORY": "Josu-F1/hormigestion-backend",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "(?i)(owner|contradict|legacy)"):
                self.load_engine()

    def test_collection_budget_yaml_environment_and_singleton_compatibility(self):
        self.write_yaml([self.repository()])
        content = self.yaml_path.read_text(encoding="utf-8")
        self.yaml_path.write_text(
            content.replace(
                "    repositories:\n",
                "    collection_budget:\n"
                "      project_max_http_calls: 9\n"
                "      repository_max_estimated_tokens: 1234\n"
                "    repositories:\n",
            ),
            encoding="utf-8",
        )
        with patch.dict(os.environ, {}, clear=True):
            yaml_settings = self.load_engine()
        self.assertEqual(yaml_settings.multirepo_project_max_http_calls, 9)
        self.assertEqual(yaml_settings.multirepo_repository_max_estimated_tokens, 1234)
        self.assertEqual(yaml_settings.multirepo_repository_max_http_calls, 48)

        with patch.dict(os.environ, {
            "AGENTE_CONFIG_PATH": str(self.yaml_path),
            "MULTIREPO_REPOSITORY_MAX_HTTP_CALLS": "7",
        }, clear=True):
            env_settings = Settings.from_env_and_file(_env_file=None)
        self.assertEqual(env_settings.multirepo_repository_max_http_calls, 7)

        singleton = Settings(github_owner="legacy-owner", github_repo="legacy-repo")
        self.assertNotIn("multirepo_project_max_http_calls", singleton.model_dump())
        self.assertEqual(singleton.multirepo_project_max_http_calls, 128)

    def test_collection_budgets_reject_zero_boolean_and_nonfinite_values(self):
        for values in (
            {"multirepo_project_max_http_calls": 0},
            {"multirepo_repository_max_files": True},
            {"multirepo_project_timeout_seconds": float("inf")},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Settings(**values)

    def test_configured_collection_coexistence_must_identify_exactly_one_entry(self):
        self.write_yaml([
            self.repository(),
            self.repository(repository_id="frontend", repo="hormigestion-frontend"),
        ])
        with patch.dict(os.environ, {
            "GITHUB_OWNER": "Josu-F1",
            "GITHUB_REPO": "hormigestion-backend",
            "GITHUB_BRANCH": "develop",
        }, clear=True):
            settings = self.load_engine()
        self.assertEqual([item.id for item in settings.github_repositories], ["backend", "frontend"])


if __name__ == "__main__":
    unittest.main()
