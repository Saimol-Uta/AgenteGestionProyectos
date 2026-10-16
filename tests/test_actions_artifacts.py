"""T4: deterministic quality-workflow selection and bounded artifact metrics integration."""

import io
import json
import unittest
import zipfile
from datetime import datetime, timezone

from agente_gestion.adapters.sources.actions import GitHubActionsSourceAdapter
from agente_gestion.adapters.sources.collection_budget import (
    BudgetedHttpGetTransport,
    CollectionBudgetLedger,
)
from agente_gestion.adapters.sources.http_transport import HttpResponse
from agente_gestion.domain.ci_metrics import CiArtifactOutcome, CiWorkflowStatus
from agente_gestion.domain.code import CIRunResult
from agente_gestion.domain.evidence import EvidenceItem, EvidenceProvenance, EvidenceSource
from agente_gestion.domain.repositories import RepositoryIdentity
from agente_gestion.ports.source_artifacts import (
    ArtifactTransportError,
    BinaryHttpResponse,
)
from agente_gestion.ports.source_errors import SourceErrorCategory, SourceProvider

try:
    from agente_gestion.adapters.sources.actions_artifacts import (
        ArtifactEnabledCISource,
        select_quality_ci_run,
    )
except ImportError:
    ArtifactEnabledCISource = None
    select_quality_ci_run = None

try:
    from agente_gestion.adapters.sources.artifacts import CiMetricsArtifactValidator
except ImportError:  # pragma: no cover
    CiMetricsArtifactValidator = None

ROOT = "/home/saimoljimenez/Univercidad/GestionProyectos/AgenteGestionProyectos"
FIXTURES = f"{ROOT}/tests/fixtures/ci_metrics"
API_ORIGIN = "https://api.github.com"
STORAGE_URL = "https://productionresultssa0.blob.core.windows.net/container/archive.zip?sig=signed-query"
SHA_BACKEND = "a" * 40
WORKFLOW_PATH = ".github/workflows/ci-quality.yml"


def load_fixture(name):
    with open(f"{FIXTURES}/{name}", "r", encoding="utf-8") as handle:
        return json.load(handle)


def make_run(
    sha=SHA_BACKEND,
    run_id="12345",
    workflow_name="Quality",
    workflow_path=WORKFLOW_PATH,
    status="success",
    run_attempt=2,
):
    return CIRunResult(
        id=run_id,
        workflow_name=workflow_name,
        commit_sha=sha,
        status=status,
        run_attempt=run_attempt,
        workflow_path=workflow_path,
    )


class FakeRunsSource:
    """In-memory CISourcePort double returning prebuilt runs and evidence hooks."""

    def __init__(self, runs):
        self._runs = list(runs)
        self.last_invocation = None

    def get_ci_results(self, project_id, commit_sha=None):
        self.last_invocation = (project_id, commit_sha)
        return [run.model_copy() for run in self._runs]

    def get_evidence(self, project_id, commit_sha=None):
        items = []
        for run in self._runs:
            items.append(EvidenceItem(
                provenance=EvidenceProvenance(
                    id=f"github_actions_{run.id}",
                    source=EvidenceSource.github_actions,
                    project_id=project_id,
                    repository="example-org/backend",
                    commit_sha=run.commit_sha,
                    external_id=run.id,
                    timestamp=datetime.now(timezone.utc),
                    payload={"is_target_match": run.commit_sha == (commit_sha or run.commit_sha)},
                ),
                category="ci_run",
                summary=f"run {run.id} {run.status}",
            ))
        return items


class FakePlainGetTransport:
    """JSON GET double responding with prepared HttpResponse bodies."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, headers=None, timeout_seconds=None):
        self.calls.append((url, params, headers, timeout_seconds))
        item = self.routes.get(url)
        if item is None:
            return HttpResponse(status_code=404, body={}, bytes_received=0)
        if isinstance(item, BaseException):
            raise item
        return HttpResponse(status_code=200, body=item, bytes_received=len(json.dumps(item).encode()))


class FakeBinaryTransport:
    """In-memory one-hop binary transport; it never opens a socket."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout_seconds=None):
        self.calls.append({"url": url, "headers": dict(headers or {}), "timeout": timeout_seconds})
        item = self.routes.get(url)
        if item is None:
            raise ArtifactTransportError(SourceErrorCategory.network, attempted=True, status_code=None)
        return item


