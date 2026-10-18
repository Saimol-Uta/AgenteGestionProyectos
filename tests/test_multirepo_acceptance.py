"""Single-run public acceptance proof for Hormigestion MultiRepo.

This test deliberately composes the real settings loader, analysis source factory,
source collection, orchestrator, report projection, and SQLite persistence. Only
HTTP/binary GET and LLM provider ports are fakes; cached v1 producer documents
come from the existing backend/frontend contract fixtures.
"""

import base64
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from jsonschema import Draft202012Validator

import agente_gestion
import agente_gestion.application.config as engine_config
from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.adapters.sources.factory import create_analysis_composite_source
from agente_gestion.adapters.sources.http_transport import HttpResponse
from agente_gestion.adapters.storage.artifacts import ArtifactStorageAdapter
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.application.config import Settings
from agente_gestion.application.orchestrator import OrchestratorConfig, ProjectOrchestrator
from agente_gestion.application.reporting import generate_report_markdown
from agente_gestion.domain.repositories import (
    CommitResolutionStatus,
    RepositoryAccessStatus,
    RepositoryAvailabilityStatus,
)
from agente_gestion.domain.run import RunStatus
from agente_gestion.domain.ci_metrics import CiArtifactOutcome
from agente_gestion.ports.source_artifacts import BinaryHttpResponse
from agente_gestion.ports.source_errors import SourceError, SourceErrorCategory, SourceProvider


PROJECT_ID = "Hormigestion"
OWNER = "Josu-F1"
BACKEND_SHA = "a" * 40
FRONTEND_SHA = "b" * 40
FIXTURE_TOKEN = "fixture-only-token-never-sent-to-a-network"
REPOSITORIES = {
    "backend": {
        "name": "hormigestion-backend",
        "sha": BACKEND_SHA,
        "run_id": "12345",
        "attempt": 2,
        "artifact_id": "7701",
        "fixture": "backend-v1.json",
        "workflow": ".github/workflows/ci.yml",
    },
    "frontend": {
        "name": "hormigestion-frontend",
        "sha": FRONTEND_SHA,
        "run_id": "23456",
        "attempt": 1,
        "artifact_id": "8801",
        "fixture": "frontend-v1.json",
        "workflow": ".github/workflows/ci-quality.yml",
    },
}


