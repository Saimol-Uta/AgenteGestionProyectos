"""Deterministic, no-network contracts for governed code branch actions."""

import base64
import hashlib
import tempfile
import unittest
from pathlib import Path

from agente_gestion.adapters.github_code_action_adapter import (
    CodeActionError,
    GitHubCodeActionAdapter,
)
from agente_gestion.domain.code_action import CodeSourceSnapshot


class FakeGitHubTransport:
    def __init__(self, *, push=True, source="def parse(value):\n    return value\n"):
        self.push = push
        self.source = source
        self.base_sha = "a" * 40
        self.calls = []

    def request(self, method, url, headers, body=None):
        self.calls.append((method, url, body))
        if method == "GET" and url.endswith("/repos/acme/project"):
            return 200, {"permissions": {"push": self.push}}
        if method == "GET" and url.endswith("/git/ref/heads/main"):
            return 200, {"object": {"sha": self.base_sha}}
        if method == "GET" and "/contents/src/parse.py" in url:
            return 200, {
                "content": base64.b64encode(self.source.encode()).decode(),
                "encoding": "base64", "sha": "file-sha",
            }
        if method == "POST" and url.endswith("/git/refs"):
            return 201, {"ref": "refs/heads/quality/test", "object": {"sha": "base-commit"}}
        if method == "PUT" and "/contents/src/parse.py" in url:
            return 201, {"commit": {"sha": "live-commit"}}
        raise AssertionError(f"Unexpected mocked GitHub request: {method} {url}")