def zip_reports_bundle(metrics_bytes):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("reports/unit-summary.json", b'{"fixture":"synthetic"}')
        archive.writestr("reports/integration-summary.json", b'{"fixture":"synthetic"}')
        archive.writestr("reports/ci-metrics.json", metrics_bytes)
    return stream.getvalue()


def artifact_metadata_row(*, sha=SHA_BACKEND, run_id="12345", attempt=2, artifact_id=77,
                          expired=False, name=None, attempt_value=2):
    return {
        "id": artifact_id,
        "name": name or f"ci-quality-{sha}-{run_id}-{attempt}",
        "expired": expired,
        "workflow_run": {
            "id": int(run_id),
            "head_sha": sha,
            "head_repository": {"full_name": "example-org/backend"},
            "run_attempt": attempt_value,
        },
    }


def artifacts_list(rows, total=None):
    return {"total_count": total if total is not None else len(rows), "artifacts": list(rows)}


def new_ledger(first_identity=None):
    ledger = CollectionBudgetLedger(
        project_max_http_calls=50,
        repository_max_http_calls=20,
        project_max_files=100,
        repository_max_files=50,
        project_max_response_bytes=10_000_000,
        repository_max_response_bytes=10_000_000,
        project_timeout_seconds=60,
        repository_timeout_seconds=60,
        project_max_estimated_tokens=10_000_000,
        repository_max_estimated_tokens=10_000_000,
        default_request_timeout_seconds=10,
        per_response_max_bytes=8_000_000,
        clock=lambda: 0.0,
    )
    if first_identity is not None:
        ledger.begin_repository(first_identity)
    return ledger


def make_wrapper(runs, json_routes, binary_routes, *, ledger=None, identity=None,
                 workflow_path=WORKFLOW_PATH):
    identity = identity or RepositoryIdentity(id="backend", owner="example-org", repo="backend")
    ledger = ledger or new_ledger(first_identity=identity)
    json_inner = FakePlainGetTransport(json_routes)
    binary_inner = FakeBinaryTransport(binary_routes)
    json_budgeted = BudgetedHttpGetTransport(json_inner, ledger, SourceProvider.github_actions, identity)
    binary_budgeted = BudgetedHttpGetTransport(binary_inner, ledger, SourceProvider.github_actions, identity)
    validator = CiMetricsArtifactValidator(binary_budgeted, clock=lambda: 0.0)
    wrapper = ArtifactEnabledCISource(
        FakeRunsSource(runs),
        validator,
        json_budgeted,
        repository_full_name="example-org/backend",
        token="fake-test-token",
        quality_workflow_path=workflow_path,
        identity=identity,
    )
    return wrapper, ledger, json_inner, binary_inner


def valid_case_routes():
    """One metadata hop to the Actions artifact list, one bounded ZIP fetch.

    The validator derives the download URL itself (T3 contract): it calls the
    API zip endpoint and follows its Location redirect to signed storage, so
    the API zip hop returns the 200 bundle directly, mirroring T3 fixtures.
    """
    metrics_bytes = json.dumps(load_fixture("backend-v1.json")).encode()
    bundle = zip_reports_bundle(metrics_bytes)
    binary_routes = {
        f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip":
            BinaryHttpResponse(
                status_code=200, headers={}, body=bundle, bytes_received=len(bundle),
            ),
        STORAGE_URL: BinaryHttpResponse(
            status_code=200, headers={}, body=bundle, bytes_received=len(bundle),
        ),
    }
    json_routes = {
        f"{API_ORIGIN}/repos/example-org/backend/actions/runs/12345/artifacts":
            artifacts_list([artifact_metadata_row()]),
    }
    return json_routes, binary_routes


