"""Public streaming-boundary contracts for the shared source budget transport."""

import gzip
import unittest

from agente_gestion.adapters.sources.collection_budget import (
    BudgetedHttpGetTransport,
    CollectionBudgetLedger,
    active_budget_scope,
)
from agente_gestion.adapters.sources.http_transport import BoundedGetTransport
from agente_gestion.domain.repositories import RepositoryIdentity
from agente_gestion.ports.source_errors import SourceError, SourceErrorCategory, SourceProvider


IDENTITY = RepositoryIdentity(id="backend", owner="example-org", repo="backend")
URL = "https://api.github.com/repos/example-org/backend"


class ChunkedResponse:
    """Fake requests response that honors iter_content chunk sizing and counts decoded bytes."""

    status_code = 200

    def __init__(
        self,
        decoded_body: bytes,
        headers: dict[str, str] | None = None,
        stream_chunks: tuple[bytes, ...] | None = None,
    ):
        self.decoded_body = decoded_body
        self.headers = headers or {}
        self.stream_chunks = stream_chunks
        self.bytes_yielded = 0
        self.yielded_chunks = 0
        self.requested_chunk_size: int | None = None
        self.closed = False

    def iter_content(self, chunk_size: int):
        self.requested_chunk_size = chunk_size
        if self.stream_chunks is not None:
            for chunk in self.stream_chunks:
                self.yielded_chunks += 1
                self.bytes_yielded += len(chunk)
                yield chunk
            return
        for offset in range(0, len(self.decoded_body), chunk_size):
            chunk = self.decoded_body[offset:offset + chunk_size]
            self.yielded_chunks += 1
            self.bytes_yielded += len(chunk)
            yield chunk

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response: ChunkedResponse):
        self.response = response
        self.trust_env = True
        self.closed = False
        self.calls: list[dict] = []

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.response

    def close(self):
        self.closed = True


def execute_request(
    body: bytes,
    *,
    headers: dict[str, str] | None = None,
    project_byte_cap: int = 100,
    repository_byte_cap: int = 100,
    per_response_byte_cap: int = 100,
    budgeted: bool = True,
    stream_chunks: tuple[bytes, ...] | None = None,
    transport_clock=None,
):
    ledger = CollectionBudgetLedger(
        project_max_http_calls=4,
        repository_max_http_calls=2,
        project_max_files=10,
        repository_max_files=10,
        project_max_response_bytes=project_byte_cap,
        repository_max_response_bytes=repository_byte_cap,
        project_timeout_seconds=20,
        repository_timeout_seconds=10,
        project_max_estimated_tokens=100,
        repository_max_estimated_tokens=50,
        default_request_timeout_seconds=5,
        per_response_max_bytes=per_response_byte_cap,
        clock=lambda: 0.0,
    )
    ledger.begin_repository(IDENTITY)
    response = ChunkedResponse(body, headers, stream_chunks)
    session = FakeSession(response)
    bounded = BoundedGetTransport(
        SourceProvider.github,
        max_response_bytes=per_response_byte_cap,
        session_factory=lambda: session,
        clock=transport_clock if transport_clock is not None else lambda: 0.0,
    )
    transport = (
        BudgetedHttpGetTransport(bounded, ledger, SourceProvider.github, IDENTITY)
        if budgeted else bounded
    )
    result = None
    error = None
    try:
        result = transport.get(URL)
    except SourceError as exc:
        error = exc
    return result, error, ledger, response, session


