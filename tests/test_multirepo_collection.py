"""Deterministic public contracts for repository-scoped source collection and budgets."""

import base64
import io
import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from unittest.mock import patch

from agente_gestion.application.config import Settings
from agente_gestion.domain.ci_metrics import CiArtifactOutcome
from agente_gestion.adapters.sources.fixtures import FixtureSourcesAdapter
from agente_gestion.domain.code import CIRunResult, CodeDiffSnippet
from agente_gestion.domain.evidence import EvidenceItem
from agente_gestion.domain.policies import QualityRule
from agente_gestion.domain.repositories import (
    CommitResolutionStatus,
    RepositoryAnalysisState,
    RepositoryIdentity,
    RepositoryProvenance,
    RepositoryAccessStatus,
    RepositoryAvailabilityStatus,
    RepositoryConfiguration,
)
from agente_gestion.domain.tasks import TaskCard
from agente_gestion.ports.source_collection import RepositorySourceCollectionResult
from agente_gestion.ports.source_artifacts import BinaryHttpResponse
from agente_gestion.ports.source_errors import SourceError, SourceErrorCategory, SourceProvider
from agente_gestion.adapters.sources.collection_budget import (
    BudgetedHttpGetTransport,
    CollectionBudgetLedger,
)
from agente_gestion.adapters.sources.http_transport import BoundedGetTransport, HttpResponse
from agente_gestion.adapters.sources.factory import (
    create_analysis_composite_source,
    create_ci_source,
    create_code_source,
    create_composite_source,
)
from agente_gestion.adapters.sources.multirepo import MultiRepoSourcesAdapter


SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 64
_NOW = "2026-10-07T12:00:00Z"


def _repo(repository_id: str, name: str, *, sha: str | None = None) -> RepositoryConfiguration:
    return RepositoryConfiguration(
        id=repository_id,
        owner="example-org",
        repo=name,
        branch="develop",
        commit_sha=sha,
        quality_workflow_path=".github/workflows/quality.yml",
    )


def _run(run_id: int, sha: str) -> dict[str, Any]:
    return {
        "id": run_id,
        "name": "Quality",
        "head_sha": sha,
        "head_branch": "develop",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "updated_at": _NOW,
        "created_at": _NOW,
        "run_started_at": _NOW,
        "workflow_id": run_id,
    }


class ScriptedTransport:
    """GET-only fake returning deterministic commit, file, and Actions responses."""

    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self.write_calls: list[str] = []
        self.branch_shas: dict[str, list[str]] = {}
        self.branch_call_counts: dict[str, int] = {}
        self.diff_sha_overrides: dict[str, str] = {}
        self.actions_shas: dict[str, list[str]] = {}
        self.actions_sequences: dict[str, list[list[str]]] = {}
        self.actions_call_counts: dict[str, int] = {}
        self.failures: dict[str, SourceErrorCategory] = {}
        self.diff_files_overrides: dict[str, list[dict[str, Any]]] = {}

    @staticmethod
    def _response(body: dict[str, Any] | list[Any], *, status: int = 200) -> HttpResponse:
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return HttpResponse(status_code=status, body=body, bytes_received=len(raw))

    @staticmethod
    def _repo_parts(url: str) -> tuple[str, str, str]:
        parts = urlsplit(url).path.split("/")
        # /repos/{owner}/{repo}/...
        return parts[2], parts[3], "/" + "/".join(parts[4:])

    def get(self, url, params=None, headers=None, timeout_seconds=None):
        parsed = urlsplit(url)
        owner, repo, endpoint = self._repo_parts(url)
        full_name = f"{owner}/{repo}"
        self.calls.append({"url": url, "params": params, "method": "GET"})

        failure = self.failures.get(full_name)
        if failure is not None and endpoint.endswith("/commits/develop"):
            provider = SourceProvider.github
            raise SourceError(provider, failure)

        if endpoint.startswith("/commits/") and params is None:
            ref = endpoint.rsplit("/", 1)[-1]
            if ref == "develop":
                values = self.branch_shas.get(full_name, [SHA_A])
                index = self.branch_call_counts.get(full_name, 0)
                self.branch_call_counts[full_name] = index + 1
                sha = values[min(index, len(values) - 1)]
            else:
                sha = ref
            return self._response({"sha": sha, "files": []})

        if endpoint.startswith("/commits/") and isinstance(params, dict):
            requested = endpoint.rsplit("/", 1)[-1]
            sha = self.diff_sha_overrides.get(full_name, requested)
            files = self.diff_files_overrides.get(full_name, [self._file_entry()])
            return self._response({"sha": sha, "files": files})

        if endpoint.startswith("/contents/"):
            content = b"new source line\n"
            return self._response({
                "encoding": "base64",
                "content": base64.b64encode(content).decode("ascii"),
                "sha": "d" * 40,
                "size": len(content),
            })

        if endpoint == "/actions/runs":
            target = (params or {}).get("head_sha")
            if full_name in self.actions_sequences:
                call_index = self.actions_call_counts.get(full_name, 0)
                sequence = self.actions_sequences[full_name]
                run_shas = sequence[min(call_index, len(sequence) - 1)]
                self.actions_call_counts[full_name] = call_index + 1
            else:
                run_shas = self.actions_shas.get(full_name, [target] if target else [])
            runs = [_run(1000 + index, sha) for index, sha in enumerate(run_shas)]
            return self._response({"total_count": len(runs), "workflow_runs": runs})

        raise AssertionError(f"Unexpected GET request: {parsed.path} {params!r}")

    @staticmethod
    def _file_entry() -> dict[str, Any]:
        return {
            "filename": "src/shared.py",
            "status": "modified",
            "patch": "@@ -1 +1 @@\n-old\n+new",
            "sha": "d" * 40,
        }


