"""Standalone bounded CI metrics artifact download and validation core.

The caller selects the Actions run/attempt and supplies its authoritative identity.
This module never selects a run, follows an arbitrary metadata URL, or writes data.
"""

from dataclasses import dataclass, field
import json
import math
import re
import stat
import struct
import time
import zipfile
from types import MappingProxyType
from typing import Any, Mapping, Optional
from urllib.parse import urljoin
import zlib

from jsonschema import Draft202012Validator

from agente_gestion.adapters.sources.binary_transport import (
    API_ORIGIN,
    BoundedBinaryGetTransport,
    normalize_artifact_url,
    parse_artifact_origin,
)
from agente_gestion.adapters.sources.ci_metrics_schema import CI_METRICS_V1_SCHEMA
from agente_gestion.domain.ci_metrics import (
    CiArtifactMetadata,
    CiArtifactOutcome,
    CiArtifactReceipt,
    CiMetricsArtifactResult,
    CiMetricsDocument,
    CiMetricsTestSuite,
    CiMetricsValidationContext,
    CiWorkflowStatus,
)
from agente_gestion.ports.source_artifacts import (
    ArtifactTransportError,
    BinaryGetTransportPort,
    BinaryHttpResponse,
    CiArtifactLimits,
)
from agente_gestion.ports.source_errors import SourceError, SourceErrorCategory


_SCHEMA_VALIDATOR = Draft202012Validator(CI_METRICS_V1_SCHEMA)
_SCHEMA_VALIDATOR.check_schema(CI_METRICS_V1_SCHEMA)
_REPOSITORY_PART = re.compile(r"^[A-Za-z0-9_.-]+$", re.ASCII)
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_DECIMAL_ID = re.compile(r"^[1-9][0-9]*$", re.ASCII)
_CORE_FIELDS = frozenset({
    "schema_version", "repository", "commit_sha", "workflow_run_id",
    "workflow_run_attempt", "status", "e2e_status", "total_tests", "failed_tests",
    "coverage_percentage", "coverage_metric", "coverage_basis", "test_suites",
})
_EOCD_SIGNATURE = b"PK\x05\x06"
_CENTRAL_SIGNATURE = b"PK\x01\x02"
_LOCAL_SIGNATURE = b"PK\x03\x04"
_DATA_DESCRIPTOR_SIGNATURE = b"PK\x07\x08"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_JSON_NUMBER_LITERAL = 1024


class _ArtifactFailure(Exception):
    def __init__(self, outcome: CiArtifactOutcome):
        self.outcome = outcome


class _DuplicateJsonKey(ValueError):
    pass


@dataclass
class _ReceiptBuilder:
    http_calls: int = 0
    redirects: int = 0
    compressed_bytes: int = 0
    expanded_bytes: int = 0
    archive_entries: int = 0
    hop_statuses: list[Optional[int]] = field(default_factory=list)

    def freeze(self) -> CiArtifactReceipt:
        return CiArtifactReceipt(
            http_calls=self.http_calls,
            redirects=self.redirects,
            compressed_bytes=self.compressed_bytes,
            expanded_bytes=self.expanded_bytes,
            archive_entries=self.archive_entries,
            hop_statuses=tuple(self.hop_statuses),
        )