class SelectionTests(unittest.TestCase):
    def setUp(self):
        if select_quality_ci_run is None:
            self.skipTest("actions_artifacts module not implemented yet")

    def test_selection_filters_target_sha_and_workflow(self):
        matching = make_run()
        other_sha = make_run(sha="b" * 40)
        other_path = make_run(workflow_path=".github/workflows/other.yml")
        runs = [other_sha, other_path, matching]
        selected = select_quality_ci_run(
            runs, target_commit_sha=SHA_BACKEND, quality_workflow_path=WORKFLOW_PATH,
        )
        self.assertEqual(selected.id, matching.id)

    def test_selection_without_selector_takes_newest_target_run(self):
        newest = make_run(run_id="99001", run_attempt=1)
        older = make_run(run_id="98000", run_attempt=1)
        selected = select_quality_ci_run(
            [newest, older], target_commit_sha=SHA_BACKEND, quality_workflow_path=None,
        )
        self.assertEqual(selected.id, "99001")

    def test_selection_prefers_highest_attempt_without_success_lookup(self):
        older_success = make_run(run_id="98000", run_attempt=1)
        latest_failed = make_run(run_id="99001", run_attempt=3, status="failure")
        runs = [older_success, latest_failed]  # newest-first order from the adapter
        selected = select_quality_ci_run(
            runs, target_commit_sha=SHA_BACKEND, quality_workflow_path=None,
        )
        self.assertEqual((selected.id, selected.run_attempt, selected.status), ("99001", 3, "failure"))

    def test_selection_no_match_returns_none(self):
        runs = [make_run(sha="b" * 40)]
        self.assertIsNone(select_quality_ci_run(
            runs, target_commit_sha=SHA_BACKEND, quality_workflow_path=None,
        ))

    def test_selection_empty_input_returns_none(self):
        self.assertIsNone(select_quality_ci_run(
            [], target_commit_sha=SHA_BACKEND, quality_workflow_path=None,
        ))