class ArtifactEnabledMultiRepoTransport(ScriptedTransport):
    """Fakes the production JSON and binary ports for two source-bound artifacts."""

    FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ci_metrics"
    METRICS = {
        "example-org/backend": (SHA_A, "12345", 2, 77, "backend-v1.json"),
        "example-org/frontend": (SHA_B, "23456", 1, 88, "frontend-v1.json"),
    }

    def __init__(self):
        super().__init__()
        self.artifact_calls: list[dict[str, Any]] = []
        self.binary_calls: list[dict[str, Any]] = []
        self._bundles = {}

    def get(self, url, params=None, headers=None, timeout_seconds=None):
        owner, repo, endpoint = self._repo_parts(url)
        full_name = f"{owner}/{repo}"
        if endpoint.startswith("/actions/artifacts/") and endpoint.endswith("/zip"):
            return self.get_binary(url, params=params, headers=headers, timeout_seconds=timeout_seconds)
        if endpoint == "/actions/runs":
            sha, run_id, attempt, _artifact_id, _fixture = self.METRICS[full_name]
            quality_path = (".github/workflows/backend-quality.yml" if repo == "backend"
                            else ".github/workflows/frontend-quality.yml")
            quality_row = {
                "id": int(run_id),
                "name": "Quality",
                "head_sha": sha,
                "head_branch": "develop",
                "event": "push",
                "workflow_id": int(run_id),
                "status": "completed",
                "conclusion": "success",
                "updated_at": "2026-10-16T10:00:00Z",
                "created_at": "2026-10-16T09:55:00Z",
                "run_started_at": "2026-10-16T09:55:01Z",
                "run_attempt": attempt,
                "path": quality_path,
            }
            decoy_id = int(run_id) - 1
            decoy_row = {
                **quality_row,
                "id": decoy_id,
                "workflow_id": decoy_id,
                "name": "Other",
                "run_attempt": 9,
                "updated_at": "2026-10-16T10:01:00Z",
                "path": ".github/workflows/unrelated.yml",
            }
            self.calls.append({"url": url, "params": params, "method": "GET"})
            return self._response({"total_count": 2, "workflow_runs": [decoy_row, quality_row]})
        if endpoint.startswith("/actions/runs/") and endpoint.endswith("/artifacts"):
            sha, run_id, attempt, artifact_id, _fixture = self.METRICS[full_name]
            self.artifact_calls.append({"url": url, "params": params, "repo": full_name})
            self.calls.append({"url": url, "params": params, "method": "GET"})
            row = {
                "id": artifact_id,
                "name": f"ci-quality-{sha}-{run_id}-{attempt}",
                "expired": False,
                "workflow_run": {
                    "id": int(run_id),
                    "head_sha": sha,
                    "head_repository": {"full_name": full_name},
                    "run_attempt": attempt,
                },
            }
            return self._response({"total_count": 1, "artifacts": [row]})
        return super().get(url, params=params, headers=headers, timeout_seconds=timeout_seconds)

    def get_binary(self, url, params=None, headers=None, timeout_seconds=None):
        parts = urlsplit(url).path.split("/")
        owner, repo, artifact_id = parts[2], parts[3], int(parts[6])
        full_name = f"{owner}/{repo}"
        sha, run_id, attempt, expected_artifact_id, fixture_name = self.METRICS[full_name]
        if artifact_id != expected_artifact_id or params is not None:
            raise AssertionError("unexpected binary artifact request")
        self.binary_calls.append({"url": url, "headers": dict(headers or {})})
        bundle = self._bundles.get(full_name)
        if bundle is None:
            document = json.dumps(json.loads((self.FIXTURES / fixture_name).read_text(encoding="utf-8")))
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
                zipped.writestr("reports/ci-metrics.json", document.encode("utf-8"))
            bundle = archive.getvalue()
            self._bundles[full_name] = bundle
        return BinaryHttpResponse(
            status_code=200,
            headers={},
            body=bundle,
            bytes_received=len(bundle),
        )


class _BudgetStreamResponse:
    status_code = 200

    def __init__(self, body: bytes, headers: dict[str, str] | None = None):
        self.body = body
        self.headers = headers or {}
        self.closed = False
        self.bytes_yielded = 0

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self.body), chunk_size):
            chunk = self.body[offset:offset + chunk_size]
            self.bytes_yielded += len(chunk)
            yield chunk

    def close(self):
        self.closed = True


class _BudgetStreamSession:
    def __init__(self, response):
        self.response = response
        self.closed = False
        self.trust_env = True
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.response

    def close(self):
        self.closed = True


class CountingTaskSource:
    def __init__(self):
        self.task_calls = 0
        self.evidence_calls = 0

    def get_tasks(self, project_id: str) -> list[TaskCard]:
        self.task_calls += 1
        return []

    def get_evidence(self, project_id: str) -> list[EvidenceItem]:
        self.evidence_calls += 1
        return []


class CountingPolicySource:
    def __init__(self):
        self.calls = 0

    def get_policies(self, project_id: str) -> list[QualityRule]:
        self.calls += 1
        return []


def _settings(repositories, **values) -> Settings:
    defaults = {
        "project_id": "multirepo-test",
        "source_mode": "real",
        "github_repositories": tuple(repositories),
        "http_timeout_seconds": 2.0,
        "http_max_response_bytes": 1024 * 1024,
        "policies_path": "/isolated/no-policy-file.yaml",
    }
    defaults.update(values)
    return Settings(**defaults)


