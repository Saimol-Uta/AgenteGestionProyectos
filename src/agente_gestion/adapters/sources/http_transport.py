"""Bounded read-only HTTP GET transport for external data source adapters."""

from dataclasses import dataclass, field
import json
import math
import time
from typing import Any, Callable, Optional, Protocol, runtime_checkable
from urllib.parse import urlsplit

import requests
import urllib3.exceptions

from agente_gestion.ports.source_errors import (
    SourceError,
    SourceErrorCategory,
    SourceProvider,
)

# Header allowlist for safe metadata propagation without leaking credentials
SAFE_HEADER_ALLOWLIST = frozenset({
    "link",
    "retry-after",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "x-ratelimit-used",
    "x-ratelimit-resource",
    "ratelimit-limit",
    "ratelimit-remaining",
    "ratelimit-reset",
    "content-type",
    "etag",
})

# Allowed origins per provider: HTTPS only, exact host match
ALLOWED_PROVIDER_HOSTS: dict[SourceProvider, str] = {
    SourceProvider.trello: "api.trello.com",
    SourceProvider.github: "api.github.com",
    SourceProvider.github_actions: "api.github.com",
}


def _safe_close(resource: Any) -> None:
    """Best-effort resource cleanup suppressing ordinary exceptions."""
    if resource is not None and hasattr(resource, "close") and callable(resource.close):
        try:
            resource.close()
        except Exception:
            pass


def _is_timeout_exception(exc: BaseException) -> bool:
    """Bounded check for direct or wrapped timeout exceptions without inspecting message text."""
    if isinstance(exc, (requests.exceptions.Timeout, urllib3.exceptions.ReadTimeoutError)):
        return True
    candidates = []
    if hasattr(exc, "__cause__") and exc.__cause__ is not None:
        candidates.append(exc.__cause__)
    if hasattr(exc, "__context__") and exc.__context__ is not None:
        candidates.append(exc.__context__)
    if hasattr(exc, "args"):
        candidates.extend(exc.args)
    for c in candidates:
        if isinstance(c, (requests.exceptions.Timeout, urllib3.exceptions.ReadTimeoutError)):
            return True
    return False


@dataclass(frozen=True)
class HttpResponse:
    """Immutable parsed HTTP response."""

    status_code: int
    body: dict[str, Any] | list[Any]
    headers: dict[str, str] = field(default_factory=dict)
    bytes_received: int = 0
    attempt_count: int = 1


@runtime_checkable
class HttpGetTransportPort(Protocol):
    """Port interface for bounded, read-only HTTP GET transport."""

    def get(
        self,
        url: str,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        timeout_seconds: Optional[float] = None,
    ) -> HttpResponse:
        """Perform a single bounded HTTP GET request returning parsed JSON."""
        ...