class ArtifactEnabledCISourceTests(unittest.TestCase):
    def setUp(self):
        if ArtifactEnabledCISource is None:
            self.skipTest("actions_artifacts module not implemented yet")

    def test_validated_artifact_enriches_selected_run_and_publishes_evidence(self):
        json_routes, binary_routes = valid_case_routes()
        wrapper, ledger, json_inner, binary_inner = make_wrapper(
            [make_run()], json_routes, binary_routes,
        )
        runs = wrapper.get_ci_results("proj", commit_sha=SHA_BACKEND)
        self.assertEqual(len(runs), 1)
        selected = runs[0]
        self.assertEqual(selected.total_tests, 49)
        self.assertEqual(selected.failed_tests, 0)
        self.assertEqual(selected.coverage_percentage, 97.9)
        self.assertEqual(selected.run_attempt, 2)
        self.assertEqual(selected.status, "success")

        evidence = wrapper.get_evidence("proj", commit_sha=SHA_BACKEND)
        artifact_items = [item for item in evidence
                          if item.provenance.id == "github_actions_artifact_12345"]
        self.assertEqual(len(artifact_items), 1)
        payload = artifact_items[0].provenance.payload
        self.assertEqual(payload["outcome"], CiArtifactOutcome.validated.value)
        self.assertTrue(payload["certifies_metrics"])
        self.assertEqual(payload["artifact_name"], f"ci-quality-{SHA_BACKEND}-12345-2")

    def test_missing_artifact_keeps_metrics_none(self):
        json_routes, binary_routes = valid_case_routes()
        json_routes = {
            f"{API_ORIGIN}/repos/example-org/backend/actions/runs/12345/artifacts":
                artifacts_list([]),
        }
        wrapper, ledger, json_inner, binary_inner = make_wrapper([], json_routes, binary_routes)
        runs_list = [make_run()]
        wrapper = replace_runs(wrapper, runs_list)
        runs = wrapper.get_ci_results("proj", commit_sha=SHA_BACKEND)
        self.assertIsNone(runs[0].total_tests)
        self.assertIsNone(runs[0].coverage_percentage)
        evidence = wrapper.get_evidence("proj", commit_sha=SHA_BACKEND)
        payload = next(
            item.provenance.payload for item in evidence
            if item.provenance.id == "github_actions_artifact_12345"
        )
        self.assertEqual(payload["outcome"], CiArtifactOutcome.artifact_missing.value)
        self.assertFalse(payload["certifies_metrics"])
        self.assertEqual(len(binary_inner.calls), 0)

    def test_in_progress_run_skips_artifact_queries(self):
        json_routes, binary_routes = valid_case_routes()
        wrapper, ledger, json_inner, binary_inner = make_wrapper(
            [make_run(status="in_progress")], json_routes, binary_routes,
        )
        runs = wrapper.get_ci_results("proj", commit_sha=SHA_BACKEND)
        self.assertIsNone(runs[0].total_tests)
        evidence = wrapper.get_evidence("proj", commit_sha=SHA_BACKEND)
        payload = next(
            item.provenance.payload for item in evidence
            if item.provenance.id == "github_actions_artifact_12345"
        )
        self.assertEqual(payload["reason"], "workflow_not_final")
        self.assertEqual(len(json_inner.calls), 0)
        self.assertEqual(len(binary_inner.calls), 0)

    def test_unknown_attempt_skips_artifact_queries(self):
        json_routes, binary_routes = valid_case_routes()
        wrapper, ledger, json_inner, binary_inner = make_wrapper(
            [make_run(run_attempt=None)], json_routes, binary_routes,
        )
        wrapper.get_ci_results("proj", commit_sha=SHA_BACKEND)
        evidence = wrapper.get_evidence("proj", commit_sha=SHA_BACKEND)
        payload = next(
            item.provenance.payload for item in evidence
            if item.provenance.id == "github_actions_artifact_12345"
        )
        self.assertEqual(payload["reason"], "attempt_unknown")
        self.assertEqual(len(json_inner.calls), 0)
        self.assertEqual(len(binary_inner.calls), 0)

    def test_ambiguous_artifact_rows_are_rejected(self):
        json_routes, binary_routes = valid_case_routes()
        json_routes = {
            f"{API_ORIGIN}/repos/example-org/backend/actions/runs/12345/artifacts":
                artifacts_list([
                    artifact_metadata_row(artifact_id=77),
                    artifact_metadata_row(artifact_id=78),
                ]),
        }
        wrapper, ledger, json_inner, binary_inner = make_wrapper([], json_routes, binary_routes)
        wrapper = replace_runs(wrapper, [make_run()])
        runs = wrapper.get_ci_results("proj", commit_sha=SHA_BACKEND)
        self.assertIsNone(runs[0].total_tests)
        evidence = wrapper.get_evidence("proj", commit_sha=SHA_BACKEND)
        payload = next(
            item.provenance.payload for item in evidence
            if item.provenance.id == "github_actions_artifact_12345"
        )
        self.assertEqual(payload["outcome"], CiArtifactOutcome.identity_mismatch.value)
        self.assertEqual(payload["reason"], "ambiguous_artifact_rows")
        self.assertEqual(len(binary_inner.calls), 0)

    def test_no_target_run_selects_nothing(self):
        json_routes, binary_routes = valid_case_routes()
        wrapper, ledger, json_inner, binary_inner = make_wrapper([], json_routes, binary_routes)
        wrapper = replace_runs(wrapper, [make_run(sha="b" * 40)])
        runs = wrapper.get_ci_results("proj", commit_sha=SHA_BACKEND)
        self.assertEqual(len(runs), 1)
        evidence = wrapper.get_evidence("proj", commit_sha=SHA_BACKEND)
        self.assertEqual(
            [item for item in evidence if item.provenance.id.startswith("github_actions_artifact_")],
            [],
        )
        self.assertEqual(len(json_inner.calls), 0)

    def test_missing_pinned_sha_skips_artifacts(self):
        json_routes, binary_routes = valid_case_routes()
        wrapper, ledger, json_inner, binary_inner = make_wrapper(
            [make_run()], json_routes, binary_routes,
        )
        wrapper.get_ci_results("proj", commit_sha=None)
        evidence = wrapper.get_evidence("proj", commit_sha=None)
        self.assertEqual(
            [item for item in evidence if item.provenance.id.startswith("github_actions_artifact_")],
            [],
        )
        self.assertEqual(len(json_inner.calls), 0)

    def test_shared_ledger_accounts_metadata_and_artifact_hops(self):
        identity = RepositoryIdentity(id="backend", owner="example-org", repo="backend")
        json_routes, binary_routes = valid_case_routes()
        wrapper, ledger, json_inner, binary_inner = make_wrapper(
            [make_run()], json_routes, binary_routes, identity=identity, ledger=new_ledger(identity),
        )
        wrapper.get_ci_results("proj", commit_sha=SHA_BACKEND)
        receipt = ledger.repository_receipt(identity)
        self.assertGreaterEqual(receipt.http_calls, 2)
        self.assertGreater(receipt.response_bytes, 0)
        self.assertFalse(receipt.exhausted)

    def test_two_repositories_share_one_ledger_without_reset(self):
        backend = RepositoryIdentity(id="backend", owner="example-org", repo="backend")
        frontend = RepositoryIdentity(id="frontend", owner="example-org", repo="frontend")
        ledger = new_ledger()
        ledger.begin_repository(backend)
        json_routes, binary_routes = valid_case_routes()
        wrapper, ledger, json_inner, binary_inner = make_wrapper(
            [make_run()], json_routes, binary_routes, identity=backend, ledger=ledger,
        )
        wrapper.get_ci_results("proj", commit_sha=SHA_BACKEND)
        backend_receipt = ledger.repository_receipt(backend)
        frontend_receipt = ledger.repository_receipt(frontend)
        self.assertGreaterEqual(backend_receipt.http_calls, 2)
        self.assertEqual(frontend_receipt.http_calls, 0)