class MultiRepoCollectionTests(unittest.TestCase):
    def setUp(self):
        self.transport = ScriptedTransport()
        self.tasks = CountingTaskSource()
        self.policies = CountingPolicySource()
        self.now = datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)
        self.factory_patches = [
            patch("agente_gestion.adapters.sources.factory.create_task_source", return_value=self.tasks),
            patch("agente_gestion.adapters.sources.factory.create_policy_source", return_value=self.policies),
        ]
        for item in self.factory_patches:
            item.start()
            self.addCleanup(item.stop)

    def collect(self, settings, *, clock=None):
        source = create_composite_source(settings, transport=self.transport, **({"clock": clock} if clock else {}))
        return source.collect_snapshot(settings.project_id, self.now)

    def test_two_develop_repositories_resolve_and_collect_independent_commits(self):
        self.transport.branch_shas = {
            "example-org/backend": [SHA_A],
            "example-org/frontend": [SHA_B],
        }
        self.transport.actions_shas = {
            "example-org/backend": [SHA_A],
            "example-org/frontend": [SHA_B],
        }
        result = self.collect(_settings([_repo("backend", "backend"), _repo("frontend", "frontend")]))

        self.assertEqual(result.target_commit_sha, None, "a multi-repository result must not expose a shared SHA")
        self.assertFalse(result.snapshot.code_diffs)
        self.assertFalse(result.snapshot.ci_runs)
        self.assertEqual(
            [(item.repository_state.identity.id, item.repository_state.target_commit_sha)
             for item in result.repository_results],
            [("backend", SHA_A), ("frontend", SHA_B)],
        )
        self.assertEqual(
            [(item.repository_state.identity.id, item.repository_state.target_commit_sha)
             for item in result.snapshot.repository_results],
            [("backend", SHA_A), ("frontend", SHA_B)],
        )
        for scoped in result.repository_results:
            self.assertTrue(scoped.code_diffs)
            self.assertEqual({diff.commit_sha for diff in scoped.code_diffs}, {scoped.repository_state.target_commit_sha})
            self.assertEqual({run.commit_sha for run in scoped.ci_runs}, {scoped.repository_state.target_commit_sha})
            self.assertTrue(all(diff.repository_identity == scoped.repository_state.identity for diff in scoped.code_diffs))
            self.assertTrue(all(run.repository_identity == scoped.repository_state.identity for run in scoped.ci_runs))
            self.assertTrue(all(run.provenance.commit_sha == scoped.repository_state.target_commit_sha
                                and run.provenance.repository_identity == scoped.repository_state.identity
                                for run in scoped.ci_runs))
            self.assertTrue(all(record.repository_identity == scoped.repository_state.identity for record in scoped.records))
            self.assertTrue(all(record.target_commit_sha == scoped.repository_state.target_commit_sha for record in scoped.records))
        self.assertEqual(self.transport.branch_call_counts, {"example-org/backend": 1, "example-org/frontend": 1})
        self.assertEqual([call["params"]["head_sha"] for call in self.transport.calls if call["params"] and "head_sha" in call["params"]], [SHA_A, SHA_B])
        self.assertEqual(self.transport.write_calls, [])
        self.assertTrue(all(call["method"] == "GET" for call in self.transport.calls))
        self.assertTrue(all(record.repository_identity is None for record in result.records))

    def test_partial_backend_code_keeps_ci_provenance_and_isolates_frontend(self):
        class PartialBackendTransport(ScriptedTransport):
            def get(inner_self, url, params=None, headers=None, timeout_seconds=None):
                _owner, repo, endpoint = inner_self._repo_parts(url)
                if repo == "backend" and endpoint.startswith("/commits/") and isinstance(params, dict):
                    inner_self.calls.append({"url": url, "params": params, "method": "GET"})
                    return inner_self._response({
                        "sha": SHA_A,
                        "files": [
                            {"filename": "styles/site.css", "status": "modified", "patch": None},
                            {"filename": "images/logo.png", "status": "added", "patch": None},
                            {
                                "filename": "src/backend.py", "status": "modified",
                                "patch": "@@ -1 +1 @@\n-old\n+new\n",
                                "sha": "d" * 40,
                            },
                        ],
                    })
                if repo == "backend" and endpoint.startswith("/contents/"):
                    inner_self.calls.append({"url": url, "params": params, "method": "GET"})
                    return inner_self._response({}, status=404)
                if repo == "backend" and endpoint == "/actions/runs":
                    inner_self.calls.append({"url": url, "params": params, "method": "GET"})
                    run = _run(1200, SHA_A)
                    run.update({
                        "conclusion": "failure",
                        "path": ".github/workflows/quality.yml",
                        "run_attempt": 3,
                    })
                    return inner_self._response({"total_count": 1, "workflow_runs": [run]})
                return super(PartialBackendTransport, inner_self).get(
                    url, params=params, headers=headers, timeout_seconds=timeout_seconds,
                )

        self.transport = PartialBackendTransport()
        self.transport.branch_shas = {
            "example-org/backend": [SHA_A],
            "example-org/frontend": [SHA_B],
        }
        original_consume = CollectionBudgetLedger.consume_estimated_tokens
        token_charges = []

        def track_token_charge(ledger, identity, amount):
            token_charges.append(identity.id if identity is not None else None)
            return original_consume(ledger, identity, amount)

        with patch.object(CollectionBudgetLedger, "consume_estimated_tokens", new=track_token_charge):
            result = self.collect(_settings([
                _repo("backend", "backend"), _repo("frontend", "frontend"),
            ]))

        backend, frontend = result.repository_results
        self.assertFalse(backend.is_complete)
        self.assertIn("styles/site.css", backend.incomplete_reason)
        self.assertIn("images/logo.png", backend.incomplete_reason)
        self.assertTrue(frontend.is_complete)
        self.assertEqual([item.file_path for item in backend.code_diffs], ["src/backend.py"])
        self.assertTrue(frontend.code_diffs)
        backend_file_evidence = {
            item.provenance.file_path: item for item in backend.evidence
            if item.provenance.file_path in {"styles/site.css", "images/logo.png"}
        }
        self.assertEqual(set(backend_file_evidence), {"styles/site.css", "images/logo.png"})
        for item in backend_file_evidence.values():
            self.assertFalse(item.provenance.payload["patch_available"])
            self.assertEqual(item.provenance.payload["reason"], "patch_unavailable")
        self.assertEqual(backend.budget_receipt.files, 3)
        self.assertEqual(frontend.budget_receipt.files, 1)
        self.assertFalse(backend.budget_receipt.exhausted)
        self.assertFalse(frontend.budget_receipt.exhausted)
        self.assertEqual(token_charges, [None, "backend", "backend", "frontend", "frontend"])

        backend_ci = backend.ci_runs[0]
        self.assertEqual(backend_ci.id, "1200")
        self.assertEqual(backend_ci.status, "failure")
        self.assertEqual(backend_ci.workflow_path, ".github/workflows/quality.yml")
        self.assertEqual(backend_ci.run_attempt, 3)
        self.assertEqual(backend_ci.provenance.repository_identity, backend.repository_state.identity)
        self.assertEqual(backend_ci.provenance.commit_sha, SHA_A)
        self.assertEqual(backend_ci.provenance.workflow_path, ".github/workflows/quality.yml")
        self.assertEqual(backend_ci.provenance.run_id, "1200")
        self.assertEqual(backend_ci.provenance.run_attempt, 3)
        self.assertEqual(
            [call["params"]["head_sha"] for call in self.transport.calls
             if call["params"] and "head_sha" in call["params"]],
            [SHA_A, SHA_B],
        )

    def test_explicit_pins_are_independent_and_skip_branch_resolution(self):
        self.transport.actions_shas = {"example-org/backend": [SHA_A], "example-org/frontend": [SHA_C]}
        result = self.collect(_settings([
            _repo("backend", "backend", sha=SHA_A),
            _repo("frontend", "frontend", sha=SHA_C),
        ]))

        self.assertEqual([item.repository_state.target_commit_sha for item in result.repository_results], [SHA_A, SHA_C])
        self.assertEqual(self.transport.branch_call_counts, {})
        commit_requests = [call for call in self.transport.calls if "/commits/" in call["url"]]
        self.assertTrue(commit_requests)
        self.assertTrue(all(SHA_A in call["url"] or SHA_C in call["url"] for call in commit_requests))
        self.assertEqual([call["params"]["head_sha"] for call in self.transport.calls if call["params"] and "head_sha" in call["params"]], [SHA_A, SHA_C])

    def test_reused_collection_reresolves_advancing_branch_without_mixing_a_run(self):
        self.transport.branch_shas = {"example-org/backend": [SHA_A, SHA_B]}
        self.transport.actions_sequences = {"example-org/backend": [[SHA_A], [SHA_B]]}
        source = create_composite_source(_settings([_repo("backend", "backend")]), transport=self.transport)

        first = source.collect_snapshot("multirepo-test", self.now)
        second = source.collect_snapshot("multirepo-test", self.now)

        self.assertEqual(first.repository_results[0].repository_state.target_commit_sha, SHA_A)
        self.assertEqual(second.repository_results[0].repository_state.target_commit_sha, SHA_B)
        for result, expected in ((first, SHA_A), (second, SHA_B)):
            scoped = result.repository_results[0]
            self.assertEqual({item.commit_sha for item in scoped.code_diffs}, {expected})
            self.assertEqual({item.commit_sha for item in scoped.ci_runs}, {expected})
            self.assertEqual(scoped.repository_state.target_commit_sha, expected)
        action_targets = [call["params"]["head_sha"] for call in self.transport.calls if call["params"] and "head_sha" in call["params"]]
        self.assertEqual(action_targets, [SHA_A, SHA_B])
        self.assertEqual(self.transport.branch_call_counts["example-org/backend"], 2)

    def test_same_sha_and_path_in_different_repositories_has_unique_evidence_identity(self):
        self.transport.branch_shas = {"example-org/backend": [SHA_A], "example-org/frontend": [SHA_A]}
        self.transport.actions_shas = {"example-org/backend": [SHA_A], "example-org/frontend": [SHA_A]}
        result = self.collect(_settings([_repo("backend", "backend"), _repo("frontend", "frontend")]))

        ids = [item.provenance.id for scoped in result.repository_results for item in scoped.evidence]
        self.assertEqual(len(ids), len(set(ids)))
        for scoped in result.repository_results:
            identity = scoped.repository_state.identity
            for diff in scoped.code_diffs:
                self.assertEqual(diff.file_path, "src/shared.py")
                self.assertEqual(diff.repository, f"{identity.owner}/{identity.repo}")
                self.assertEqual(diff.provenance.repository_identity, identity)
                self.assertEqual(diff.provenance.commit_sha, SHA_A)
            for evidence in scoped.evidence:
                self.assertIn(f"{identity.id}:", evidence.provenance.id)
                self.assertEqual(evidence.provenance.repository, f"{identity.owner}/{identity.repo}")
                self.assertEqual(evidence.provenance.commit_sha, SHA_A)

    def test_scoped_result_rejects_contradictory_child_identity_instead_of_replacing_it(self):
        identity = RepositoryIdentity(id="backend", owner="example-org", repo="backend")
        other = RepositoryIdentity(id="frontend", owner="example-org", repo="backend")
        state = RepositoryAnalysisState(
            identity=identity,
            branch="develop",
            target_commit_sha=SHA_A,
            commit_resolution_status=CommitResolutionStatus.resolved,
        )
        diff = CodeDiffSnippet(
            repository="example-org/backend",
            commit_sha=SHA_A,
            file_path="src/shared.py",
            lines=[1],
            content="line",
            repository_identity=other,
            provenance=RepositoryProvenance(
                repository_identity=other, commit_sha=SHA_A, source="github",
            ),
        )

        with self.assertRaisesRegex(ValueError, "identity"):
            RepositorySourceCollectionResult(
                repository_state=state, code_diffs=[diff], is_complete=False,
            )

    def test_code_response_sha_change_is_rejected_and_actions_are_not_queried(self):
        self.transport.branch_shas = {"example-org/backend": [SHA_A]}
        self.transport.diff_sha_overrides = {"example-org/backend": SHA_B}
        result = self.collect(_settings([_repo("backend", "backend")]))

        scoped = result.repository_results[0]
        self.assertEqual(scoped.repository_state.target_commit_sha, SHA_A)
        self.assertEqual(scoped.repository_state.commit_resolution_status, CommitResolutionStatus.resolved)
        self.assertFalse(scoped.code_diffs)
        code_record = next(record for record in scoped.records if record.provider == SourceProvider.github)
        self.assertEqual(code_record.error.category, SourceErrorCategory.malformed_payload)
        self.assertFalse(any(call["params"] and "head_sha" in call["params"] for call in self.transport.calls))
        self.assertFalse(result.snapshot.is_complete)

    def test_mismatched_ci_sha_is_preserved_only_as_noncertifying_metadata(self):
        self.transport.branch_shas = {"example-org/backend": [SHA_A]}
        self.transport.actions_shas = {"example-org/backend": [SHA_B]}
        result = self.collect(_settings([_repo("backend", "backend")]))

        scoped = result.repository_results[0]
        self.assertEqual(len(scoped.ci_runs), 1)
        self.assertEqual(scoped.ci_runs[0].commit_sha, SHA_B)
        self.assertEqual(scoped.ci_runs[0].repository_identity, scoped.repository_state.identity)
        self.assertEqual(scoped.ci_runs[0].provenance.commit_sha, SHA_B)
        self.assertIs(scoped.ci_runs[0].provenance.certifies_target, False)
        self.assertEqual(result.snapshot.repository_results[0].ci_runs, [])
        ci_record = next(record for record in scoped.records if record.provider == SourceProvider.github_actions)
        self.assertEqual(ci_record.status.value, "failed")
        self.assertEqual(ci_record.error.category, SourceErrorCategory.not_found)
        mismatched_evidence = [item for item in scoped.evidence if item.provenance.source.value == "github_actions"]
        self.assertEqual(len(mismatched_evidence), 1)
        self.assertEqual(mismatched_evidence[0].provenance.commit_sha, SHA_B)
        self.assertFalse(mismatched_evidence[0].provenance.payload["is_target_match"])

    def test_frontend_access_failures_preserve_backend_and_shared_project_data(self):
        for category, expected_access in (
            (SourceErrorCategory.permission_denied, RepositoryAccessStatus.denied),
            (SourceErrorCategory.not_found, RepositoryAccessStatus.unknown),
            (SourceErrorCategory.timeout, RepositoryAccessStatus.unknown),
        ):
            with self.subTest(category=category):
                self.transport = ScriptedTransport()
                self.tasks.task_calls = 0
                self.tasks.evidence_calls = 0
                self.policies.calls = 0
                self.transport.branch_shas = {"example-org/backend": [SHA_A]}
                self.transport.actions_shas = {"example-org/backend": [SHA_A]}
                self.transport.failures = {"example-org/frontend": category}
                result = self.collect(_settings([_repo("backend", "backend"), _repo("frontend", "frontend")]))

                backend, frontend = result.repository_results
                self.assertEqual(backend.repository_state.target_commit_sha, SHA_A)
                self.assertTrue(backend.code_diffs)
                self.assertTrue(backend.ci_runs)
                self.assertEqual(frontend.repository_state.commit_resolution_status, CommitResolutionStatus.failed)
                self.assertEqual(frontend.repository_state.access_status, expected_access)
                self.assertEqual(
                    frontend.repository_state.availability_status,
                    (RepositoryAvailabilityStatus.unknown if category == SourceErrorCategory.permission_denied
                     else RepositoryAvailabilityStatus.unavailable),
                )
                frontend_error = next(record.error for record in frontend.records if record.provider == SourceProvider.github)
                self.assertEqual(frontend_error.category, category)
                self.assertEqual(self.tasks.task_calls, 1)
                self.assertEqual(self.tasks.evidence_calls, 1)
                self.assertEqual(self.policies.calls, 1)
                self.assertFalse(result.snapshot.is_complete)
                self.assertEqual(result.snapshot.repository_results[0].repository_state.identity.id, "backend")
                self.assertEqual(result.snapshot.tasks, [])
                self.assertEqual(result.snapshot.policies, [])

    def test_project_collection_is_not_repeated_for_each_repository(self):
        self.transport.branch_shas = {"example-org/backend": [SHA_A], "example-org/frontend": [SHA_B]}
        self.transport.actions_shas = {"example-org/backend": [SHA_A], "example-org/frontend": [SHA_B]}
        self.collect(_settings([_repo("backend", "backend"), _repo("frontend", "frontend")]))

        self.assertEqual(self.tasks.task_calls, 1)
        self.assertEqual(self.tasks.evidence_calls, 1)
        self.assertEqual(self.policies.calls, 1)
        self.assertEqual(len([call for call in self.transport.calls if call["url"].startswith("https://api.trello.com")]), 0)

    def test_remote_off_uses_no_transport_and_never_labels_fixture_code_as_a_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture_dir = Path(temporary)
            (fixture_dir / "fixture.json").write_text(
                json.dumps({"tasks": [], "code_diffs": [], "ci_runs": [], "policies": []}),
                encoding="utf-8",
            )
            settings = _settings(
                [_repo("backend", "backend")],
                source_mode="real",
                allow_remote_ai=False,
                fixtures_dir=str(fixture_dir),
            )
            source = create_analysis_composite_source(settings, transport=self.transport)
            result = source.collect_snapshot("fixture", self.now)

        self.assertEqual(self.transport.calls, [])
        self.assertEqual(result.repository_results, [])
        self.assertEqual(result.snapshot.repository_results, [])
        self.assertTrue(all(diff.repository_identity is None for diff in result.snapshot.code_diffs))
        self.assertIsNone(result.target_commit_sha)
        self.assertEqual(self.transport.write_calls, [])

    def test_single_source_factories_preserve_fixture_mode_with_repository_collection(self):
        settings = _settings([_repo("backend", "backend"), _repo("frontend", "frontend")],
                             source_mode="fixtures")

        code_source = create_code_source(settings)
        ci_source = create_ci_source(settings)

        self.assertIsInstance(code_source, FixtureSourcesAdapter)
        self.assertIsInstance(ci_source, FixtureSourcesAdapter)

    def test_analysis_factory_keeps_remote_gate_and_fixture_mode_unscoped(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture_dir = Path(temporary)
            (fixture_dir / "analysis-fixture.json").write_text(
                json.dumps({"tasks": [], "code_diffs": [], "ci_runs": [], "policies": []}),
                encoding="utf-8",
            )
            allowed = _settings(
                [_repo("backend", "backend")],
                github_token="test-only-token",
                fixtures_dir=str(fixture_dir),
            )
            self.transport.branch_shas = {"example-org/backend": [SHA_A]}
            self.transport.actions_shas = {"example-org/backend": [SHA_A]}
            remote_source = create_analysis_composite_source(allowed, transport=self.transport)
            remote_result = remote_source.collect_snapshot("analysis-fixture", self.now)

            self.assertEqual(remote_result.repository_results[0].repository_state.target_commit_sha, SHA_A)
            self.assertTrue(self.transport.calls)

            self.transport.calls.clear()
            fixture_only = _settings(
                [_repo("backend", "backend")],
                source_mode="fixtures",
                github_token="test-only-token",
                fixtures_dir=str(fixture_dir),
            )
            fixture_source = create_analysis_composite_source(fixture_only, transport=self.transport)
            fixture_diffs = fixture_source.get_diffs("analysis-fixture")
            fixture_runs = fixture_source.get_ci_results("analysis-fixture")

        self.assertEqual(self.transport.calls, [])
        self.assertTrue(all(diff.repository_identity is None for diff in fixture_diffs))
        self.assertTrue(all(run.repository_identity is None for run in fixture_runs))

    def test_repository_file_counters_are_independent_within_the_project_budget(self):
        first = RepositoryIdentity(id="backend", owner="example-org", repo="backend")
        second = RepositoryIdentity(id="frontend", owner="example-org", repo="frontend")
        ledger = CollectionBudgetLedger(
            project_max_http_calls=10, repository_max_http_calls=5,
            project_max_files=3, repository_max_files=1,
            project_max_response_bytes=100, repository_max_response_bytes=100,
            project_timeout_seconds=20, repository_timeout_seconds=10,
            project_max_estimated_tokens=100, repository_max_estimated_tokens=50,
            default_request_timeout_seconds=5, per_response_max_bytes=100,
            clock=lambda: 0.0,
        )
        ledger.begin_repository(first)
        ledger.begin_repository(second)

        self.assertTrue(ledger.consume_files(first, 1))
        self.assertFalse(ledger.consume_files(first, 1))
        self.assertTrue(ledger.consume_files(second, 1))

        self.assertEqual(ledger.repository_receipt(first).files, 2)
        self.assertTrue(ledger.repository_receipt(first).exhausted)
        self.assertEqual(ledger.repository_receipt(second).files, 1)
        self.assertFalse(ledger.repository_receipt(second).exhausted)
        self.assertEqual(ledger.receipt().files, 3)
        self.assertFalse(ledger.receipt().exhausted)

    def test_project_token_budget_blocks_repository_work_before_github_requests(self):
        result = self.collect(_settings(
            [_repo("backend", "backend")],
            multirepo_project_max_estimated_tokens=1,
            multirepo_repository_max_estimated_tokens=100,
        ))

        scoped = result.repository_results[0]
        code_record = next(record for record in scoped.records if record.provider == SourceProvider.github)
        self.assertEqual(code_record.error.category, SourceErrorCategory.limit_exceeded)
        self.assertGreater(result.budget_receipt.estimated_tokens, 1)
        self.assertTrue(result.budget_receipt.exhausted)
        self.assertTrue(scoped.budget_receipt.exhausted)
        self.assertEqual(self.transport.calls, [])

    def test_project_call_budget_preserves_code_and_reports_scoped_exhaustion(self):
        self.transport.branch_shas = {"example-org/backend": [SHA_A]}
        self.transport.actions_shas = {"example-org/backend": [SHA_A]}
        settings = _settings(
            [_repo("backend", "backend")],
            multirepo_project_max_http_calls=3,
            multirepo_repository_max_http_calls=10,
        )
        result = self.collect(settings)

        scoped = result.repository_results[0]
        self.assertTrue(scoped.code_diffs)
        self.assertFalse(scoped.ci_runs)
        ci_record = next(record for record in scoped.records if record.provider == SourceProvider.github_actions)
        self.assertEqual(ci_record.error.category, SourceErrorCategory.limit_exceeded)
        self.assertEqual(scoped.budget_receipt.http_calls, 3)
        self.assertLessEqual(result.budget_receipt.http_calls, 3)
        self.assertEqual(len(self.transport.calls), 3)
        self.assertTrue(scoped.budget_receipt.exhausted)

    def test_repository_call_budget_is_independent_and_does_not_spend_another_repo(self):
        self.transport.branch_shas = {
            "example-org/backend": [SHA_A],
            "example-org/frontend": [SHA_B],
        }
        result = self.collect(_settings(
            [_repo("backend", "backend"), _repo("frontend", "frontend")],
            multirepo_project_max_http_calls=20,
            multirepo_repository_max_http_calls=1,
        ))

        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual([item.budget_receipt.http_calls for item in result.repository_results], [1, 1])
        self.assertTrue(all(item.budget_receipt.exhausted for item in result.repository_results))
        self.assertTrue(all(not item.code_diffs for item in result.repository_results))
        self.assertTrue(all(
            next(record for record in item.records if record.provider == SourceProvider.github)
            .error.category == SourceErrorCategory.limit_exceeded
            for item in result.repository_results
        ))
        self.assertEqual(result.budget_receipt.http_calls, 2)
        self.assertFalse(result.budget_receipt.exhausted)

    def test_streamed_overflow_probe_is_charged_and_stops_after_one_chunk(self):
        class StreamResponse:
            status_code = 200
            headers = {}

            def __init__(self):
                self.closed = False
                self.bytes_yielded = 0
                self.requested_chunk_size = None
                self.body = b'{"ok": true}' + b" " * 100

            def iter_content(self, chunk_size):
                self.requested_chunk_size = chunk_size
                body = self.body
                for offset in range(0, len(body), chunk_size):
                    chunk = body[offset:offset + chunk_size]
                    self.bytes_yielded += len(chunk)
                    yield chunk

            def close(self):
                self.closed = True

        class StreamSession:
            def __init__(self, response):
                self.response = response
                self.closed = False
                self.trust_env = True
                self.calls = []

            def get(self, url, **kwargs):
                self.calls.append({"url": url, **kwargs})
                return self.response

            def close(self):
                self.closed = True

        identity = RepositoryIdentity(id="backend", owner="example-org", repo="backend")
        ledger = CollectionBudgetLedger(
            project_max_http_calls=4, repository_max_http_calls=2,
            project_max_files=2, repository_max_files=2,
            project_max_response_bytes=7, repository_max_response_bytes=100,
            project_timeout_seconds=20, repository_timeout_seconds=10,
            project_max_estimated_tokens=100, repository_max_estimated_tokens=100,
            default_request_timeout_seconds=5, per_response_max_bytes=100,
            clock=lambda: 0.0,
        )
        ledger.begin_repository(identity)
        response = StreamResponse()
        session = StreamSession(response)
        bounded = BoundedGetTransport(
            SourceProvider.github, max_response_bytes=100,
            session_factory=lambda: session, clock=lambda: 0.0,
        )
        budgeted = BudgetedHttpGetTransport(bounded, ledger, SourceProvider.github, identity)

        with self.assertRaises(SourceError) as context:
            budgeted.get("https://api.github.com/repos/example-org/backend")

        self.assertEqual(context.exception.category, SourceErrorCategory.limit_exceeded)
        self.assertEqual(response.requested_chunk_size, 7)
        self.assertEqual(response.bytes_yielded, 14)
        self.assertLess(response.bytes_yielded, len(response.body))
        self.assertEqual(ledger.receipt().response_bytes, 14)
        self.assertEqual(ledger.repository_receipt(identity).response_bytes, 14)
        self.assertFalse(ledger.repository_receipt(identity).exhausted)
        self.assertEqual(ledger.receipt().http_calls, 1)
        self.assertTrue(ledger.receipt().exhausted)
        self.assertFalse(session.calls[0]["allow_redirects"])
        self.assertTrue(response.closed)
        self.assertTrue(session.closed)

    def test_stream_with_content_length_exactly_filling_repository_budget_is_accepted(self):
        identity = RepositoryIdentity(id="backend", owner="example-org", repo="backend")
        ledger = CollectionBudgetLedger(
            project_max_http_calls=4, repository_max_http_calls=2,
            project_max_files=2, repository_max_files=2,
            project_max_response_bytes=100, repository_max_response_bytes=7,
            project_timeout_seconds=20, repository_timeout_seconds=10,
            project_max_estimated_tokens=100, repository_max_estimated_tokens=100,
            default_request_timeout_seconds=5, per_response_max_bytes=100,
            clock=lambda: 0.0,
        )
        ledger.begin_repository(identity)
        response = _BudgetStreamResponse(b'{"x":1}', {"Content-Length": "7"})
        session = _BudgetStreamSession(response)
        bounded = BoundedGetTransport(
            SourceProvider.github, max_response_bytes=100,
            session_factory=lambda: session, clock=lambda: 0.0,
        )
        budgeted = BudgetedHttpGetTransport(bounded, ledger, SourceProvider.github, identity)

        result = budgeted.get("https://api.github.com/repos/example-org/backend")

        self.assertEqual(result.body, {"x": 1})
        self.assertEqual(response.bytes_yielded, 7)
        self.assertEqual(ledger.receipt().response_bytes, 7)
        self.assertEqual(ledger.repository_receipt(identity).response_bytes, 7)
        self.assertTrue(response.closed)
        self.assertTrue(session.closed)

    def test_repository_stream_byte_budget_stops_before_another_chunk(self):
        identity = RepositoryIdentity(id="backend", owner="example-org", repo="backend")
        ledger = CollectionBudgetLedger(
            project_max_http_calls=4, repository_max_http_calls=2,
            project_max_files=2, repository_max_files=2,
            project_max_response_bytes=100, repository_max_response_bytes=7,
            project_timeout_seconds=20, repository_timeout_seconds=10,
            project_max_estimated_tokens=100, repository_max_estimated_tokens=100,
            default_request_timeout_seconds=5, per_response_max_bytes=100,
            clock=lambda: 0.0,
        )
        ledger.begin_repository(identity)
        response = _BudgetStreamResponse(b'{"ok": true}' + b" " * 100)
        session = _BudgetStreamSession(response)
        bounded = BoundedGetTransport(
            SourceProvider.github, max_response_bytes=100,
            session_factory=lambda: session, clock=lambda: 0.0,
        )
        budgeted = BudgetedHttpGetTransport(bounded, ledger, SourceProvider.github, identity)

        with self.assertRaises(SourceError) as context:
            budgeted.get("https://api.github.com/repos/example-org/backend")

        self.assertEqual(context.exception.category, SourceErrorCategory.limit_exceeded)
        self.assertEqual(response.bytes_yielded, 14)
        self.assertLess(response.bytes_yielded, len(response.body))
        self.assertEqual(ledger.receipt().response_bytes, 14)
        self.assertEqual(ledger.repository_receipt(identity).response_bytes, 14)
        self.assertTrue(ledger.repository_receipt(identity).exhausted)
        self.assertFalse(ledger.receipt().exhausted)
        self.assertTrue(response.closed)
        self.assertTrue(session.closed)

    def test_global_file_budget_stops_later_repository_before_network_work(self):
        self.transport.branch_shas = {"example-org/backend": [SHA_A]}
        self.transport.actions_shas = {"example-org/backend": [SHA_A]}
        result = self.collect(_settings(
            [_repo("backend", "backend"), _repo("frontend", "frontend")],
            multirepo_project_max_files=1,
            multirepo_repository_max_files=1,
        ))

        backend, frontend = result.repository_results
        self.assertEqual(backend.budget_receipt.files, 1)
        self.assertTrue(backend.code_diffs)
        self.assertFalse(frontend.code_diffs)
        self.assertEqual(frontend.repository_state.target_commit_sha, None)
        self.assertEqual(frontend.records[0].error.category, SourceErrorCategory.limit_exceeded)
        self.assertFalse(any("frontend" in call["url"] for call in self.transport.calls))
        self.assertEqual(result.budget_receipt.files, 1)

    def test_fake_clock_deadline_prevents_followup_requests_and_records_timeout(self):
        ticks = [0.0]

        def clock():
            return ticks[0]

        class SlowTransport(ScriptedTransport):
            def get(inner_self, *args, **kwargs):
                response = super(SlowTransport, inner_self).get(*args, **kwargs)
                ticks[0] += 2.0
                return response

        self.transport = SlowTransport()
        self.transport.branch_shas = {"example-org/backend": [SHA_A]}
        settings = _settings(
            [_repo("backend", "backend")],
            multirepo_repository_timeout_seconds=1.0,
            multirepo_project_timeout_seconds=5.0,
        )
        result = self.collect(settings, clock=clock)

        scoped = result.repository_results[0]
        self.assertEqual(scoped.repository_state.commit_resolution_status, CommitResolutionStatus.failed)
        code_record = next(record for record in scoped.records if record.provider == SourceProvider.github)
        self.assertEqual(code_record.error.category, SourceErrorCategory.timeout)
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(scoped.budget_receipt.http_calls, 1)
        self.assertTrue(scoped.budget_receipt.exhausted)

    def test_project_deadline_blocks_later_repository_even_with_time_left_per_repo(self):
        ticks = [0.0]

        def clock():
            return ticks[0]

        class SlowTransport(ScriptedTransport):
            def get(inner_self, *args, **kwargs):
                response = super(SlowTransport, inner_self).get(*args, **kwargs)
                ticks[0] += 2.0
                return response

        self.transport = SlowTransport()
        self.transport.branch_shas = {"example-org/backend": [SHA_A]}
        result = self.collect(_settings(
            [_repo("backend", "backend"), _repo("frontend", "frontend")],
            multirepo_project_timeout_seconds=1.0,
            multirepo_repository_timeout_seconds=5.0,
        ), clock=clock)

        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(result.budget_receipt.http_calls, 1)
        self.assertTrue(result.budget_receipt.exhausted)
        for scoped in result.repository_results:
            self.assertEqual(scoped.records[0].error.category, SourceErrorCategory.timeout)
            self.assertTrue(scoped.budget_receipt.exhausted)

    def test_estimated_token_budget_keeps_code_and_stops_ci_without_model_claim(self):
        self.transport.branch_shas = {"example-org/backend": [SHA_A]}
        self.transport.actions_shas = {"example-org/backend": [SHA_A]}
        result = self.collect(_settings(
            [_repo("backend", "backend")],
            multirepo_repository_max_estimated_tokens=1,
            multirepo_project_max_estimated_tokens=100,
        ))

        scoped = result.repository_results[0]
        self.assertTrue(scoped.code_diffs)
        self.assertGreater(scoped.budget_receipt.estimated_tokens, 1)
        self.assertTrue(scoped.budget_receipt.exhausted)
        ci_record = next(record for record in scoped.records if record.provider == SourceProvider.github_actions)
        self.assertEqual(ci_record.error.category, SourceErrorCategory.limit_exceeded)
        self.assertIn("estimated", scoped.budget_receipt.token_estimate_note.lower())
        self.assertFalse(any(call["url"].endswith("/actions/runs") for call in self.transport.calls))

    def test_analysis_factory_collects_metrics_for_each_fixed_repository(self):
        backend = RepositoryConfiguration(
            id="backend", owner="example-org", repo="backend", branch="develop",
            commit_sha=SHA_A, quality_workflow_path=".github/workflows/backend-quality.yml",
        )
        frontend = RepositoryConfiguration(
            id="frontend", owner="example-org", repo="frontend", branch="develop",
            commit_sha=SHA_B, quality_workflow_path=".github/workflows/frontend-quality.yml",
        )
        transport = ArtifactEnabledMultiRepoTransport()
        settings = _settings(
            [backend, frontend],
            github_token="synthetic-test-token",
            allow_remote_ai=True,
        )
        try:
            source = create_analysis_composite_source(
                settings,
                transport=transport,
                binary_transport=transport,
            )
        except TypeError as exc:
            self.fail(f"analysis factory must expose its budgeted binary fake port: {exc}")

        self.assertIsInstance(source, MultiRepoSourcesAdapter)
        result = source.collect_snapshot("multirepo-test", self.now)

        self.assertEqual(len(result.repository_results), 2)
        scoped_by_id = {
            scoped.repository_state.identity.id: scoped for scoped in result.repository_results
        }
        expected = {
            "backend": (SHA_A, "12345", 2, 49, 97.9,
                        "Loaded backend TypeScript sources in src/**/*.ts."),
            "frontend": (SHA_B, "23456", 1, 31, 92.73,
                         "Loaded frontend TypeScript sources; UI TSX is excluded."),
        }
        for repository_id, (sha, run_id, attempt, total, coverage, basis) in expected.items():
            with self.subTest(repository=repository_id):
                scoped = scoped_by_id[repository_id]
                self.assertEqual(scoped.repository_state.target_commit_sha, sha)
                self.assertEqual(
                    len(scoped.ci_runs), 2,
                    f"records={[record.model_dump(mode='json') for record in scoped.records]}; "
                    f"calls={[call['url'] for call in transport.calls]}; "
                    f"metadata={transport.artifact_calls}; "
                    f"binary_urls={[call['url'] for call in transport.binary_calls]}",
                )
                run = next(run for run in scoped.ci_runs if run.id == run_id)
                decoy = next(run for run in scoped.ci_runs if run.id != run_id)
                self.assertIsNone(decoy.metrics_artifact)
                self.assertIsNone(decoy.total_tests)
                self.assertEqual((run.id, run.commit_sha, run.run_attempt), (run_id, sha, attempt))
                self.assertEqual(run.repository_identity.id, repository_id)
                self.assertEqual((run.total_tests, run.failed_tests, run.coverage_percentage),
                                 (total, 0, coverage))
                self.assertEqual(run.metrics_artifact.outcome, CiArtifactOutcome.validated)
                self.assertTrue(run.metrics_artifact.certifies_metrics)
                self.assertEqual(run.metrics_artifact.metrics.coverage_basis, basis)
                self.assertEqual(run.metrics_artifact.metrics.coverage_metric, "lines")
                if repository_id == "frontend":
                    self.assertIsNone(run.metrics_artifact.metrics.test_suites)
                    self.assertEqual(run.metrics_artifact.metrics.e2e_status, "not_implemented")
                artifact_evidence = [item for item in scoped.evidence
                                     if item.category == "ci_artifact_metrics"]
                self.assertEqual(len(artifact_evidence), 1)
                self.assertEqual(artifact_evidence[0].provenance.repository,
                                 f"example-org/{repository_id}")
                self.assertEqual(artifact_evidence[0].provenance.commit_sha, sha)
                self.assertEqual(artifact_evidence[0].provenance.external_id, run_id)
                self.assertEqual(artifact_evidence[0].provenance.payload["outcome"],
                                 CiArtifactOutcome.validated.value)
                self.assertIsNotNone(scoped.budget_receipt)
                self.assertEqual(scoped.budget_receipt.http_calls, 5)

        public_result = result.model_dump(mode="json")
        serialized_runs = {
            item["repository_state"]["identity"]["id"]: {
                run["id"]: run for run in item["ci_runs"]
            }
            for item in public_result["repository_results"]
        }
        backend_run = serialized_runs["backend"][expected["backend"][1]]
        frontend_run = serialized_runs["frontend"][expected["frontend"][1]]
        self.assertEqual(backend_run["metrics_artifact"]["metrics"]["coverage_basis"],
                         expected["backend"][5])
        self.assertNotIn("test_suites", frontend_run["metrics_artifact"]["metrics"])
        self.assertEqual({call["repo"] for call in transport.artifact_calls},
                         {"example-org/backend", "example-org/frontend"})
        self.assertEqual(len(transport.artifact_calls), 2)
        self.assertEqual(len(transport.binary_calls), 2)
        self.assertEqual(result.budget_receipt.http_calls, 10)
        self.assertEqual(transport.write_calls, [])

    def test_singleton_factory_path_remains_single_repository_and_legacy_shape(self):
        settings = Settings(
            project_id="legacy",
            source_mode="real",
            github_owner="example-org",
            github_repo="backend",
            github_branch="develop",
            github_commit_sha=SHA_A,
            http_timeout_seconds=2.0,
        )
        self.transport.actions_shas = {"example-org/backend": [SHA_A]}
        source = create_composite_source(settings, transport=self.transport)
        result = source.collect_snapshot("legacy", self.now)

        self.assertEqual(result.repository_results, [])
        self.assertEqual(result.target_commit_sha, SHA_A)
        self.assertTrue(result.snapshot.code_diffs)
        self.assertTrue(result.snapshot.ci_runs)
        self.assertNotIn("metrics_artifact", result.snapshot.ci_runs[0].model_dump())
        self.assertFalse(any("/actions/runs/" in call["url"] and call["url"].endswith("/artifacts")
                             for call in self.transport.calls))
        self.assertEqual(self.transport.write_calls, [])

    def test_large_commit_does_not_monopolize_project_budget_or_block_second_repo(self):
        """A repository with a large commit exhausts its repo budget, but project ledger clamping allows the second repo to collect cleanly."""
        self.transport.branch_shas = {
            "example-org/backend": [SHA_A],
            "example-org/frontend": [SHA_B],
        }
        self.transport.actions_shas = {
            "example-org/backend": [SHA_A],
            "example-org/frontend": [SHA_B],
        }
        num_lines = 600
        large_patch = f"@@ -1,1 +1,{num_lines} @@\n-old\n" + ("+large_line_content_item_with_extra_payload_for_testing\n" * num_lines)
        self.transport.diff_files_overrides = {
            "example-org/backend": [{
                "filename": "src/large.py",
                "status": "modified",
                "patch": large_patch,
                "sha": "d" * 40,
            }],
            "example-org/frontend": [{
                "filename": "src/small.py",
                "status": "modified",
                "patch": "@@ -1 +1 @@\n-old\n+new",
                "sha": "d" * 40,
            }],
        }
        settings = _settings(
            [_repo("backend", "backend"), _repo("frontend", "frontend")],
            multirepo_repository_max_estimated_tokens=2500,
            multirepo_project_max_estimated_tokens=10000,
        )
        result = self.collect(settings)

        backend, frontend = result.repository_results

        # Backend exceeded its 2500 token budget, marked exhausted and incomplete
        self.assertGreater(backend.budget_receipt.estimated_tokens, 2500)
        self.assertTrue(backend.budget_receipt.exhausted)
        self.assertFalse(backend.is_complete)
        self.assertIn("Repository source budget exhausted.", backend.incomplete_reason)

        # Frontend was NOT blocked by backend! It resolved SHA, collected diffs and CI
        self.assertEqual(frontend.repository_state.target_commit_sha, SHA_B)
        self.assertEqual(frontend.repository_state.commit_resolution_status, CommitResolutionStatus.resolved)
        self.assertTrue(frontend.code_diffs)
        self.assertTrue(frontend.ci_runs)
        self.assertFalse(frontend.budget_receipt.exhausted)
        self.assertTrue(frontend.is_complete)

        # Shared project pool clamped backend to 2500, so project is NOT exhausted
        self.assertFalse(result.budget_receipt.exhausted)
        self.assertLessEqual(result.budget_receipt.estimated_tokens, 10000)

        # Overall snapshot is incomplete because partial evidence is never declared complete
        self.assertFalse(result.snapshot.is_complete)
        self.assertIn("Repository source budget exhausted.", result.snapshot.incomplete_reason)

    def test_ci_budget_reservation_permits_actions_retrieval_when_tokens_reserved(self):
        """When code collection consumes tokens within its code share, CI is not starved and executes within reserved pool."""
        self.transport.branch_shas = {"example-org/backend": [SHA_A]}
        self.transport.actions_shas = {"example-org/backend": [SHA_A]}
        settings = _settings(
            [_repo("backend", "backend")],
            multirepo_repository_max_estimated_tokens=50000,
            multirepo_project_max_estimated_tokens=100000,
        )
        result = self.collect(settings)

        scoped = result.repository_results[0]
        self.assertTrue(scoped.code_diffs)
        self.assertTrue(scoped.ci_runs)
        self.assertFalse(scoped.budget_receipt.exhausted)
        self.assertTrue(scoped.is_complete)
        self.assertTrue(any(call["url"].endswith("/actions/runs") for call in self.transport.calls))

    def test_multirepo_snippet_deduplication_prefers_full_file_without_duplicate_diff(self):
        """MultiRepo budgeted code source deduplicates snippets per file, preferring full_file."""
        self.transport.branch_shas = {"example-org/backend": [SHA_A]}
        self.transport.actions_shas = {"example-org/backend": [SHA_A]}
        settings = _settings(
            [_repo("backend", "backend")],
            multirepo_repository_max_estimated_tokens=50000,
            multirepo_project_max_estimated_tokens=100000,
        )
        result = self.collect(settings)

        scoped = result.repository_results[0]
        # In ScriptedTransport, src/shared.py has both diff and contents endpoint.
        # code_diffs must contain exactly one snippet for src/shared.py with evidence_scope=full_file
        file_snippets = [d for d in scoped.code_diffs if d.file_path == "src/shared.py"]
        self.assertEqual(len(file_snippets), 1)
        self.assertEqual(file_snippets[0].evidence_scope, "full_file")

        # Evidence retains full provenance and availability flags
        diff_ev = next(e for e in scoped.evidence if e.provenance.file_path == "src/shared.py")
        self.assertTrue(diff_ev.provenance.payload["patch_available"])
        self.assertTrue(diff_ev.provenance.payload["full_file_available"])
        self.assertEqual(diff_ev.provenance.payload["source_sha256"], file_snippets[0].source_sha256)


if __name__ == "__main__":
    unittest.main()
