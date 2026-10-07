"""Tests for T5b: source configuration, fresh source factory, and CLI read-only source checks."""

import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

from pydantic import SecretStr

from agente_gestion.application.config import Settings
from agente_gestion.adapters.sources.factory import (
    create_task_source,
    create_code_source,
    create_ci_source,
    create_policy_source,
    create_composite_source,
)
from agente_gestion.adapters.sources.fixtures import FixtureSourcesAdapter
from agente_gestion.adapters.sources.trello import TrelloSourceAdapter
from agente_gestion.adapters.sources.github import (
    GitHubCodeSourceAdapter,
    GitHubCodeSourceAdapter as GitHubSourceAdapter,
)
from agente_gestion.adapters.sources.actions import GitHubActionsSourceAdapter
from agente_gestion.adapters.sources.local_policy import LocalYamlPolicyAdapter
from agente_gestion.adapters.sources.composite import CompositeSourcesAdapter
from agente_gestion.adapters.sources.http_transport import (
    HttpGetTransportPort,
    HttpResponse,
)
from agente_gestion.ports.source_errors import (
    SourceError,
    SourceErrorCategory,
    SourceProvider,
)
from agente_gestion.cli import main, build_parser


SAMPLE_SHA = "a" * 40
SAMPLE_SHA_2 = "b" * 40


class FakeTransport(HttpGetTransportPort):
    """Deterministic fake HTTP transport for real adapter integration tests."""

    def __init__(self, responses: dict[str, HttpResponse | Exception] | None = None):
        self.responses = responses or {}
        self.calls: list[dict] = []

    def get(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        timeout_seconds: float | None = None,
    ) -> HttpResponse:
        self.calls.append({"url": url, "params": params, "headers": headers})
        if url in self.responses:
            resp = self.responses[url]
            if isinstance(resp, Exception):
                raise resp
            return resp
        # Default mock responses
        if "api.trello.com/1/boards/" in url and "/lists/all" in url:
            return HttpResponse(status_code=200, body=[{"id": "list-1", "idBoard": "board-1", "name": "Done", "closed": False}])
        if "api.trello.com/1/boards/" in url and "/cards/all" in url:
            return HttpResponse(status_code=200, body=[{
                "id": "card-1", "idBoard": "board-1", "idList": "list-1", "name": "Task 1",
                "desc": "Detail", "closed": False, "due": "2026-10-06T12:00:00Z", "idMembers": [], "labels": []
            }])
        if "api.github.com/repos/" in url and "/commits/" in url:
            return HttpResponse(status_code=200, body={"sha": SAMPLE_SHA, "files": []})
        if "api.github.com/repos/" in url and "/actions/runs" in url:
            return HttpResponse(status_code=200, body={"total_count": 0, "workflow_runs": []})
        if "api.github.com/repos/" in url and url.endswith(f"/repos/test-owner/test-repo"):
            return HttpResponse(status_code=200, body={"default_branch": "main"})
        return HttpResponse(status_code=200, body={})


