"""Behavioral contracts for standalone CI artifact transport and validation."""

import io
import json
import os
from pathlib import Path
import struct
import unittest
import urllib3
import warnings
from jsonschema import Draft202012Validator

from agente_gestion.adapters.sources.ci_metrics_schema import CI_METRICS_V1_SCHEMA
import zipfile

from agente_gestion.adapters.sources.artifacts import CiMetricsArtifactValidator
from agente_gestion.adapters.sources.binary_transport import BoundedBinaryGetTransport
from agente_gestion.adapters.sources.collection_budget import (
    BudgetedHttpGetTransport,
    CollectionBudgetLedger,
    active_budget_scope,
)
from agente_gestion.domain.ci_metrics import (
    CiArtifactMetadata,
    CiArtifactOutcome,
    CiMetricsValidationContext,
    CiWorkflowStatus,
)
from agente_gestion.domain.repositories import RepositoryIdentity
from agente_gestion.ports.source_artifacts import (
    ArtifactTransportError,
    BinaryHttpResponse,
    CiArtifactLimits,
)
from agente_gestion.ports.source_errors import SourceError, SourceErrorCategory, SourceProvider


FIXTURES = Path(__file__).parent / "fixtures" / "ci_metrics"
SHA_BACKEND = "a" * 40
SHA_FRONTEND = "b" * 40
API_ORIGIN = "https://api.github.com"
STORAGE_URL = "https://productionresultssa0.blob.core.windows.net/container/archive.zip?sig=signed-query"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def context(
    repository="example-org/backend",
    sha=SHA_BACKEND,
    run_id="12345",
    attempt="2",
    status=CiWorkflowStatus.success,
):
    return CiMetricsValidationContext(repository, sha, run_id, attempt, status)


def metadata(
    *,
    repository="example-org/backend",
    sha=SHA_BACKEND,
    run_id=12345,
    attempt="2",
    artifact_id=77,
    expired=False,
    workflow_attempt=None,
    name=None,
    include_head_sha=True,
    archive_url="https://evil.invalid/arbitrary.zip",
):
    workflow_run = {"id": run_id}
    if include_head_sha:
        workflow_run["head_sha"] = sha
    if workflow_attempt is not None:
        workflow_run["run_attempt"] = workflow_attempt
    workflow_run["head_repository"] = {"full_name": repository}
    return {
        "id": artifact_id,
        "name": name or f"ci-quality-{sha}-{run_id}-{attempt}",
        "expired": expired,
        "workflow_run": workflow_run,
        "archive_download_url": archive_url,
    }