class CiMetricsArtifactValidator:
    """Download and validate one already-selected GitHub Actions artifact.

    T4 can wrap ``BoundedBinaryGetTransport`` in the existing
    ``BudgetedHttpGetTransport``. Each redirect invokes that wrapper separately,
    so the shared collection ledger receives one call and the observed bytes per hop.
    """

    def __init__(
        self,
        transport: Optional[BinaryGetTransportPort] = None,
        *,
        limits: Optional[CiArtifactLimits] = None,
        clock=None,
    ):
        self.limits = limits if limits is not None else CiArtifactLimits()
        self._clock = clock or time.monotonic
        allowed_origins = (API_ORIGIN, *self.limits.allowed_storage_origins)
        try:
            self._allowed_origins = frozenset(parse_artifact_origin(origin) for origin in allowed_origins)
        except Exception:
            raise ValueError("Invalid CI artifact origin policy.") from None
        self._transport = transport if transport is not None else BoundedBinaryGetTransport(
            max_response_bytes=self.limits.max_compressed_bytes,
            timeout_seconds=self.limits.timeout_seconds,
            allowed_origins=allowed_origins,
            clock=self._clock,
        )

    def validate(
        self,
        context: CiMetricsValidationContext,
        artifact_metadata: CiArtifactMetadata | Mapping[str, Any] | None,
        *,
        token: Optional[str] = None,
    ) -> CiMetricsArtifactResult:
        """Fetch and validate using explicit expected identity; never returns partial metrics."""
        receipt = _ReceiptBuilder()
        parsed_context = self._validate_context(context)
        if parsed_context is None:
            return self._result(CiArtifactOutcome.invalid_context, context, receipt)
        expected_repository, expected_sha, run_id, attempt, workflow_status = parsed_context

        if artifact_metadata is None:
            return self._result(CiArtifactOutcome.artifact_missing, context, receipt)
        try:
            metadata = (
                artifact_metadata if isinstance(artifact_metadata, CiArtifactMetadata)
                else CiArtifactMetadata.from_github_payload(artifact_metadata)
            )
        except Exception:
            return self._result(CiArtifactOutcome.invalid_metadata, context, receipt)
        if not self._metadata_shape_valid(metadata):
            return self._result(CiArtifactOutcome.invalid_metadata, context, receipt)

        expected_name = f"ci-quality-{expected_sha}-{run_id}-{attempt}"
        if (metadata.workflow_run_id != run_id or metadata.name != expected_name
                or (metadata.workflow_run_attempt is not None
                    and metadata.workflow_run_attempt != attempt)):
            return self._result(CiArtifactOutcome.identity_mismatch, context, receipt)
        if metadata.workflow_head_sha is None or not _FULL_SHA.fullmatch(metadata.workflow_head_sha):
            return self._result(CiArtifactOutcome.source_sha_unproven, context, receipt)
        if metadata.workflow_head_sha != expected_sha:
            return self._result(CiArtifactOutcome.identity_mismatch, context, receipt)
        if (metadata.workflow_repository is not None
                and metadata.workflow_repository.casefold() != expected_repository.casefold()):
            return self._result(CiArtifactOutcome.identity_mismatch, context, receipt)
        if metadata.expired:
            return self._result(CiArtifactOutcome.artifact_expired, context, receipt)
        if token is not None and (
            not isinstance(token, str) or not token or len(token) > 8192
            or any(ord(char) < 32 or ord(char) == 127 for char in token)
        ):
            return self._result(CiArtifactOutcome.invalid_context, context, receipt)

        owner, repo = expected_repository.split("/", 1)
        initial_url = (
            f"{API_ORIGIN}/repos/{owner}/{repo}/actions/artifacts/{metadata.artifact_id}/zip"
        )
        try:
            current_url, current_origin = normalize_artifact_url(initial_url)
        except Exception:
            return self._result(CiArtifactOutcome.invalid_context, context, receipt)
        if current_origin not in self._allowed_origins:
            return self._result(CiArtifactOutcome.invalid_context, context, receipt)

        deadline = self._clock() + self.limits.timeout_seconds
        visited = {current_url}
        redirects = 0
        archive_body = None
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                return self._result(CiArtifactOutcome.timeout, context, receipt)
            request_headers = {"Accept-Encoding": "identity"}
            if current_origin == parse_artifact_origin(API_ORIGIN):
                request_headers["Accept"] = "application/vnd.github+json"
                if token is not None:
                    request_headers["Authorization"] = f"Bearer {token}"
            try:
                response = self._transport.get(
                    current_url,
                    headers=request_headers,
                    timeout_seconds=remaining,
                )
            except ArtifactTransportError as exc:
                if exc.attempted:
                    receipt.http_calls += 1
                    receipt.hop_statuses.append(exc.status_code)
                    receipt.compressed_bytes += max(0, exc.bytes_received)
                return self._result(
                    self._outcome_for_category(exc.category), context, receipt,
                )
            except SourceError as exc:
                return self._result(self._outcome_for_category(exc.category), context, receipt)
            except Exception:
                return self._result(CiArtifactOutcome.transport_error, context, receipt)

            receipt.http_calls += 1
            if not isinstance(response, BinaryHttpResponse):
                return self._result(CiArtifactOutcome.transport_error, context, receipt)
            status = response.status_code if type(response.status_code) is int else None
            receipt.hop_statuses.append(status)
            if status is None or not isinstance(response.headers, Mapping) or not isinstance(response.body, bytes):
                return self._result(CiArtifactOutcome.transport_error, context, receipt)
            received = response.bytes_received
            if type(received) is not int or received < 0:
                return self._result(CiArtifactOutcome.transport_error, context, receipt)
            if status == 200:
                receipt.compressed_bytes += received
                if received > self.limits.max_compressed_bytes:
                    return self._result(CiArtifactOutcome.limit_exceeded, context, receipt)
                if received != len(response.body):
                    return self._result(CiArtifactOutcome.transport_error, context, receipt)
                if received == 0:
                    return self._result(CiArtifactOutcome.invalid_archive, context, receipt)
                content_encoding = self._header(response.headers, "content-encoding")
                if content_encoding not in (None, "", "identity"):
                    return self._result(CiArtifactOutcome.invalid_archive, context, receipt)
                archive_body = response.body
                break
            if 300 <= status < 400:
                receipt.redirects += 1
                if status not in _REDIRECT_STATUSES:
                    return self._result(CiArtifactOutcome.redirect_rejected, context, receipt)
                if redirects >= self.limits.max_redirects:
                    return self._result(CiArtifactOutcome.redirect_limit_exceeded, context, receipt)
                location = self._header(response.headers, "location")
                if not isinstance(location, str) or not location:
                    return self._result(CiArtifactOutcome.redirect_rejected, context, receipt)
                try:
                    target_url, target_origin = normalize_artifact_url(urljoin(current_url, location))
                except Exception:
                    return self._result(CiArtifactOutcome.redirect_rejected, context, receipt)
                if target_origin not in self._allowed_origins:
                    return self._result(CiArtifactOutcome.redirect_rejected, context, receipt)
                if target_url in visited:
                    return self._result(CiArtifactOutcome.redirect_loop, context, receipt)
                visited.add(target_url)
                redirects += 1
                current_url, current_origin = target_url, target_origin
                continue
            if status == 401:
                outcome = CiArtifactOutcome.authentication_required
            elif status == 403:
                outcome = CiArtifactOutcome.permission_denied
            elif status == 404:
                outcome = CiArtifactOutcome.artifact_missing
            elif status == 410:
                outcome = CiArtifactOutcome.artifact_expired
            elif status == 429:
                outcome = CiArtifactOutcome.rate_limited
            else:
                outcome = CiArtifactOutcome.transport_error
            return self._result(outcome, context, receipt)

        try:
            self._check_deadline(deadline)
            document_bytes = self._extract_metrics_member(archive_body, receipt, deadline)
            self._check_deadline(deadline)
        except _ArtifactFailure as failure:
            return self._result(failure.outcome, context, receipt)
        except Exception:
            return self._result(CiArtifactOutcome.invalid_archive, context, receipt)
        if len(document_bytes) > self.limits.max_json_bytes:
            return self._result(CiArtifactOutcome.limit_exceeded, context, receipt)
        try:
            self._check_deadline(deadline)
            payload = self._parse_json_document(document_bytes)
            self._check_deadline(deadline)
        except _ArtifactFailure as failure:
            return self._result(failure.outcome, context, receipt)
        except Exception:
            return self._result(CiArtifactOutcome.invalid_json, context, receipt)
        try:
            self._check_deadline(deadline)
            schema_valid = _SCHEMA_VALIDATOR.is_valid(payload)
            self._check_deadline(deadline)
        except _ArtifactFailure as failure:
            return self._result(failure.outcome, context, receipt)
        if not schema_valid:
            return self._result(CiArtifactOutcome.invalid_schema, context, receipt)

        try:
            metrics = self._make_document(payload)
            semantics_valid = self._semantics_valid(metrics)
            identity_bound = self._document_identity_matches(
                metrics, expected_repository, expected_sha, run_id, attempt,
            )
            status_matches = metrics.status == workflow_status
        except Exception:
            return self._result(
                CiArtifactOutcome.invalid_semantics, context, receipt,
                schema_valid=True, semantics_valid=False,
            )
        if not semantics_valid:
            return self._result(
                CiArtifactOutcome.invalid_semantics, context, receipt,
                schema_valid=True, semantics_valid=False,
                identity_bound=identity_bound, workflow_status_matches=status_matches,
            )
        if not identity_bound:
            return self._result(
                CiArtifactOutcome.identity_mismatch, context, receipt,
                schema_valid=True, semantics_valid=True,
                identity_bound=False, workflow_status_matches=status_matches,
            )
        if not status_matches:
            return self._result(
                CiArtifactOutcome.workflow_status_mismatch, context, receipt,
                schema_valid=True, semantics_valid=True,
                identity_bound=True, workflow_status_matches=False,
            )
        return CiMetricsArtifactResult(
            outcome=CiArtifactOutcome.validated,
            workflow_status=workflow_status,
            schema_valid=True,
            semantics_valid=True,
            identity_bound=True,
            workflow_status_matches=True,
            metrics=metrics,
            green_eligible=(workflow_status == CiWorkflowStatus.success),
            receipt=receipt.freeze(),
        )

    def _validate_context(self, context):
        if not isinstance(context, CiMetricsValidationContext):
            return None
        repository = context.repository
        if (not isinstance(repository, str) or len(repository) > 140
                or repository.count("/") != 1):
            return None
        owner, repo = repository.split("/", 1)
        if (not _REPOSITORY_PART.fullmatch(owner) or not _REPOSITORY_PART.fullmatch(repo)
                or owner in {".", ".."} or repo in {".", ".."}):
            return None
        sha = context.target_source_sha
        if not isinstance(sha, str) or not _FULL_SHA.fullmatch(sha):
            return None
        if (not isinstance(context.workflow_run_id, str) or len(context.workflow_run_id) > 20
                or not _DECIMAL_ID.fullmatch(context.workflow_run_id)):
            return None
        if (not isinstance(context.workflow_run_attempt, str)
                or len(context.workflow_run_attempt) > 20
                or not _DECIMAL_ID.fullmatch(context.workflow_run_attempt)):
            return None
        try:
            workflow_status = CiWorkflowStatus(context.workflow_status)
        except (TypeError, ValueError):
            return None
        return repository, sha, context.workflow_run_id, context.workflow_run_attempt, workflow_status

    @staticmethod
    def _metadata_shape_valid(metadata: CiArtifactMetadata) -> bool:
        return (
            type(metadata.artifact_id) is int and 0 < metadata.artifact_id <= (2**63 - 1)
            and isinstance(metadata.name, str) and len(metadata.name) <= 128
            and type(metadata.expired) is bool
            and isinstance(metadata.workflow_run_id, str)
            and len(metadata.workflow_run_id) <= 20
            and _DECIMAL_ID.fullmatch(metadata.workflow_run_id) is not None
            and (metadata.workflow_head_sha is None or isinstance(metadata.workflow_head_sha, str))
            and (metadata.workflow_repository is None
                 or (isinstance(metadata.workflow_repository, str)
                     and len(metadata.workflow_repository) <= 140))
            and (metadata.workflow_run_attempt is None
                 or (isinstance(metadata.workflow_run_attempt, str)
                     and len(metadata.workflow_run_attempt) <= 20
                     and _DECIMAL_ID.fullmatch(metadata.workflow_run_attempt) is not None))
        )

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> Optional[str]:
        for key, value in headers.items():
            if isinstance(key, str) and key.lower() == name:
                return value if isinstance(value, str) else None
        return None

    @staticmethod
    def _outcome_for_category(category: SourceErrorCategory) -> CiArtifactOutcome:
        return {
            SourceErrorCategory.authentication: CiArtifactOutcome.authentication_required,
            SourceErrorCategory.permission_denied: CiArtifactOutcome.permission_denied,
            SourceErrorCategory.rate_limit: CiArtifactOutcome.rate_limited,
            SourceErrorCategory.not_found: CiArtifactOutcome.artifact_missing,
            SourceErrorCategory.limit_exceeded: CiArtifactOutcome.limit_exceeded,
            SourceErrorCategory.timeout: CiArtifactOutcome.timeout,
            SourceErrorCategory.malformed_payload: CiArtifactOutcome.invalid_archive,
            SourceErrorCategory.configuration: CiArtifactOutcome.invalid_context,
            SourceErrorCategory.network: CiArtifactOutcome.transport_error,
        }.get(category, CiArtifactOutcome.transport_error)

    def _result(
        self,
        outcome: CiArtifactOutcome,
        context,
        receipt: _ReceiptBuilder,
        *,
        schema_valid=False,
        semantics_valid=False,
        identity_bound=False,
        workflow_status_matches=False,
    ) -> CiMetricsArtifactResult:
        workflow_status = None
        if isinstance(context, CiMetricsValidationContext):
            try:
                workflow_status = CiWorkflowStatus(context.workflow_status)
            except (TypeError, ValueError):
                pass
        return CiMetricsArtifactResult(
            outcome=outcome,
            workflow_status=workflow_status,
            schema_valid=schema_valid,
            semantics_valid=semantics_valid,
            identity_bound=identity_bound,
            workflow_status_matches=workflow_status_matches,
            metrics=None,
            green_eligible=False,
            receipt=receipt.freeze(),
        )

    @staticmethod
    def _document_identity_matches(metrics, repository, sha, run_id, attempt) -> bool:
        return (
            metrics.repository.casefold() == repository.casefold()
            and metrics.commit_sha == sha
            and metrics.workflow_run_id == run_id
            and (metrics.workflow_run_attempt is None or metrics.workflow_run_attempt == attempt)
        )

    @staticmethod
    def _semantics_valid(metrics: CiMetricsDocument) -> bool:
        total, failed = metrics.total_tests, metrics.failed_tests
        if total is not None and failed is not None and failed > total:
            return False
        if metrics.coverage_percentage is None:
            if metrics.coverage_metric is not None:
                return False
        elif metrics.coverage_metric != "lines":
            return False

        suites = metrics.test_suites
        complete = {}
        if suites is not None:
            for name in ("unit", "integration"):
                suite = suites.get(name)
                if suite is None:
                    continue
                categories = (
                    suite.failed, suite.passed, suite.cancelled, suite.skipped, suite.todo,
                )
                if (suite.success is True
                        and any(value is not None and value > 0
                                for value in (suite.failed, suite.cancelled))):
                    return False
                counts = (suite.tests, *categories)
                if all(value is not None for value in counts):
                    if suite.tests != sum(categories):
                        return False
                    complete[name] = suite

            unit = complete.get("unit")
            integration = complete.get("integration")
            if unit is not None and integration is not None:
                if total is not None and total != unit.tests + integration.tests:
                    return False
                if failed is not None and failed != unit.failed + integration.failed:
                    return False

        if metrics.status != CiWorkflowStatus.success:
            return True
        if total is None or failed is None or failed != 0:
            return False
        if suites is None:
            return True
        for name in ("unit", "integration"):
            suite = suites.get(name)
            if suite is None:
                return False
            counts = (
                suite.tests, suite.failed, suite.passed, suite.cancelled, suite.skipped,
                suite.todo,
            )
            if any(value is None for value in counts):
                return False
            if suite.success is not True or suite.failed != 0 or suite.cancelled != 0:
                return False
        return True

    @staticmethod
    def _make_document(payload: dict[str, Any]) -> CiMetricsDocument:
        suites = None
        if "test_suites" in payload:
            suites = MappingProxyType({
                name: None if value is None else CiMetricsTestSuite(**value)
                for name, value in payload["test_suites"].items()
            })
        extensions = MappingProxyType({
            key: value for key, value in payload.items() if key not in _CORE_FIELDS
        })
        return CiMetricsDocument(
            repository=payload["repository"],
            commit_sha=payload["commit_sha"],
            workflow_run_id=payload["workflow_run_id"],
            workflow_run_attempt=payload.get("workflow_run_attempt"),
            status=CiWorkflowStatus(payload["status"]),
            total_tests=payload["total_tests"],
            failed_tests=payload["failed_tests"],
            coverage_percentage=payload["coverage_percentage"],
            coverage_metric=payload["coverage_metric"],
            coverage_basis=payload["coverage_basis"],
            test_suites=suites,
            e2e_status=payload.get("e2e_status"),
            extensions=extensions,
        )

    @staticmethod
    def _object_no_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateJsonKey("Duplicate JSON object key.")
            result[key] = value
        return result

    @staticmethod
    def _parse_int(value):
        if len(value.lstrip("-")) > _MAX_JSON_NUMBER_LITERAL:
            raise ValueError("JSON integer literal exceeds the parser limit.")
        return int(value)

    @staticmethod
    def _parse_float(value):
        if len(value) > _MAX_JSON_NUMBER_LITERAL:
            raise ValueError("JSON number literal exceeds the parser limit.")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Non-finite JSON number.")
        mantissa = value.lower().split("e", 1)[0]
        if number == 0.0 and any(char in "123456789" for char in mantissa):
            raise ValueError("JSON number underflows the supported numeric range.")
        return number

    @staticmethod
    def _reject_constant(_value):
        raise ValueError("Non-finite JSON constant.")

    @classmethod
    def _parse_json_document(cls, document_bytes: bytes) -> dict[str, Any]:
        text = document_bytes.decode("utf-8", errors="strict")
        payload = json.loads(
            text,
            object_pairs_hook=cls._object_no_duplicate_keys,
            parse_constant=cls._reject_constant,
            parse_float=cls._parse_float,
            parse_int=cls._parse_int,
        )
        if not isinstance(payload, dict):
            raise ValueError("The metrics document must be a JSON object.")
        return payload

    def _extract_metrics_member(self, archive_bytes: bytes, receipt: _ReceiptBuilder, deadline: float) -> bytes:
        self._check_deadline(deadline)
        if not isinstance(archive_bytes, bytes) or len(archive_bytes) > self.limits.max_compressed_bytes:
            raise _ArtifactFailure(CiArtifactOutcome.limit_exceeded)
        eocd_offset = self._find_eocd(archive_bytes)
        if eocd_offset is None:
            raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
        try:
            eocd = struct.unpack_from("<4s4H2LH", archive_bytes, eocd_offset)
        except struct.error:
            raise _ArtifactFailure(CiArtifactOutcome.invalid_archive) from None
        (signature, disk, directory_disk, entries_on_disk, entries_total,
         directory_size, directory_offset, comment_size) = eocd
        receipt.archive_entries = entries_total
        if (signature != _EOCD_SIGNATURE or disk != 0 or directory_disk != 0
                or entries_on_disk != entries_total or entries_total == 0xFFFF
                or directory_size == 0xFFFFFFFF or directory_offset == 0xFFFFFFFF
                or eocd_offset + 22 + comment_size != len(archive_bytes)
                or directory_offset + directory_size != eocd_offset):
            raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
        if entries_total > self.limits.max_archive_entries:
            raise _ArtifactFailure(CiArtifactOutcome.limit_exceeded)
        if entries_total == 0:
            raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)

        records = []
        cursor = directory_offset
        for _index in range(entries_total):
            self._check_deadline(deadline)
            if cursor + 46 > eocd_offset:
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
            try:
                central = struct.unpack_from("<4s6H3L5H2L", archive_bytes, cursor)
            except struct.error:
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive) from None
            (
                member_signature, _version_made, version_needed, flags, method, _mtime, _mdate,
                crc, compressed_size, claimed_size, name_size, extra_size, member_comment_size,
                disk_start, _internal_attr, external_attr, local_offset,
            ) = central
            central_end = cursor + 46 + name_size + extra_size + member_comment_size
            if (member_signature != _CENTRAL_SIGNATURE or version_needed > 20
                    or disk_start != 0 or central_end > eocd_offset
                    or compressed_size == 0xFFFFFFFF or claimed_size == 0xFFFFFFFF
                    or local_offset == 0xFFFFFFFF or local_offset >= directory_offset):
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
            if flags & 1 or flags & ~0x080E:
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
            if method not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
            if method == zipfile.ZIP_STORED and flags & 0x0006:
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
            if claimed_size > self.limits.max_expanded_bytes:
                raise _ArtifactFailure(CiArtifactOutcome.limit_exceeded)

            name_start = cursor + 46
            name_bytes = archive_bytes[name_start:name_start + name_size]
            extra_start = name_start + name_size
            self._validate_extra(archive_bytes[extra_start:extra_start + extra_size])
            try:
                name = name_bytes.decode("ascii")
            except UnicodeDecodeError:
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive) from None
            path = self._validate_member_name(name)
            is_directory = name.endswith("/")
            mode = (external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(mode)
            dos_directory = bool(external_attr & 0x10)
            if is_directory:
                if (file_type not in (0, stat.S_IFDIR)
                        or not (dos_directory or file_type == stat.S_IFDIR)):
                    raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
            elif file_type not in (0, stat.S_IFREG) or dos_directory:
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)

            records.append({
                "name": name,
                "name_bytes": name_bytes,
                "path": path,
                "is_directory": is_directory,
                "flags": flags,
                "method": method,
                "crc": crc,
                "compressed_size": compressed_size,
                "claimed_size": claimed_size,
                "local_offset": local_offset,
            })
            cursor = central_end
        if cursor != eocd_offset:
            raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)

        path_kinds = {}
        for record in records:
            path_key = record["path"].casefold()
            if path_key in path_kinds:
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
            path_kinds[path_key] = record["is_directory"]
        for record in records:
            parts = record["path"].casefold().split("/")
            for index in range(1, len(parts)):
                parent_kind = path_kinds.get("/".join(parts[:index]))
                if parent_kind is False:
                    raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
            if not record["is_directory"]:
                path_key = record["path"].casefold()
                if any(other.startswith(path_key + "/") for other in path_kinds):
                    raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)

        candidates = []
        for record in records:
            leaf = record["path"].rsplit("/", 1)[-1]
            if leaf.casefold() == "ci-metrics.json":
                if leaf != "ci-metrics.json" or record["is_directory"]:
                    raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
                candidates.append(record)
        if len(candidates) != 1:
            raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
        selected = candidates[0]

        document = None
        expected_local_offset = 0
        for record in sorted(records, key=lambda item: item["local_offset"]):
            self._check_deadline(deadline)
            local_offset = record["local_offset"]
            if local_offset != expected_local_offset or local_offset + 30 > directory_offset:
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
            try:
                local = struct.unpack_from("<4s5H3L2H", archive_bytes, local_offset)
            except struct.error:
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive) from None
            (local_signature, local_version, local_flags, local_method, _local_time,
             _local_date, local_crc, local_compressed, local_size, local_name_size,
             local_extra_size) = local
            if (local_signature != _LOCAL_SIGNATURE or local_version > 20
                    or local_flags != record["flags"] or local_method != record["method"]):
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
            local_name_start = local_offset + 30
            local_name_end = local_name_start + local_name_size
            local_extra_end = local_name_end + local_extra_size
            if local_extra_end > directory_offset:
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
            local_name = archive_bytes[local_name_start:local_name_end]
            local_extra = archive_bytes[local_name_end:local_extra_end]
            self._validate_extra(local_extra)
            if local_name != record["name_bytes"]:
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
            flags = record["flags"]
            crc = record["crc"]
            compressed_size = record["compressed_size"]
            claimed_size = record["claimed_size"]
            if flags & 0x0008:
                if (local_crc not in (0, crc) or local_compressed not in (0, compressed_size)
                        or local_size not in (0, claimed_size)):
                    raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
            elif (local_crc != crc or local_compressed != compressed_size
                    or local_size != claimed_size):
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)

            data_start = local_extra_end
            data_end = data_start + compressed_size
            if data_end > directory_offset:
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
            if record["method"] == zipfile.ZIP_STORED and compressed_size != claimed_size:
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
            member_bytes = self._decode_member(
                archive_bytes[data_start:data_end], record["method"], claimed_size,
                crc, receipt, deadline,
            )
            member_end = data_end
            if flags & 0x0008:
                descriptor_start = data_end
                if archive_bytes[descriptor_start:descriptor_start + 4] == _DATA_DESCRIPTOR_SIGNATURE:
                    descriptor_start += 4
                if descriptor_start + 12 > directory_offset:
                    raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
                try:
                    descriptor_crc, descriptor_compressed, descriptor_size = struct.unpack_from(
                        "<3L", archive_bytes, descriptor_start,
                    )
                except struct.error:
                    raise _ArtifactFailure(CiArtifactOutcome.invalid_archive) from None
                if (descriptor_crc != crc or descriptor_compressed != compressed_size
                        or descriptor_size != claimed_size):
                    raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
                member_end = descriptor_start + 12
            if record["is_directory"] and member_bytes:
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
            expected_local_offset = member_end
            if record is selected:
                if len(member_bytes) > self.limits.max_json_bytes:
                    raise _ArtifactFailure(CiArtifactOutcome.limit_exceeded)
                document = member_bytes

        if expected_local_offset != directory_offset or document is None:
            raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
        return document

    @staticmethod
    def _find_eocd(data: bytes) -> Optional[int]:
        search_start = max(0, len(data) - (22 + 65535))
        offset = data.rfind(_EOCD_SIGNATURE, search_start)
        while offset >= 0:
            if offset + 22 <= len(data):
                try:
                    comment_length = struct.unpack_from("<H", data, offset + 20)[0]
                except struct.error:
                    return None
                if offset + 22 + comment_length == len(data):
                    return offset
            offset = data.rfind(_EOCD_SIGNATURE, search_start, offset)
        return None

    @staticmethod
    def _validate_extra(extra: bytes) -> None:
        cursor = 0
        while cursor < len(extra):
            if cursor + 4 > len(extra):
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
            field_id, size = struct.unpack_from("<HH", extra, cursor)
            cursor += 4
            if cursor + size > len(extra) or field_id == 0x0001:
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
            cursor += size

    @staticmethod
    def _validate_member_name(name: str) -> str:
        is_directory = name.endswith("/")
        path = name[:-1] if is_directory else name
        if (not path or "\\" in name or name.startswith("/") or "\x00" in name
                or any(not re.fullmatch(r"[A-Za-z0-9._-]+", part, re.ASCII)
                       or part in {".", ".."} for part in path.split("/"))):
            raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
        return path

    def _decode_member(self, compressed, method, claimed_size, claimed_crc, receipt, deadline):
        self._check_deadline(deadline)
        if method == zipfile.ZIP_STORED:
            remaining = self.limits.max_expanded_bytes - receipt.expanded_bytes
            if len(compressed) > remaining:
                receipt.expanded_bytes += max(0, remaining) + 1
                raise _ArtifactFailure(CiArtifactOutcome.limit_exceeded)
            decoded = compressed
            self._record_expanded(len(decoded), receipt)
        else:
            decoder = zlib.decompressobj(-zlib.MAX_WBITS)
            output = []
            cursor = 0
            while cursor < len(compressed):
                self._check_deadline(deadline)
                chunk = compressed[cursor:cursor + 4096]
                cursor += len(chunk)
                pending = chunk
                while pending:
                    remaining = self.limits.max_expanded_bytes - receipt.expanded_bytes
                    try:
                        decoded_chunk = decoder.decompress(pending, max(1, remaining + 1))
                    except zlib.error:
                        raise _ArtifactFailure(CiArtifactOutcome.invalid_archive) from None
                    pending = decoder.unconsumed_tail
                    if decoded_chunk:
                        self._record_expanded(len(decoded_chunk), receipt)
                        output.append(decoded_chunk)
                    if decoder.unused_data:
                        raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
                    if pending and not decoded_chunk:
                        raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
            if decoder.unconsumed_tail or decoder.unused_data or not decoder.eof:
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
            try:
                final_chunk = decoder.flush()
            except zlib.error:
                raise _ArtifactFailure(CiArtifactOutcome.invalid_archive) from None
            if final_chunk:
                self._record_expanded(len(final_chunk), receipt)
                output.append(final_chunk)
            decoded = b"".join(output)
        self._check_deadline(deadline)
        if len(decoded) != claimed_size or (zlib.crc32(decoded) & 0xFFFFFFFF) != claimed_crc:
            raise _ArtifactFailure(CiArtifactOutcome.invalid_archive)
        return decoded

    def _record_expanded(self, amount, receipt):
        receipt.expanded_bytes += amount
        if receipt.expanded_bytes > self.limits.max_expanded_bytes:
            raise _ArtifactFailure(CiArtifactOutcome.limit_exceeded)

    def _check_deadline(self, deadline):
        if self._clock() >= deadline:
            raise _ArtifactFailure(CiArtifactOutcome.timeout)


__all__ = ["CiMetricsArtifactValidator"]