def replace_runs(wrapper, runs):
    """Swap the runs source of a wrapper for precise error-path cases."""
    wrapper._runs_source = FakeRunsSource(runs)
    return wrapper


class ActionsRowMetadataTests(unittest.TestCase):
    """The actions adapter publishes run_attempt and workflow_path when rows provide them."""

    def setUp(self):
        self.sha = SHA_BACKEND

    def fake_transport(self, rows):
        body = {"total_count": len(rows), "workflow_runs": list(rows)}
        url = f"{API_ORIGIN}/repos/example-org/backend/actions/runs"
        return FakePlainGetTransport({url: body})

    def base_row(self, *, include_optional=False, run_attempt=2, path=WORKFLOW_PATH):
        row = {
            "id": 12345,
            "name": "Quality",
            "head_sha": self.sha,
            "head_branch": "develop",
            "event": "push",
            "workflow_id": 441,
            "status": "completed",
            "conclusion": "success",
            "updated_at": "2026-10-16T10:00:00Z",
            "created_at": "2026-10-16T09:55:00Z",
            "run_started_at": "2026-10-16T09:55:01Z",
        }
        if include_optional:
            row["run_attempt"] = run_attempt
            row["path"] = path
        return row

    def adapter(self, rows):
        transport = self.fake_transport(rows)
        return GitHubActionsSourceAdapter(
            owner="example-org",
            repo="backend",
            target_commit_sha=self.sha,
            transport=transport,
        )

    def test_rows_with_attempt_and_path_populate_models(self):
        adapter = self.adapter([self.base_row(include_optional=True)])
        runs = adapter.get_ci_results("proj")
        self.assertEqual(runs[0].run_attempt, 2)
        self.assertEqual(runs[0].workflow_path, WORKFLOW_PATH)

    def test_rows_without_optional_fields_keep_prior_shape(self):
        adapter = self.adapter([self.base_row()])
        runs = adapter.get_ci_results("proj")
        run = runs[0]
        self.assertIsNone(run.run_attempt)
        self.assertIsNone(run.workflow_path)
        evidence = adapter.get_evidence("proj")
        serialized = run.model_dump(mode="json")
        self.assertNotIn("run_attempt", serialized)
        self.assertNotIn("workflow_path", serialized)
        self.assertNotIn("run_attempt", evidence[0].provenance.payload)


if __name__ == "__main__":
    unittest.main()
