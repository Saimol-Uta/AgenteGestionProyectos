"""Tests for centralized Settings, dotenv loading, source precedence, and security."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from pydantic import SecretStr
from agente_gestion.application.config import Settings, SystemConfig


class ConfigSettingsTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.env_file = os.path.join(self.temp_dir, ".env")
        self.yaml_file = os.path.join(self.temp_dir, "config.yaml")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_env(self, content: str) -> str:
        with open(self.env_file, "w", encoding="utf-8") as f:
            f.write(content)
        return self.env_file

    def _write_yaml(self, content: str) -> str:
        with open(self.yaml_file, "w", encoding="utf-8") as f:
            f.write(content)
        return self.yaml_file

    def test_alias_system_config_is_settings(self):
        """SystemConfig should be an alias to Settings, not a separate implementation."""
        self.assertIs(SystemConfig, Settings)

    def test_dotenv_loading(self):
        """Values in .env are correctly loaded into Settings."""
        self._write_env(
            "OLLAMA_BASE_URL=http://localhost:11435\n"
            "ALLOW_REMOTE_AI=true\n"
            "GEMINI_MODEL=gemini-2.5-pro\n"
            "QUALITY_TIMEOUT_SECONDS=45.0\n"
            "GEMINI_API_KEY=synthetic-test-key-123\n"
        )
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=self.env_file)
            self.assertEqual(settings.ollama_base_url, "http://localhost:11435")
            self.assertTrue(settings.allow_remote_ai)
            self.assertEqual(settings.gemini_model, "gemini-2.5-pro")
            self.assertEqual(settings.quality_timeout_seconds, 45.0)
            self.assertIsNotNone(settings.gemini_api_key)
            self.assertEqual(settings.gemini_api_key.get_secret_value(), "synthetic-test-key-123")

    def test_os_beats_dotenv_for_all_required_vars_including_false(self):
        """OS environment variables must override dotenv values for all required variables,

        including strict boolean false.
        """
        self._write_env(
            "OLLAMA_BASE_URL=http://dotenv-host:11435\n"
            "ALLOW_REMOTE_AI=true\n"
            "GEMINI_MODEL=dotenv-gemini\n"
            "QUALITY_TIMEOUT_SECONDS=99.0\n"
            "GEMINI_API_KEY=dotenv-key\n"
        )
        os_env = {
            "OLLAMA_BASE_URL": "http://os-host:11435",
            "ALLOW_REMOTE_AI": "false",
            "GEMINI_MODEL": "os-gemini",
            "QUALITY_TIMEOUT_SECONDS": "30.0",
            "GEMINI_API_KEY": "os-key",
        }
        with patch.dict(os.environ, os_env, clear=True):
            settings = Settings(_env_file=self.env_file)
            self.assertEqual(settings.ollama_base_url, "http://os-host:11435")
            self.assertFalse(settings.allow_remote_ai)
            self.assertEqual(settings.gemini_model, "os-gemini")
            self.assertEqual(settings.quality_timeout_seconds, 30.0)
            self.assertEqual(settings.gemini_api_key.get_secret_value(), "os-key")

    def test_os_strict_boolean_false_interprets_false(self):
        """ALLOW_REMOTE_AI=false in OS env is strictly False even if dotenv has true."""
        self._write_env("ALLOW_REMOTE_AI=true\n")
        with patch.dict(os.environ, {"ALLOW_REMOTE_AI": "false"}, clear=True):
            settings = Settings(_env_file=self.env_file)
            self.assertFalse(settings.allow_remote_ai)

    def test_yaml_lower_priority_than_dotenv_and_os(self):
        """YAML values have lower priority than .env and OS environment variables."""
        self._write_yaml(
            "project:\n"
            "  id: yaml-project\n"
            "  allow_remote_ai: true\n"
            "execution:\n"
            "  quality_timeout_seconds: 200.0\n"
            "providers:\n"
            "  local:\n"
            "    base_url: http://yaml-host:11435\n"
            "    model: yaml-model\n"
        )
        self._write_env(
            "OLLAMA_BASE_URL=http://dotenv-host:11435\n"
            "ALLOW_REMOTE_AI=false\n"
        )
        with patch.dict(os.environ, {"PROJECT_ID": "os-project"}, clear=True):
            settings = Settings.from_env_and_file(
                config_path=self.yaml_file,
                _env_file=self.env_file,
            )
            # OS beat YAML
            self.assertEqual(settings.project_id, "os-project")
            # Dotenv beat YAML
            self.assertEqual(settings.ollama_base_url, "http://dotenv-host:11435")
            self.assertFalse(settings.allow_remote_ai)
            # Non-overridden YAML is preserved
            self.assertEqual(settings.quality_timeout_seconds, 200.0)
            self.assertEqual(settings.ollama_monitoring_model, "yaml-model")

    def test_cli_overrides_highest_priority(self):
        """Explicit overrides have the highest priority over OS, dotenv, YAML, and defaults."""
        self._write_yaml(
            "project:\n"
            "  id: yaml-project\n"
        )
        self._write_env("PROJECT_ID=dotenv-project\n")
        with patch.dict(os.environ, {"PROJECT_ID": "os-project"}, clear=True):
            settings = Settings.from_env_and_file(
                config_path=self.yaml_file,
                overrides={"project_id": "cli-project"},
                _env_file=self.env_file,
            )
            self.assertEqual(settings.project_id, "cli-project")

    def test_no_dotenv_opt_out(self):
        """Passing _env_file=None prevents loading .env values."""
        self._write_env("OLLAMA_BASE_URL=http://dotenv-host:11435\n")
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None)
            # Falls back to default, not dotenv
            self.assertEqual(settings.ollama_base_url, "http://127.0.0.1:11435")

            settings_from_method = Settings.from_env_and_file(_env_file=None)
            self.assertEqual(settings_from_method.ollama_base_url, "http://127.0.0.1:11435")

    def test_missing_or_empty_key_yields_none(self):
        """Empty or missing GEMINI_API_KEY must evaluate to None to prevent remote bypass."""
        with patch.dict(os.environ, {}, clear=True):
            # Missing key
            s1 = Settings(_env_file=None)
            self.assertIsNone(s1.gemini_api_key)

        # Empty key in env
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=True):
            s2 = Settings(_env_file=None)
            self.assertIsNone(s2.gemini_api_key)

        # Whitespace key in env
        with patch.dict(os.environ, {"GEMINI_API_KEY": "   "}, clear=True):
            s3 = Settings(_env_file=None)
            self.assertIsNone(s3.gemini_api_key)

        # Empty key in dotenv
        self._write_env("GEMINI_API_KEY=\n")
        with patch.dict(os.environ, {}, clear=True):
            s4 = Settings(_env_file=self.env_file)
            self.assertIsNone(s4.gemini_api_key)

    def test_no_secrets_repr_or_dump(self):
        """GEMINI_API_KEY must not leak via repr, str, model_dump or JSON serialization."""
        raw_key = "super-secret-key-do-not-leak"
        settings = Settings(_env_file=None, gemini_api_key=raw_key)

        # Key is wrapped in SecretStr
        self.assertIsInstance(settings.gemini_api_key, SecretStr)
        self.assertEqual(settings.gemini_api_key.get_secret_value(), raw_key)

        # repr must not contain the secret
        self.assertNotIn(raw_key, repr(settings))
        self.assertNotIn(raw_key, repr(settings.gemini_api_key))
        self.assertNotIn(raw_key, str(settings.gemini_api_key))

        # model_dump must not contain raw secret
        dumped = settings.model_dump()
        self.assertNotIn(raw_key, str(dumped))

        # JSON dump must not contain raw secret
        dumped_json = settings.model_dump_json()
        self.assertNotIn(raw_key, dumped_json)

    def test_unrelated_dotenv_keys_ignored(self):
        """Unrelated keys in .env should be safely ignored without errors."""
        self._write_env(
            "UNUSED_CUSTOM_KEY=whatever\n"
            "SOME_RANDOM_FLAG=123\n"
            "PROJECT_ID=custom-project\n"
        )
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=self.env_file)
            self.assertEqual(settings.project_id, "custom-project")
            self.assertFalse(hasattr(settings, "UNUSED_CUSTOM_KEY"))

    def test_old_env_only_loader_compatibility(self):
        """SystemConfig.from_env_and_file remains backwards compatible with existing signatures."""
        self._write_yaml(
            "project:\n"
            "  id: compat-project\n"
            "  default_fixture: fix_compat\n"
            "  timezone: America/Bogota\n"
            "  allow_remote_ai: false\n"
            "execution:\n"
            "  max_llm_calls_per_run: 5\n"
            "  quality_timeout_seconds: 60.0\n"
            "providers:\n"
            "  local:\n"
            "    base_url: http://127.0.0.1:11435\n"
            "    model: qwen-compat\n"
        )
        with patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://127.0.0.1:11436"}, clear=True):
            cfg = SystemConfig.from_env_and_file(
                config_path=self.yaml_file,
                overrides={"max_repair_attempts": 3},
                _env_file=None,
            )
            self.assertEqual(cfg.project_id, "compat-project")
            self.assertEqual(cfg.default_fixture, "fix_compat")
            self.assertEqual(cfg.timezone, "America/Bogota")
            self.assertFalse(cfg.allow_remote_ai)
            self.assertEqual(cfg.max_llm_calls_per_run, 5)
            self.assertEqual(cfg.quality_timeout_seconds, 60.0)
            self.assertEqual(cfg.ollama_monitoring_model, "qwen-compat")
            self.assertEqual(cfg.ollama_base_url, "http://127.0.0.1:11436")
            self.assertEqual(cfg.max_repair_attempts, 3)

    def test_verifier_timeout_seconds_default_is_120(self):
        """verifier_timeout_seconds must default to 120.0."""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None)
            self.assertEqual(settings.verifier_timeout_seconds, 120.0)

    def test_verifier_timeout_seconds_loads_from_env(self):
        """VERIFIER_TIMEOUT_SECONDS in OS environment is parsed correctly, including 220.0 budget."""
        with patch.dict(os.environ, {"VERIFIER_TIMEOUT_SECONDS": "220.0"}, clear=True):
            s1 = Settings(_env_file=None)
            self.assertEqual(s1.verifier_timeout_seconds, 220.0)

        # Also supports integer 220
        with patch.dict(os.environ, {"VERIFIER_TIMEOUT_SECONDS": "220"}, clear=True):
            s2 = Settings(_env_file=None)
            self.assertEqual(s2.verifier_timeout_seconds, 220.0)

    def test_verifier_timeout_seconds_loads_from_yaml(self):
        """verifier_timeout_seconds under execution section in YAML is parsed."""
        self._write_yaml(
            "execution:\n"
            "  quality_timeout_seconds: 45.0\n"
            "  verifier_timeout_seconds: 150.0\n"
        )
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env_and_file(
                config_path=self.yaml_file,
                _env_file=None,
            )
            self.assertEqual(settings.quality_timeout_seconds, 45.0)
            self.assertEqual(settings.verifier_timeout_seconds, 150.0)

    def test_independent_quality_and_verifier_timeouts_in_config(self):
        """quality_timeout_seconds and verifier_timeout_seconds must be independent budgets."""
        with patch.dict(os.environ, {}, clear=True):
            # Quality override only
            s_q = Settings(_env_file=None, quality_timeout_seconds=45.0)
            self.assertEqual(s_q.quality_timeout_seconds, 45.0)
            self.assertEqual(s_q.verifier_timeout_seconds, 120.0)

            # Verifier override only
            s_v = Settings(_env_file=None, verifier_timeout_seconds=88.0)
            self.assertEqual(s_v.quality_timeout_seconds, 120.0)
            self.assertEqual(s_v.verifier_timeout_seconds, 88.0)

            # Both configured independently
            s_both = Settings(_env_file=None, quality_timeout_seconds=45.0, verifier_timeout_seconds=88.0)
            self.assertEqual(s_both.quality_timeout_seconds, 45.0)
            self.assertEqual(s_both.verifier_timeout_seconds, 88.0)

    def test_verifier_timeout_precedence_hierarchy(self):
        """CLI override > OS env > dotenv > YAML > defaults precedence for verifier_timeout_seconds."""
        self._write_yaml(
            "execution:\n"
            "  verifier_timeout_seconds: 180.0\n"
        )
        self._write_env("VERIFIER_TIMEOUT_SECONDS=150.0\n")

        # Dotenv beats YAML
        with patch.dict(os.environ, {}, clear=True):
            s_dotenv = Settings.from_env_and_file(
                config_path=self.yaml_file,
                _env_file=self.env_file,
            )
            self.assertEqual(s_dotenv.verifier_timeout_seconds, 150.0)

        # OS beats dotenv and YAML
        with patch.dict(os.environ, {"VERIFIER_TIMEOUT_SECONDS": "220.0"}, clear=True):
            s_os = Settings.from_env_and_file(
                config_path=self.yaml_file,
                _env_file=self.env_file,
            )
            self.assertEqual(s_os.verifier_timeout_seconds, 220.0)

        # CLI override beats OS, dotenv, YAML
        with patch.dict(os.environ, {"VERIFIER_TIMEOUT_SECONDS": "220.0"}, clear=True):
            s_cli = Settings.from_env_and_file(
                config_path=self.yaml_file,
                overrides={"verifier_timeout_seconds": 99.0},
                _env_file=self.env_file,
            )
            self.assertEqual(s_cli.verifier_timeout_seconds, 99.0)

    def test_verifier_timeout_positive_validation(self):
        """verifier_timeout_seconds must be positive (> 0); non-positive values fail validation."""
        from pydantic import ValidationError
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValidationError):
                Settings(_env_file=None, verifier_timeout_seconds=0.0)
            with self.assertRaises(ValidationError):
                Settings(_env_file=None, verifier_timeout_seconds=-10.0)



    def test_source_mode_default_fixtures(self):
        """source_mode defaults to 'fixtures' and does not require credentials."""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None)
            self.assertEqual(settings.source_mode, "fixtures")

    def test_source_mode_validation(self):
        """source_mode must be 'fixtures' or 'real' (case-insensitive)."""
        with patch.dict(os.environ, {}, clear=True):
            s1 = Settings(_env_file=None, source_mode="real")
            self.assertEqual(s1.source_mode, "real")
            s2 = Settings(_env_file=None, source_mode="FIXTURES")
            self.assertEqual(s2.source_mode, "fixtures")

            from pydantic import ValidationError
            with self.assertRaises((ValidationError, ValueError)):
                Settings(_env_file=None, source_mode="invalid_mode")

    def test_sources_yaml_parsing(self):
        """YAML configuration with sources section maps cleanly to Settings."""
        self._write_yaml(
            "sources:\n"
            "  mode: real\n"
            "  policies_path: custom/policies.yaml\n"
            "  http_timeout_seconds: 45.0\n"
            "  http_max_response_bytes: 20971520\n"
            "  trello:\n"
            "    board_id: yaml-board\n"
            "    api_key: yaml-trello-key\n"
            "    token: yaml-trello-token\n"
            "    mapping:\n"
            "      pending_list_ids: ['list-p']\n"
            "      in_progress_list_ids: ['list-ip']\n"
            "      completed_list_ids: ['list-c']\n"
            "      blocked_labels: ['blocked-tag']\n"
            "  github:\n"
            "    owner: yaml-owner\n"
            "    repo: yaml-repo\n"
            "    branch: yaml-branch\n"
            "    token: yaml-gh-token\n"
            "    commit_sha: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        )
        with patch.dict(os.environ, {}, clear=True):
            s = Settings.from_env_and_file(config_path=self.yaml_file, _env_file=None)
            self.assertEqual(s.source_mode, "real")
            self.assertEqual(s.policies_path, "custom/policies.yaml")
            self.assertEqual(s.http_timeout_seconds, 45.0)
            self.assertEqual(s.http_max_response_bytes, 20971520)
            self.assertEqual(s.trello_board_id, "yaml-board")
            self.assertEqual(s.trello_api_key.get_secret_value(), "yaml-trello-key")
            self.assertEqual(s.trello_token.get_secret_value(), "yaml-trello-token")
            self.assertEqual(s.trello_pending_list_ids, ["list-p"])
            self.assertEqual(s.trello_in_progress_list_ids, ["list-ip"])
            self.assertEqual(s.trello_completed_list_ids, ["list-c"])
            self.assertEqual(s.trello_blocked_labels, ["blocked-tag"])
            self.assertEqual(s.github_owner, "yaml-owner")
            self.assertEqual(s.github_repo, "yaml-repo")
            self.assertEqual(s.github_branch, "yaml-branch")
            self.assertEqual(s.github_token.get_secret_value(), "yaml-gh-token")
            self.assertEqual(s.github_commit_sha, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    def test_sources_precedence_cli_over_env_over_yaml(self):
        """CLI override > OS env > dotenv > YAML precedence for source configuration."""
        self._write_yaml(
            "sources:\n"
            "  mode: fixtures\n"
            "  github:\n"
            "    owner: yaml-owner\n"
            "    repo: yaml-repo\n"
        )
        self._write_env(
            "SOURCE_MODE=real\n"
            "GITHUB_OWNER=dotenv-owner\n"
        )
        with patch.dict(os.environ, {"GITHUB_OWNER": "os-owner"}, clear=True):
            # Without CLI overrides
            s1 = Settings.from_env_and_file(
                config_path=self.yaml_file,
                _env_file=self.env_file,
            )
            self.assertEqual(s1.source_mode, "real")
            self.assertEqual(s1.github_owner, "os-owner")

            # With CLI override
            s2 = Settings.from_env_and_file(
                config_path=self.yaml_file,
                overrides={"source_mode": "fixtures", "github_owner": "cli-owner"},
                _env_file=self.env_file,
            )
            self.assertEqual(s2.source_mode, "fixtures")
            self.assertEqual(s2.github_owner, "cli-owner")

    def test_github_alias_consistency(self):
        """Consistent aliases work, but conflicting aliases raise static errors."""
        with patch.dict(os.environ, {}, clear=True):
            # Consistent repository alias
            s = Settings(_env_file=None, github_owner="o", github_repository="repo-1")
            self.assertEqual(s.github_repo, "repo-1")

            # Conflicting repository alias
            from pydantic import ValidationError
            with self.assertRaises((ValidationError, ValueError)):
                Settings(_env_file=None, github_owner="o", github_repo="r1", github_repository="r2")

            # Conflicting branch / ref alias
            with self.assertRaises((ValidationError, ValueError)):
                Settings(_env_file=None, github_owner="o", github_repo="r", github_branch="b1", github_ref="b2")

    def test_github_commit_sha_full_hex_validation(self):
        """Full 40 or 64 hex characters required for commit SHA."""
        with patch.dict(os.environ, {}, clear=True):
            # Valid 40-char SHA
            s40 = Settings(_env_file=None, github_commit_sha="a" * 40)
            self.assertEqual(s40.github_commit_sha, "a" * 40)

            # Valid 64-char SHA
            s64 = Settings(_env_file=None, github_commit_sha="b" * 64)
            self.assertEqual(s64.github_commit_sha, "b" * 64)

            # Short SHA rejected before HTTP
            from pydantic import ValidationError
            with self.assertRaises((ValidationError, ValueError)):
                Settings(_env_file=None, github_commit_sha="d08bff4")

            # Non-hex SHA rejected
            with self.assertRaises((ValidationError, ValueError)):
                Settings(_env_file=None, github_commit_sha="z" * 40)

    def test_sources_secrets_protection_and_known_secrets(self):
        """Source secrets must be SecretStr, hidden from dumps and repr, and retrievable via get_known_secret_values."""
        t_key = "trello-super-secret-key"
        t_tok = "trello-super-secret-token"
        gh_tok = "github-super-secret-token"
        gem_key = "gemini-super-secret-key"

        settings = Settings(
            _env_file=None,
            trello_api_key=t_key,
            trello_token=t_tok,
            github_token=gh_tok,
            gemini_api_key=gem_key,
        )

        self.assertIsInstance(settings.trello_api_key, SecretStr)
        self.assertIsInstance(settings.trello_token, SecretStr)
        self.assertIsInstance(settings.github_token, SecretStr)

        # Repr and dumps protection
        dump_str = settings.model_dump_json()
        repr_str = repr(settings)
        for sec in (t_key, t_tok, gh_tok, gem_key):
            self.assertNotIn(sec, dump_str)
            self.assertNotIn(sec, repr_str)

        # get_known_secret_values
        known = settings.get_known_secret_values()
        for sec in (t_key, t_tok, gh_tok, gem_key):
            self.assertIn(sec, known)

    def test_trello_state_strategy_default_list_mapping(self):
        """trello_state_strategy defaults to 'list_mapping'."""
        with patch.dict(os.environ, {}, clear=True):
            s = Settings(_env_file=None)
            self.assertEqual(s.trello_state_strategy, "list_mapping")

    def test_trello_state_strategy_os_and_dotenv_loading(self):
        """TRELLO_STATE_STRATEGY is parsed from OS env and dotenv, normalizing to lowercase."""
        # OS env
        with patch.dict(os.environ, {"TRELLO_STATE_STRATEGY": "date_fields"}, clear=True):
            s1 = Settings(_env_file=None)
            self.assertEqual(s1.trello_state_strategy, "date_fields")

        # Dotenv
        self._write_env("TRELLO_STATE_STRATEGY=DATE_FIELDS\n")
        with patch.dict(os.environ, {}, clear=True):
            s2 = Settings(_env_file=self.env_file)
            self.assertEqual(s2.trello_state_strategy, "date_fields")

    def test_trello_state_strategy_yaml_flat_and_nested_parsing(self):
        """YAML extracts trello_state_strategy both flat and nested under sources.trello.state_strategy."""
        # Nested sources.trello.state_strategy
        self._write_yaml(
            "sources:\n"
            "  trello:\n"
            "    state_strategy: date_fields\n"
        )
        with patch.dict(os.environ, {}, clear=True):
            s_nested = Settings.from_env_and_file(config_path=self.yaml_file, _env_file=None)
            self.assertEqual(s_nested.trello_state_strategy, "date_fields")

        # Flat top-level trello_state_strategy
        self._write_yaml("trello_state_strategy: date_fields\n")
        with patch.dict(os.environ, {}, clear=True):
            s_flat = Settings.from_env_and_file(config_path=self.yaml_file, _env_file=None)
            self.assertEqual(s_flat.trello_state_strategy, "date_fields")

    def test_trello_state_strategy_invalid_rejected(self):
        """Invalid, blank, non-string strategies fail validation without leaking values."""
        from pydantic import ValidationError
        with patch.dict(os.environ, {}, clear=True):
            for bad in ["unknown_strategy", "", "   ", 123]:
                with self.assertRaises((ValidationError, ValueError)):
                    Settings(_env_file=None, trello_state_strategy=bad)

    def test_trello_state_strategy_precedence(self):
        """Precedence order: init/override > OS env > dotenv > YAML > default for trello_state_strategy."""
        self._write_yaml("sources:\n  trello:\n    state_strategy: list_mapping\n")
        self._write_env("TRELLO_STATE_STRATEGY=date_fields\n")

        # Dotenv beats YAML
        with patch.dict(os.environ, {}, clear=True):
            s_dotenv = Settings.from_env_and_file(config_path=self.yaml_file, _env_file=self.env_file)
            self.assertEqual(s_dotenv.trello_state_strategy, "date_fields")

        # OS beats dotenv
        with patch.dict(os.environ, {"TRELLO_STATE_STRATEGY": "list_mapping"}, clear=True):
            s_os = Settings.from_env_and_file(config_path=self.yaml_file, _env_file=self.env_file)
            self.assertEqual(s_os.trello_state_strategy, "list_mapping")

        # Override beats OS
        with patch.dict(os.environ, {"TRELLO_STATE_STRATEGY": "list_mapping"}, clear=True):
            s_ov = Settings.from_env_and_file(
                config_path=self.yaml_file,
                overrides={"trello_state_strategy": "date_fields"},
                _env_file=self.env_file,
            )
            self.assertEqual(s_ov.trello_state_strategy, "date_fields")

    def test_cross_layer_alias_precedence_repo(self):
        """Higher priority source with alias overrides lower priority without false conflict (Defect 3)."""
        self._write_yaml(
            "sources:\n"
            "  github:\n"
            "    repo: yaml_repo\n"
        )
        self._write_env(
            "GITHUB_REPOSITORY=dotenv_repo\n"
        )
        # 1. Dotenv GITHUB_REPOSITORY beats YAML repo without ValidationError
        with patch.dict(os.environ, {}, clear=True):
            s_dotenv = Settings.from_env_and_file(
                config_path=self.yaml_file,
                _env_file=self.env_file,
            )
            self.assertEqual(s_dotenv.github_repo, "dotenv_repo")
            self.assertEqual(s_dotenv.github_repository, "dotenv_repo")

        # 2. OS env GITHUB_REPO beats dotenv GITHUB_REPOSITORY
        with patch.dict(os.environ, {"GITHUB_REPO": "os_repo"}, clear=True):
            s_os = Settings.from_env_and_file(
                config_path=self.yaml_file,
                _env_file=self.env_file,
            )
            self.assertEqual(s_os.github_repo, "os_repo")
            self.assertEqual(s_os.github_repository, "os_repo")

        # 3. CLI override with github_repository beats OS env GITHUB_REPO
        with patch.dict(os.environ, {"GITHUB_REPO": "os_repo"}, clear=True):
            s_cli = Settings.from_env_and_file(
                config_path=self.yaml_file,
                overrides={"github_repository": "cli_repo"},
                _env_file=self.env_file,
            )
            self.assertEqual(s_cli.github_repo, "cli_repo")
            self.assertEqual(s_cli.github_repository, "cli_repo")

    def test_cross_layer_alias_precedence_branch(self):
        """Higher priority source with ref/branch alias overrides lower priority (Defect 3)."""
        self._write_yaml(
            "sources:\n"
            "  github:\n"
            "    branch: yaml_branch\n"
        )
        self._write_env(
            "GITHUB_REF=dotenv_ref\n"
        )
        with patch.dict(os.environ, {}, clear=True):
            s = Settings.from_env_and_file(
                config_path=self.yaml_file,
                _env_file=self.env_file,
            )
            self.assertEqual(s.github_branch, "dotenv_ref")
            self.assertEqual(s.github_ref, "dotenv_ref")

    def test_same_layer_contradictory_aliases_rejected(self):
        """Contradictory aliases within the same priority layer must raise validation error (Defect 3)."""
        from pydantic import ValidationError

        # Contradictory in OS env
        with patch.dict(os.environ, {"GITHUB_REPO": "repo1", "GITHUB_REPOSITORY": "repo2"}, clear=True):
            with self.assertRaises((ValidationError, ValueError)):
                Settings(_env_file=None)

        # Contradictory in YAML
        self._write_yaml(
            "sources:\n"
            "  github:\n"
            "    repo: r1\n"
            "    repository: r2\n"
        )
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises((ValidationError, ValueError)):
                Settings.from_env_and_file(config_path=self.yaml_file, _env_file=None)

    def test_invalid_timezone_rejected_statically(self):
        """Explicit invalid timezone must be rejected statically before HTTP (Defect 4)."""
        from pydantic import ValidationError

        with patch.dict(os.environ, {}, clear=True):
            # Invalid timezone in Settings construction
            with self.assertRaises((ValidationError, ValueError)):
                Settings(_env_file=None, timezone="Invalid/Nonexistent_TZ")

            # Valid timezones accepted
            s_utc = Settings(_env_file=None, timezone="UTC")
            self.assertEqual(s_utc.timezone, "UTC")
            s_bogota = Settings(_env_file=None, timezone="America/Bogota")
            self.assertEqual(s_bogota.timezone, "America/Bogota")


if __name__ == "__main__":
    unittest.main()