class HormigestionFakeGitHub:
    """GET-only fake for branch, code, Actions metadata, and cached ZIP ports."""

    def __init__(self, *, deny_frontend: bool = False):
        self.deny_frontend = deny_frontend
        self.calls = []
        self.binary_calls = []
        self.write_calls = []
        self._archives = {}

    @staticmethod
    def _response(body):
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return HttpResponse(status_code=200, body=body, bytes_received=len(raw))

    @staticmethod
    def _identity(owner, repo):
        return f"{owner}/{repo}"

    @staticmethod
    def _source_bytes():
        return b"def divide(total):\n    return 1 / total\n"

    @classmethod
    def _git_blob_sha(cls):
        source = cls._source_bytes()
        header = b"blob " + str(len(source)).encode("ascii") + bytes((0,))
        return hashlib.sha1(header + source).hexdigest()

    @staticmethod
    def _record(repo, endpoint, method="GET"):
        return {"method": method, "repository": repo, "endpoint": endpoint}

    def get(self, url, params=None, headers=None, timeout_seconds=None):
        parsed = urlsplit(url)
        parts = parsed.path.split("/")
        if len(parts) < 5 or parts[1] != "repos":
            raise AssertionError(f"Unexpected fake GitHub URL path: {parsed.path}")
        owner, repo_name = parts[2], parts[3]
        full_name = self._identity(owner, repo_name)
        endpoint = "/" + "/".join(parts[4:])
        self.calls.append(self._record(full_name, endpoint))

        configured_id = next(
            (repo_id for repo_id, value in REPOSITORIES.items()
             if value["name"] == repo_name),
            None,
        )
        if configured_id is None:
            raise AssertionError(f"Unexpected repository in fake request: {full_name}")
        configured = REPOSITORIES[configured_id]

        if endpoint.startswith("/actions/artifacts/") and endpoint.endswith("/zip"):
            return self.get_binary(url, params=params, headers=headers, timeout_seconds=timeout_seconds)

        if (self.deny_frontend and configured_id == "frontend"
                and endpoint == "/commits/develop"):
            raise SourceError(SourceProvider.github, SourceErrorCategory.permission_denied)

        if endpoint == "/commits/develop" and params is None:
            return self._response({"sha": configured["sha"], "files": []})

        if endpoint == f"/commits/{configured['sha']}" and isinstance(params, dict):
            return self._response({
                "sha": configured["sha"],
                "files": [{
                    "filename": "src/quality_sample.py",
                    "status": "modified",
                    "patch": "@@ -1,2 +1,2 @@\n-def divide(total):\n-    return 1 / total\n+def divide(total):\n+    return 1 / total\n",
                    "sha": self._git_blob_sha(),
                }],
            })

        if endpoint.startswith("/contents/"):
            source = self._source_bytes()
            return self._response({
                "encoding": "base64",
                "content": base64.b64encode(source).decode("ascii"),
                "sha": self._git_blob_sha(),
                "size": len(source),
            })

        if endpoint == "/actions/runs":
            target = (params or {}).get("head_sha")
            if target != configured["sha"]:
                return self._response({"total_count": 0, "workflow_runs": []})
            decoy = {
                "id": int(configured["run_id"]) - 1,
                "name": "Unrelated workflow",
                "head_sha": configured["sha"],
                "head_branch": "develop",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "updated_at": "2026-10-16T10:01:00Z",
                "created_at": "2026-10-16T09:55:00Z",
                "run_started_at": "2026-10-16T09:55:01Z",
                "run_attempt": 9,
                "workflow_id": 999,
                "path": ".github/workflows/unrelated.yml",
            }
            selected = {
                "id": int(configured["run_id"]),
                "name": "Quality",
                "head_sha": configured["sha"],
                "head_branch": "develop",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "updated_at": "2026-10-16T10:00:00Z",
                "created_at": "2026-10-16T09:55:00Z",
                "run_started_at": "2026-10-16T09:55:01Z",
                "run_attempt": configured["attempt"],
                "workflow_id": int(configured["run_id"]),
                "path": configured["workflow"],
            }
            return self._response({"total_count": 2, "workflow_runs": [decoy, selected]})

        artifact_suffix = f"/actions/runs/{configured['run_id']}/artifacts"
        if endpoint == artifact_suffix:
            artifact_name = (
                f"ci-quality-{configured['sha']}-{configured['run_id']}-{configured['attempt']}"
            )
            return self._response({
                "total_count": 1,
                "artifacts": [{
                    "id": int(configured["artifact_id"]),
                    "name": artifact_name,
                    "expired": False,
                    "workflow_run": {
                        "id": int(configured["run_id"]),
                        "head_sha": configured["sha"],
                        "head_repository": {"full_name": full_name},
                        "run_attempt": configured["attempt"],
                    },
                }],
            })

        raise AssertionError(f"Unexpected fake GitHub GET: {full_name}{endpoint} {params!r}")

    def get_binary(self, url, params=None, headers=None, timeout_seconds=None):
        parsed = urlsplit(url)
        parts = parsed.path.split("/")
        if len(parts) != 8 or parts[1:3] != ["repos", OWNER]:
            raise AssertionError(f"Unexpected fake artifact URL path: {parsed.path}")
        repo_name, artifact_id = parts[3], parts[6]
        configured_id = next(
            (repo_id for repo_id, value in REPOSITORIES.items()
             if value["name"] == repo_name and value["artifact_id"] == artifact_id),
            None,
        )
        if configured_id is None or params is not None:
            raise AssertionError(f"Unexpected artifact request: {parsed.path}")
        configured = REPOSITORIES[configured_id]
        self.binary_calls.append(self._record(f"{OWNER}/{repo_name}", parsed.path))
        archive = self._archives.get(configured_id)
        if archive is None:
            fixture_dir = Path(__file__).resolve().parent / "fixtures" / "ci_metrics"
            document = json.loads(
                (fixture_dir / configured["fixture"]).read_text(encoding="utf-8")
            )
            document.update({
                "repository": f"{OWNER}/{repo_name}",
                "commit_sha": configured["sha"],
                "workflow_run_id": configured["run_id"],
                "workflow_run_attempt": str(configured["attempt"]),
            })
            schema_path = (
                Path(__file__).resolve().parent
                / "fixtures" / "ci-metrics.v1.schema.json"
            )
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            Draft202012Validator(schema).validate(document)
            encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
            archive_buffer = io.BytesIO()
            with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                bundle.writestr("reports/ci-metrics.json", encoded)
            archive = archive_buffer.getvalue()
            self._archives[configured_id] = archive
        return BinaryHttpResponse(
            status_code=200,
            headers={},
            body=archive,
            bytes_received=len(archive),
        )


class CountingFakeProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.roles = []

    def generate(self, request):
        self.roles.append(request.role)
        return super().generate(request)


class MultiRepoAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="t10-multirepo-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.transport = HormigestionFakeGitHub()
        self.provider = CountingFakeProvider()
        self.fixture_dir = self.root / "fixtures"
        self.fixture_dir.mkdir()
        (self.fixture_dir / f"{PROJECT_ID}.json").write_text(
            json.dumps({"tasks": [], "code_diffs": [], "ci_runs": [], "policies": []}),
            encoding="utf-8",
        )

    def _assert_snapshot_imports(self):
        expected_root = Path(__file__).resolve().parents[1]
        self.assertEqual(
            Path(agente_gestion.__file__).resolve(),
            expected_root / "src" / "agente_gestion" / "__init__.py",
        )
        self.assertEqual(
            Path(engine_config.__file__).resolve(),
            expected_root / "src" / "agente_gestion" / "application" / "config.py",
        )
        loader = Settings.from_env_and_file.__func__
        self.assertEqual(
            Path(loader.__code__.co_filename).resolve(),
            expected_root / "src" / "agente_gestion" / "application" / "config.py",
        )

    def _settings(self):
        config_path = self.root / "hormigestion-acceptance.yaml"
        repositories = [
            {
                "id": repository_id,
                "owner": OWNER,
                "repo": configured["name"],
                "branch": "develop",
                "quality_workflow_path": configured["workflow"],
            }
            for repository_id, configured in REPOSITORIES.items()
        ]
        config_path.write_text(yaml.safe_dump({
            "project": {"id": PROJECT_ID},
            "sources": {
                "mode": "real",
                "github": {
                    "repositories": repositories,
                    "collection_budget": {
                        "project_max_http_calls": 32,
                        "repository_max_http_calls": 16,
                    },
                },
            },
        }, sort_keys=False), encoding="utf-8")
        settings = Settings.from_env_and_file(
            config_path=str(config_path),
            overrides={
                "project_id": PROJECT_ID,
                "source_mode": "real",
                "allow_remote_ai": True,
                "github_token": FIXTURE_TOKEN,
                "trello_board_id": "",
                "trello_api_key": "",
                "trello_token": "",
                "is_fake": False,
                "fixtures_dir": str(self.fixture_dir),
                "policies_path": str(self.root / "no-policy-file.yaml"),
                "timezone": "UTC",
            },
            _env_file=None,
        )
        self.assertEqual(settings.source_mode, "real")
        self.assertEqual(settings.project_id, PROJECT_ID)
        self.assertIsNone(settings.github_owner)
        self.assertIsNone(settings.github_repo)
        self.assertIsNone(settings.github_commit_sha)
        self.assertIsNone(settings.trello_api_key)
        self.assertIsNone(settings.trello_token)
        self.assertFalse(settings.trello_board_id)
        self.assertEqual(
            [(item.id, item.owner, item.repo, item.branch, item.commit_sha)
             for item in settings.github_repositories],
            [
                ("backend", OWNER, REPOSITORIES["backend"]["name"], "develop", None),
                ("frontend", OWNER, REPOSITORIES["frontend"]["name"], "develop", None),
            ],
        )
        return settings

    def _execute(self, *, deny_frontend=False):
        self._assert_snapshot_imports()
        self.transport.deny_frontend = deny_frontend
        settings = self._settings()
        source = create_analysis_composite_source(
            settings,
            transport=self.transport,
            binary_transport=self.transport,
        )
        database = self.root / "analysis.sqlite"
        run_dir = self.root / "runs"
        storage = SQLiteStorageAdapter(str(database))
        artifacts = ArtifactStorageAdapter(base_dir=str(run_dir))
        orchestrator = ProjectOrchestrator(
            sqlite_storage=storage,
            artifact_storage=artifacts,
            elaborador_provider=self.provider,
            revisor_provider=self.provider,
            verificador_provider=self.provider,
            aprobador_provider=self.provider,
            source_adapter=source,
            config=OrchestratorConfig(
                project_id=PROJECT_ID,
                allow_remote_ai=True,
                max_llm_calls_per_run=8,
                runs_dir=str(run_dir),
                db_path=str(database),
                timezone="UTC",
            ),
        )
        run = orchestrator.execute()
        snapshot = storage.get_snapshot(run.snapshot_id)
        report = storage.get_report(run.report_id)
        self.assertIsNotNone(snapshot)
        self.assertIsNotNone(report)
        self.assertEqual(storage.get_run(run.run_id).run_id, run.run_id)
        self.assertTrue(database.is_file())
        self.assertTrue(run_dir.is_dir())
        return run, snapshot, report, storage

    def test_one_hormigestion_run_keeps_ci_metrics_provenance_findings_and_report_independent(self):
        self._assert_snapshot_imports()
        run, snapshot, report, _storage = self._execute()

        self.assertIn(run.status, (RunStatus.awaiting_human, RunStatus.completed))
        self.assertEqual(snapshot.project_id, PROJECT_ID)
        self.assertNotIn("target_commit_sha", snapshot.model_dump(mode="json"))
        self.assertEqual(snapshot.code_diffs, [])
        self.assertEqual(snapshot.ci_runs, [])
        self.assertEqual(len(snapshot.repository_results), 2)
        by_id = {item.repository_state.identity.id: item for item in snapshot.repository_results}
        report_by_id = {
            item.repository_state.identity.id: item for item in report.repository_results
        }
        expected_metrics = {
            "backend": (BACKEND_SHA, "12345", 2, 49, 0, 97.9,
                        "Loaded backend TypeScript sources in src/**/*.ts."),
            "frontend": (FRONTEND_SHA, "23456", 1, 31, 0, 92.73,
                         "Loaded frontend TypeScript sources; UI TSX is excluded."),
        }
        self.assertEqual(set(by_id), set(expected_metrics))
        self.assertEqual(set(report_by_id), set(expected_metrics))

        for repository_id, (sha, run_id, attempt, total, failed, coverage, basis) in expected_metrics.items():
            with self.subTest(repository=repository_id):
                captured = by_id[repository_id]
                state = captured.repository_state
                ci = next(item for item in captured.ci_runs if item.id == run_id)
                projected = report_by_id[repository_id]
                self.assertEqual(state.identity.owner, OWNER)
                self.assertEqual(state.identity.repo, REPOSITORIES[repository_id]["name"])
                self.assertEqual(state.branch, "develop")
                self.assertEqual(state.target_commit_sha, sha)
                self.assertEqual(state.commit_resolution_status, CommitResolutionStatus.resolved)
                self.assertEqual(state.access_status, RepositoryAccessStatus.allowed)
                self.assertEqual(state.availability_status, RepositoryAvailabilityStatus.available)
                self.assertTrue(captured.code_diffs)
                self.assertEqual({item.commit_sha for item in captured.code_diffs}, {sha})
                self.assertEqual(ci.commit_sha, sha)
                self.assertEqual(ci.run_attempt, attempt)
                self.assertEqual(ci.provenance.repository_identity.id, repository_id)
                self.assertEqual(ci.provenance.commit_sha, sha)
                self.assertEqual(ci.provenance.run_id, run_id)
                self.assertEqual(ci.provenance.run_attempt, attempt)
                self.assertTrue(ci.metrics_artifact.certifies_metrics)
                self.assertEqual(ci.metrics_artifact.outcome, CiArtifactOutcome.validated)
                self.assertEqual(ci.metrics_artifact.artifact_name,
                                 f"ci-quality-{sha}-{run_id}-{attempt}")
                self.assertEqual(ci.metrics_artifact.metrics.repository,
                                 f"{OWNER}/{REPOSITORIES[repository_id]['name']}")
                self.assertEqual(ci.metrics_artifact.metrics.coverage_basis, basis)
                self.assertEqual((ci.total_tests, ci.failed_tests, ci.coverage_percentage),
                                 (total, failed, coverage))
                self.assertEqual(projected.ci_result.id, run_id)
                self.assertEqual(projected.ci_result.run_attempt, attempt)
                self.assertEqual(projected.coverage_percentage, coverage)
                self.assertEqual(projected.coverage_scope, basis)
                self.assertTrue(projected.confirmed_findings)
                self.assertTrue(all(
                    finding.repository_identity.id == repository_id
                    and finding.commit_sha == sha
                    for finding in projected.confirmed_findings
                ))
                self.assertTrue(all(
                    f"{repository_id}::{sha}::" in finding.id
                    for finding in projected.confirmed_findings
                ))

        frontend_metrics = report_by_id["frontend"].ci_result.metrics_artifact.metrics
        self.assertIsNone(frontend_metrics.test_suites)
        self.assertEqual(frontend_metrics.e2e_status, "not_implemented")
        self.assertIsNone(report.ci_result)
        self.assertEqual(report.human_decision.value, "pending")
        self.assertNotIn("coverage_percentage", report.model_dump(mode="json"))
        markdown = generate_report_markdown(report)
        self.assertIn(BACKEND_SHA, markdown)
        self.assertIn(FRONTEND_SHA, markdown)
        self.assertIn("97.9", markdown)
        self.assertIn("92.7", markdown)
        self.assertIn("Loaded backend TypeScript sources", markdown)
        self.assertIn("UI TSX is excluded", markdown)
        self.assertEqual(
            [line for line in markdown.splitlines() if line.startswith("- **Cobertura global:**")],
            ["- **Cobertura global:** No se calcula cobertura global ni se promedian porcentajes entre repositorios."],
        )
        self.assertEqual(len([role for role in self.provider.roles if "elaborador" in role]), 1)
        self.assertEqual(len([role for role in self.provider.roles if "revisor" in role]), 2)
        self.assertEqual(len([role for role in self.provider.roles if "verificador" in role]), 2)
        self.assertEqual(len([role for role in self.provider.roles if "aprobador" in role]), 1)
        self.assertEqual(len(self.transport.binary_calls), 2)
        self.assertEqual({call["repository"] for call in self.transport.binary_calls}, {
            f"{OWNER}/hormigestion-backend", f"{OWNER}/hormigestion-frontend",
        })
        self.assertTrue(self.transport.calls)
        self.assertTrue(all(call["method"] == "GET" for call in self.transport.calls))
        self.assertTrue(all(call["method"] == "GET" for call in self.transport.binary_calls))
        self.assertEqual(self.transport.write_calls, [])

    def test_private_frontend_denial_keeps_backend_and_marks_report_partial_unknown(self):
        run, snapshot, report, _storage = self._execute(deny_frontend=True)

        captured = {item.repository_state.identity.id: item for item in snapshot.repository_results}
        projected = {item.repository_state.identity.id: item for item in report.repository_results}
        backend = captured["backend"]
        denied = captured["frontend"]
        self.assertEqual(backend.repository_state.target_commit_sha, BACKEND_SHA)
        self.assertTrue(backend.code_diffs)
        self.assertTrue(backend.ci_runs)
        self.assertTrue(projected["backend"].confirmed_findings)
        self.assertEqual(denied.repository_state.access_status, RepositoryAccessStatus.denied)
        self.assertEqual(denied.repository_state.commit_resolution_status, CommitResolutionStatus.failed)
        self.assertEqual(denied.repository_state.availability_status, RepositoryAvailabilityStatus.unknown)
        self.assertFalse(denied.code_diffs)
        self.assertFalse(denied.ci_runs)
        self.assertIsNone(projected["frontend"].ci_result)
        self.assertIsNone(projected["frontend"].coverage_percentage)
        self.assertTrue(projected["frontend"].source_problems)
        self.assertTrue(any("denied" in issue.lower() for issue in projected["frontend"].source_problems))
        self.assertEqual(report.human_decision.value, "pending")
        self.assertNotEqual(report.recommendation, "recomendar_aprobacion")
        self.assertIn("1 de 2 repositorios", report.summary)
        self.assertEqual(run.project_id, PROJECT_ID)
        self.assertEqual(len(self.transport.binary_calls), 1)
        self.assertEqual(self.transport.binary_calls[0]["repository"],
                         f"{OWNER}/hormigestion-backend")
        self.assertEqual(self.transport.write_calls, [])
        self.assertTrue(all(call["method"] == "GET" for call in self.transport.calls))


if __name__ == "__main__":
    unittest.main()