class SourceFactoryTestCase(unittest.TestCase):
    """Tests for fresh source factory constructing isolated adapters per invocation."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.policies_path = os.path.join(self.temp_dir, "policies.yaml")
        with open(self.policies_path, "w", encoding="utf-8") as f:
            f.write(
                "policies:\n"
                "  - id: Q1\n"
                "    description: Tests passing\n"
                "    metric_key: failed_tests\n"
                "    operator: '<='\n"
                "    threshold: 0\n"
                "    source: ci\n"
                "    mandatory: true\n"
            )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_factory_fixtures_mode_returns_fixture_adapter(self):
        settings = Settings(
            _env_file=None,
            source_mode="fixtures",
            fixtures_dir=self.temp_dir,
        )
        task_src = create_task_source(settings)
        code_src = create_code_source(settings)
        ci_src = create_ci_source(settings)
        pol_src = create_policy_source(settings)

        self.assertIsInstance(task_src, FixtureSourcesAdapter)
        self.assertIsInstance(code_src, FixtureSourcesAdapter)
        self.assertIsInstance(ci_src, FixtureSourcesAdapter)
        self.assertIsInstance(pol_src, FixtureSourcesAdapter)

    def test_factory_real_mode_requires_only_selected_provider_config(self):
        # Trello only config
        s_trello = Settings(
            _env_file=None,
            source_mode="real",
            trello_board_id="board-123",
            trello_api_key="api-key-123",
            trello_token="token-123",
            trello_completed_list_ids=["list-1"],
        )
        fake_tr = FakeTransport()
        task_src = create_task_source(s_trello, transport=fake_tr)
        self.assertIsInstance(task_src, TrelloSourceAdapter)
        self.assertEqual(task_src.state_strategy, "list_mapping")

    def test_factory_trello_state_strategy_date_fields_without_groups(self):
        """create_task_source passes state_strategy='date_fields' without requiring list mapping groups."""
        s_date = Settings(
            _env_file=None,
            source_mode="real",
            trello_board_id="board-123",
            trello_api_key="api-key-123",
            trello_token="token-123",
            trello_state_strategy="date_fields",
        )
        fake_tr = FakeTransport()
        task_src = create_task_source(s_date, transport=fake_tr)
        self.assertIsInstance(task_src, TrelloSourceAdapter)
        self.assertEqual(task_src.state_strategy, "date_fields")

        # Code source should fail because github config is missing
        with self.assertRaises(SourceError) as cm:
            create_code_source(s_date, transport=fake_tr)
        self.assertEqual(cm.exception.provider, SourceProvider.github)
        self.assertEqual(cm.exception.category, SourceErrorCategory.configuration)

    def test_factory_ci_source_requires_target_sha_before_http(self):
        s_gh = Settings(
            _env_file=None,
            source_mode="real",
            github_owner="test-owner",
            github_repo="test-repo",
            github_token="gh-token-123",
        )
        fake_tr = FakeTransport()
        # When commit_sha is not configured and not passed, create_ci_source must fail statically
        with self.assertRaises(SourceError) as cm:
            create_ci_source(s_gh, transport=fake_tr)
        self.assertEqual(cm.exception.provider, SourceProvider.github_actions)
        self.assertEqual(cm.exception.category, SourceErrorCategory.configuration)

        # Passing explicit target_sha succeeds
        ci_src = create_ci_source(s_gh, commit_sha=SAMPLE_SHA, transport=fake_tr)
        self.assertIsInstance(ci_src, GitHubActionsSourceAdapter)

    def test_factory_composite_source_fresh_instance_passes_known_secrets(self):
        settings = Settings(
            _env_file=None,
            source_mode="real",
            trello_board_id="board-123",
            trello_api_key="trello-sec-key",
            trello_token="trello-sec-tok",
            trello_completed_list_ids=["list-1"],
            github_owner="test-owner",
            github_repo="test-repo",
            github_token="gh-sec-tok",
            gemini_api_key="gemini-sec-key",
            policies_path=self.policies_path,
        )
        fake_tr = FakeTransport()
        composite = create_composite_source(
            settings,
            transport=fake_tr,
        )
        self.assertIsInstance(composite, CompositeSourcesAdapter)
        # Verify known secrets are preserved privately
        secrets = settings.get_known_secret_values()
        self.assertIn("trello-sec-key", secrets)
        self.assertIn("trello-sec-tok", secrets)
        self.assertIn("gh-sec-tok", secrets)
        self.assertIn("gemini-sec-key", secrets)
        # None of the secrets in repr
        self.assertNotIn("trello-sec-key", repr(composite))
        self.assertNotIn("gh-sec-tok", repr(composite))


class CLISourceCheckTestCase(unittest.TestCase):
    """Tests for CLI source-check and source mode flag handling."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.runs_dir = os.path.join(self.temp_dir, "runs")
        self.config_path = os.path.join(self.temp_dir, "config.yaml")
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write(
                "project:\n"
                "  id: test-proj\n"
                "sources:\n"
                "  mode: fixtures\n"
            )
        self.empty_env = os.path.join(self.temp_dir, "empty.env")
        open(self.empty_env, "w").close()
        orig_main = main
        def safe_main(*args: Any, **kwargs: Any) -> int:
            if "--env-file" not in sys.argv and "--help" not in sys.argv and "-h" not in sys.argv:
                sys.argv.extend(["--env-file", self.empty_env])
            return orig_main(*args, **kwargs)
        self.patcher = patch(f"{__name__}.main", side_effect=safe_main)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cli_help_smoke(self):
        stdout_capture = io.StringIO()
        with patch.object(sys, "argv", ["agente_gestion", "--help"]), patch("sys.stdout", stdout_capture):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)
        out = stdout_capture.getvalue()
        self.assertIn("source-check", out)
        self.assertIn("run", out)

    def test_cli_run_real_missing_config_fails_safely_exit_2(self):
        """CLI run in real mode with missing Trello config fails early with exit 2 and safe error."""
        cmd = [
            "run",
            "test-proj",
            "--sources", "real",
            "--config", self.config_path,
            "--db-path", self.db_path,
            "--output-dir", self.runs_dir,
        ]
        fake_tr = FakeTransport()
        stderr_capture = io.StringIO()
        with patch("agente_gestion.adapters.sources.factory.BoundedGetTransport", return_value=fake_tr):
            with patch.object(sys, "argv", ["agente_gestion"] + cmd), patch("sys.stderr", stderr_capture):
                exit_code = main()

        self.assertEqual(exit_code, 2)
        err = stderr_capture.getvalue()
        self.assertIn("source configuration failed for 'trello'", err.lower())
        self.assertIn("configuration", err.lower())
        self.assertNotIn("traceback", err.lower())
        self.assertNotIn("pydantic", err.lower())
        self.assertEqual(len(fake_tr.calls), 0)
        self.assertEqual(len(os.listdir(self.runs_dir)), 0)
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            run_count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        self.assertEqual(run_count, 0)

    def test_cli_explicit_real_and_fixture_contradiction_rejected(self):
        """Conflicting explicit --sources real and --fixture must be rejected early."""
        cmd = [
            "run",
            "test-proj",
            "--sources", "real",
            "--fixture", "fixture_1_standard",
            "--config", self.config_path,
        ]
        stderr_capture = io.StringIO()
        with patch.object(sys, "argv", ["agente_gestion"] + cmd), patch("sys.stderr", stderr_capture):
            exit_code = main()
        self.assertNotEqual(exit_code, 0)
        err = stderr_capture.getvalue()
        self.assertIn("contradictory", err.lower())

    def test_cli_fixture_override_takes_precedence_over_config_real(self):
        """Explicit --fixture overrides config source_mode: real (backward compatibility)."""
        real_config_path = os.path.join(self.temp_dir, "real_config.yaml")
        with open(real_config_path, "w", encoding="utf-8") as f:
            f.write(
                "project:\n"
                "  id: fixture_1_standard\n"
                "sources:\n"
                "  mode: real\n"
            )
        cmd = [
            "run",
            "fixture_1_standard",
            "--fixture", "fixture_1_standard",
            "--fake",
            "--config", real_config_path,
            "--db-path", self.db_path,
            "--output-dir", self.runs_dir,
        ]
        stdout_capture = io.StringIO()
        with patch.object(sys, "argv", ["agente_gestion"] + cmd), patch("sys.stdout", stdout_capture):
            exit_code = main()
        self.assertEqual(exit_code, 0)

    def test_cli_source_check_fixtures_mode(self):
        """source-check in fixtures mode runs without network and prints metadata."""
        cmd = [
            "source-check",
            "all",
            "--sources", "fixtures",
            "--project", "fixture_1_standard",
        ]
        stdout_capture = io.StringIO()
        with patch.object(sys, "argv", ["agente_gestion"] + cmd), patch("sys.stdout", stdout_capture):
            exit_code = main()
        self.assertEqual(exit_code, 0)
        out = stdout_capture.getvalue()
        self.assertIn("trello", out.lower())
        self.assertIn("github", out.lower())
        self.assertIn("success", out.lower())
        # No DB file created during check
        self.assertFalse(os.path.exists(self.db_path))

    def test_cli_source_check_real_isolated_trello_success_and_failure(self):
        """source-check trello executes read-only and prints only allowed metadata."""
        # Missing config fails safely
        cmd_fail = [
            "source-check",
            "trello",
            "--sources", "real",
            "--config", self.config_path,
        ]
        stdout_capture = io.StringIO()
        with patch.object(sys, "argv", ["agente_gestion"] + cmd_fail), patch("sys.stdout", stdout_capture):
            exit_code = main()
        self.assertNotEqual(exit_code, 0)
        out = stdout_capture.getvalue()
        self.assertIn("failed", out.lower())
        self.assertIn("configuration", out.lower())
        self.assertIn("unknown", out.lower())

    def test_cli_source_check_real_isolated_actions_requires_commit_sha(self):
        """Isolated github-actions check requires explicit commit SHA before HTTP."""
        gh_config_path = os.path.join(self.temp_dir, "gh_config.yaml")
        with open(gh_config_path, "w", encoding="utf-8") as f:
            f.write(
                "sources:\n"
                "  mode: real\n"
                "  github:\n"
                "    owner: test-owner\n"
                "    repo: test-repo\n"
            )
        # Without --commit
        cmd = [
            "source-check",
            "github-actions",
            "--sources", "real",
            "--config", gh_config_path,
        ]
        stdout_capture = io.StringIO()
        with patch.object(sys, "argv", ["agente_gestion"] + cmd), patch("sys.stdout", stdout_capture):
            exit_code = main()
        self.assertNotEqual(exit_code, 0)
        out = stdout_capture.getvalue()
        self.assertIn("failed", out.lower())
        self.assertIn("configuration", out.lower())

    def test_cli_source_check_real_never_leaks_payload_or_secrets(self):
        """Ensures source-check output contains no card descriptions, patches, or secrets."""
        secret_key = "super-secret-trello-key-999"
        secret_token = "super-secret-trello-token-888"
        sec_config_path = os.path.join(self.temp_dir, "sec_config.yaml")
        with open(sec_config_path, "w", encoding="utf-8") as f:
            f.write(
                f"sources:\n"
                f"  mode: real\n"
                f"  trello:\n"
                f"    board_id: board-123\n"
                f"    api_key: {secret_key}\n"
                f"    token: {secret_token}\n"
                f"    mapping:\n"
                f"      completed_list_ids: ['list-1']\n"
            )
        fake_tr = FakeTransport({
            "https://api.trello.com/1/boards/board-123/lists/all": HttpResponse(
                status_code=200,
                body=[{"id": "list-1", "idBoard": "board-123", "name": "Done", "closed": False}],
            ),
            "https://api.trello.com/1/boards/board-123/cards/all": HttpResponse(
                status_code=200,
                body=[{
                    "id": "card-1", "idBoard": "board-123", "idList": "list-1",
                    "name": "Super Secret Task Title",
                    "desc": "Sensitive internal customer data do not disclose",
                    "closed": False,
                    "due": "2026-10-06T12:00:00Z",
                    "idMembers": [],
                    "labels": [],
                }],
            ),
        })

        cmd = [
            "source-check",
            "trello",
            "--sources", "real",
            "--config", sec_config_path,
        ]
        stdout_capture = io.StringIO()
        with patch("agente_gestion.adapters.sources.factory.BoundedGetTransport", return_value=fake_tr):
            with patch.object(sys, "argv", ["agente_gestion"] + cmd), patch("sys.stdout", stdout_capture):
                exit_code = main()

        out = stdout_capture.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertNotIn(secret_key, out)
        self.assertNotIn(secret_token, out)
        self.assertNotIn("Super Secret Task Title", out)
        self.assertNotIn("Sensitive internal customer data", out)
        self.assertIn("success", out.lower())
        self.assertIn("1", out)  # count

    def test_factory_composite_source_fixtures_mode_zero_http(self):
        """create_composite_source in fixtures mode returns FixtureSourcesAdapter and issues 0 HTTP calls (Defect 1)."""
        fake_tr = FakeTransport()
        # 1. Default settings
        s_default = Settings(_env_file=None, source_mode="fixtures", fixtures_dir=self.temp_dir)
        adapter_def = create_composite_source(s_default, transport=fake_tr)
        self.assertIsInstance(adapter_def, FixtureSourcesAdapter)
        self.assertEqual(len(fake_tr.calls), 0)

        # 2. Settings with dummy real credentials and repo
        s_with_creds = Settings(
            _env_file=None,
            source_mode="fixtures",
            fixtures_dir=self.temp_dir,
            trello_board_id="board-123",
            trello_api_key="key-123",
            trello_token="tok-123",
            github_owner="owner",
            github_repo="repo",
            github_commit_sha=SAMPLE_SHA,
        )
        adapter_creds = create_composite_source(s_with_creds, transport=fake_tr)
        self.assertIsInstance(adapter_creds, FixtureSourcesAdapter)
        self.assertEqual(len(fake_tr.calls), 0)

    def test_factory_composite_source_real_without_sha_succeeds(self):
        """create_composite_source in real mode without commit_sha creates Composite with None default SHA (Defect 1)."""
        fake_tr = FakeTransport()
        s_real = Settings(
            _env_file=None,
            source_mode="real",
            trello_board_id="board-123",
            trello_api_key="key-123",
            trello_token="tok-123",
            trello_completed_list_ids=["list-1"],
            github_owner="owner",
            github_repo="repo",
            github_commit_sha=None,
            policies_path=os.path.join(self.temp_dir, "policies.yaml"),
        )
        with open(s_real.policies_path, "w", encoding="utf-8") as f:
            f.write("policies:\n  - id: P1\n    description: test\n    metric_key: m\n    operator: '=='\n    threshold: 1\n    source: ci\n    mandatory: false\n")

        comp = create_composite_source(s_real, transport=fake_tr)
        self.assertIsInstance(comp, CompositeSourcesAdapter)
        self.assertIsNone(comp._default_commit_sha)

    def test_cli_source_check_cross_provider_secret_in_sha_is_guarded(self):
        """SHA containing another provider's secret (e.g. Trello API key) must not leak and Actions must not query it (Defect 2)."""
        secret_trello_key = "abcdef1234"  # 10 chars hex
        leaking_sha = (secret_trello_key + "0" * 30).lower()  # 40 chars hex

        config_path = os.path.join(self.temp_dir, "leak_config.yaml")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(
                f"sources:\n"
                f"  mode: real\n"
                f"  trello:\n"
                f"    board_id: board-123\n"
                f"    api_key: {secret_trello_key}\n"
                f"    token: token-123\n"
                f"    mapping:\n"
                f"      completed_list_ids: ['list-1']\n"
                f"  github:\n"
                f"    owner: test-owner\n"
                f"    repo: test-repo\n"
            )

        fake_tr = FakeTransport({
            "https://api.trello.com/1/boards/board-123/lists/all": HttpResponse(
                status_code=200, body=[{"id": "list-1", "idBoard": "board-123", "name": "Done", "closed": False}]
            ),
            "https://api.trello.com/1/boards/board-123/cards/all": HttpResponse(
                status_code=200, body=[]
            ),
            "https://api.github.com/repos/test-owner/test-repo": HttpResponse(
                status_code=200, body={"default_branch": "main"}
            ),
            "https://api.github.com/repos/test-owner/test-repo/commits/main": HttpResponse(
                status_code=200, body={"sha": leaking_sha, "files": []}
            ),
        })

        cmd = [
            "source-check",
            "all",
            "--sources", "real",
            "--config", config_path,
        ]
        stdout_capture = io.StringIO()
        with patch("agente_gestion.adapters.sources.factory.BoundedGetTransport", return_value=fake_tr):
            with patch.object(sys, "argv", ["agente_gestion"] + cmd), patch("sys.stdout", stdout_capture):
                exit_code = main()

        out = stdout_capture.getvalue()
        # Must exit nonzero due to incomplete/failed source
        self.assertNotEqual(exit_code, 0)
        # Must NOT leak secret or the unsafe leaking SHA anywhere in stdout
        self.assertNotIn(secret_trello_key, out)
        self.assertNotIn(leaking_sha, out)
        # GitHub must be marked failed with malformed_payload
        self.assertIn("malformed_payload", out)
        # Actions must not have been queried with leaking SHA
        actions_calls = [c for c in fake_tr.calls if "actions" in c["url"]]
        for call in actions_calls:
            self.assertNotIn(leaking_sha, str(call))

    def test_cli_source_check_invalid_timezone_fails_safely(self):
        """Invalid timezone in config fails source-check with exit 2 and safe error (Defect 4)."""
        tz_config_path = os.path.join(self.temp_dir, "tz_config.yaml")
        with open(tz_config_path, "w", encoding="utf-8") as f:
            f.write(
                "project:\n"
                "  timezone: Invalid/Nonexistent_TZ\n"
                "sources:\n"
                "  mode: real\n"
            )
        cmd = [
            "source-check",
            "trello",
            "--config", tz_config_path,
        ]
        stderr_capture = io.StringIO()
        stdout_capture = io.StringIO()
        with patch.object(sys, "argv", ["agente_gestion"] + cmd), patch("sys.stderr", stderr_capture), patch("sys.stdout", stdout_capture):
            exit_code = main()

        self.assertEqual(exit_code, 2)
        combined = stdout_capture.getvalue() + stderr_capture.getvalue()
        self.assertIn("configuration", combined.lower())
        self.assertNotIn("Invalid/Nonexistent_TZ", combined)

    def test_cli_source_check_real_trello_date_fields(self):
        """source-check trello in date_fields mode executes read-only, verifies states, and issues 2 GETs."""
        config_path = os.path.join(self.temp_dir, "date_config.yaml")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(
                "sources:\n"
                "  mode: real\n"
                "  trello:\n"
                "    board_id: board-123\n"
                "    api_key: api-key-123\n"
                "    token: token-123\n"
                "    state_strategy: date_fields\n"
            )
        fake_tr = FakeTransport({
            "https://api.trello.com/1/boards/board-123/lists/all": HttpResponse(
                status_code=200,
                body=[{"id": "list-phase", "idBoard": "board-123", "name": "Backlog", "closed": False}],
            ),
            "https://api.trello.com/1/boards/board-123/cards/all": HttpResponse(
                status_code=200,
                body=[
                    {
                        "id": "card-done", "idBoard": "board-123", "idList": "list-phase",
                        "name": "Finished Task", "closed": False, "dueComplete": True,
                        "due": "2026-10-06T12:00:00Z", "start": "2026-10-01T00:00:00Z",
                    },
                    {
                        "id": "card-prog", "idBoard": "board-123", "idList": "list-phase",
                        "name": "Started Task", "closed": False, "dueComplete": False,
                        "due": "2026-10-10T12:00:00Z", "start": "2026-10-01T00:00:00Z",
                    },
                ],
            ),
        })
        cmd = [
            "source-check",
            "trello",
            "--sources", "real",
            "--config", config_path,
        ]
        stdout_capture = io.StringIO()
        with patch("agente_gestion.adapters.sources.factory.BoundedGetTransport", return_value=fake_tr):
            with patch.object(sys, "argv", ["agente_gestion"] + cmd), patch("sys.stdout", stdout_capture):
                exit_code = main()

        self.assertEqual(exit_code, 0)
        out = stdout_capture.getvalue()
        self.assertIn("success", out.lower())
        self.assertIn("2", out)  # 2 cards found
        self.assertEqual(len(fake_tr.calls), 2)
        # Verify factory created adapter derived correct task states
        settings = Settings.from_env_and_file(config_path=config_path, _env_file=self.empty_env)
        adapter = create_task_source(settings, transport=fake_tr)
        tasks = adapter.get_tasks("test-proj")
        t_map = {t.id: t for t in tasks}
        from agente_gestion.domain.tasks import TaskState
        self.assertEqual(t_map["card-done"].state, TaskState.completed)
        self.assertEqual(t_map["card-prog"].state, TaskState.in_progress)

    def test_cli_source_check_invalid_state_strategy_fails_safely(self):
        """Invalid trello state_strategy fails source-check with exit 2 and safe error without leaking value."""
        bad_config_path = os.path.join(self.temp_dir, "bad_strat_config.yaml")
        with open(bad_config_path, "w", encoding="utf-8") as f:
            f.write(
                "sources:\n"
                "  mode: real\n"
                "  trello:\n"
                "    board_id: board-123\n"
                "    api_key: api-key-123\n"
                "    token: token-123\n"
                "    state_strategy: UNSUPPORTED_STRATEGY\n"
            )
        cmd = [
            "source-check",
            "trello",
            "--config", bad_config_path,
        ]
        stderr_capture = io.StringIO()
        stdout_capture = io.StringIO()
        with patch.object(sys, "argv", ["agente_gestion"] + cmd), patch("sys.stderr", stderr_capture), patch("sys.stdout", stdout_capture):
            exit_code = main()

        self.assertEqual(exit_code, 2)
        combined = stdout_capture.getvalue() + stderr_capture.getvalue()
        self.assertIn("configuration", combined.lower())
        self.assertNotIn("UNSUPPORTED_STRATEGY", combined)


if __name__ == "__main__":
    unittest.main()
