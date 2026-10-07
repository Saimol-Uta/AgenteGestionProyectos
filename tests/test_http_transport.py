"""Tests for bounded read-only HTTP GET transport."""

import math
import traceback
import unittest
from typing import Any

import requests

from agente_gestion.ports.source_errors import (
    SourceError,
    SourceErrorCategory,
    SourceProvider,
)
from agente_gestion.adapters.sources.http_transport import (
    BoundedGetTransport,
    HttpGetTransportPort,
    HttpResponse,
)


class FakeResponse:
    """Fake requests.Response for deterministic unit testing."""

    def __init__(
        self,
        status_code: int = 200,
        chunks: list[bytes] | None = None,
        headers: dict[str, str] | None = None,
        stream_exc: Exception | None = None,
    ):
        self.status_code = status_code
        self.chunks = chunks if chunks is not None else [b'{"ok": true}']
        self.headers = headers if headers is not None else {}
        self.stream_exc = stream_exc
        self.closed = False

    def iter_content(self, chunk_size: int = 65536):
        for chunk in self.chunks:
            if self.stream_exc:
                raise self.stream_exc
            yield chunk

    def close(self):
        self.closed = True


class FakeSession:
    """Fake requests.Session capturing requests and controlling lifecycle."""

    def __init__(
        self,
        response: FakeResponse | None = None,
        get_exc: Exception | None = None,
    ):
        self.response = response or FakeResponse()
        self.get_exc = get_exc
        self.trust_env = True
        self.closed = False
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any):
        self.calls.append({"url": url, **kwargs})
        if self.get_exc:
            raise self.get_exc
        return self.response

    def close(self):
        self.closed = True