class Phase2GitHubCodeActionTests(unittest.TestCase):
    @staticmethod
    def _source_snapshot(repo: str, file_path: str, source: str, *, complete=True):
        return CodeSourceSnapshot(
            content=source, is_complete=complete, repository=repo,
            commit_sha="a" * 40, file_path=file_path,
            source_sha256=hashlib.sha256(source.encode()).hexdigest(),
            total_lines=len(source.splitlines()),
        )

    def _sandbox(self, root: Path, source: str, *, complete=True):
        return GitHubCodeActionAdapter(
            branches_dir=str(root),
            source_lookup=lambda repo, path: self._source_snapshot(
                repo, path, source, complete=complete,
            ),
        )

    def _create(self, adapter, *, target="quality/finding-001", excerpt="    return value",
                code="    return value.strip()", file_path="src/parse.py"):
        return adapter.create_verification_branch(
            repo="acme/project", base_branch="main", target_branch=target,
            file_path=file_path, original_excerpt=excerpt, suggested_code=code,
            finding_id="finding-001", actor="gestor-local",
        )

    def test_sandbox_replaces_one_exact_excerpt_preserves_complete_source_and_is_deterministic(self):
        source = "def parse(value):\n    return value\n"
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._sandbox(Path(tmp) / "branches", source)
            result = self._create(adapter)
            file = Path(tmp) / "branches" / "quality" / "finding-001" / "src" / "parse.py"
            self.assertEqual(file.read_text(encoding="utf-8"),
                             "def parse(value):\n    return value.strip()\n")
            self.assertEqual(result.execution_mode, "sandbox")
            self.assertEqual(result.branch_url, "sandbox://branches/quality/finding-001")
            self.assertEqual(len(result.commit_sha_applied), 64)
            self.assertEqual(self._create(adapter).commit_sha_applied, result.commit_sha_applied)

    def test_sandbox_requires_complete_source_and_supported_syntax_before_writing(self):
        source = "def parse(value):\n    return value\n"
        cases = (
            ("missing", None, "src/parse.py", "    return value.strip()"),
            ("partial", CodeSourceSnapshot(content="    return value\n", is_complete=False),
             "src/parse.py", "    return value.strip()"),
            ("unsupported", self._source_snapshot(
                "acme/project", "src/parse.ts", source,
            ), "src/parse.ts", "    return value.trim()"),
        )
        expected_codes = {
            "missing": "fuente_no_verificable",
            "partial": "fuente_no_verificable",
            "unsupported": "validacion_sintaxis_no_soportada",
        }
        for label, snapshot, file_path, code in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "branches"
                adapter = GitHubCodeActionAdapter(
                    branches_dir=str(root), source_lookup=lambda _repo, _path: snapshot,
                )
                with self.assertRaises(CodeActionError) as caught:
                    self._create(adapter, file_path=file_path, code=code)
                self.assertEqual(caught.exception.code, expected_codes[label])
                self.assertFalse(root.exists())

    def test_sandbox_refuses_stale_or_ambiguous_excerpt_without_appending(self):
        cases = (
            ("def parse(value):\n    return value\n", "    return missing", "stale"),
            ("def parse(value):\n    return value\n    return value\n", "    return value", "ambiguous"),
        )
        for source, excerpt, label in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "branches"
                adapter = self._sandbox(root, source)
                with self.assertRaises(CodeActionError) as caught:
                    self._create(adapter, excerpt=excerpt)
                self.assertEqual(caught.exception.code, "parche_obsoleto_o_ambiguo")
                self.assertFalse(root.exists())

    def test_sandbox_checks_python_syntax_and_does_not_overwrite_existing_branch_file(self):
        source = "def parse(value):\n    return value\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            adapter = self._sandbox(root, source)
            with self.assertRaises(CodeActionError) as caught:
                self._create(adapter, code="    return (")
            self.assertEqual(caught.exception.code, "codigo_sugerido_invalido")
            self.assertFalse(root.exists())
            result = self._create(adapter)
            target = root / "quality" / "finding-001" / "src" / "parse.py"
            original = target.read_text(encoding="utf-8")
            with self.assertRaises(CodeActionError):
                self._create(adapter, code="    return str(value)")
            self.assertEqual(target.read_text(encoding="utf-8"), original)
            self.assertEqual(result.execution_mode, "sandbox")

    def test_sandbox_validates_json_syntax_before_writing(self):
        source = '{"value": 1}\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            adapter = self._sandbox(root, source)
            with self.assertRaises(CodeActionError) as caught:
                self._create(
                    adapter, file_path="src/config.json", excerpt='{"value": 1}', code='{"value":',
                )
            self.assertEqual(caught.exception.code, "codigo_sugerido_invalido")
            self.assertFalse(root.exists())

    def test_branch_and_file_paths_reject_traversal_and_git_unsafe_names(self):
        source = "def parse(value):\n    return value\n"
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._sandbox(Path(tmp) / "branches", source)
            for branch in ("../escape", "quality//bad", "quality/has space", "-leading", "@"):
                with self.subTest(branch=branch), self.assertRaises(CodeActionError):
                    self._create(adapter, target=branch)
            for path in ("../outside.py", "/absolute.py", "src\\..\\outside.py", "src//bad.py"):
                with self.subTest(path=path), self.assertRaises(CodeActionError):
                    self._create(adapter, file_path=path)

    def test_live_mode_requires_push_permission_and_uses_only_injected_transport(self):
        source = "def parse(value):\n    return value\n"
        with tempfile.TemporaryDirectory() as tmp:
            transport = FakeGitHubTransport(source=source)
            adapter = GitHubCodeActionAdapter(
                branches_dir=str(Path(tmp) / "branches"), token="non-secret-test-token",
                repository="acme/project", external_writes_enabled=True,
                project_mode="real", base_branch="main", http_client=transport,
                source_lookup=lambda repo, path: self._source_snapshot(repo, path, source),
            )
            result = self._create(adapter)
            self.assertEqual(result.execution_mode, "live")
            self.assertEqual(result.commit_sha_applied, "live-commit")
            self.assertEqual(result.branch_url, "https://github.com/acme/project/tree/quality/finding-001")
            self.assertEqual([call[0] for call in transport.calls], ["GET", "GET", "GET", "POST", "PUT"])
            self.assertNotIn("Authorization", str(transport.calls))

    def test_readonly_token_falls_back_to_explicit_sandbox_and_never_claims_remote_branch(self):
        source = "def parse(value):\n    return value\n"
        with tempfile.TemporaryDirectory() as tmp:
            transport = FakeGitHubTransport(push=False, source=source)
            adapter = GitHubCodeActionAdapter(
                branches_dir=str(Path(tmp) / "branches"), token="readonly-test-token",
                repository="acme/project", external_writes_enabled=True,
                project_mode="real", base_branch="main", http_client=transport,
                source_lookup=lambda repo, path: self._source_snapshot(repo, path, source),
            )
            result = self._create(adapter)
            self.assertEqual(result.execution_mode, "sandbox")
            self.assertTrue(result.branch_url.startswith("sandbox://"))
            self.assertEqual(len(transport.calls), 1)

    def test_sandbox_refuses_symlink_escape(self):
        source = "def parse(value):\n    return value\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "quality").symlink_to(outside, target_is_directory=True)
            adapter = self._sandbox(root, source)
            with self.assertRaises(CodeActionError):
                self._create(adapter)
            self.assertEqual(list(outside.iterdir()), [])

    def test_network_failure_is_not_reported_as_live_or_sandbox_success(self):
        import requests

        class UnavailableTransport:
            def request(self, *_args, **_kwargs):
                raise requests.ConnectionError("offline")

        source = "def parse(value):\n    return value\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            adapter = GitHubCodeActionAdapter(
                branches_dir=str(root), token="test-token", repository="acme/project",
                external_writes_enabled=True, project_mode="real", base_branch="main",
                http_client=UnavailableTransport(),
                source_lookup=lambda repo, path: self._source_snapshot(repo, path, source),
            )
            with self.assertRaises(CodeActionError) as caught:
                self._create(adapter)
            self.assertEqual(caught.exception.code, "github_no_disponible")
            self.assertFalse(root.exists())

    def test_disabled_or_fixture_mode_never_touches_github_transport(self):
        source = "def parse(value):\n    return value\n"
        with tempfile.TemporaryDirectory() as tmp:
            transport = FakeGitHubTransport()
            adapter = GitHubCodeActionAdapter(
                branches_dir=str(Path(tmp) / "branches"), token="test-token",
                repository="acme/project", external_writes_enabled=False,
                project_mode="real", http_client=transport,
                source_lookup=lambda repo, path: self._source_snapshot(repo, path, source),
            )
            self.assertEqual(self._create(adapter).execution_mode, "sandbox")
            self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
