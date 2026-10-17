"""T4: deterministic quality-workflow selection and bounded artifact metrics integration."""

import copy
import io
import json
import unittest
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from agente_gestion.adapters.sources.actions import GitHubActionsSourceAdapter
import agente_gestion.adapters.sources.actions_artifacts as actions_artifacts
from agente_gestion.adapters.sources.collection_budget import (
    BudgetedHttpGetTransport,
    CollectionBudgetLedger,
)
from agente_gestion.adapters.sources.http_transport import HttpResponse
from agente_gestion.domain.ci_metrics import CiArtifactOutcome, CiWorkflowStatus
from agente_gestion.domain.code import CIRunResult
from agente_gestion.domain.evidence import EvidenceItem, EvidenceProvenance, EvidenceSource
from agente_gestion.domain.repositories import (
    CommitResolutionStatus,
    RepositoryAnalysisState,
    RepositoryIdentity,
)
from agente_gestion.ports.source_collection import RepositorySourceCollectionResult
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
        page = (params or {}).get("page")
        item = self.routes.get((url, page), self.routes.get(url))
        if item is None:
            return HttpResponse(status_code=404, body={}, bytes_received=0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            item = item(params)
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


def new_ledger(first_identity=None, *, project_max_http_calls=50, repository_max_http_calls=20):
    ledger = CollectionBudgetLedger(
        project_max_http_calls=project_max_http_calls,
        repository_max_http_calls=repository_max_http_calls,
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


def routes_for_document(document):
    """One metadata GET and one in-memory bounded ZIP GET for a producer document."""
    metrics_bytes = json.dumps(document).encode()
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


def valid_case_routes(fixture_name="backend-v1.json"):
    """Build deterministic routes from one synthetic contract fixture."""
    return routes_for_document(load_fixture(fixture_name))


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

    def test_newest_run_precedes_a_higher_attempt_on_an_older_run(self):
        newest_failure = make_run(run_id="99001", run_attempt=1, status="failure")
        older_success = make_run(run_id="98000", run_attempt=3, status="success")
        runs = [newest_failure, older_success]  # authoritative newest-first adapter order
        selected = select_quality_ci_run(
            runs, target_commit_sha=SHA_BACKEND, quality_workflow_path=WORKFLOW_PATH,
        )
        self.assertEqual((selected.id, selected.run_attempt, selected.status), ("99001", 1, "failure"))

    def test_duplicate_entries_compare_attempt_only_within_the_same_run(self):
        latest_attempt = make_run(run_id="99001", run_attempt=3, status="failure")
        earlier_attempt = make_run(run_id="99001", run_attempt=1, status="success")
        older_run = make_run(run_id="98000", run_attempt=8, status="success")
        selected = select_quality_ci_run(
            [latest_attempt, earlier_attempt, older_run],
            target_commit_sha=SHA_BACKEND,
            quality_workflow_path=WORKFLOW_PATH,
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

    def _assert_public_scoped_roundtrip(self, document, expected_repository):
        json_routes, binary_routes = routes_for_document(document)
        wrapper, ledger, json_inner, binary_inner = make_wrapper(
            [make_run(status=document["status"])], json_routes, binary_routes,
        )
        run = wrapper.get_ci_results("proj", commit_sha=SHA_BACKEND)[0]

        self.assertTrue(run.metrics_artifact.certifies_metrics)
        self.assertEqual(run.metrics_artifact.metrics.repository, expected_repository)
        self.assertEqual(run.metrics_artifact.metrics.workflow_run_attempt,
                         document.get("workflow_run_attempt"))
        self.assertEqual(run.metrics_artifact.metrics.coverage_basis,
                         document["coverage_basis"])
        self.assertIsNone(run.metrics_artifact.metrics.coverage_percentage)
        self.assertIsNone(run.metrics_artifact.metrics.coverage_metric)
        self.assertEqual(run.metrics_artifact.metrics.test_suites["integration"], None)
        self.assertEqual(run.run_attempt, 2)

        identity = RepositoryIdentity(id="backend", owner="example-org", repo="backend")
        scoped_input = run.model_copy(update={"repository_identity": identity})
        public_run = scoped_input.model_dump(mode="json")
        public_metrics = public_run["metrics_artifact"]["metrics"]
        self.assertEqual(public_metrics["repository"], expected_repository)
        self.assertEqual(public_metrics["workflow_run_attempt"],
                         document.get("workflow_run_attempt"))
        self.assertIsNone(public_metrics["coverage_percentage"])
        self.assertIsNone(public_metrics["coverage_metric"])
        self.assertEqual(public_metrics["coverage_basis"], document["coverage_basis"])

        round_tripped = CIRunResult.model_validate(public_run)
        copied = copy.deepcopy(round_tripped)
        self.assertEqual(copied, round_tripped)
        self.assertEqual(copied.run_attempt, 2)
        self.assertEqual(copied.metrics_artifact.metrics.workflow_run_attempt,
                         document.get("workflow_run_attempt"))
        self.assertEqual(copied.metrics_artifact.metrics.repository, expected_repository)

        state = RepositoryAnalysisState(
            identity=identity,
            branch="develop",
            target_commit_sha=SHA_BACKEND,
            commit_resolution_status=CommitResolutionStatus.resolved,
        )
        scoped = RepositorySourceCollectionResult(repository_state=state, ci_runs=[copied])
        scoped_run = scoped.model_dump(mode="json")["ci_runs"][0]
        self.assertEqual(scoped_run["repository_identity"], identity.model_dump(mode="json"))
        self.assertEqual(scoped_run["metrics_artifact"]["metrics"]["repository"], expected_repository)
        self.assertEqual(scoped_run["metrics_artifact"]["metrics"]["workflow_run_attempt"],
                         document.get("workflow_run_attempt"))
        self.assertEqual(scoped_run["metrics_artifact"]["metrics"]["coverage_basis"],
                         document["coverage_basis"])
        self.assertIsNone(scoped_run["metrics_artifact"]["metrics"]["coverage_percentage"])

        metadata_url = f"{API_ORIGIN}/repos/example-org/backend/actions/runs/12345/artifacts"
        binary_url = f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip"
        expected_bytes = (
            len(json.dumps(json_routes[metadata_url]).encode())
            + len(binary_routes[binary_url].body)
        )
        receipt = ledger.repository_receipt(identity)
        self.assertEqual(receipt.http_calls, 2)
        self.assertEqual(receipt.response_bytes, expected_bytes)
        self.assertFalse(receipt.exhausted)
        self.assertEqual(len(json_inner.calls), 1)
        self.assertEqual(len(binary_inner.calls), 1)
        self.assertEqual(json_inner.calls[0][0], metadata_url)
        self.assertEqual(binary_inner.calls[0]["url"], binary_url)
        artifact_receipt = copied.metrics_artifact.receipt
        self.assertEqual(artifact_receipt.http_calls, 1)
        self.assertGreater(artifact_receipt.compressed_bytes, 0)
        self.assertGreater(artifact_receipt.expanded_bytes, 0)
        self.assertEqual(len(artifact_receipt.hop_statuses), 1)

    def test_omitted_payload_attempt_survives_public_scoped_roundtrip(self):
        document = load_fixture("failure-v1.json")
        document.pop("workflow_run_attempt")
        self.assertIsNone(document.get("workflow_run_attempt"))

        self._assert_public_scoped_roundtrip(document, "example-org/backend")

    def test_repository_casefold_survives_public_scoped_roundtrip(self):
        document = load_fixture("failure-v1.json")
        document["repository"] = "Example-Org/Backend"
        self.assertEqual(document["workflow_run_attempt"], "2")

        self._assert_public_scoped_roundtrip(document, "Example-Org/Backend")

    def test_public_roundtrip_still_rejects_inconsistent_artifact_identity(self):
        document = load_fixture("failure-v1.json")
        json_routes, binary_routes = routes_for_document(document)
        wrapper, _ledger, _json_inner, _binary_inner = make_wrapper(
            [make_run(status=document["status"])], json_routes, binary_routes,
        )
        run = wrapper.get_ci_results("proj", commit_sha=SHA_BACKEND)[0]
        identity = RepositoryIdentity(id="backend", owner="example-org", repo="backend")
        public_run = run.model_copy(update={"repository_identity": identity}).model_dump(mode="json")
        invalid_values = (
            ("metrics_artifact.metrics.commit_sha", "b" * 40),
            ("metrics_artifact.metrics.workflow_run_id", "99999"),
            ("metrics_artifact.metrics.workflow_run_attempt", "3"),
            ("metrics_artifact.metrics.repository", "foreign-org/backend"),
            ("metrics_artifact.artifact_name", "ci-quality-wrong"),
        )
        for dotted_path, value in invalid_values:
            with self.subTest(field=dotted_path):
                invalid = copy.deepcopy(public_run)
                target = invalid
                parts = dotted_path.split(".")
                for part in parts[:-1]:
                    target = target[part]
                target[parts[-1]] = value
                with self.assertRaises(ValueError):
                    CIRunResult.model_validate(invalid)

        missing_actual_attempt = copy.deepcopy(public_run)
        missing_actual_attempt.pop("run_attempt")
        with self.assertRaises(ValueError):
            CIRunResult.model_validate(missing_actual_attempt)

        unknown_actual_attempt = copy.deepcopy(public_run)
        unknown_actual_attempt.pop("run_attempt")
        unknown_actual_attempt["metrics_artifact"]["metrics"]["workflow_run_attempt"] = None
        with self.assertRaises(ValueError):
            CIRunResult.model_validate(unknown_actual_attempt)
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
        self.assertEqual(selected.metrics_artifact.outcome, CiArtifactOutcome.validated)
        self.assertTrue(selected.metrics_artifact.certifies_metrics)
        self.assertEqual(selected.metrics_artifact.metrics.coverage_basis,
                         "Loaded backend TypeScript sources in src/**/*.ts.")
        self.assertEqual(selected.metrics_artifact.metrics.coverage_metric, "lines")
        self.assertIsNone(selected.metrics_artifact.metrics.e2e_status)
        public_run = selected.model_dump(mode="json")
        self.assertEqual(public_run["metrics_artifact"]["metrics"]["coverage_basis"],
                         "Loaded backend TypeScript sources in src/**/*.ts.")

        evidence = wrapper.get_evidence("proj", commit_sha=SHA_BACKEND)
        artifact_items = [item for item in evidence
                          if item.provenance.id == "github_actions_artifact_12345"]
        self.assertEqual(len(artifact_items), 1)
        payload = artifact_items[0].provenance.payload
        self.assertEqual(payload["outcome"], CiArtifactOutcome.validated.value)
        self.assertTrue(payload["certifies_metrics"])
        self.assertEqual(payload["artifact_name"], f"ci-quality-{SHA_BACKEND}-12345-2")

    def test_validated_failure_artifact_preserves_nullable_public_metrics(self):
        json_routes, binary_routes = valid_case_routes("failure-v1.json")
        wrapper, _ledger, _json_inner, _binary_inner = make_wrapper(
            [make_run(status="failure")], json_routes, binary_routes,
        )

        run = wrapper.get_ci_results("proj", commit_sha=SHA_BACKEND)[0]
        public_run = run.model_dump(mode="json")

        self.assertEqual(run.metrics_artifact.outcome, CiArtifactOutcome.validated)
        self.assertTrue(run.metrics_artifact.certifies_metrics)
        self.assertEqual(run.total_tests, 49)
        self.assertEqual(run.failed_tests, 2)
        self.assertIsNone(run.coverage_percentage)
        self.assertIsNone(run.metrics_artifact.metrics.coverage_percentage)
        self.assertIsNone(run.metrics_artifact.metrics.coverage_metric)
        self.assertEqual(run.metrics_artifact.metrics.coverage_basis,
                         "Coverage was unavailable in this failed run.")
        self.assertIsNone(run.metrics_artifact.metrics.test_suites["integration"])
        public_metrics = public_run["metrics_artifact"]["metrics"]
        self.assertIsNone(public_metrics["coverage_percentage"])
        self.assertIsNone(public_metrics["coverage_metric"])
        self.assertIsNone(public_metrics["test_suites"]["integration"])
        identity = RepositoryIdentity(id="backend", owner="example-org", repo="backend")
        scoped_run = CIRunResult.model_validate({
            **run.model_dump(),
            "repository_identity": identity,
        })
        scoped = RepositorySourceCollectionResult(
            repository_state=RepositoryAnalysisState(
                identity=identity,
                branch="develop",
                target_commit_sha=SHA_BACKEND,
                commit_resolution_status=CommitResolutionStatus.resolved,
            ),
            ci_runs=[scoped_run],
        )
        scoped_public = scoped.model_dump(mode="json")["ci_runs"][0]["metrics_artifact"]["metrics"]
        self.assertIsNone(scoped_public["coverage_percentage"])
        self.assertIsNone(scoped_public["coverage_metric"])
        self.assertEqual(scoped_public["coverage_basis"],
                         "Coverage was unavailable in this failed run.")

    def test_newest_failed_run_does_not_fall_back_to_older_green_artifact(self):
        newest_failure = make_run(run_id="99001", run_attempt=1, status="failure")
        older_success = make_run(run_id="98000", run_attempt=3, status="success")
        json_routes, binary_routes = valid_case_routes()
        wrapper, _ledger, json_inner, _binary_inner = make_wrapper(
            [newest_failure, older_success], json_routes, binary_routes,
        )

        runs = wrapper.get_ci_results("proj", commit_sha=SHA_BACKEND)
        metadata_calls = [call for call in json_inner.calls if "/actions/runs/" in call[0]]

        self.assertEqual([(run.id, run.run_attempt) for run in runs], [("99001", 1), ("98000", 3)])
        self.assertEqual(len(metadata_calls), 1)
        self.assertTrue(metadata_calls[0][0].endswith("/runs/99001/artifacts"))
        self.assertTrue(all(run.total_tests is None for run in runs))

    def test_pending_or_unknown_newest_run_does_not_fall_back_to_old_success(self):
        older_success = make_run(run_id="98000", run_attempt=3, status="success")
        newest_cases = (
            make_run(run_id="99001", run_attempt=1, status="in_progress"),
            make_run(run_id="99002", run_attempt=1, status="queued"),
            make_run(run_id="99003", run_attempt=1, status="unknown"),
            make_run(run_id="99004", run_attempt=None, status="success"),
        )
        for newest in newest_cases:
            with self.subTest(run_id=newest.id, status=newest.status, attempt=newest.run_attempt):
                json_routes, binary_routes = valid_case_routes()
                wrapper, _ledger, json_inner, binary_inner = make_wrapper(
                    [newest, older_success], json_routes, binary_routes,
                )
                runs = wrapper.get_ci_results("proj", commit_sha=SHA_BACKEND)
                evidence = wrapper.get_evidence("proj", commit_sha=SHA_BACKEND)
                artifacts = [item for item in evidence
                             if item.provenance.id.startswith("github_actions_artifact_")]
                self.assertEqual(runs[0].id, newest.id)
                self.assertTrue(all(run.total_tests is None for run in runs))
                self.assertEqual(len(artifacts), 1)
                self.assertEqual(artifacts[0].provenance.payload["run_id"], newest.id)
                self.assertEqual(artifacts[0].provenance.payload["certifies_metrics"], False)
                self.assertEqual(len(json_inner.calls), 0)
                self.assertEqual(len(binary_inner.calls), 0)

    def test_paginated_metadata_finds_later_matching_artifact(self):
        matching = artifact_metadata_row()
        unrelated = [
            artifact_metadata_row(artifact_id=100 + index, name=f"unrelated-{index}")
            for index in range(100)
        ]
        url = f"{API_ORIGIN}/repos/example-org/backend/actions/runs/12345/artifacts"
        routes = {
            (url, 1): artifacts_list(unrelated, total=101),
            (url, 2): artifacts_list([matching], total=101),
        }
        _json, binary_routes = valid_case_routes()
        wrapper, ledger, json_inner, binary_inner = make_wrapper([make_run()], routes, binary_routes)

        runs = wrapper.get_ci_results("proj", commit_sha=SHA_BACKEND)

        self.assertEqual(runs[0].total_tests, 49)
        metadata_calls = [call for call in json_inner.calls if call[0] == url]
        self.assertEqual([call[1]["page"] for call in metadata_calls], [1, 2])
        self.assertTrue(all(call[1]["per_page"] == 100 for call in metadata_calls))
        self.assertTrue(all(0 < call[3] <= 5.0 for call in metadata_calls))
        self.assertEqual(len(binary_inner.calls), 1)
        self.assertEqual(ledger.repository_receipt(RepositoryIdentity(
            id="backend", owner="example-org", repo="backend",
        )).http_calls, 3)

    def test_duplicate_artifacts_across_pages_are_ambiguous(self):
        duplicate = artifact_metadata_row()
        url = f"{API_ORIGIN}/repos/example-org/backend/actions/runs/12345/artifacts"
        first_page = [duplicate] + [
            artifact_metadata_row(artifact_id=300 + index, name=f"unrelated-{index}")
            for index in range(99)
        ]
        routes = {
            (url, 1): artifacts_list(first_page, total=101),
            (url, 2): artifacts_list([duplicate], total=101),
        }
        _json, binary_routes = valid_case_routes()
        wrapper, _ledger, json_inner, binary_inner = make_wrapper([make_run()], routes, binary_routes)

        runs = wrapper.get_ci_results("proj", commit_sha=SHA_BACKEND)
        evidence = wrapper.get_evidence("proj", commit_sha=SHA_BACKEND)
        artifact = next(item for item in evidence
                        if item.provenance.id == "github_actions_artifact_12345")

        self.assertIsNone(runs[0].total_tests)
        self.assertEqual(artifact.provenance.payload["outcome"], CiArtifactOutcome.identity_mismatch.value)
        self.assertEqual(artifact.provenance.payload["reason"], "ambiguous_artifact_rows")
        self.assertEqual(len([call for call in json_inner.calls if call[0] == url]), 2)
        self.assertEqual(len(binary_inner.calls), 0)

    def test_incomplete_metadata_pages_never_certify_first_page_match(self):
        first_page = [artifact_metadata_row()]
        url = f"{API_ORIGIN}/repos/example-org/backend/actions/runs/12345/artifacts"
        routes = {(url, 1): artifacts_list(first_page, total=1001)}
        _json, binary_routes = valid_case_routes()
        wrapper, _ledger, json_inner, binary_inner = make_wrapper([make_run()], routes, binary_routes)

        runs = wrapper.get_ci_results("proj", commit_sha=SHA_BACKEND)
        evidence = wrapper.get_evidence("proj", commit_sha=SHA_BACKEND)
        artifact = next(item for item in evidence
                        if item.provenance.id == "github_actions_artifact_12345")

        self.assertIsNone(runs[0].total_tests)
        self.assertEqual(artifact.provenance.payload["outcome"], CiArtifactOutcome.limit_exceeded.value)
        self.assertEqual(artifact.provenance.payload["reason"], "artifact_metadata_incomplete")
        self.assertEqual(len([call for call in json_inner.calls if call[0] == url]), 1)
        self.assertEqual(len(binary_inner.calls), 0)

    def test_metadata_page_budget_exhaustion_is_typed_and_noncertifying(self):
        identity = RepositoryIdentity(id="backend", owner="example-org", repo="backend")
        ledger = new_ledger(identity, project_max_http_calls=10, repository_max_http_calls=1)
        url = f"{API_ORIGIN}/repos/example-org/backend/actions/runs/12345/artifacts"
        first_page = [
            artifact_metadata_row(artifact_id=300 + index, name=f"unrelated-{index}")
            for index in range(100)
        ]
        routes = {
            (url, 1): artifacts_list(first_page, total=101),
            (url, 2): artifacts_list([artifact_metadata_row()], total=101),
        }
        _json, binary_routes = valid_case_routes()
        wrapper, _ledger, json_inner, binary_inner = make_wrapper(
            [make_run()], routes, binary_routes, ledger=ledger, identity=identity,
        )

        runs = wrapper.get_ci_results("proj", commit_sha=SHA_BACKEND)
        evidence = wrapper.get_evidence("proj", commit_sha=SHA_BACKEND)
        artifact = next(item for item in evidence
                        if item.provenance.id == "github_actions_artifact_12345")

        self.assertIsNone(runs[0].total_tests)
        self.assertEqual(artifact.provenance.payload["outcome"], CiArtifactOutcome.limit_exceeded.value)
        self.assertFalse(artifact.provenance.payload["certifies_metrics"])
        self.assertEqual(ledger.repository_receipt(identity).http_calls, 1)
        self.assertTrue(ledger.repository_receipt(identity).exhausted)
        self.assertEqual(len(binary_inner.calls), 0)

    def test_metadata_deadline_stops_pagination_before_later_page(self):
        matching = artifact_metadata_row()
        url = f"{API_ORIGIN}/repos/example-org/backend/actions/runs/12345/artifacts"
        first_page = [matching] + [
            artifact_metadata_row(artifact_id=300 + index, name=f"unrelated-{index}")
            for index in range(99)
        ]
        routes = {
            (url, 1): artifacts_list(first_page, total=101),
            (url, 2): artifacts_list([
                artifact_metadata_row(artifact_id=499, name="last-unrelated")
            ], total=101),
        }
        _json, binary_routes = valid_case_routes()
        wrapper, _ledger, json_inner, binary_inner = make_wrapper([make_run()], routes, binary_routes)
        times = iter([0.0, 0.0, 6.0, 6.0, 6.0])
        clock = lambda: next(times, 6.0)

        with patch.object(actions_artifacts, "time", SimpleNamespace(monotonic=clock), create=True):
            runs = wrapper.get_ci_results("proj", commit_sha=SHA_BACKEND)
        evidence = wrapper.get_evidence("proj", commit_sha=SHA_BACKEND)
        artifact = next(item for item in evidence
                        if item.provenance.id == "github_actions_artifact_12345")

        self.assertIsNone(runs[0].total_tests)
        self.assertEqual(artifact.provenance.payload["outcome"], CiArtifactOutcome.timeout.value)
        self.assertEqual(artifact.provenance.payload["reason"], "artifact_metadata_timeout")
        self.assertEqual(len([call for call in json_inner.calls if call[0] == url]), 1)
        self.assertEqual(len(binary_inner.calls), 0)

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

    def test_no_selector_rejects_competing_workflow_paths_explicitly(self):
        routes = {}
        wrapper, _ledger, json_inner, binary_inner = make_wrapper(
            [
                make_run(run_id="99001", run_attempt=1, workflow_path=".github/workflows/quality-a.yml"),
                make_run(run_id="98000", run_attempt=3, workflow_path=".github/workflows/quality-b.yml"),
            ],
            routes,
            {},
            workflow_path=None,
        )

        runs = wrapper.get_ci_results("proj", commit_sha=SHA_BACKEND)
        evidence = wrapper.get_evidence("proj", commit_sha=SHA_BACKEND)
        selections = [item for item in evidence
                      if item.provenance.id == "github_actions_artifact_selection"]

        self.assertEqual(len(runs), 2)
        self.assertTrue(all(run.total_tests is None for run in runs))
        self.assertEqual(len(selections), 1)
        self.assertEqual(selections[0].provenance.payload["outcome"],
                         CiArtifactOutcome.invalid_context.value)
        self.assertEqual(selections[0].provenance.payload["reason"], "ambiguous_quality_workflow")
        self.assertEqual(len(json_inner.calls), 0)
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
        self.assertNotIn("metrics_artifact", serialized)
        self.assertNotIn("run_attempt", evidence[0].provenance.payload)


if __name__ == "__main__":
    unittest.main()