class CollectionBudgetStreamingTests(unittest.TestCase):
    def assert_closed_and_scope_cleared(self, response: ChunkedResponse, session: FakeSession):
        self.assertTrue(response.closed)
        self.assertTrue(session.closed)
        self.assertIsNone(active_budget_scope())
        self.assertEqual(len(session.calls), 1)
        self.assertFalse(session.calls[0]["allow_redirects"])
        self.assertFalse(session.trust_env)

    def test_exact_project_and_repository_caps_accept_eof_without_trusting_headers(self):
        body = b'{"x":1}'
        cases = (
            ("project", None),
            ("project", {"Content-Length": "7x"}),
            ("project", {"Content-Length": "7"}),
            ("repository", None),
            ("repository", {"Content-Length": "7x"}),
            ("repository", {"Content-Length": "7"}),
        )
        for scope, headers in cases:
            with self.subTest(scope=scope, headers=headers):
                project_cap = 7 if scope == "project" else 100
                repository_cap = 7 if scope == "repository" else 100
                result, error, ledger, response, session = execute_request(
                    body,
                    headers=headers,
                    project_byte_cap=project_cap,
                    repository_byte_cap=repository_cap,
                )

                self.assertIsNone(error)
                self.assertEqual(result.body, {"x": 1})
                self.assertEqual(result.bytes_received, 7)
                self.assertEqual(response.bytes_yielded, 7)
                self.assertEqual(ledger.receipt().response_bytes, 7)
                self.assertEqual(ledger.repository_receipt(IDENTITY).response_bytes, 7)
                self.assertEqual(ledger.receipt().exhausted, scope == "project")
                self.assertEqual(ledger.repository_receipt(IDENTITY).exhausted, scope == "repository")
                self.assert_closed_and_scope_cleared(response, session)

    def test_empty_chunks_preserve_exact_cap_eof_for_project_and_repository(self):
        body = b'{"x":1}'
        cases = (
            ("project", (body, b""), 7, 100),
            ("repository", (body, b"", b"", b""), 100, 7),
        )
        for scope, chunks, project_cap, repository_cap in cases:
            with self.subTest(scope=scope, empty_chunks=len(chunks) - 1):
                result, error, ledger, response, session = execute_request(
                    body,
                    project_byte_cap=project_cap,
                    repository_byte_cap=repository_cap,
                    stream_chunks=chunks,
                )

                self.assertIsNone(error)
                self.assertEqual(result.body, {"x": 1})
                self.assertEqual(result.bytes_received, 7)
                self.assertEqual(response.bytes_yielded, 7)
                self.assertEqual(response.yielded_chunks, len(chunks))
                self.assertEqual(ledger.receipt().response_bytes, 7)
                self.assertEqual(ledger.repository_receipt(IDENTITY).response_bytes, 7)
                self.assertEqual(ledger.receipt().exhausted, scope == "project")
                self.assertEqual(ledger.repository_receipt(IDENTITY).exhausted, scope == "repository")
                self.assertEqual(ledger.receipt().http_calls, 1)
                self.assert_closed_and_scope_cleared(response, session)

    def test_empty_chunks_do_not_allow_a_second_positive_overflow_chunk(self):
        body = b'{"x":1}'
        chunks = (body, b"", b"", b" ", b'{"later":true}')
        result, error, ledger, response, session = execute_request(
            body,
            project_byte_cap=7,
            repository_byte_cap=100,
            stream_chunks=chunks,
        )

        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertEqual(error.category, SourceErrorCategory.limit_exceeded)
        self.assertEqual(response.bytes_yielded, 8)
        self.assertEqual(response.yielded_chunks, 4)
        self.assertEqual(ledger.receipt().response_bytes, 8)
        self.assertEqual(ledger.repository_receipt(IDENTITY).response_bytes, 8)
        self.assertTrue(ledger.receipt().exhausted)
        self.assertFalse(ledger.repository_receipt(IDENTITY).exhausted)
        self.assertEqual(ledger.receipt().http_calls, 1)
        self.assert_closed_and_scope_cleared(response, session)

    def test_deadline_is_checked_while_empty_eof_probe_chunks_repeat(self):
        class StepClock:
            def __init__(self):
                self.tick = 0.0

            def __call__(self):
                self.tick += 1.0
                return self.tick

        body = b'{"x":1}'
        empty_probe_chunks = (body,) + (b"",) * 20
        clock = StepClock()
        result, error, ledger, response, session = execute_request(
            body,
            project_byte_cap=7,
            repository_byte_cap=100,
            stream_chunks=empty_probe_chunks,
            transport_clock=clock,
        )

        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertEqual(error.category, SourceErrorCategory.timeout)
        self.assertEqual(response.bytes_yielded, 7)
        self.assertEqual(response.yielded_chunks, 5)
        self.assertLess(response.yielded_chunks, len(empty_probe_chunks))
        self.assertEqual(ledger.receipt().response_bytes, 7)
        self.assertEqual(ledger.repository_receipt(IDENTITY).response_bytes, 7)
        self.assertTrue(ledger.receipt().exhausted)
        self.assertFalse(ledger.repository_receipt(IDENTITY).exhausted)
        self.assert_closed_and_scope_cleared(response, session)

    def test_unbudgeted_legacy_transport_accepts_exact_response_without_length(self):
        result, error, _ledger, response, session = execute_request(
            b'{"x":1}',
            project_byte_cap=7,
            repository_byte_cap=100,
            per_response_byte_cap=7,
            budgeted=False,
        )

        self.assertIsNone(error)
        self.assertEqual(result.body, {"x": 1})
        self.assertEqual(result.bytes_received, 7)
        self.assertEqual(response.bytes_yielded, 7)
        self.assert_closed_and_scope_cleared(response, session)

    def test_oversized_content_length_does_not_reject_small_actual_body(self):
        result, error, ledger, response, session = execute_request(
            b'{"x":1}',
            headers={"Content-Length": "999999999999"},
            project_byte_cap=100,
            repository_byte_cap=100,
            per_response_byte_cap=7,
        )

        self.assertIsNone(error)
        self.assertEqual(result.body, {"x": 1})
        self.assertEqual(result.bytes_received, 7)
        self.assertEqual(response.bytes_yielded, 7)
        self.assertEqual(ledger.receipt().response_bytes, 7)
        self.assertEqual(ledger.repository_receipt(IDENTITY).response_bytes, 7)
        self.assertFalse(ledger.receipt().exhausted)
        self.assertFalse(ledger.repository_receipt(IDENTITY).exhausted)
        self.assert_closed_and_scope_cleared(response, session)

    def test_actual_overflow_is_counted_and_only_the_exceeded_scope_is_exhausted(self):
        body = b'{"x":1} '
        cases = (
            ("project", 7, 100, 100, True, False),
            ("repository", 100, 7, 100, False, True),
            ("per-response", 100, 100, 7, False, False),
        )
        for label, project_cap, repository_cap, response_cap, project_exhausted, repository_exhausted in cases:
            with self.subTest(scope=label):
                result, error, ledger, response, session = execute_request(
                    body,
                    headers={"Content-Length": "999999999999"},
                    project_byte_cap=project_cap,
                    repository_byte_cap=repository_cap,
                    per_response_byte_cap=response_cap,
                )

                self.assertIsNone(result)
                self.assertIsNotNone(error)
                self.assertEqual(error.category, SourceErrorCategory.limit_exceeded)
                self.assertEqual(response.bytes_yielded, 8)
                self.assertEqual(ledger.receipt().response_bytes, 8)
                self.assertEqual(ledger.repository_receipt(IDENTITY).response_bytes, 8)
                self.assertEqual(ledger.receipt().exhausted, project_exhausted)
                self.assertEqual(ledger.repository_receipt(IDENTITY).exhausted, repository_exhausted)
                self.assert_closed_and_scope_cleared(response, session)

    def test_gzip_content_length_is_not_decoded_stream_length_or_json_eof(self):
        decoded = b"{}" + b" " * 4096 + b"{}"
        encoded = gzip.compress(decoded, mtime=0)
        encoded_length = len(encoded)
        self.assertGreater(encoded_length, 2)
        self.assertLess(encoded_length * 2, len(decoded))
        headers = {
            "Content-Encoding": "gzip",
            "Content-Length": str(encoded_length),
        }

        for scope in ("project", "repository"):
            with self.subTest(scope=scope):
                project_cap = encoded_length if scope == "project" else encoded_length * 10
                repository_cap = encoded_length if scope == "repository" else encoded_length * 10
                result, error, ledger, response, session = execute_request(
                    decoded,
                    headers=headers,
                    project_byte_cap=project_cap,
                    repository_byte_cap=repository_cap,
                    per_response_byte_cap=len(decoded),
                )

                observed = min(len(decoded), encoded_length * 2)
                self.assertIsNone(result)
                self.assertIsNotNone(error)
                self.assertEqual(error.category, SourceErrorCategory.limit_exceeded)
                self.assertEqual(response.bytes_yielded, observed)
                self.assertLess(response.bytes_yielded, len(decoded))
                self.assertEqual(ledger.receipt().response_bytes, observed)
                self.assertEqual(ledger.repository_receipt(IDENTITY).response_bytes, observed)
                self.assertEqual(ledger.receipt().exhausted, scope == "project")
                self.assertEqual(ledger.repository_receipt(IDENTITY).exhausted, scope == "repository")
                self.assert_closed_and_scope_cleared(response, session)

                legacy_response = ChunkedResponse(decoded, headers)
                legacy_session = FakeSession(legacy_response)
                legacy = BoundedGetTransport(
                    SourceProvider.github,
                    max_response_bytes=encoded_length,
                    session_factory=lambda: legacy_session,
                    clock=lambda: 0.0,
                )
                with self.assertRaises(SourceError) as legacy_error:
                    legacy.get(URL)
                self.assertEqual(legacy_error.exception.category, SourceErrorCategory.limit_exceeded)
                self.assertEqual(legacy_response.bytes_yielded, observed)
                self.assertTrue(legacy_response.closed)
                self.assertTrue(legacy_session.closed)

    def test_valid_gzip_encoded_length_control_parses_complete_body(self):
        decoded = b"{}"
        encoded = gzip.compress(decoded, mtime=0)
        result, error, ledger, response, session = execute_request(
            decoded,
            headers={"Content-Encoding": "gzip", "Content-Length": str(len(encoded))},
            project_byte_cap=100,
            repository_byte_cap=100,
            per_response_byte_cap=100,
        )

        self.assertIsNone(error)
        self.assertEqual(result.body, {})
        self.assertEqual(result.bytes_received, len(decoded))
        self.assertEqual(response.bytes_yielded, len(decoded))
        self.assertEqual(ledger.receipt().response_bytes, len(decoded))
        self.assertEqual(ledger.repository_receipt(IDENTITY).response_bytes, len(decoded))
        self.assertFalse(ledger.receipt().exhausted)
        self.assertFalse(ledger.repository_receipt(IDENTITY).exhausted)
        self.assert_closed_and_scope_cleared(response, session)


if __name__ == "__main__":
    unittest.main()