class BoundedGetTransport(HttpGetTransportPort):
    """
    Thread-safe, bounded, single-attempt GET transport.

    Enforces HTTPS origin restrictions, streaming byte caps,
    wall-clock stream budget checks, sanitized error mapping,
    and automatic socket/session cleanup on all exit paths.
    """

    def __init__(
        self,
        provider: SourceProvider | str,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 2 * 1024 * 1024,
        session_factory: Optional[Callable[[], requests.Session]] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        valid_prov: Optional[SourceProvider] = None
        for p in SourceProvider:
            if provider == p or provider == p.value:
                valid_prov = p
                break
        if valid_prov is None:
            raise ValueError("Invalid source provider.")

        if valid_prov not in ALLOWED_PROVIDER_HOSTS:
            # local_policy or non-HTTP provider
            raise SourceError(valid_prov, SourceErrorCategory.configuration) from None

        self.provider = valid_prov

        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0.0
        ):
            raise SourceError(self.provider, SourceErrorCategory.configuration) from None
        self.timeout_seconds = float(timeout_seconds)

        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or max_response_bytes <= 0
        ):
            raise SourceError(self.provider, SourceErrorCategory.configuration) from None
        self.max_response_bytes = max_response_bytes

        self._session_factory = session_factory
        self._clock = clock if clock is not None else time.monotonic

    def __repr__(self) -> str:
        return (
            f"BoundedGetTransport(provider={self.provider!r}, "
            f"timeout_seconds={self.timeout_seconds!r}, "
            f"max_response_bytes={self.max_response_bytes!r})"
        )

    def _validate_url(self, url: str) -> None:
        if not isinstance(url, str) or not url.strip():
            raise SourceError(self.provider, SourceErrorCategory.configuration) from None

        try:
            parts = urlsplit(url)
        except Exception:
            raise SourceError(self.provider, SourceErrorCategory.configuration) from None

        if parts.scheme.lower() != "https":
            raise SourceError(self.provider, SourceErrorCategory.configuration) from None

        if parts.username is not None or parts.password is not None:
            raise SourceError(self.provider, SourceErrorCategory.configuration) from None

        try:
            port = parts.port
        except Exception:
            raise SourceError(self.provider, SourceErrorCategory.configuration) from None
        if port is not None and port != 443:
            raise SourceError(self.provider, SourceErrorCategory.configuration) from None

        if parts.fragment:
            raise SourceError(self.provider, SourceErrorCategory.configuration) from None

        hostname = (parts.hostname or "").lower()
        expected_host = ALLOWED_PROVIDER_HOSTS.get(self.provider)
        if not hostname or hostname != expected_host:
            raise SourceError(self.provider, SourceErrorCategory.configuration) from None

    def _validate_timeout(self, timeout_seconds: Optional[float]) -> float:
        if timeout_seconds is None:
            return self.timeout_seconds
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0.0
        ):
            raise SourceError(self.provider, SourceErrorCategory.configuration) from None
        return float(timeout_seconds)

    def get(
        self,
        url: str,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        timeout_seconds: Optional[float] = None,
    ) -> HttpResponse:
        # Preflight validation before touching network or session
        self._validate_url(url)
        effective_timeout = self._validate_timeout(timeout_seconds)

        if params is not None and not isinstance(params, dict):
            raise SourceError(self.provider, SourceErrorCategory.configuration) from None
        if headers is not None and not isinstance(headers, dict):
            raise SourceError(self.provider, SourceErrorCategory.configuration) from None

        session: Optional[requests.Session] = None
        response: Optional[requests.Response] = None

        try:
            try:
                if self._session_factory is not None:
                    session = self._session_factory()
                else:
                    session = requests.Session()
                session.trust_env = False
            except SourceError:
                raise
            except Exception:
                raise SourceError(self.provider, SourceErrorCategory.network) from None

            try:
                response = session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=effective_timeout,
                    stream=True,
                    allow_redirects=False,
                )
            except SourceError:
                raise
            except Exception as exc:
                if _is_timeout_exception(exc):
                    raise SourceError(self.provider, SourceErrorCategory.timeout) from None
                raise SourceError(self.provider, SourceErrorCategory.network) from None

            try:
                status_code = response.status_code

                # Reject all 3xx redirects without following Location
                if 300 <= status_code < 400:
                    raise SourceError(self.provider, SourceErrorCategory.network) from None

                if status_code == 401:
                    raise SourceError(self.provider, SourceErrorCategory.authentication) from None
                elif status_code == 403:
                    resp_headers_lower = {
                        k.lower(): str(v) for k, v in response.headers.items()
                    }
                    if self.provider in (SourceProvider.github, SourceProvider.github_actions):
                        rem = resp_headers_lower.get("x-ratelimit-remaining")
                        if rem == "0" or "retry-after" in resp_headers_lower:
                            raise SourceError(self.provider, SourceErrorCategory.rate_limit) from None
                    raise SourceError(self.provider, SourceErrorCategory.permission_denied) from None
                elif status_code == 404:
                    raise SourceError(self.provider, SourceErrorCategory.not_found) from None
                elif status_code == 429:
                    raise SourceError(self.provider, SourceErrorCategory.rate_limit) from None
                elif 500 <= status_code < 600:
                    raise SourceError(self.provider, SourceErrorCategory.network) from None
                elif status_code < 200 or status_code >= 300:
                    raise SourceError(self.provider, SourceErrorCategory.network) from None
            except SourceError:
                raise
            except Exception:
                raise SourceError(self.provider, SourceErrorCategory.network) from None

            # Stream response chunks with byte cap and wall-clock check
            start_time = self._clock()
            deadline = start_time + effective_timeout
            chunks: list[bytes] = []
            bytes_received = 0
            chunk_size = 65536

            try:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    bytes_received += len(chunk)
                    if bytes_received > self.max_response_bytes:
                        raise SourceError(self.provider, SourceErrorCategory.limit_exceeded) from None
                    if self._clock() > deadline:
                        raise SourceError(self.provider, SourceErrorCategory.timeout) from None
                    chunks.append(chunk)
            except SourceError:
                raise
            except Exception as exc:
                if _is_timeout_exception(exc):
                    raise SourceError(self.provider, SourceErrorCategory.timeout) from None
                raise SourceError(self.provider, SourceErrorCategory.network) from None

            raw_bytes = b"".join(chunks)

            try:
                text = raw_bytes.decode("utf-8")
                parsed_json = json.loads(text)
            except (UnicodeDecodeError, ValueError, RecursionError):
                raise SourceError(self.provider, SourceErrorCategory.malformed_payload) from None
            except Exception:
                raise SourceError(self.provider, SourceErrorCategory.malformed_payload) from None

            if not isinstance(parsed_json, (dict, list)):
                raise SourceError(self.provider, SourceErrorCategory.malformed_payload) from None

            try:
                safe_headers: dict[str, str] = {}
                for k, v in response.headers.items():
                    k_lower = str(k).lower()
                    if k_lower in SAFE_HEADER_ALLOWLIST:
                        safe_headers[k_lower] = str(v)
            except SourceError:
                raise
            except Exception:
                raise SourceError(self.provider, SourceErrorCategory.network) from None

            return HttpResponse(
                status_code=status_code,
                body=parsed_json,
                headers=safe_headers,
                bytes_received=bytes_received,
                attempt_count=1,
            )
        finally:
            if response is not None:
                _safe_close(response)
            if session is not None:
                _safe_close(session)