class TestHttpTransport(unittest.TestCase):
    """Test suite for HttpGetTransportPort, HttpResponse, and BoundedGetTransport."""

    def test_protocol_conformance_and_surface(self):
        """Transport satisfies HttpGetTransportPort protocol with GET-only public surface."""
        transport = BoundedGetTransport(SourceProvider.trello)
        self.assertIsInstance(transport, HttpGetTransportPort)
        self.assertTrue(callable(getattr(transport, "get", None)))
        self.assertFalse(hasattr(transport, "post"))
        self.assertFalse(hasattr(transport, "put"))
        self.assertFalse(hasattr(transport, "delete"))
        self.assertFalse(hasattr(transport, "request"))

    def test_successful_get_dict_and_list_bodies(self):
        """Valid JSON dict and list payloads parse cleanly with allowlisted headers."""
        fake_resp = FakeResponse(
            status_code=200,
            chunks=[b'{"key": "value"}'],
            headers={
                "Link": '<https://api.github.com/resource?page=2>; rel="next"',
                "X-RateLimit-Remaining": "4900",
                "Set-Cookie": "session=secret_cookie_val",
                "Server": "GitHub.com",
            },
        )
        fake_sess = FakeSession(response=fake_resp)
        transport = BoundedGetTransport(
            SourceProvider.github,
            session_factory=lambda: fake_sess,
        )

        resp = transport.get("https://api.github.com/repos/org/repo")
        self.assertIsInstance(resp, HttpResponse)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.body, {"key": "value"})
        self.assertEqual(
            resp.headers,
            {
                "link": '<https://api.github.com/resource?page=2>; rel="next"',
                "x-ratelimit-remaining": "4900",
            },
        )
        self.assertNotIn("set-cookie", resp.headers)
        self.assertNotIn("server", resp.headers)
        self.assertEqual(resp.attempt_count, 1)
        self.assertEqual(resp.bytes_received, len(b'{"key": "value"}'))
        self.assertTrue(fake_resp.closed)
        self.assertTrue(fake_sess.closed)

        # Also verify root list body
        fake_list_resp = FakeResponse(
            status_code=200,
            chunks=[b'[{"id": "c1"}, {"id": "c2"}]'],
        )
        fake_sess_list = FakeSession(response=fake_list_resp)
        transport_trello = BoundedGetTransport(
            SourceProvider.trello,
            session_factory=lambda: fake_sess_list,
        )
        resp_list = transport_trello.get("https://api.trello.com/1/boards/b1/cards")
        self.assertEqual(resp_list.body, [{"id": "c1"}, {"id": "c2"}])
        self.assertEqual(resp_list.status_code, 200)
        self.assertTrue(fake_list_resp.closed)
        self.assertTrue(fake_sess_list.closed)

    def test_session_flags_and_timeout_propagation(self):
        """Transport sets trust_env=False, stream=True, allow_redirects=False, and timeouts."""
        fake_resp = FakeResponse(chunks=[b'{"ok": true}'])
        fake_sess = FakeSession(response=fake_resp)
        transport = BoundedGetTransport(
            SourceProvider.trello,
            timeout_seconds=7.5,
            session_factory=lambda: fake_sess,
        )

        transport.get(
            "https://api.trello.com/1/members/me",
            params={"filter": "open"},
            headers={"Authorization": "OAuth token=secret"},
        )
        self.assertFalse(fake_sess.trust_env)
        self.assertEqual(len(fake_sess.calls), 1)
        call = fake_sess.calls[0]
        self.assertEqual(call["url"], "https://api.trello.com/1/members/me")
        self.assertEqual(call["params"], {"filter": "open"})
        self.assertEqual(call["headers"], {"Authorization": "OAuth token=secret"})
        self.assertEqual(call["timeout"], 7.5)
        self.assertTrue(call["stream"])
        self.assertFalse(call["allow_redirects"])

        # Override timeout per-call
        fake_resp2 = FakeResponse(chunks=[b'{"ok": true}'])
        fake_sess2 = FakeSession(response=fake_resp2)
        transport2 = BoundedGetTransport(
            SourceProvider.trello,
            timeout_seconds=10.0,
            session_factory=lambda: fake_sess2,
        )
        transport2.get("https://api.trello.com/1/members/me", timeout_seconds=2.5)
        self.assertEqual(fake_sess2.calls[0]["timeout"], 2.5)

    def test_single_attempt_without_retries(self):
        """Transport performs exactly one attempt on network failure without retry/sleep."""
        fake_sess = FakeSession(get_exc=requests.exceptions.ConnectionError("offline"))
        transport = BoundedGetTransport(
            SourceProvider.github,
            session_factory=lambda: fake_sess,
        )
        with self.assertRaises(SourceError) as ctx:
            transport.get("https://api.github.com/user")
        self.assertEqual(ctx.exception.provider, SourceProvider.github)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.network)
        self.assertEqual(len(fake_sess.calls), 1)
        self.assertTrue(fake_sess.closed)

    def test_preflight_origin_and_url_security_checks(self):
        """Rejects non-HTTPS, userinfo, non-443 ports, fragments, and foreign hosts safely."""
        fake_sess = FakeSession()
        transport = BoundedGetTransport(
            SourceProvider.trello,
            session_factory=lambda: fake_sess,
        )

        invalid_urls = [
            "http://api.trello.com/1/boards",
            "https://user:pass@api.trello.com/1/boards",
            "https://user@api.trello.com/1/boards",
            "https://api.trello.com:8080/1/boards",
            "https://api.trello.com/1/boards#fragment",
            "https://evil.com/1/boards",
            "https://trello.com/1/boards",
            "https://api.github.com/1/boards",
            "ftp://api.trello.com/1/boards",
            "",
            "not_a_url",
        ]

        for url in invalid_urls:
            with self.subTest(url=url):
                with self.assertRaises(SourceError) as ctx:
                    transport.get(url)
                self.assertEqual(ctx.exception.provider, SourceProvider.trello)
                self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

        # Network was never contacted for invalid URLs
        self.assertEqual(len(fake_sess.calls), 0)

        # Port 443 explicit is allowed
        fake_resp_443 = FakeResponse(chunks=[b'{"ok": true}'])
        fake_sess_443 = FakeSession(response=fake_resp_443)
        transport_443 = BoundedGetTransport(
            SourceProvider.trello,
            session_factory=lambda: fake_sess_443,
        )
        resp_443 = transport_443.get("https://api.trello.com:443/1/boards")
        self.assertEqual(resp_443.status_code, 200)

    def test_provider_initialization_restrictions(self):
        """Rejects local_policy as invalid HTTP provider, and unknown providers."""
        with self.assertRaises(SourceError) as ctx:
            BoundedGetTransport(SourceProvider.local_policy)
        self.assertEqual(ctx.exception.provider, SourceProvider.local_policy)
        self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

        with self.assertRaises(ValueError):
            BoundedGetTransport("unsupported_provider")

    def test_redirect_rejection(self):
        """All 3xx redirects are rejected as network category without following Location."""
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                fake_resp = FakeResponse(
                    status_code=code,
                    headers={"Location": "https://evil.com/steal"},
                )
                fake_sess = FakeSession(response=fake_resp)
                transport = BoundedGetTransport(
                    SourceProvider.github,
                    session_factory=lambda: fake_sess,
                )
                with self.assertRaises(SourceError) as ctx:
                    transport.get("https://api.github.com/repos/org/repo")
                self.assertEqual(ctx.exception.category, SourceErrorCategory.network)
                self.assertTrue(fake_resp.closed)
                self.assertTrue(fake_sess.closed)

    def test_status_code_mapping(self):
        """Maps HTTP status codes to canonical SourceError categories."""
        mappings = [
            (401, {}, SourceErrorCategory.authentication),
            (403, {}, SourceErrorCategory.permission_denied),
            (404, {}, SourceErrorCategory.not_found),
            (429, {}, SourceErrorCategory.rate_limit),
            (500, {}, SourceErrorCategory.network),
            (502, {}, SourceErrorCategory.network),
            (503, {}, SourceErrorCategory.network),
            (504, {}, SourceErrorCategory.network),
            (400, {}, SourceErrorCategory.network),
            (418, {}, SourceErrorCategory.network),
        ]
        for code, headers, expected_category in mappings:
            with self.subTest(code=code):
                fake_resp = FakeResponse(status_code=code, headers=headers)
                fake_sess = FakeSession(response=fake_resp)
                transport = BoundedGetTransport(
                    SourceProvider.trello,
                    session_factory=lambda: fake_sess,
                )
                with self.assertRaises(SourceError) as ctx:
                    transport.get("https://api.trello.com/1/cards")
                self.assertEqual(ctx.exception.category, expected_category)
                self.assertTrue(fake_resp.closed)
                self.assertTrue(fake_sess.closed)

    def test_github_403_rate_limit_detection(self):
        """GitHub 403 with x-ratelimit-remaining: 0 or Retry-After maps to rate_limit."""
        # Remaining 0 on GitHub -> rate_limit
        fake_resp_zero = FakeResponse(
            status_code=403,
            headers={"X-RateLimit-Remaining": "0"},
        )
        fake_sess_zero = FakeSession(response=fake_resp_zero)
        transport_gh = BoundedGetTransport(
            SourceProvider.github,
            session_factory=lambda: fake_sess_zero,
        )
        with self.assertRaises(SourceError) as ctx:
            transport_gh.get("https://api.github.com/repos/org/repo")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.rate_limit)

        # Retry-After on GitHub -> rate_limit
        fake_resp_retry = FakeResponse(
            status_code=403,
            headers={"Retry-After": "60"},
        )
        fake_sess_retry = FakeSession(response=fake_resp_retry)
        transport_gha = BoundedGetTransport(
            SourceProvider.github_actions,
            session_factory=lambda: fake_sess_retry,
        )
        with self.assertRaises(SourceError) as ctx:
            transport_gha.get("https://api.github.com/repos/org/repo/actions/runs")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.rate_limit)

        # 403 on Trello with rate headers remains permission_denied (no false heuristic)
        fake_resp_trello = FakeResponse(
            status_code=403,
            headers={"X-RateLimit-Remaining": "0"},
        )
        fake_sess_trello = FakeSession(response=fake_resp_trello)
        transport_trello = BoundedGetTransport(
            SourceProvider.trello,
            session_factory=lambda: fake_sess_trello,
        )
        with self.assertRaises(SourceError) as ctx:
            transport_trello.get("https://api.trello.com/1/boards")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.permission_denied)

    def test_transport_timeout_and_network_exceptions(self):
        """Low-level requests Timeout maps to timeout; RequestException to network."""
        fake_sess_timeout = FakeSession(
            get_exc=requests.exceptions.ReadTimeout("read timed out")
        )
        transport = BoundedGetTransport(
            SourceProvider.github,
            session_factory=lambda: fake_sess_timeout,
        )
        with self.assertRaises(SourceError) as ctx:
            transport.get("https://api.github.com/user")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.timeout)

        fake_sess_conn = FakeSession(
            get_exc=requests.exceptions.SSLError("cert verification failure")
        )
        transport_ssl = BoundedGetTransport(
            SourceProvider.github,
            session_factory=lambda: fake_sess_conn,
        )
        with self.assertRaises(SourceError) as ctx:
            transport_ssl.get("https://api.github.com/user")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.network)

    def test_json_decode_and_root_payload_validation(self):
        """Malformed JSON, non-UTF-8, empty body, and scalar roots map to malformed_payload."""
        bad_bodies = [
            [b"not valid json"],
            [b""],
            [b"   "],
            [b"\xff\xfe\x00\x00"],  # invalid utf-8
            [b"12345"],             # integer root
            [b'"just a string"'],   # string root
            [b"true"],              # boolean root
            [b"null"],              # null root
        ]
        for chunks in bad_bodies:
            with self.subTest(chunks=chunks):
                fake_resp = FakeResponse(chunks=chunks)
                fake_sess = FakeSession(response=fake_resp)
                transport = BoundedGetTransport(
                    SourceProvider.trello,
                    session_factory=lambda: fake_sess,
                )
                with self.assertRaises(SourceError) as ctx:
                    transport.get("https://api.trello.com/1/boards")
                self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)
                self.assertTrue(fake_resp.closed)
                self.assertTrue(fake_sess.closed)

    def test_byte_limit_enforcement_and_lying_content_length(self):
        """Streaming chunk cap rejects oversize payloads regardless of Content-Length."""
        # 10 bytes cap, response yields 12 bytes
        fake_resp = FakeResponse(
            chunks=[b'{"a": 1, ', b'"b": 2}'],
            headers={"Content-Length": "5"},  # lying Content-Length
        )
        fake_sess = FakeSession(response=fake_resp)
        transport = BoundedGetTransport(
            SourceProvider.github,
            max_response_bytes=10,
            session_factory=lambda: fake_sess,
        )
        with self.assertRaises(SourceError) as ctx:
            transport.get("https://api.github.com/repos/org/repo")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.limit_exceeded)
        self.assertTrue(fake_resp.closed)
        self.assertTrue(fake_sess.closed)

    def test_stream_read_failure(self):
        """Network error raised while reading chunk stream maps to network category."""
        fake_resp = FakeResponse(
            chunks=[b'{"part": 1}'],
            stream_exc=requests.exceptions.ChunkedEncodingError("connection dropped"),
        )
        fake_sess = FakeSession(response=fake_resp)
        transport = BoundedGetTransport(
            SourceProvider.trello,
            session_factory=lambda: fake_sess,
        )
        with self.assertRaises(SourceError) as ctx:
            transport.get("https://api.trello.com/1/cards")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.network)
        self.assertTrue(fake_resp.closed)
        self.assertTrue(fake_sess.closed)

    def test_wall_clock_timeout_budget_check_between_chunks(self):
        """Elapsed budget check stops slow trickle stream when wall-clock expires."""
        current_time = 100.0

        def tick_clock() -> float:
            nonlocal current_time
            current_time += 6.0
            return current_time

        fake_resp = FakeResponse(chunks=[b'{"part": 1, ', b'"part": 2}'])
        fake_sess = FakeSession(response=fake_resp)
        transport = BoundedGetTransport(
            SourceProvider.github,
            timeout_seconds=5.0,
            session_factory=lambda: fake_sess,
            clock=tick_clock,
        )
        with self.assertRaises(SourceError) as ctx:
            transport.get("https://api.github.com/repos/org/repo")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.timeout)
        self.assertTrue(fake_resp.closed)
        self.assertTrue(fake_sess.closed)

    def test_credential_sentinel_and_secret_non_leakage(self):
        """Tokens, secret headers, URL query secrets, and server bodies never leak to errors."""
        secret_token = "SECRET_BEARER_TOKEN_xyz987"
        fake_resp = FakeResponse(
            status_code=500,
            chunks=[b'{"internal_server_leak": "database credentials exposed"}'],
        )
        fake_sess = FakeSession(response=fake_resp)
        transport = BoundedGetTransport(
            SourceProvider.github,
            session_factory=lambda: fake_sess,
        )

        with self.assertRaises(SourceError) as ctx:
            transport.get(
                f"https://api.github.com/repos/org/repo?token={secret_token}",
                headers={"Authorization": f"Bearer {secret_token}"},
            )

        err_str = str(ctx.exception)
        err_repr = repr(ctx.exception)
        tb_text = traceback.format_exc()

        self.assertNotIn(secret_token, err_str)
        self.assertNotIn(secret_token, err_repr)
        self.assertNotIn("database credentials exposed", err_str)
        self.assertNotIn("database credentials exposed", err_repr)
        self.assertEqual(
            err_str,
            "Source 'github' failed with error category 'network'.",
        )

        # Repr of transport and response don't leak secrets
        trans_repr = repr(transport)
        self.assertNotIn(secret_token, trans_repr)

        http_resp = HttpResponse(status_code=200, body={"data": 1})
        self.assertNotIn("raw_bytes", dir(http_resp))
        self.assertNotIn("request", dir(http_resp))

    def test_constructor_and_override_parameter_validation(self):
        """Invalid timeout or max_response_bytes values raise SourceError configuration."""
        invalid_timeouts = [0, -1, -5.5, math.nan, math.inf, -math.inf, True, False, "10"]
        for t in invalid_timeouts:
            with self.subTest(timeout=t):
                with self.assertRaises(SourceError) as ctx:
                    BoundedGetTransport(SourceProvider.trello, timeout_seconds=t)  # type: ignore
                self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

        invalid_bytes = [0, -100, True, False, 10.5, "1024", math.inf]
        for b in invalid_bytes:
            with self.subTest(max_bytes=b):
                with self.assertRaises(SourceError) as ctx:
                    BoundedGetTransport(SourceProvider.trello, max_response_bytes=b)  # type: ignore
                self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

        # Per-call timeout invalid override
        transport = BoundedGetTransport(SourceProvider.trello)
        for t in invalid_timeouts:
            with self.subTest(override_timeout=t):
                with self.assertRaises(SourceError) as ctx:
                    transport.get("https://api.trello.com/1/cards", timeout_seconds=t)  # type: ignore
                self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

        # Invalid params / headers types
        with self.assertRaises(SourceError) as ctx:
            transport.get("https://api.trello.com/1/cards", params="not_a_dict")  # type: ignore
        self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

        with self.assertRaises(SourceError) as ctx:
            transport.get("https://api.trello.com/1/cards", headers=["not_a_dict"])  # type: ignore
        self.assertEqual(ctx.exception.category, SourceErrorCategory.configuration)

    def test_invalid_constructor_provider_sentinel_suppression(self):
        """Invalid provider raises static ValueError without interpolating sentinel input."""
        sentinel = "FORBIDDEN_KEY_12345"
        with self.assertRaises(ValueError) as ctx:
            BoundedGetTransport(f"bad_provider_{sentinel}")
        self.assertEqual(str(ctx.exception), "Invalid source provider.")
        self.assertNotIn(sentinel, str(ctx.exception))
        self.assertNotIn(sentinel, repr(ctx.exception))

    def test_validate_url_context_suppression_on_malformed_port_and_url(self):
        """Malformed URL and invalid port raise SourceError configuration with suppressed context."""
        sentinel = "MALICIOUS_PORT_SECRET_9999"
        transport = BoundedGetTransport(SourceProvider.trello)

        test_urls = [
            f"https://api.trello.com:999999_{sentinel}/cards",
            f"https://[{sentinel}]/path",
        ]
        for url in test_urls:
            with self.subTest(url=url):
                try:
                    transport.get(url)
                except SourceError as exc:
                    self.assertEqual(exc.category, SourceErrorCategory.configuration)
                    self.assertIsNone(exc.__cause__)
                    tb_text = traceback.format_exc()
                    self.assertNotIn(sentinel, tb_text)
                    self.assertNotIn(sentinel, str(exc))
                    self.assertNotIn("ValueError", tb_text)
                else:
                    self.fail("Expected SourceError was not raised")

    def test_session_lifecycle_guarded_and_best_effort_cleanup(self):
        """Guards session factory/setup and ensures cleanup exceptions never override outcome."""
        sentinel = "SESSION_SENTINEL_LEAK_TEST"

        # 1. Factory failure with sentinel
        def failing_factory():
            raise RuntimeError(f"factory failure {sentinel}")

        transport = BoundedGetTransport(
            SourceProvider.github,
            session_factory=failing_factory,
        )
        try:
            transport.get("https://api.github.com/user")
        except SourceError as exc:
            self.assertEqual(exc.category, SourceErrorCategory.network)
            self.assertNotIn(sentinel, str(exc))
            self.assertNotIn(sentinel, traceback.format_exc())
        else:
            self.fail("Expected SourceError on factory failure")

        # 2. Session setup failure (e.g. trust_env property setter fails)
        class ExplodingSession(FakeSession):
            def __init__(self):
                super().__init__()
                self._armed = True

            @property
            def trust_env(self):
                return False

            @trust_env.setter
            def trust_env(self, val):
                if getattr(self, "_armed", False):
                    raise RuntimeError(f"trust_env setup failure {sentinel}")

        exploding_sess = ExplodingSession()
        transport_setup = BoundedGetTransport(
            SourceProvider.github,
            session_factory=lambda: exploding_sess,
        )
        try:
            transport_setup.get("https://api.github.com/user")
        except SourceError as exc:
            self.assertEqual(exc.category, SourceErrorCategory.network)
            self.assertNotIn(sentinel, str(exc))
            self.assertNotIn(sentinel, traceback.format_exc())
            self.assertTrue(exploding_sess.closed)
        else:
            self.fail("Expected SourceError on setup failure")

        # 3. Cleanup close exception does not override successful HTTP response
        class ExplodingCloseResponse(FakeResponse):
            def close(self):
                super().close()
                raise RuntimeError(f"response close failure {sentinel}")

        class ExplodingCloseSession(FakeSession):
            def close(self):
                super().close()
                raise RuntimeError(f"session close failure {sentinel}")

        clean_resp = ExplodingCloseResponse(chunks=[b'{"clean": true}'])
        clean_sess = ExplodingCloseSession(response=clean_resp)
        transport_cleanup = BoundedGetTransport(
            SourceProvider.trello,
            session_factory=lambda: clean_sess,
        )
        resp = transport_cleanup.get("https://api.trello.com/1/cards")
        self.assertEqual(resp.body, {"clean": True})
        self.assertTrue(clean_resp.closed)
        self.assertTrue(clean_sess.closed)

        # 4. Cleanup close exception does not override primary error (e.g. 401 auth)
        err_resp = ExplodingCloseResponse(status_code=401)
        err_sess = ExplodingCloseSession(response=err_resp)
        transport_err_cleanup = BoundedGetTransport(
            SourceProvider.trello,
            session_factory=lambda: err_sess,
        )
        try:
            transport_err_cleanup.get("https://api.trello.com/1/cards")
        except SourceError as exc:
            self.assertEqual(exc.category, SourceErrorCategory.authentication)
            self.assertNotIn(sentinel, str(exc))
            self.assertNotIn(sentinel, traceback.format_exc())
            self.assertTrue(err_resp.closed)
            self.assertTrue(err_sess.closed)
        else:
            self.fail("Expected SourceError(authentication) on 401 with exploding close")

    def test_json_decode_valueerror_and_recursion_guard(self):
        """Large numbers exceeding int limits and deeply nested JSON map to malformed_payload."""
        # Huge integer > 4300 digits raises ValueError during json loads
        huge_int_chunks = [b'{"big": ', b"9" * 5000, b"}"]
        fake_resp_huge = FakeResponse(chunks=huge_int_chunks)
        fake_sess_huge = FakeSession(response=fake_resp_huge)
        transport_huge = BoundedGetTransport(
            SourceProvider.github,
            session_factory=lambda: fake_sess_huge,
        )
        with self.assertRaises(SourceError) as ctx:
            transport_huge.get("https://api.github.com/repos/org/repo")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

        # Patched json.loads reproducing RecursionError within byte cap
        from unittest.mock import patch
        fake_resp_nested = FakeResponse(chunks=[b'{"nested": true}'])
        fake_sess_nested = FakeSession(response=fake_resp_nested)
        transport_nested = BoundedGetTransport(
            SourceProvider.github,
            session_factory=lambda: fake_sess_nested,
        )
        with patch("json.loads", side_effect=RecursionError("maximum recursion depth exceeded")):
            with self.assertRaises(SourceError) as ctx:
                transport_nested.get("https://api.github.com/repos/org/repo")
            self.assertEqual(ctx.exception.category, SourceErrorCategory.malformed_payload)

    def test_urllib3_read_timeout_wrapped_in_connection_error(self):
        """Urllib3 ReadTimeoutError wrapped inside ConnectionError is classified as timeout."""
        import urllib3.exceptions
        timeout_inner = urllib3.exceptions.ReadTimeoutError(None, "https://api.github.com", "Read timed out")
        wrapped_timeout = requests.exceptions.ConnectionError(timeout_inner)

        fake_resp = FakeResponse(chunks=[b'{"part": 1}'], stream_exc=wrapped_timeout)
        fake_sess = FakeSession(response=fake_resp)
        transport = BoundedGetTransport(
            SourceProvider.github,
            session_factory=lambda: fake_sess,
        )
        with self.assertRaises(SourceError) as ctx:
            transport.get("https://api.github.com/user")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.timeout)

        # Standard ConnectionError remains network
        standard_conn_err = requests.exceptions.ConnectionError("Connection refused")
        fake_resp2 = FakeResponse(chunks=[b'{"part": 1}'], stream_exc=standard_conn_err)
        fake_sess2 = FakeSession(response=fake_resp2)
        transport2 = BoundedGetTransport(
            SourceProvider.github,
            session_factory=lambda: fake_sess2,
        )
        with self.assertRaises(SourceError) as ctx:
            transport2.get("https://api.github.com/user")
        self.assertEqual(ctx.exception.category, SourceErrorCategory.network)


if __name__ == "__main__":
    unittest.main()