def zip_member(body, *, name="ci-metrics.json", compression=zipfile.ZIP_DEFLATED, mode=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
        info = zipfile.ZipInfo(name)
        info.compress_type = compression
        if mode is not None:
            info.external_attr = mode << 16
        archive.writestr(info, body)
    return stream.getvalue()


def zip_member_streaming(body):
    class NonSeekableBuffer(io.BytesIO):
        def seekable(self):
            return False

        def seek(self, *args, **kwargs):
            raise io.UnsupportedOperation("non-seekable fixture")

    stream = NonSeekableBuffer()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ci-metrics.json", body)
    return stream.getvalue()


def zip_members(*members):
    """members are (name, bytes, compression, mode) tuples."""
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body, compression, mode in members:
            info = zipfile.ZipInfo(name)
            info.compress_type = compression
            if mode is not None:
                info.external_attr = mode << 16
            archive.writestr(info, body)
    return stream.getvalue()


def response(status=200, body=b"", headers=None, *, received=None):
    return BinaryHttpResponse(
        status_code=status,
        headers=headers or {},
        body=body,
        bytes_received=len(body) if received is None else received,
    )


class FakeBinaryTransport:
    """In-memory one-hop transport; it never opens a socket."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout_seconds=None):
        self.calls.append({"url": url, "headers": dict(headers or {}), "timeout": timeout_seconds})
        item = self.routes.get(url)
        if isinstance(item, BaseException):
            raise item
        if item is None:
            raise ArtifactTransportError(
                SourceErrorCategory.network, attempted=True, status_code=None,
            )
        return item


class MemoryRaw:
    def __init__(self, body, *, overread=0):
        self.stream = io.BytesIO(body)
        self.overread = overread
        self.closed = False
        self.requested = []

    def read(self, amount, decode_content=False):
        self.requested.append((amount, decode_content))
        chunk = self.stream.read(amount + self.overread)
        return chunk

    def close(self):
        self.closed = True


class MemoryResponse:
    def __init__(self, body, *, status=200, headers=None, raw=None):
        self.status_code = status
        self.headers = headers or {}
        self.raw = raw or MemoryRaw(body)
        self.closed = False

    def close(self):
        self.closed = True
        self.raw.close()


class MemorySession:
    def __init__(self, response):
        self.response = response
        self.trust_env = True
        self.closed = False
        self.calls = []
        self.mounts = []

    def mount(self, prefix, adapter):
        self.mounts.append((prefix, adapter))

    def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.response

    def close(self):
        self.closed = True


def ledger_for(identity, *, project_calls=5, repo_calls=5, project_bytes=1_000_000, repo_bytes=1_000_000):
    ledger = CollectionBudgetLedger(
        project_max_http_calls=project_calls,
        repository_max_http_calls=repo_calls,
        project_max_files=10,
        repository_max_files=10,
        project_max_response_bytes=project_bytes,
        repository_max_response_bytes=repo_bytes,
        project_timeout_seconds=30,
        repository_timeout_seconds=30,
        project_max_estimated_tokens=100,
        repository_max_estimated_tokens=100,
        default_request_timeout_seconds=10,
        per_response_max_bytes=1_000_000,
        clock=lambda: 0.0,
    )
    ledger.begin_repository(identity)
    return ledger


class CiMetricsArtifactTests(unittest.TestCase):
    def test_faithful_backend_and_frontend_contract_fixtures_validate_without_invented_suites(self):
        backend = load_fixture("backend-v1.json")
        backend_transport = FakeBinaryTransport({
            f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip":
                response(body=zip_member(json.dumps(backend).encode()))
        })
        backend_result = CiMetricsArtifactValidator(backend_transport).validate(
            context(), metadata(),
        )
        self.assertEqual(backend_result.outcome, CiArtifactOutcome.validated)
        self.assertTrue(backend_result.certifies_metrics)
        self.assertEqual(backend_result.metrics.total_tests, 49)
        self.assertEqual(backend_result.metrics.failed_tests, 0)
        self.assertEqual(backend_result.metrics.test_suites["integration"].tests, 5)

        frontend = load_fixture("frontend-v1.json")
        frontend_context = context(
            repository="example-org/frontend", sha=SHA_FRONTEND,
            run_id="23456", attempt="1",
        )
        frontend_metadata = metadata(
            repository="example-org/frontend", sha=SHA_FRONTEND,
            run_id=23456, attempt="1", artifact_id=88,
        )
        frontend_transport = FakeBinaryTransport({
            f"{API_ORIGIN}/repos/example-org/frontend/actions/artifacts/88/zip":
                response(body=zip_member(json.dumps(frontend).encode()))
        })
        frontend_result = CiMetricsArtifactValidator(frontend_transport).validate(
            frontend_context, frontend_metadata,
        )
        self.assertTrue(frontend_result.certifies_metrics)
        self.assertEqual(frontend_result.metrics.coverage_basis,
                         "Loaded frontend TypeScript sources; UI TSX is excluded.")
        self.assertIsNone(frontend_result.metrics.test_suites)
        self.assertEqual(frontend_result.metrics.e2e_status, "not_implemented")
        self.assertEqual(frontend_transport.calls[0]["url"],
                         f"{API_ORIGIN}/repos/example-org/frontend/actions/artifacts/88/zip")

    def test_published_reports_bundle_accepts_root_and_safe_directory_prefix(self):
        metrics_bytes = json.dumps(load_fixture("backend-v1.json")).encode()
        # Auxiliary reports are synthetic; the published ZIP bytes were not fetched.
        auxiliary = (
            ("unit-summary.json", b'{"fixture":"synthetic unit summary"}'),
            ("integration-summary.json", b'{"fixture":"synthetic integration summary"}'),
        )
        for prefix in ("", "reports/"):
            with self.subTest(prefix=prefix):
                members = []
                if prefix:
                    members.append(("reports/", b"", zipfile.ZIP_STORED, 0o40755))
                members.extend(
                    (prefix + name, body, zipfile.ZIP_DEFLATED, None)
                    for name, body in (*auxiliary, ("ci-metrics.json", metrics_bytes))
                )
                archive = zip_members(*members)
                result = self._validate_archive_bytes(archive)
                self.assertEqual(result.outcome, CiArtifactOutcome.validated)
                self.assertTrue(result.certifies_metrics)
                self.assertEqual(result.metrics.total_tests, 49)
                self.assertEqual(result.receipt.archive_entries, len(members))
                self.assertEqual(
                    result.receipt.expanded_bytes,
                    sum(len(body) for _name, body, _compression, _mode in members),
                )

    def test_published_bundle_validates_auxiliary_paths_types_crc_and_compression(self):
        metrics_bytes = json.dumps(load_fixture("backend-v1.json")).encode()
        valid_aux = b'{"fixture":"synthetic summary"}'
        archives = [
            ("unsafe auxiliary path", zip_members(
                ("reports/../unit-summary.json", valid_aux, zipfile.ZIP_DEFLATED, None),
                ("reports/ci-metrics.json", metrics_bytes, zipfile.ZIP_DEFLATED, None),
            )),
            ("auxiliary symlink", zip_members(
                ("reports/unit-summary.json", valid_aux, zipfile.ZIP_DEFLATED, 0o120777),
                ("reports/ci-metrics.json", metrics_bytes, zipfile.ZIP_DEFLATED, None),
            )),
            ("unsupported auxiliary compression", zip_members(
                ("reports/unit-summary.json", valid_aux, zipfile.ZIP_BZIP2, None),
                ("reports/ci-metrics.json", metrics_bytes, zipfile.ZIP_DEFLATED, None),
            )),
        ]
        corrupt_aux = bytearray(zip_members(
            ("reports/unit-summary.json", valid_aux, zipfile.ZIP_DEFLATED, None),
            ("reports/ci-metrics.json", metrics_bytes, zipfile.ZIP_DEFLATED, None),
        ))
        local = corrupt_aux.find(b"PK\x03\x04")
        central = corrupt_aux.find(b"PK\x01\x02")
        local_crc = struct.unpack_from("<L", corrupt_aux, local + 14)[0]
        central_crc = struct.unpack_from("<L", corrupt_aux, central + 16)[0]
        struct.pack_into("<L", corrupt_aux, local + 14, local_crc ^ 1)
        struct.pack_into("<L", corrupt_aux, central + 16, central_crc ^ 1)
        archives.append(("corrupt auxiliary CRC", bytes(corrupt_aux)))

        truncated_aux = bytearray(zip_members(
            ("reports/unit-summary.json", valid_aux, zipfile.ZIP_DEFLATED, None),
            ("reports/ci-metrics.json", metrics_bytes, zipfile.ZIP_DEFLATED, None),
        ))
        local = truncated_aux.find(b"PK\x03\x04")
        local_name_size, local_extra_size = struct.unpack_from("<HH", truncated_aux, local + 26)
        data_start = local + 30 + local_name_size + local_extra_size
        compressed_size = struct.unpack_from("<L", truncated_aux, local + 18)[0]
        del truncated_aux[data_start + compressed_size - 1]
        struct.pack_into("<L", truncated_aux, local + 18, compressed_size - 1)
        central = truncated_aux.find(b"PK\x01\x02")
        struct.pack_into("<L", truncated_aux, central + 20, compressed_size - 1)
        first_name, first_extra, first_comment = struct.unpack_from("<3H", truncated_aux, central + 28)
        second_central = truncated_aux.find(b"PK\x01\x02", central + 46 + first_name + first_extra + first_comment)
        second_local = struct.unpack_from("<L", truncated_aux, second_central + 42)[0]
        struct.pack_into("<L", truncated_aux, second_central + 42, second_local - 1)
        eocd = truncated_aux.rfind(b"PK\x05\x06")
        directory_offset = struct.unpack_from("<L", truncated_aux, eocd + 16)[0]
        struct.pack_into("<L", truncated_aux, eocd + 16, directory_offset - 1)
        archives.append(("auxiliary missing actual deflate EOF", bytes(truncated_aux)))

        encrypted_aux = bytearray(zip_members(
            ("reports/unit-summary.json", valid_aux, zipfile.ZIP_DEFLATED, None),
            ("reports/ci-metrics.json", metrics_bytes, zipfile.ZIP_DEFLATED, None),
        ))
        encrypted_central = encrypted_aux.find(b"PK\x01\x02")
        struct.pack_into("<H", encrypted_aux, 6,
                         struct.unpack_from("<H", encrypted_aux, 6)[0] | 1)
        struct.pack_into("<H", encrypted_aux, encrypted_central + 8,
                         struct.unpack_from("<H", encrypted_aux, encrypted_central + 8)[0] | 1)
        archives.append(("encrypted auxiliary member", bytes(encrypted_aux)))

        zip64_aux = bytearray(zip_members(
            ("reports/unit-summary.json", valid_aux, zipfile.ZIP_DEFLATED, None),
            ("reports/ci-metrics.json", metrics_bytes, zipfile.ZIP_DEFLATED, None),
        ))
        zip64_central = zip64_aux.find(b"PK\x01\x02")
        struct.pack_into("<L", zip64_aux, zip64_central + 24, 0xFFFFFFFF)
        archives.append(("ZIP64 auxiliary size sentinel", bytes(zip64_aux)))

        for label, archive in archives:
            with self.subTest(label=label):
                result = self._validate_archive_bytes(archive)
                self.assertEqual(result.outcome, CiArtifactOutcome.invalid_archive)
                self.assertIsNone(result.metrics)
                self.assertFalse(result.certifies_metrics)

    def test_complete_suite_relationships_are_checked_for_every_workflow_status(self):
        status_cases = (
            (CiWorkflowStatus.success, ("aggregate total", lambda d: d.update(total_tests=50))),
            (CiWorkflowStatus.success, ("suite category sum", lambda d: d["test_suites"]["unit"].update(passed=43))),
            (CiWorkflowStatus.failure, ("aggregate total", lambda d: d.update(total_tests=50))),
            (CiWorkflowStatus.failure, ("aggregate failures", lambda d: d.update(failed_tests=1))),
            (CiWorkflowStatus.failure, ("suite category sum", lambda d: d["test_suites"]["unit"].update(passed=43))),
            (CiWorkflowStatus.cancelled, ("aggregate total", lambda d: d.update(total_tests=50))),
            (CiWorkflowStatus.cancelled, ("aggregate failures", lambda d: d.update(failed_tests=1))),
            (CiWorkflowStatus.cancelled, ("suite category sum", lambda d: d["test_suites"]["unit"].update(passed=43))),
        )
        initial = f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip"
        for status, (label, mutate) in status_cases:
            with self.subTest(status=status.value, relationship=label):
                document = load_fixture("backend-v1.json")
                document["status"] = status.value
                document["private_marker"] = "semantic-payload-must-not-escape"
                mutate(document)
                archive = zip_member(json.dumps(document).encode())
                result = CiMetricsArtifactValidator(FakeBinaryTransport({
                    initial: response(body=archive),
                })).validate(context(status=status), metadata())
                self.assertEqual(result.outcome, CiArtifactOutcome.invalid_semantics)
                self.assertTrue(result.schema_valid)
                self.assertFalse(result.semantics_valid)
                self.assertIsNone(result.metrics)
                self.assertFalse(result.certifies_metrics)
                self.assertFalse(result.green_eligible)
                self.assertEqual(result.receipt.http_calls, 1)
                self.assertEqual(result.receipt.compressed_bytes, len(archive))
                self.assertNotIn("semantic-payload-must-not-escape", repr(result))

    def test_coverage_percentage_and_metric_must_remain_a_consistent_pair(self):
        cases = (
            ("known percentage without metric", 97.9, None),
            ("unknown percentage with metric", None, "lines"),
        )
        initial = f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip"
        for status in CiWorkflowStatus:
            for label, percentage, metric in cases:
                with self.subTest(status=status.value, relationship=label):
                    document = load_fixture("backend-v1.json")
                    document["status"] = status.value
                    document["coverage_percentage"] = percentage
                    document["coverage_metric"] = metric
                    archive = zip_member(json.dumps(document).encode())
                    result = CiMetricsArtifactValidator(FakeBinaryTransport({
                        initial: response(body=archive),
                    })).validate(context(status=status), metadata())
                    self.assertEqual(result.outcome, CiArtifactOutcome.invalid_semantics)
                    self.assertTrue(result.schema_valid)
                    self.assertFalse(result.semantics_valid)
                    self.assertIsNone(result.metrics)
                    self.assertFalse(result.certifies_metrics)
                    self.assertFalse(result.green_eligible)

        zero = load_fixture("backend-v1.json")
        zero["coverage_percentage"] = 0
        zero["coverage_metric"] = "lines"
        zero_result = self._validate_archive_bytes(zip_member(json.dumps(zero).encode()))
        self.assertTrue(zero_result.certifies_metrics)
        self.assertEqual(zero_result.metrics.coverage_percentage, 0)
        self.assertEqual(zero_result.metrics.coverage_metric, "lines")

    def test_reports_bundle_expansion_cap_counts_auxiliary_members_and_get_bytes(self):
        metrics_bytes = json.dumps(load_fixture("backend-v1.json")).encode()
        auxiliary = b"x" * 17
        archive = zip_members(
            ("ci-metrics.json", metrics_bytes, zipfile.ZIP_DEFLATED, None),
            ("unit-summary.json", auxiliary, zipfile.ZIP_DEFLATED, None),
        )
        limits = CiArtifactLimits(
            max_expanded_bytes=len(metrics_bytes) + 16,
            max_json_bytes=len(metrics_bytes),
        )
        result = self._validate_archive_bytes(archive, limits=limits)
        self.assertEqual(result.outcome, CiArtifactOutcome.limit_exceeded)
        self.assertGreater(result.receipt.expanded_bytes, limits.max_expanded_bytes)
        self.assertEqual(result.receipt.http_calls, 1)
        self.assertEqual(result.receipt.compressed_bytes, len(archive))
        self.assertIsNone(result.metrics)

        oversized_member = zip_members(
            ("ci-metrics.json", metrics_bytes, zipfile.ZIP_DEFLATED, None),
            ("unit-summary.json", b"x" * (limits.max_expanded_bytes + 1), zipfile.ZIP_DEFLATED, None),
        )
        member_result = self._validate_archive_bytes(oversized_member, limits=limits)
        self.assertEqual(member_result.outcome, CiArtifactOutcome.limit_exceeded)
        self.assertIsNone(member_result.metrics)

    def test_nullable_unknown_and_known_zero_are_preserved_without_backend_phrase(self):
        document = load_fixture("frontend-v1.json")
        document["total_tests"] = 0
        document["failed_tests"] = 0
        document["coverage_percentage"] = None
        document["coverage_metric"] = None
        document["coverage_basis"] = "Frontend coverage was not collected by this producer."
        transport = FakeBinaryTransport({
            f"{API_ORIGIN}/repos/example-org/frontend/actions/artifacts/88/zip":
                response(body=zip_member(json.dumps(document).encode()))
        })
        result = CiMetricsArtifactValidator(transport).validate(
            context("example-org/frontend", SHA_FRONTEND, "23456", "1"),
            metadata(repository="example-org/frontend", sha=SHA_FRONTEND,
                     run_id=23456, attempt="1", artifact_id=88),
        )
        self.assertTrue(result.certifies_metrics)
        self.assertEqual(result.metrics.total_tests, 0)
        self.assertEqual(result.metrics.failed_tests, 0)
        self.assertIsNone(result.metrics.coverage_percentage)
        self.assertIsNone(result.metrics.coverage_metric)
        self.assertEqual(result.metrics.coverage_basis,
                         "Frontend coverage was not collected by this producer.")
        self.assertIsNone(result.metrics.test_suites)

    def test_stored_and_data_descriptor_zip_members_decode_completely(self):
        document = json.dumps(load_fixture("backend-v1.json")).encode()
        for label, archive in (
            ("stored", zip_member(document, compression=zipfile.ZIP_STORED)),
            ("streaming data descriptor", zip_member_streaming(document)),
        ):
            with self.subTest(label=label):
                result = self._validate_archive_bytes(archive)
                self.assertTrue(result.certifies_metrics)
                self.assertEqual(result.receipt.expanded_bytes, len(document))

    def test_failed_workflow_with_valid_failure_document_retains_metrics_but_is_not_green(self):
        document = load_fixture("failure-v1.json")
        # Unknown suite categories remain unknown; partial failure data is not forced to sum.
        document["test_suites"]["unit"]["passed"] = None
        transport = FakeBinaryTransport({
            f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip":
                response(body=zip_member(json.dumps(document).encode()))
        })
        result = CiMetricsArtifactValidator(transport).validate(
            context(status=CiWorkflowStatus.failure), metadata(),
        )
        self.assertEqual(result.outcome, CiArtifactOutcome.validated)
        self.assertTrue(result.document_valid)
        self.assertTrue(result.identity_bound)
        self.assertTrue(result.workflow_status_matches)
        self.assertTrue(result.certifies_metrics)
        self.assertFalse(result.green_eligible)
        self.assertEqual(result.workflow_status, CiWorkflowStatus.failure)
        self.assertEqual(result.metrics.failed_tests, 2)
        self.assertIsNone(result.metrics.coverage_percentage)
        self.assertIsNone(result.metrics.test_suites["integration"])

    def test_failed_workflow_with_passing_suites_is_valid_metrics_but_not_green(self):
        document = load_fixture("backend-v1.json")
        document["status"] = "failure"
        archive = zip_member(json.dumps(document).encode())
        result = CiMetricsArtifactValidator(FakeBinaryTransport({
            f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip":
                response(body=archive),
        })).validate(context(status=CiWorkflowStatus.failure), metadata())
        self.assertEqual(result.outcome, CiArtifactOutcome.validated)
        self.assertTrue(result.certifies_metrics)
        self.assertEqual(result.metrics.failed_tests, 0)
        self.assertTrue(result.metrics.test_suites["unit"].success)
        self.assertFalse(result.green_eligible)

    def test_success_cannot_ignore_missing_or_failing_represented_test_evidence(self):
        cases = []
        missing_totals = load_fixture("frontend-v1.json")
        missing_totals["total_tests"] = None
        missing_totals["failed_tests"] = None
        cases.append(("unknown top-level counts", missing_totals))

        e2e_placeholder = load_fixture("frontend-v1.json")
        e2e_placeholder["total_tests"] = None
        e2e_placeholder["failed_tests"] = None
        self.assertEqual(e2e_placeholder["e2e_status"], "not_implemented")
        cases.append(("e2e placeholder is not test evidence", e2e_placeholder))

        failed_count = load_fixture("frontend-v1.json")
        failed_count["failed_tests"] = 1
        cases.append(("represented top-level failure", failed_count))

        suite_failure = load_fixture("backend-v1.json")
        suite_failure["failed_tests"] = 1
        suite_failure["test_suites"]["unit"]["failed"] = 1
        suite_failure["test_suites"]["unit"]["passed"] = 43
        suite_failure["test_suites"]["unit"]["success"] = False
        cases.append(("represented suite failure", suite_failure))

        missing_suite = load_fixture("backend-v1.json")
        missing_suite["test_suites"]["integration"] = None
        cases.append(("missing represented suite", missing_suite))

        for label, document in cases:
            with self.subTest(label=label):
                transport = FakeBinaryTransport({
                    f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip":
                        response(body=zip_member(json.dumps(document).encode()))
                })
                result = CiMetricsArtifactValidator(transport).validate(context(), metadata())
                self.assertEqual(result.outcome, CiArtifactOutcome.invalid_semantics)
                self.assertTrue(result.schema_valid)
                self.assertFalse(result.semantics_valid)
                self.assertIsNone(result.metrics)
                self.assertFalse(result.certifies_metrics)

    def test_cancelled_workflow_metrics_remain_valid_but_never_green(self):
        document = load_fixture("failure-v1.json")
        document["test_suites"]["unit"]["passed"] = None
        document["status"] = "cancelled"
        transport = FakeBinaryTransport({
            f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip":
                response(body=zip_member(json.dumps(document).encode()))
        })
        result = CiMetricsArtifactValidator(transport).validate(
            context(status=CiWorkflowStatus.cancelled), metadata(),
        )
        self.assertTrue(result.certifies_metrics)
        self.assertEqual(result.workflow_status, CiWorkflowStatus.cancelled)
        self.assertFalse(result.green_eligible)
        self.assertEqual(result.metrics.failed_tests, 2)

    def test_workflow_status_is_compared_not_derived_from_test_counts(self):
        document = load_fixture("backend-v1.json")
        transport = FakeBinaryTransport({
            f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip":
                response(body=zip_member(json.dumps(document).encode()))
        })
        result = CiMetricsArtifactValidator(transport).validate(
            context(status=CiWorkflowStatus.failure), metadata(),
        )
        self.assertEqual(result.outcome, CiArtifactOutcome.workflow_status_mismatch)
        self.assertTrue(result.document_valid)
        self.assertTrue(result.identity_bound)
        self.assertFalse(result.workflow_status_matches)
        self.assertIsNone(result.metrics)
        self.assertFalse(result.green_eligible)

    def test_optional_document_attempt_may_be_absent_but_present_attempt_must_match(self):
        document = load_fixture("backend-v1.json")
        del document["workflow_run_attempt"]
        transport = FakeBinaryTransport({
            f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip":
                response(body=zip_member(json.dumps(document).encode()))
        })
        result = CiMetricsArtifactValidator(transport).validate(context(), metadata())
        self.assertTrue(result.certifies_metrics)
        self.assertIsNone(result.metrics.workflow_run_attempt)

        document["workflow_run_attempt"] = "1"
        transport = FakeBinaryTransport({
            f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip":
                response(body=zip_member(json.dumps(document).encode()))
        })
        result = CiMetricsArtifactValidator(transport).validate(context(), metadata())
        self.assertEqual(result.outcome, CiArtifactOutcome.identity_mismatch)
        self.assertTrue(result.schema_valid)
        self.assertFalse(result.identity_bound)
        self.assertIsNone(result.metrics)

    def test_full_repository_sha_run_attempt_and_synthetic_sha_mismatches_never_return_metrics(self):
        mutations = (
            ("repository", lambda d: d.update(repository="other-org/backend")),
            ("source sha", lambda d: d.update(commit_sha="c" * 40)),
            ("run id", lambda d: d.update(workflow_run_id="99999")),
            ("synthetic merge sha", lambda d: d.update(commit_sha="d" * 40)),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                document = load_fixture("backend-v1.json")
                mutate(document)
                transport = FakeBinaryTransport({
                    f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip":
                        response(body=zip_member(json.dumps(document).encode()))
                })
                result = CiMetricsArtifactValidator(transport).validate(context(), metadata())
                self.assertEqual(result.outcome, CiArtifactOutcome.identity_mismatch)
                self.assertTrue(result.schema_valid)
                self.assertFalse(result.identity_bound)
                self.assertIsNone(result.metrics)

    def test_artifact_metadata_run_repository_name_and_id_are_bound_before_download(self):
        mismatches = (
            metadata(run_id=99999),
            metadata(repository="other-org/backend"),
            metadata(name="ci-quality-old-run"),
            metadata(workflow_attempt=1),
            metadata(artifact_id=True),
        )
        for item in mismatches:
            with self.subTest(name=item.get("name"), run=item.get("workflow_run", {}).get("id")):
                transport = FakeBinaryTransport({})
                result = CiMetricsArtifactValidator(transport).validate(context(), item)
                expected = (CiArtifactOutcome.invalid_metadata if item.get("id") is True
                            else CiArtifactOutcome.identity_mismatch)
                self.assertEqual(result.outcome, expected)
                self.assertEqual(result.receipt.http_calls, 0)
                self.assertIsNone(result.metrics)
                self.assertEqual(transport.calls, [])

    def test_missing_or_mismatched_workflow_head_sha_is_noncertifying_without_fetch(self):
        transport = FakeBinaryTransport({})
        missing = CiMetricsArtifactValidator(transport).validate(
            context(), metadata(include_head_sha=False),
        )
        self.assertEqual(missing.outcome, CiArtifactOutcome.source_sha_unproven)
        self.assertEqual(missing.receipt.http_calls, 0)
        self.assertEqual(transport.calls, [])

        mismatched = CiMetricsArtifactValidator(transport).validate(
            context(), metadata(sha="e" * 40),
        )
        self.assertEqual(mismatched.outcome, CiArtifactOutcome.identity_mismatch)
        self.assertEqual(mismatched.receipt.http_calls, 0)

    def test_metadata_missing_expired_or_inaccessible_has_typed_noncertifying_outcome(self):
        never = FakeBinaryTransport({})
        missing = CiMetricsArtifactValidator(never).validate(context(), None)
        self.assertEqual(missing.outcome, CiArtifactOutcome.artifact_missing)
        self.assertEqual(missing.receipt.http_calls, 0)

        expired = CiMetricsArtifactValidator(never).validate(
            context(), metadata(expired=True),
        )
        self.assertEqual(expired.outcome, CiArtifactOutcome.artifact_expired)
        self.assertEqual(expired.receipt.http_calls, 0)

        for code, expected in (
            (401, CiArtifactOutcome.authentication_required),
            (403, CiArtifactOutcome.permission_denied),
            (404, CiArtifactOutcome.artifact_missing),
            (410, CiArtifactOutcome.artifact_expired),
            (429, CiArtifactOutcome.rate_limited),
        ):
            with self.subTest(code=code):
                transport = FakeBinaryTransport({
                    f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip":
                        response(status=code)
                })
                result = CiMetricsArtifactValidator(transport).validate(context(), metadata())
                self.assertEqual(result.outcome, expected)
                self.assertIsNone(result.metrics)
                self.assertEqual(result.receipt.http_calls, 1)
                self.assertEqual(result.receipt.hop_statuses, (code,))

    def test_malformed_metadata_is_explicit_and_never_uses_untrusted_download_url(self):
        transport = FakeBinaryTransport({})
        for malformed in ([], {"id": True, "name": "x", "expired": False, "workflow_run": {}}):
            with self.subTest(metadata=type(malformed).__name__):
                result = CiMetricsArtifactValidator(transport).validate(context(), malformed)
                self.assertEqual(result.outcome, CiArtifactOutcome.invalid_metadata)
                self.assertEqual(result.receipt.http_calls, 0)

        document = load_fixture("backend-v1.json")
        canonical = f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip"
        transport = FakeBinaryTransport({
            canonical: response(body=zip_member(json.dumps(document).encode()))
        })
        result = CiMetricsArtifactValidator(transport).validate(
            context(), metadata(archive_url="https://evil.invalid/steal?token=secret"),
        )
        self.assertTrue(result.certifies_metrics)
        self.assertEqual(transport.calls[0]["url"], canonical)
        self.assertNotIn("evil.invalid", repr(result))

    def test_redirect_authentication_stays_on_api_origin_and_receipt_counts_each_hop(self):
        document = load_fixture("backend-v1.json")
        archive = zip_member(json.dumps(document).encode())
        initial = f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip"
        token = "ghs_top_secret_fixture"
        transport = FakeBinaryTransport({
            initial: response(status=302, headers={"location": STORAGE_URL}),
            STORAGE_URL: response(body=archive),
        })
        identity = RepositoryIdentity(id="backend", owner="example-org", repo="backend")
        ledger = ledger_for(identity)
        budgeted = BudgetedHttpGetTransport(
            transport, ledger, SourceProvider.github_actions, identity,
        )
        result = CiMetricsArtifactValidator(budgeted).validate(context(), metadata(), token=token)
        self.assertTrue(result.certifies_metrics)
        self.assertEqual(result.receipt.http_calls, 2)
        self.assertEqual(result.receipt.redirects, 1)
        self.assertEqual(result.receipt.compressed_bytes, len(archive))
        self.assertEqual(result.receipt.hop_statuses, (302, 200))
        self.assertEqual(ledger.receipt().http_calls, 2)
        self.assertEqual(ledger.receipt().response_bytes, len(archive))
        self.assertEqual(ledger.repository_receipt(identity).http_calls, 2)
        self.assertEqual(ledger.repository_receipt(identity).response_bytes, len(archive))
        self.assertEqual(transport.calls[0]["headers"].get("Authorization"), f"Bearer {token}")
        self.assertNotIn("Authorization", transport.calls[1]["headers"])
        self.assertNotIn("Cookie", transport.calls[0]["headers"])
        self.assertNotIn("Proxy-Authorization", transport.calls[0]["headers"])
        self.assertEqual(transport.calls[1]["headers"].get("Accept-Encoding"), "identity")
        self.assertNotIn(token, repr(result))
        self.assertNotIn("signed-query", repr(result))
        self.assertEqual(active_budget_scope(), None)

    def test_redirect_rejects_unsafe_schemes_hosts_ports_userinfo_and_loops(self):
        initial = f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip"
        bad_destinations = (
            "http://productionresultssa0.blob.core.windows.net/a",
            "https://user@productionresultssa0.blob.core.windows.net/a",
            "https://productionresultssa0.blob.core.windows.net:444/a",
            "https://evil.invalid/a",
            "https://127.0.0.1/a",
        )
        for location in bad_destinations:
            with self.subTest(location=location):
                transport = FakeBinaryTransport({initial: response(status=302, headers={"Location": location})})
                result = CiMetricsArtifactValidator(transport).validate(context(), metadata())
                self.assertEqual(result.outcome, CiArtifactOutcome.redirect_rejected)
                self.assertEqual(result.receipt.http_calls, 1)
                self.assertEqual(len(transport.calls), 1)

        storage = STORAGE_URL
        unsupported = FakeBinaryTransport({
            initial: response(status=305, headers={"Location": STORAGE_URL}),
        })
        unsupported_result = CiMetricsArtifactValidator(unsupported).validate(context(), metadata())
        self.assertEqual(unsupported_result.outcome, CiArtifactOutcome.redirect_rejected)
        self.assertEqual(unsupported_result.receipt.http_calls, 1)
        self.assertEqual(len(unsupported.calls), 1)

        loop_transport = FakeBinaryTransport({
            initial: response(status=302, headers={"Location": storage}),
            storage: response(status=302, headers={"Location": initial}),
        })
        loop_result = CiMetricsArtifactValidator(loop_transport).validate(context(), metadata())
        self.assertEqual(loop_result.outcome, CiArtifactOutcome.redirect_loop)
        self.assertEqual(loop_result.receipt.http_calls, 2)
        self.assertEqual(len(loop_transport.calls), 2)

    def test_redirect_count_is_finite_and_transport_errors_do_not_leak_details(self):
        initial = f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip"
        redirects = {
            initial: response(status=302, headers={"Location": "https://productionresultssa0.blob.core.windows.net/1"}),
            "https://productionresultssa0.blob.core.windows.net/1": response(status=302, headers={"Location": "https://productionresultssa1.blob.core.windows.net/2"}),
            "https://productionresultssa1.blob.core.windows.net/2": response(status=302, headers={"Location": "https://productionresultssa2.blob.core.windows.net/3"}),
            "https://productionresultssa2.blob.core.windows.net/3": response(status=302, headers={"Location": "https://productionresultssa3.blob.core.windows.net/4"}),
        }
        result = CiMetricsArtifactValidator(FakeBinaryTransport(redirects)).validate(context(), metadata())
        self.assertEqual(result.outcome, CiArtifactOutcome.redirect_limit_exceeded)
        self.assertEqual(result.receipt.http_calls, 4)
        self.assertEqual(result.receipt.redirects, 4)

        secret_url = "https://productionresultssa0.blob.core.windows.net/private?sig=never-log"
        error = ArtifactTransportError(
            SourceErrorCategory.network, attempted=True, bytes_received=3,
        )
        transport = FakeBinaryTransport({initial: error})
        result = CiMetricsArtifactValidator(transport).validate(context(), metadata())
        self.assertEqual(result.outcome, CiArtifactOutcome.transport_error)
        self.assertEqual(result.receipt.http_calls, 1)
        self.assertEqual(result.receipt.compressed_bytes, 3)
        self.assertNotIn(secret_url, repr(result))
        self.assertNotIn("sig=", repr(error))

    def test_archive_rejects_duplicates_paths_symlinks_encryption_and_unsupported_compression(self):
        valid = json.dumps(load_fixture("backend-v1.json")).encode()
        symlink_mode = (0o120777)
        with self.assertWarnsRegex(UserWarning, "Duplicate name"):
            duplicate_same_name = zip_members(
                ("ci-metrics.json", valid, zipfile.ZIP_DEFLATED, None),
                ("ci-metrics.json", valid, zipfile.ZIP_DEFLATED, None),
            )
        archives = (
            ("duplicate documents", zip_members(
                ("ci-metrics.json", valid, zipfile.ZIP_DEFLATED, None),
                ("reports/ci-metrics.json", valid, zipfile.ZIP_DEFLATED, None),
            )),
            ("duplicate same member", duplicate_same_name),
            ("traversal path", zip_member(valid, name="../ci-metrics.json")),
            ("windows path", zip_member(valid, name="..\\ci-metrics.json")),
            ("symlink", zip_member(b"ci-metrics.json", mode=symlink_mode)),
            ("unsupported compression", zip_member(valid, compression=zipfile.ZIP_BZIP2)),
        )
        for label, archive in archives:
            with self.subTest(label=label):
                transport = FakeBinaryTransport({
                    f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip":
                        response(body=archive)
                })
                result = CiMetricsArtifactValidator(transport).validate(context(), metadata())
                self.assertEqual(result.outcome, CiArtifactOutcome.invalid_archive)
                self.assertIsNone(result.metrics)

        encrypted = bytearray(zip_member(valid))
        encrypted[6:8] = struct.pack("<H", struct.unpack_from("<H", encrypted, 6)[0] | 1)
        central = encrypted.find(b"PK\x01\x02")
        encrypted[central + 8:central + 10] = struct.pack(
            "<H", struct.unpack_from("<H", encrypted, central + 8)[0] | 1,
        )
        encrypted_result = self._validate_archive_bytes(bytes(encrypted))
        self.assertEqual(encrypted_result.outcome, CiArtifactOutcome.invalid_archive)

    def test_archive_rejects_corrupt_headers_crc_actual_eof_and_claimed_size_prefixes(self):
        valid = json.dumps(load_fixture("backend-v1.json")).encode()
        corrupt_header = b"not a zip" + zip_member(valid)
        self.assertEqual(self._validate_archive_bytes(corrupt_header).outcome,
                         CiArtifactOutcome.invalid_archive)

        bad_crc = bytearray(zip_member(valid))
        local_crc = struct.unpack_from("<L", bad_crc, 14)[0]
        struct.pack_into("<L", bad_crc, 14, local_crc ^ 1)
        central = bad_crc.find(b"PK\x01\x02")
        central_crc = struct.unpack_from("<L", bad_crc, central + 16)[0]
        struct.pack_into("<L", bad_crc, central + 16, central_crc ^ 1)
        self.assertEqual(self._validate_archive_bytes(bytes(bad_crc)).outcome,
                         CiArtifactOutcome.invalid_archive)

        # A valid JSON prefix followed by more actual decompressed bytes must never be accepted.
        prefix = valid
        full_body = prefix + b" {\"later\":true}"
        oversized_actual = bytearray(zip_member(full_body))
        struct.pack_into("<L", oversized_actual, 22, len(prefix))
        central = oversized_actual.find(b"PK\x01\x02")
        struct.pack_into("<L", oversized_actual, central + 24, len(prefix))
        truncated_claim = self._validate_archive_bytes(bytes(oversized_actual))
        self.assertEqual(truncated_claim.outcome, CiArtifactOutcome.invalid_archive)
        self.assertIsNone(truncated_claim.metrics)

        # Remove the actual deflate EOF byte and update ZIP offsets/sizes consistently.
        truncated_stream = bytearray(zip_member(valid))
        local_name_length, local_extra_length = struct.unpack_from("<HH", truncated_stream, 26)
        data_start = 30 + local_name_length + local_extra_length
        compressed_size = struct.unpack_from("<L", truncated_stream, 18)[0]
        central_offset = truncated_stream.rfind(b"PK\x05\x06")
        directory_offset = struct.unpack_from("<L", truncated_stream, central_offset + 16)[0]
        del truncated_stream[data_start + compressed_size - 1]
        struct.pack_into("<L", truncated_stream, 18, compressed_size - 1)
        central = truncated_stream.find(b"PK\x01\x02")
        struct.pack_into("<L", truncated_stream, central + 20, compressed_size - 1)
        new_eocd = truncated_stream.rfind(b"PK\x05\x06")
        struct.pack_into("<L", truncated_stream, new_eocd + 16, directory_offset - 1)
        eof_result = self._validate_archive_bytes(bytes(truncated_stream))
        self.assertEqual(eof_result.outcome, CiArtifactOutcome.invalid_archive)

    def test_archive_and_json_limits_and_entry_count_are_actual_bounded_resources(self):
        valid = json.dumps(load_fixture("backend-v1.json")).encode()
        archive = zip_member(valid)
        compressed_limit = CiArtifactLimits(max_compressed_bytes=len(archive) - 1)
        compressed_result = self._validate_archive_bytes(archive, limits=compressed_limit)
        self.assertEqual(compressed_result.outcome, CiArtifactOutcome.limit_exceeded)
        self.assertGreater(compressed_result.receipt.compressed_bytes, compressed_limit.max_compressed_bytes)
        self.assertIsNone(compressed_result.metrics)

        expanded_limit = CiArtifactLimits(max_expanded_bytes=128, max_json_bytes=128)
        oversized_body = bytearray(archive)
        struct.pack_into("<L", oversized_body, 22, 128)
        central = oversized_body.find(b"PK\x01\x02")
        struct.pack_into("<L", oversized_body, central + 24, 128)
        expanded_result = self._validate_archive_bytes(bytes(oversized_body), limits=expanded_limit)
        self.assertEqual(expanded_result.outcome, CiArtifactOutcome.limit_exceeded)
        self.assertGreater(expanded_result.receipt.expanded_bytes, 128)

        too_many = zip_members(*(
            (f"extra-{index}.txt", b"x", zipfile.ZIP_DEFLATED, None)
            for index in range(17)
        ))
        many_result = self._validate_archive_bytes(too_many)
        self.assertEqual(many_result.outcome, CiArtifactOutcome.limit_exceeded)
        self.assertEqual(many_result.receipt.archive_entries, 17)

    def test_json_rejects_malformed_utf8_duplicate_keys_nonfinite_values_and_prefixes(self):
        cases = (
            ("malformed UTF-8", b"{\xff}"),
            ("duplicate JSON keys", b'{"schema_version":"v1","schema_version":"v1"}'),
            ("NaN", b'{"value":NaN}'),
            ("infinity", b'{"value":Infinity}'),
            ("overflowing exponent", b'{"value":1e999}'),
            ("underflowing exponent", b'{"value":1e-9999}'),
            ("oversized integer literal", b'{"value":' + b"9" * 1025 + b"}"),
            ("valid prefix plus trailing document", json.dumps(load_fixture("backend-v1.json")).encode() + b"{}"),
        )
        for label, body in cases:
            with self.subTest(label=label):
                result = self._validate_archive_bytes(zip_member(body))
                self.assertEqual(result.outcome, CiArtifactOutcome.invalid_json)
                self.assertIsNone(result.metrics)

    def test_fixed_python_schema_matches_the_checked_in_official_schema_copy(self):
        checked_in = json.loads((Path(__file__).parents[1]
                                 / "src/agente_gestion/adapters/sources/schemas/ci-metrics.v1.schema.json")
                                .read_text(encoding="utf-8"))
        self.assertEqual(CI_METRICS_V1_SCHEMA, checked_in)
        Draft202012Validator.check_schema(CI_METRICS_V1_SCHEMA)

    def test_schema_errors_are_distinct_and_never_return_partial_metrics(self):
        changes = (
            ("wrong version", lambda d: d.update(schema_version="v2")),
            ("numeric run id", lambda d: d.update(workflow_run_id=12345)),
            ("suite extension", lambda d: d["test_suites"]["unit"].update(extra=1)),
            ("coverage range", lambda d: d.update(coverage_percentage=101)),
            ("numeric attempt", lambda d: d.update(workflow_run_attempt=2)),
        )
        for label, mutate in changes:
            with self.subTest(label=label):
                document = load_fixture("backend-v1.json")
                mutate(document)
                result = self._validate_archive_bytes(zip_member(json.dumps(document).encode()))
                self.assertEqual(result.outcome, CiArtifactOutcome.invalid_schema)
                self.assertFalse(result.schema_valid)
                self.assertIsNone(result.metrics)

    def test_validation_deadline_covers_archive_processing_and_returns_no_partial_metrics(self):
        class StepClock:
            def __init__(self):
                self.calls = 0

            def __call__(self):
                self.calls += 1
                return 0.0 if self.calls <= 2 else 1.0

        archive = zip_member(json.dumps(load_fixture("backend-v1.json")).encode())
        initial = f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip"
        transport = FakeBinaryTransport({initial: response(body=archive)})
        validator = CiMetricsArtifactValidator(
            transport,
            limits=CiArtifactLimits(timeout_seconds=0.5),
            clock=StepClock(),
        )
        result = validator.validate(context(), metadata())
        self.assertEqual(result.outcome, CiArtifactOutcome.timeout)
        self.assertEqual(result.receipt.http_calls, 1)
        self.assertEqual(result.receipt.compressed_bytes, len(archive))
        self.assertIsNone(result.metrics)

    def test_payload_schema_extensions_never_select_an_untrusted_schema(self):
        document = load_fixture("backend-v1.json")
        document["$schema"] = "https://evil.invalid/schema.json"
        document["$ref"] = "https://evil.invalid/remote"
        document["custom_extension"] = {"value": 9}
        document["private_link"] = "https://storage.invalid/archive?sig=must-not-appear-in-repr"
        document["coverage_basis"] = "source text with token=must-not-appear-either"
        result = self._validate_archive_bytes(zip_member(json.dumps(document).encode()))
        self.assertTrue(result.certifies_metrics)
        self.assertEqual(result.metrics.extensions["custom_extension"]["value"], 9)
        self.assertNotIn("must-not-appear-in-repr", repr(result))
        self.assertNotIn("must-not-appear-either", repr(result))

    def test_shared_budget_cap_failure_reports_actual_hops_bytes_and_clears_scope(self):
        document = load_fixture("backend-v1.json")
        archive = zip_member(json.dumps(document).encode())
        initial = f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip"
        identity = RepositoryIdentity(id="backend", owner="example-org", repo="backend")
        cap = len(archive) - 1
        ledger = ledger_for(identity, project_bytes=cap, repo_bytes=1_000_000)
        raw = MemoryRaw(archive)
        memory_response = MemoryResponse(archive, raw=raw)
        session = MemorySession(memory_response)
        one_hop = BoundedBinaryGetTransport(
            max_response_bytes=1_000_000,
            session_factory=lambda: session,
            clock=lambda: 0.0,
        )
        budgeted = BudgetedHttpGetTransport(
            one_hop, ledger, SourceProvider.github_actions, identity,
        )
        result = CiMetricsArtifactValidator(budgeted).validate(context(), metadata())
        self.assertEqual(result.outcome, CiArtifactOutcome.limit_exceeded)
        self.assertEqual(result.receipt.http_calls, 1)
        self.assertEqual(result.receipt.compressed_bytes, cap + 1)
        self.assertEqual(result.receipt.hop_statuses, (200,))
        self.assertIsNone(result.metrics)
        self.assertEqual(ledger.receipt().http_calls, 1)
        self.assertEqual(ledger.receipt().response_bytes, cap + 1)
        self.assertTrue(memory_response.closed)
        self.assertTrue(session.closed)
        self.assertIsNone(active_budget_scope())

    def test_rejected_reports_bundle_still_charges_actual_get_bytes_and_closes(self):
        metrics = json.dumps(load_fixture("backend-v1.json")).encode()
        archive = zip_members(
            ("reports/../unit-summary.json", b"synthetic auxiliary", zipfile.ZIP_DEFLATED, None),
            ("reports/ci-metrics.json", metrics, zipfile.ZIP_DEFLATED, None),
        )
        identity = RepositoryIdentity(id="backend", owner="example-org", repo="backend")
        ledger = ledger_for(identity)
        raw = MemoryRaw(archive)
        memory_response = MemoryResponse(archive, raw=raw)
        session = MemorySession(memory_response)
        one_hop = BoundedBinaryGetTransport(
            max_response_bytes=1_000_000,
            session_factory=lambda: session,
            clock=lambda: 0.0,
        )
        budgeted = BudgetedHttpGetTransport(
            one_hop, ledger, SourceProvider.github_actions, identity,
        )
        result = CiMetricsArtifactValidator(budgeted).validate(context(), metadata())
        self.assertEqual(result.outcome, CiArtifactOutcome.invalid_archive)
        self.assertIsNone(result.metrics)
        self.assertFalse(result.certifies_metrics)
        self.assertEqual(result.receipt.http_calls, 1)
        self.assertEqual(result.receipt.compressed_bytes, len(archive))
        self.assertEqual(ledger.receipt().http_calls, 1)
        self.assertEqual(ledger.receipt().response_bytes, len(archive))
        self.assertTrue(memory_response.closed)
        self.assertTrue(session.closed)
        self.assertIsNone(active_budget_scope())

    def test_missing_expected_status_or_attempt_inputs_fail_closed_before_network(self):
        transport = FakeBinaryTransport({})
        invalid = (
            CiMetricsValidationContext("example-org/backend", "bad", "12345", "2", "success"),
            CiMetricsValidationContext("example-org/backend", SHA_BACKEND, "0123", "2", "success"),
            CiMetricsValidationContext("example-org/backend", SHA_BACKEND, "12345", "0", "success"),
            CiMetricsValidationContext("example-org/backend", SHA_BACKEND, "12345", "2", "queued"),
        )
        for item in invalid:
            with self.subTest(context=item):
                result = CiMetricsArtifactValidator(transport).validate(item, metadata())
                self.assertEqual(result.outcome, CiArtifactOutcome.invalid_context)
                self.assertEqual(result.receipt.http_calls, 0)
        self.assertEqual(transport.calls, [])

    def test_case_variant_duplicate_metrics_document_is_ambiguous(self):
        metrics_bytes = json.dumps(load_fixture("backend-v1.json")).encode()
        archives = (
            ("case-variant duplicate document", zip_members(
                ("ci-metrics.json", metrics_bytes, zipfile.ZIP_DEFLATED, None),
                ("reports/CI-METRICS.JSON", metrics_bytes, zipfile.ZIP_DEFLATED, None),
            )),
            ("case-variant duplicate root document", zip_member(
                metrics_bytes, name="CI-METRICS.JSON",
            )),
        )
        for label, archive in archives:
            with self.subTest(archive=label):
                result = self._validate_archive_bytes(archive)
                self.assertEqual(result.outcome, CiArtifactOutcome.invalid_archive)
                self.assertIsNone(result.metrics)
                self.assertFalse(result.certifies_metrics)

    def test_semantics_failure_surfaces_before_later_status_wrapper(self):
        document = load_fixture("backend-v1.json")
        document["total_tests"] = 50  # Suites declare 49 complete tests.
        initial = f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip"
        result = CiMetricsArtifactValidator(FakeBinaryTransport({
            initial: response(body=zip_member(json.dumps(document).encode())),
        })).validate(context(status=CiWorkflowStatus.failure), metadata())
        self.assertEqual(result.outcome, CiArtifactOutcome.invalid_semantics)
        self.assertNotEqual(result.outcome, CiArtifactOutcome.workflow_status_mismatch)
        self.assertTrue(result.schema_valid)
        self.assertFalse(result.semantics_valid)
        self.assertIsNone(result.metrics)
        self.assertFalse(result.certifies_metrics)
        self.assertFalse(result.green_eligible)

    def _validate_archive_bytes(self, archive, *, limits=None):
        initial = f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip"
        transport = FakeBinaryTransport({initial: response(body=archive)})
        return CiMetricsArtifactValidator(transport, limits=limits).validate(context(), metadata())


class BinaryTransportTests(unittest.TestCase):
    def test_binary_get_uses_raw_identity_bytes_and_ignores_claimed_content_length(self):
        body = b"PK\x03\x04" + b"real archive bytes"
        raw = MemoryRaw(body)
        fake_response = MemoryResponse(
            body,
            headers={"Content-Length": "1", "Content-Encoding": "identity"},
            raw=raw,
        )
        session = MemorySession(fake_response)
        transport = BoundedBinaryGetTransport(
            max_response_bytes=1024,
            session_factory=lambda: session,
            clock=lambda: 0.0,
        )
        result = transport.get(
            f"{API_ORIGIN}/repos/example-org/backend/actions/artifacts/77/zip",
            headers={"Authorization": "Bearer test-token", "Cookie": "must-not-send"},
            timeout_seconds=5,
        )
        self.assertEqual(result.body, body)
        self.assertEqual(result.bytes_received, len(body))
        self.assertFalse(session.trust_env)
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(session.calls[0]["stream"])
        self.assertFalse(session.calls[0]["allow_redirects"])
        self.assertEqual(session.calls[0]["timeout"], 5)
        self.assertEqual(session.calls[0]["headers"]["Accept-Encoding"], "identity")
        self.assertEqual(session.calls[0]["headers"]["Authorization"], "Bearer test-token")
        self.assertNotIn("Cookie", session.calls[0]["headers"])
        self.assertTrue(fake_response.closed)
        self.assertTrue(session.closed)
        self.assertTrue(all(not decode for _amount, decode in raw.requested))

    def test_binary_get_strips_credentials_and_proxy_headers_from_allowed_storage_origin(self):
        body = b"archive"
        response_object = MemoryResponse(body)
        session = MemorySession(response_object)
        transport = BoundedBinaryGetTransport(
            max_response_bytes=32,
            session_factory=lambda: session,
            clock=lambda: 0.0,
        )
        transport.get(
            STORAGE_URL,
            headers={
                "Authorization": "Bearer must-not-cross-origin",
                "Cookie": "session=must-not-cross-origin",
                "Proxy-Authorization": "Basic must-not-cross-origin",
                "X-Api-Key": "must-not-cross-origin",
            },
        )
        sent = session.calls[0]["headers"]
        self.assertEqual(sent, {"Accept-Encoding": "identity"})
        self.assertTrue(response_object.closed)
        self.assertTrue(session.closed)

    def test_artifact_limits_reject_nonfinite_or_unbounded_configuration(self):
        with self.assertRaises(ValueError):
            CiArtifactLimits(timeout_seconds=float("nan"))
        with self.assertRaises(ValueError):
            CiArtifactLimits(max_compressed_bytes=65 * 1024 * 1024)
        with self.assertRaises(ValueError):
            CiArtifactLimits(max_json_bytes=1025, max_expanded_bytes=1024)
        with self.assertRaises(ValueError):
            CiArtifactLimits(allowed_storage_origins=["https://evil.invalid"])
        with self.assertRaises(ValueError):
            CiMetricsArtifactValidator(
                FakeBinaryTransport({}),
                limits=CiArtifactLimits(allowed_storage_origins=("https://127.0.0.1",)),
            )

    def test_binary_get_rejects_unsafe_origin_compression_and_actual_overread(self):
        invalid_urls = (
            "http://api.github.com/repos/o/r/a",
            "https://user@api.github.com/repos/o/r/a",
            "https://api.github.com:444/repos/o/r/a",
            "https://127.0.0.1/a",
            "https://evil.invalid/a",
        )
        session = MemorySession(MemoryResponse(b"x"))
        transport = BoundedBinaryGetTransport(
            max_response_bytes=8,
            session_factory=lambda: session,
            clock=lambda: 0.0,
        )
        self.assertFalse(hasattr(transport, "post"))
        self.assertFalse(hasattr(transport, "put"))
        self.assertFalse(hasattr(transport, "delete"))
        with self.assertRaises(SourceError):
            transport.get(f"{API_ORIGIN}/artifact", params={"query": "forbidden"})
        for url in invalid_urls:
            with self.subTest(url=url), self.assertRaises(SourceError) as error:
                transport.get(url)
            self.assertEqual(error.exception.category, SourceErrorCategory.configuration)
        self.assertEqual(session.calls, [])

        encoded_response = MemoryResponse(b"encoded", headers={"Content-Encoding": "gzip"})
        encoded_session = MemorySession(encoded_response)
        encoded_transport = BoundedBinaryGetTransport(
            max_response_bytes=32,
            session_factory=lambda: encoded_session,
            clock=lambda: 0.0,
        )
        with self.assertRaises(ArtifactTransportError) as encoded_error:
            encoded_transport.get(f"{API_ORIGIN}/artifact")
        self.assertEqual(encoded_error.exception.category, SourceErrorCategory.malformed_payload)
        self.assertTrue(encoded_response.closed)
        self.assertTrue(encoded_session.closed)

        oversized_raw = MemoryRaw(b"0123456789", overread=5)
        oversized_response = MemoryResponse(b"0123456789", raw=oversized_raw)
        oversized_session = MemorySession(oversized_response)
        capped = BoundedBinaryGetTransport(
            max_response_bytes=8,
            session_factory=lambda: oversized_session,
            clock=lambda: 0.0,
        )
        with self.assertRaises(ArtifactTransportError) as overrun:
            capped.get(f"{API_ORIGIN}/artifact")
        self.assertEqual(overrun.exception.category, SourceErrorCategory.limit_exceeded)
        self.assertGreater(overrun.exception.bytes_received, 8)
        self.assertTrue(oversized_response.closed)
        self.assertTrue(oversized_session.closed)

    def test_binary_deadline_closes_an_in_flight_response_and_reports_observed_bytes(self):
        class StepClock:
            def __init__(self):
                self.calls = 0

            def __call__(self):
                self.calls += 1
                return 0.0 if self.calls <= 3 else 1.0

        body = b"bounded bytes"
        response_object = MemoryResponse(body)
        session = MemorySession(response_object)
        transport = BoundedBinaryGetTransport(
            max_response_bytes=1024,
            timeout_seconds=1,
            session_factory=lambda: session,
            clock=StepClock(),
        )
        with self.assertRaises(ArtifactTransportError) as timed_out:
            transport.get(f"{API_ORIGIN}/artifact", timeout_seconds=0.5)
        self.assertEqual(timed_out.exception.category, SourceErrorCategory.timeout)
        self.assertTrue(timed_out.exception.attempted)
        self.assertEqual(timed_out.exception.bytes_received, len(body))
        self.assertTrue(response_object.closed)
        self.assertTrue(session.closed)
        self.assertEqual(len(session.calls), 1)

    def test_requests_session_uses_zero_retry_http_adapter_without_opening_sockets(self):
        import requests

        session = requests.Session()
        session.headers.update({"Authorization": "Bearer session-secret", "Cookie": "session-secret"})
        session.cookies.set("session", "secret", domain="api.github.com")
        session.proxies["https"] = "http://proxy.invalid"
        body = b"raw body"
        response_object = MemoryResponse(body)
        session.get = lambda url, **kwargs: response_object
        transport = BoundedBinaryGetTransport(
            max_response_bytes=32,
            session_factory=lambda: session,
            clock=lambda: 0.0,
        )
        result = transport.get(f"{API_ORIGIN}/artifact")
        self.assertEqual(result.body, body)
        self.assertEqual(session.get_adapter("https://").max_retries.total, 0)
        self.assertFalse(session.trust_env)
        self.assertNotIn("Authorization", session.headers)
        self.assertNotIn("Cookie", session.headers)
        self.assertFalse(session.cookies)
        self.assertFalse(session.proxies)
        self.assertTrue(response_object.closed)

    def test_requests_urllib3_raw_in_memory_response_honors_bounded_reads_without_sockets(self):
        body = b"x" * 80
        raw = urllib3.response.HTTPResponse(
            body=io.BytesIO(body), status=200, preload_content=False,
            decode_content=False, headers={"Content-Length": "1"},
        )
        memory_response = MemoryResponse(body, raw=raw)
        session = MemorySession(memory_response)
        transport = BoundedBinaryGetTransport(
            max_response_bytes=8,
            session_factory=lambda: session,
            clock=lambda: 0.0,
        )
        with self.assertRaises(ArtifactTransportError) as overrun:
            transport.get(f"{API_ORIGIN}/artifact")
        self.assertEqual(overrun.exception.category, SourceErrorCategory.limit_exceeded)
        self.assertEqual(overrun.exception.bytes_received, 9)
        self.assertTrue(memory_response.closed)
        self.assertTrue(session.closed)


if __name__ == "__main__":
    unittest.main()
