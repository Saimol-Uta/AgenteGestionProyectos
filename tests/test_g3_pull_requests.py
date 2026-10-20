"""Deterministic, no-network contracts for controlled pull request publication."""

import base64
import hashlib
import inspect
import json
import tempfile
import unittest
from pathlib import Path

import requests

from agente_gestion.adapters.github_code_action_adapter import (
    CodeActionError,
    GitHubCodeActionAdapter,
)
from agente_gestion.domain.code_action import (
    BranchExecutionResult,
    CodeSourceSnapshot,
    PullRequestExecutionResult,
)
from agente_gestion.ports.code_action_port import CodeActionPort


REPO = "acme/project"
BASE_BRANCH = "develop"
HEAD_BRANCH = "quality/finding-001"
HEAD_COMMIT = "d" * 40
PATCH_SHA256 = hashlib.sha256(b"reviewed patch content").hexdigest()
FINDING_ID = "finding-001"
ACTOR = "gestor-local"
SOURCE = "def parse(value):\n    return value\n"


class _FakeGitHubHttp:
    """Injected transport: every call is recorded, nothing ever reaches the network."""

    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers, body=None):
        self.calls.append((method, url, dict(headers), body))
        if not self.responses:
            raise AssertionError(f"Unexpected synthetic request: {method} {url}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _pull_request_payload(number=42, head_sha=HEAD_COMMIT, state="open", head_ref=HEAD_BRANCH):
    return {
        "number": number,
        "html_url": f"https://github.com/{REPO}/pull/{number}",
        "state": state,
        "head": {"ref": head_ref, "sha": head_sha, "repo": {"full_name": REPO}},
        "base": {"ref": BASE_BRANCH},
    }


class PullRequestPublicationTests(unittest.TestCase):
    """Publication seam: simulated by default, live only behind the existing gate."""

    def _branch_result(self, mode="sandbox", commit=HEAD_COMMIT, branch=HEAD_BRANCH):
        if mode == "live":
            url = f"https://github.com/{REPO}/tree/{branch}"
        else:
            url = f"sandbox://branches/{branch}"
        return BranchExecutionResult(
            execution_mode=mode, branch_name=branch, branch_url=url, commit_sha_applied=commit,
        )

    def _sandbox_adapter(self, root):
        fake = _FakeGitHubHttp()
        adapter = GitHubCodeActionAdapter(
            branches_dir=str(root), repository=REPO, base_branch=BASE_BRANCH,
            external_writes_enabled=False, project_mode="fixtures", http_client=fake,
        )
        return adapter, fake

    def _live_adapter(self, root, responses):
        fake = _FakeGitHubHttp(responses)
        adapter = GitHubCodeActionAdapter(
            branches_dir=str(root), token="synthetic-offline-token", repository=REPO,
            base_branch=BASE_BRANCH, external_writes_enabled=True, project_mode="real",
            http_client=fake,
        )
        return adapter, fake

    def _publish(self, adapter, **overrides):
        arguments = {
            "repo": REPO, "base_branch": BASE_BRANCH,
            "branch_result": self._branch_result(), "finding_id": FINDING_ID,
            "actor": ACTOR, "patch_sha256": PATCH_SHA256,
        }
        arguments.update(overrides)
        return adapter.create_pull_request(**arguments)

    # ------------------------------------------------------------------ sandbox

    def test_sandbox_pull_request_is_explicitly_simulated_and_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            adapter, fake = self._sandbox_adapter(root)
            result = self._publish(adapter)
            self.assertIsInstance(result, PullRequestExecutionResult)
            self.assertEqual(result.execution_mode, "sandbox")
            self.assertEqual(result.state, "simulated")
            self.assertTrue(result.pull_request_url.startswith("sandbox://pulls/"))
            self.assertGreaterEqual(result.pull_request_number, 1)
            self.assertEqual(result.repository, REPO)
            self.assertEqual(result.base_branch, BASE_BRANCH)
            self.assertEqual(result.head_branch, HEAD_BRANCH)
            self.assertEqual(result.head_commit_sha, HEAD_COMMIT)
            self.assertFalse(result.recovered)
            self.assertEqual(fake.calls, [])
            manifests = list((root / ".pull_requests").glob("*.json"))
            self.assertEqual(len(manifests), 1)
            payload = json.loads(manifests[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["identity"], {
                "base_branch": BASE_BRANCH, "finding_id": FINDING_ID,
                "head_branch": HEAD_BRANCH, "head_commit_sha": HEAD_COMMIT,
                "patch_sha256": PATCH_SHA256, "repository": REPO,
            })

    def test_sandbox_pull_request_replay_is_idempotent_and_never_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            adapter, fake = self._sandbox_adapter(root)
            first = self._publish(adapter)
            second = self._publish(adapter)
            self.assertEqual(second.pull_request_number, first.pull_request_number)
            self.assertEqual(second.pull_request_url, first.pull_request_url)
            self.assertTrue(second.recovered)
            self.assertEqual(len(list((root / ".pull_requests").glob("*.json"))), 1)
            self.assertEqual(fake.calls, [])

    def test_sandbox_pull_request_refuses_a_second_publication_for_the_same_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            adapter, _fake = self._sandbox_adapter(root)
            self._publish(adapter)
            with self.assertRaises(CodeActionError) as caught:
                self._publish(adapter, patch_sha256=hashlib.sha256(b"other patch").hexdigest())
            self.assertEqual(caught.exception.code, "pull_request_duplicada")
            self.assertEqual(len(list((root / ".pull_requests").glob("*.json"))), 1)

    # -------------------------------------------------------------- validation

    def test_pull_request_requires_the_protected_develop_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            adapter, fake = self._sandbox_adapter(root)
            for base in ("main", "master", "release/backend", "DEVELOP"):
                with self.subTest(base=base), self.assertRaises(CodeActionError) as caught:
                    self._publish(adapter, base_branch=base)
                self.assertEqual(caught.exception.code, "rama_base_no_protegida")
            self.assertEqual(fake.calls, [])
            self.assertFalse(root.exists())

    def test_pull_request_refuses_a_protected_or_equal_head_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            adapter, fake = self._sandbox_adapter(root)
            for head in ("main", "master", "develop", BASE_BRANCH):
                with self.subTest(head=head), self.assertRaises(CodeActionError) as caught:
                    self._publish(adapter, branch_result=self._branch_result(branch=head))
                self.assertEqual(caught.exception.code, "rama_cabecera_invalida")
            self.assertEqual(fake.calls, [])
            self.assertFalse(root.exists())

    def test_pull_request_rejects_an_unbound_patch_or_incomplete_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            adapter, _fake = self._sandbox_adapter(root)
            cases = (
                ({"patch_sha256": "not-a-digest"}, "parche_invalido"),
                ({"patch_sha256": "zz" * 32}, "parche_invalido"),
                ({"patch_sha256": "ab" * 31}, "parche_invalido"),
                ({"finding_id": ""}, "hallazgo_invalido"),
                ({"actor": "   "}, "actor_invalido"),
                ({"repo": "not-a-repository"}, "repositorio_invalido"),
            )
            for overrides, expected_code in cases:
                with self.subTest(overrides=overrides), self.assertRaises(CodeActionError) as caught:
                    self._publish(adapter, **overrides)
                self.assertEqual(caught.exception.code, expected_code)
            self.assertFalse(root.exists())

    # -------------------------------------------------------------------- live

    def test_live_pull_request_opens_one_pr_against_develop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            adapter, fake = self._live_adapter(root, [
                (200, {"permissions": {"push": True}}),
                (200, {"object": {"sha": HEAD_COMMIT}}),
                (200, []),
                (201, _pull_request_payload()),
            ])
            result = self._publish(adapter, branch_result=self._branch_result(mode="live"))
            self.assertEqual(result.execution_mode, "live")
            self.assertEqual(result.state, "open")
            self.assertEqual(result.pull_request_number, 42)
            self.assertEqual(result.pull_request_url, f"https://github.com/{REPO}/pull/42")
            self.assertEqual(result.repository, REPO)
            self.assertEqual(result.base_branch, BASE_BRANCH)
            self.assertEqual(result.head_branch, HEAD_BRANCH)
            self.assertEqual(result.head_commit_sha, HEAD_COMMIT)
            self.assertFalse(result.recovered)
            self.assertEqual([call[0] for call in fake.calls], ["GET", "GET", "GET", "POST"])
            method, url, _headers, body = fake.calls[3]
            self.assertTrue(url.endswith("/pulls"), url)
            self.assertEqual(body["base"], BASE_BRANCH)
            self.assertEqual(body["head"], HEAD_BRANCH)
            self.assertIn(FINDING_ID, body["title"])
            self.assertIn(PATCH_SHA256, body["body"])

    def test_live_pull_request_recovers_the_existing_pr_without_a_second_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            adapter, fake = self._live_adapter(root, [
                (200, {"permissions": {"push": True}}),
                (200, {"object": {"sha": HEAD_COMMIT}}),
                (200, [_pull_request_payload(number=7)]),
            ])
            result = self._publish(adapter, branch_result=self._branch_result(mode="live"))
            self.assertTrue(result.recovered)
            self.assertEqual(result.pull_request_number, 7)
            self.assertEqual(result.pull_request_url, f"https://github.com/{REPO}/pull/7")
            self.assertEqual([call[0] for call in fake.calls], ["GET", "GET", "GET"])
            self.assertFalse(any(call[0] == "POST" for call in fake.calls))

    def test_live_pull_request_refuses_to_duplicate_a_pr_bound_to_another_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            adapter, fake = self._live_adapter(root, [
                (200, {"permissions": {"push": True}}),
                (200, {"object": {"sha": HEAD_COMMIT}}),
                (200, [_pull_request_payload(number=9, head_sha="e" * 40)]),
            ])
            with self.assertRaises(CodeActionError) as caught:
                self._publish(adapter, branch_result=self._branch_result(mode="live"))
            self.assertEqual(caught.exception.code, "pull_request_cabeza_inconsistente")
            self.assertFalse(any(call[0] == "POST" for call in fake.calls))

    def test_live_pull_request_validates_the_head_commit_before_creating(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            adapter, fake = self._live_adapter(root, [
                (200, {"permissions": {"push": True}}),
                (200, {"object": {"sha": "f" * 40}}),
            ])
            with self.assertRaises(CodeActionError) as caught:
                self._publish(adapter, branch_result=self._branch_result(mode="live"))
            self.assertEqual(caught.exception.code, "pull_request_cabeza_inconsistente")
            self.assertEqual([call[0] for call in fake.calls], ["GET", "GET"])

    def test_live_pull_request_unknown_outcome_never_claims_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            adapter, fake = self._live_adapter(root, [
                (200, {"permissions": {"push": True}}),
                (200, {"object": {"sha": HEAD_COMMIT}}),
                (200, []),
                requests.ConnectionError("offline"),
            ])
            with self.assertRaises(CodeActionError) as caught:
                self._publish(adapter, branch_result=self._branch_result(mode="live"))
            self.assertEqual(caught.exception.code, "pull_request_no_confirmado")
            self.assertEqual(caught.exception.status_code, 503)
            self.assertEqual([call[0] for call in fake.calls], ["GET", "GET", "GET", "POST"])
            self.assertEqual(len([call for call in fake.calls if call[0] == "POST"]), 1)

    def test_live_pull_request_recovers_after_an_unknown_outcome_without_duplicating(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            adapter, fake = self._live_adapter(root, [
                (200, {"permissions": {"push": True}}),
                (200, {"object": {"sha": HEAD_COMMIT}}),
                (200, []),
                requests.ConnectionError("offline"),
            ])
            with self.assertRaises(CodeActionError):
                self._publish(adapter, branch_result=self._branch_result(mode="live"))
            retry, retry_fake = self._live_adapter(root, [
                (200, {"permissions": {"push": True}}),
                (200, {"object": {"sha": HEAD_COMMIT}}),
                (200, [_pull_request_payload(number=11)]),
            ])
            recovered = self._publish(retry, branch_result=self._branch_result(mode="live"))
            self.assertTrue(recovered.recovered)
            self.assertEqual(recovered.pull_request_number, 11)
            self.assertFalse(any(call[0] == "POST" for call in retry_fake.calls))
            self.assertEqual(len([call for call in fake.calls if call[0] == "POST"]), 1)

    def test_live_pull_request_refuses_a_server_error_as_an_unknown_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            adapter, fake = self._live_adapter(root, [
                (200, {"permissions": {"push": True}}),
                (200, {"object": {"sha": HEAD_COMMIT}}),
                (200, []),
                (502, {"message": "bad gateway"}),
            ])
            with self.assertRaises(CodeActionError) as caught:
                self._publish(adapter, branch_result=self._branch_result(mode="live"))
            self.assertEqual(caught.exception.code, "pull_request_no_confirmado")
            self.assertEqual(len([call for call in fake.calls if call[0] == "POST"]), 1)

    def test_live_pull_request_reports_a_rejected_creation_without_claiming_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            adapter, fake = self._live_adapter(root, [
                (200, {"permissions": {"push": True}}),
                (200, {"object": {"sha": HEAD_COMMIT}}),
                (200, []),
                (422, {"message": "No commits between develop and quality/finding-001"}),
            ])
            with self.assertRaises(CodeActionError) as caught:
                self._publish(adapter, branch_result=self._branch_result(mode="live"))
            self.assertEqual(caught.exception.code, "pull_request_no_creado")
            self.assertEqual(len([call for call in fake.calls if call[0] == "POST"]), 1)

    def test_live_pull_request_requires_push_permission_and_the_open_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            adapter, fake = self._live_adapter(root, [
                (200, {"permissions": {"push": False}}),
            ])
            with self.assertRaises(CodeActionError) as caught:
                self._publish(adapter, branch_result=self._branch_result(mode="live"))
            self.assertEqual(caught.exception.code, "permiso_github_insuficiente")
            self.assertEqual([call[0] for call in fake.calls], ["GET"])

            closed_fake = _FakeGitHubHttp()
            closed = GitHubCodeActionAdapter(
                branches_dir=str(root), token="synthetic-offline-token", repository=REPO,
                base_branch=BASE_BRANCH, external_writes_enabled=False, project_mode="real",
                http_client=closed_fake,
            )
            with self.assertRaises(CodeActionError) as gate:
                self._publish(closed, branch_result=self._branch_result(mode="live"))
            self.assertEqual(gate.exception.code, "publicacion_no_habilitada")
            self.assertEqual(closed_fake.calls, [])

    def test_sandbox_branch_result_is_never_published_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            fake = _FakeGitHubHttp()
            adapter = GitHubCodeActionAdapter(
                branches_dir=str(root), token="synthetic-offline-token", repository=REPO,
                base_branch=BASE_BRANCH, external_writes_enabled=True, project_mode="real",
                http_client=fake,
            )
            result = self._publish(adapter, branch_result=self._branch_result(mode="sandbox"))
            self.assertEqual(result.execution_mode, "sandbox")
            self.assertEqual(result.state, "simulated")
            self.assertEqual(fake.calls, [])

    # -------------------------------------------------------------- contract

    def test_code_action_port_declares_a_keyword_only_pull_request_operation(self):
        signature = inspect.signature(CodeActionPort.create_pull_request)
        names = [name for name in signature.parameters if name != "self"]
        self.assertEqual(sorted(names), sorted([
            "repo", "base_branch", "branch_result", "finding_id", "actor", "patch_sha256",
        ]))
        for name in names:
            self.assertIs(
                signature.parameters[name].kind, inspect.Parameter.KEYWORD_ONLY, name,
            )
        self.assertIn("create_pull_request", CodeActionPort.__abstractmethods__)

    def test_pull_request_result_rejects_an_untyped_or_ambiguous_state(self):
        with self.assertRaises(Exception):
            PullRequestExecutionResult(
                execution_mode="sandbox", pull_request_number=1,
                pull_request_url="https://github.com/acme/project/pull/1", state="open",
                repository=REPO, base_branch=BASE_BRANCH, head_branch=HEAD_BRANCH,
                head_commit_sha=HEAD_COMMIT,
            )
        with self.assertRaises(Exception):
            PullRequestExecutionResult(
                execution_mode="live", pull_request_number=1,
                pull_request_url="sandbox://pulls/acme/project/1", state="open",
                repository=REPO, base_branch=BASE_BRANCH, head_branch=HEAD_BRANCH,
                head_commit_sha=HEAD_COMMIT,
            )

    # ------------------------------------------------------------- preserve

    @staticmethod
    def _source_lookup(source):
        def lookup(repo, file_path):
            return CodeSourceSnapshot(
                content=source, is_complete=True, repository=repo, commit_sha=HEAD_COMMIT,
                file_path=file_path,
                source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
                total_lines=len(source.splitlines()),
            )
        return lookup

    def test_legacy_sandbox_branch_operation_is_preserved(self):
        source = SOURCE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            fake = _FakeGitHubHttp()
            adapter = GitHubCodeActionAdapter(
                branches_dir=str(root), repository=REPO, base_branch=BASE_BRANCH,
                external_writes_enabled=False, project_mode="fixtures", http_client=fake,
                source_lookup=self._source_lookup(source),
            )
            result = adapter.create_verification_branch(
                repo=REPO, base_branch=BASE_BRANCH, target_branch=HEAD_BRANCH,
                file_path="src/parse.py", original_excerpt="    return value",
                suggested_code="    return value.strip()", finding_id=FINDING_ID, actor=ACTOR,
            )
            self.assertEqual(result.execution_mode, "sandbox")
            self.assertEqual(result.branch_name, HEAD_BRANCH)
            self.assertEqual(result.branch_url, f"sandbox://branches/{HEAD_BRANCH}")
            self.assertEqual(len(result.commit_sha_applied), 64)
            written = root / HEAD_BRANCH / "src" / "parse.py"
            self.assertEqual(
                written.read_text(encoding="utf-8"),
                "def parse(value):\n    return value.strip()\n",
            )
            self.assertEqual(fake.calls, [])

    def test_legacy_live_branch_operation_is_preserved(self):
        source = SOURCE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            fake = _FakeGitHubHttp([
                (200, {"permissions": {"push": True}}),
                (200, {"object": {"sha": HEAD_COMMIT}}),
                (200, {
                    "encoding": "base64",
                    "content": base64.b64encode(source.encode("utf-8")).decode("ascii"),
                    "sha": "b" * 40,
                }),
                (201, {"ref": f"refs/heads/{HEAD_BRANCH}"}),
                (201, {"commit": {"sha": HEAD_COMMIT}}),
            ])
            adapter = GitHubCodeActionAdapter(
                branches_dir=str(root), token="synthetic-offline-token", repository=REPO,
                base_branch=BASE_BRANCH, external_writes_enabled=True, project_mode="real",
                http_client=fake, source_lookup=self._source_lookup(source),
            )
            result = adapter.create_verification_branch(
                repo=REPO, base_branch=BASE_BRANCH, target_branch=HEAD_BRANCH,
                file_path="src/parse.py", original_excerpt="    return value",
                suggested_code="    return value.strip()", finding_id=FINDING_ID, actor=ACTOR,
            )
            self.assertEqual(result.execution_mode, "live")
            self.assertEqual(result.commit_sha_applied, HEAD_COMMIT)
            self.assertEqual(result.branch_url, f"https://github.com/{REPO}/tree/{HEAD_BRANCH}")
            self.assertEqual([call[0] for call in fake.calls], ["GET", "GET", "GET", "POST", "PUT"])

    # ------------------------------------------------------------- partial

    def test_partial_publication_failure_preserves_the_branch_and_recovers(self):
        source = SOURCE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            fake = _FakeGitHubHttp()
            adapter = GitHubCodeActionAdapter(
                branches_dir=str(root), repository=REPO, base_branch=BASE_BRANCH,
                external_writes_enabled=False, project_mode="fixtures", http_client=fake,
                source_lookup=self._source_lookup(source),
            )
            branch = adapter.create_verification_branch(
                repo=REPO, base_branch=BASE_BRANCH, target_branch=HEAD_BRANCH,
                file_path="src/parse.py", original_excerpt="    return value",
                suggested_code="    return value.strip()", finding_id=FINDING_ID, actor=ACTOR,
            )
            written = root / HEAD_BRANCH / "src" / "parse.py"
            self.assertTrue(written.exists())
            with self.assertRaises(CodeActionError) as caught:
                self._publish(adapter, branch_result=branch, patch_sha256="zz" * 32)
            self.assertEqual(caught.exception.code, "parche_invalido")
            self.assertEqual(
                written.read_text(encoding="utf-8"),
                "def parse(value):\n    return value.strip()\n",
            )
            self.assertEqual(branch.branch_name, HEAD_BRANCH)
            self.assertEqual(len(branch.commit_sha_applied), 64)

            result = self._publish(adapter, branch_result=branch)
            self.assertEqual(result.state, "simulated")
            self.assertEqual(result.head_branch, branch.branch_name)
            self.assertEqual(result.head_commit_sha, branch.commit_sha_applied)
            replay = self._publish(adapter, branch_result=branch)
            self.assertTrue(replay.recovered)
            self.assertEqual(replay.pull_request_number, result.pull_request_number)
            self.assertEqual(len(list((root / ".pull_requests").glob("*.json"))), 1)
            self.assertEqual(fake.calls, [])

    def test_live_partial_publication_keeps_the_branch_and_recovers_without_duplicating(self):
        source = SOURCE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "branches"
            fake = _FakeGitHubHttp([
                (200, {"permissions": {"push": True}}),
                (200, {"object": {"sha": HEAD_COMMIT}}),
                (200, {
                    "encoding": "base64",
                    "content": base64.b64encode(source.encode("utf-8")).decode("ascii"),
                    "sha": "b" * 40,
                }),
                (201, {"ref": f"refs/heads/{HEAD_BRANCH}"}),
                (201, {"commit": {"sha": HEAD_COMMIT}}),
                (200, {"permissions": {"push": True}}),
                (200, {"object": {"sha": HEAD_COMMIT}}),
                (200, []),
                requests.ConnectionError("offline"),
            ])
            adapter = GitHubCodeActionAdapter(
                branches_dir=str(root), token="synthetic-offline-token", repository=REPO,
                base_branch=BASE_BRANCH, external_writes_enabled=True, project_mode="real",
                http_client=fake, source_lookup=self._source_lookup(source),
            )
            branch = adapter.create_verification_branch(
                repo=REPO, base_branch=BASE_BRANCH, target_branch=HEAD_BRANCH,
                file_path="src/parse.py", original_excerpt="    return value",
                suggested_code="    return value.strip()", finding_id=FINDING_ID, actor=ACTOR,
            )
            self.assertEqual(branch.execution_mode, "live")
            self.assertEqual(branch.commit_sha_applied, HEAD_COMMIT)
            with self.assertRaises(CodeActionError) as caught:
                self._publish(adapter, branch_result=branch)
            self.assertEqual(caught.exception.code, "pull_request_no_confirmado")
            self.assertEqual(branch.commit_sha_applied, HEAD_COMMIT)
            self.assertEqual(
                len([call for call in fake.calls if call[0] == "POST" and call[1].endswith("/pulls")]), 1,
            )
            retry, retry_fake = self._live_adapter(root, [
                (200, {"permissions": {"push": True}}),
                (200, {"object": {"sha": HEAD_COMMIT}}),
                (200, [_pull_request_payload(number=13)]),
            ])
            recovered = self._publish(retry, branch_result=branch)
            self.assertTrue(recovered.recovered)
            self.assertEqual(recovered.pull_request_number, 13)
            self.assertFalse(any(call[0] == "POST" for call in retry_fake.calls))


if __name__ == "__main__":
    unittest.main()
