"""Bounded raw binary transport for one-hop GitHub Actions artifact GETs."""

import ipaddress
import math
import re
import time
from typing import Callable, Mapping, Optional
from urllib.parse import urlsplit

import requests
import urllib3.exceptions

from agente_gestion.adapters.sources.collection_budget import active_budget_scope
from agente_gestion.ports.source_artifacts import (
    ArtifactTransportError,
    BinaryHttpResponse,
    DEFAULT_GITHUB_ARTIFACT_STORAGE_ORIGINS,
)
from agente_gestion.ports.source_errors import SourceError, SourceErrorCategory, SourceProvider


API_ORIGIN = "https://api.github.com"
_DEFAULT_ALLOWED_ORIGINS = (API_ORIGIN, *DEFAULT_GITHUB_ARTIFACT_STORAGE_ORIGINS)
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.ASCII)
_SAFE_REQUEST_HEADERS = frozenset({"authorization", "accept", "x-github-api-version"})


def _hostname(host: Optional[str]) -> str:
    if not isinstance(host, str) or not host or host.endswith(".") or len(host) > 253:
        raise ValueError("Invalid HTTPS origin.")
    normalized = host.lower()
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        pass
    else:
        raise ValueError("IP literal origins are not allowed.")
    if normalized in {"localhost", "localhost.localdomain"}:
        raise ValueError("Local origins are not allowed.")
    if normalized.endswith((".local", ".localhost", ".internal", ".onion")):
        raise ValueError("Local origins are not allowed.")
    labels = normalized.split(".")
    if len(labels) < 2 or any(not _DNS_LABEL.fullmatch(label) for label in labels):
        raise ValueError("Invalid HTTPS hostname.")
    return normalized


def _parse_url(url: str) -> tuple[str, tuple[str, str, int]]:
    if (not isinstance(url, str) or not url or len(url) > 16384 or "\\" in url
            or any(ord(char) <= 32 or ord(char) >= 127 for char in url)):
        raise ValueError("Invalid HTTPS URL.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except Exception:
        raise ValueError("Invalid HTTPS URL.") from None
    if (parts.scheme.lower() != "https" or parts.username is not None or parts.password is not None
            or parts.fragment or (port is not None and port != 443)):
        raise ValueError("Invalid HTTPS URL.")
    host = _hostname(parts.hostname)
    canonical = f"https://{host}{parts.path or '/'}"
    if parts.query:
        canonical += f"?{parts.query}"
    return canonical, ("https", host, 443)


def _parse_origin(origin: str) -> tuple[str, str, int]:
    _canonical, normalized = _parse_url(origin)
    parts = urlsplit(origin)
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("Storage allow-list entries must be HTTPS origins.")
    return normalized


def normalize_artifact_url(url: str) -> tuple[str, tuple[str, str, int]]:
    """Validate and canonicalize one HTTPS artifact URL without exposing it in errors."""
    return _parse_url(url)


def parse_artifact_origin(origin: str) -> tuple[str, str, int]:
    """Validate an exact HTTPS origin used by the explicit artifact host allow-list."""
    return _parse_origin(origin)


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, (requests.exceptions.Timeout, urllib3.exceptions.TimeoutError,
                        urllib3.exceptions.ReadTimeoutError)):
        return True
    cause = getattr(exc, "__cause__", None)
    context = getattr(exc, "__context__", None)
    return bool(cause is not None and _is_timeout(cause)) or bool(
        context is not None and _is_timeout(context)
    )


def _close(resource) -> None:
    if resource is not None and callable(getattr(resource, "close", None)):
        try:
            resource.close()
        except Exception:
            pass


class BoundedBinaryGetTransport:
    """A GET-only, single-hop transport that reads bounded identity-encoded bytes."""

    def __init__(
        self,
        *,
        max_response_bytes: int = 8 * 1024 * 1024,
        timeout_seconds: float = 15.0,
        allowed_origins: tuple[str, ...] = _DEFAULT_ALLOWED_ORIGINS,
        session_factory: Optional[Callable[[], requests.Session]] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        if (type(max_response_bytes) is not int or max_response_bytes <= 0
                or max_response_bytes > 64 * 1024 * 1024):
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or timeout_seconds > 30):
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
        if not isinstance(allowed_origins, tuple) or len(allowed_origins) > 9:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
        try:
            normalized_origins = frozenset(_parse_origin(origin) for origin in allowed_origins)
        except Exception:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
        if _parse_origin(API_ORIGIN) not in normalized_origins:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None

        self.max_response_bytes = max_response_bytes
        self.timeout_seconds = float(timeout_seconds)
        self._allowed_origins = normalized_origins
        self._session_factory = session_factory
        self._clock = clock or time.monotonic

    def _validate_url(self, url: str) -> tuple[str, tuple[str, str, int]]:
        try:
            canonical, origin = _parse_url(url)
        except Exception:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
        if origin not in self._allowed_origins:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
        return canonical, origin

    @staticmethod
    def _request_headers(headers: Optional[Mapping[str, str]], *, api_origin: bool) -> dict[str, str]:
        if headers is not None and not isinstance(headers, Mapping):
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
        safe: dict[str, str] = {"Accept-Encoding": "identity"}
        if headers:
            for key, value in headers.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
                normalized = key.lower()
                if normalized not in _SAFE_REQUEST_HEADERS:
                    continue
                if len(value) > 8192 or any(ord(char) < 32 or ord(char) == 127 for char in value):
                    raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
                if normalized == "authorization" and not api_origin:
                    continue
                if normalized == "x-github-api-version" and not api_origin:
                    continue
                canonical_key = {
                    "authorization": "Authorization",
                    "accept": "Accept",
                    "x-github-api-version": "X-GitHub-Api-Version",
                }[normalized]
                safe[canonical_key] = value
        return safe

    def get(
        self,
        url: str,
        params=None,
        headers: Optional[Mapping[str, str]] = None,
        timeout_seconds: Optional[float] = None,
    ) -> BinaryHttpResponse:
        if params is not None:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
        canonical_url, origin = self._validate_url(url)
        if timeout_seconds is None:
            effective_timeout = self.timeout_seconds
        elif (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
              or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
        else:
            effective_timeout = min(float(timeout_seconds), self.timeout_seconds)

        scope = active_budget_scope()
        if scope is not None:
            effective_timeout = min(effective_timeout, scope.timeout_seconds)
        outgoing_headers = self._request_headers(
            headers, api_origin=(origin == _parse_origin(API_ORIGIN)),
        )
        session = None
        response = None
        attempted = False
        bytes_received = 0
        status_code = None
        chunks: list[bytes] = []
        try:
            try:
                session = self._session_factory() if self._session_factory else requests.Session()
                session.trust_env = False
                if hasattr(session, "auth"):
                    session.auth = None
                if hasattr(session, "headers") and hasattr(session.headers, "clear"):
                    session.headers.clear()
                if hasattr(session, "proxies") and hasattr(session.proxies, "clear"):
                    session.proxies.clear()
                cookies = getattr(session, "cookies", None)
                if callable(getattr(cookies, "clear", None)):
                    cookies.clear()
                if isinstance(session, requests.Session):
                    # Keep the one-attempt contract explicit even if requests defaults change.
                    session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
            except Exception:
                raise ArtifactTransportError(
                    SourceErrorCategory.network, attempted=False,
                ) from None

            started = self._clock()
            deadline = started + effective_timeout
            try:
                self._check_deadline(deadline, scope, attempted=attempted, bytes_received=bytes_received)
                attempted = True
                response = session.get(
                    canonical_url,
                    headers=outgoing_headers,
                    timeout=effective_timeout,
                    stream=True,
                    allow_redirects=False,
                )
            except ArtifactTransportError:
                raise
            except SourceError as exc:
                raise ArtifactTransportError(
                    exc.category, attempted=attempted, bytes_received=bytes_received,
                    status_code=status_code,
                ) from None
            except Exception as exc:
                category = SourceErrorCategory.timeout if _is_timeout(exc) else SourceErrorCategory.network
                raise ArtifactTransportError(
                    category, attempted=attempted, bytes_received=bytes_received,
                    status_code=status_code,
                ) from None

            status_code = getattr(response, "status_code", None)
            if type(status_code) is not int or not 100 <= status_code <= 599:
                raise ArtifactTransportError(
                    SourceErrorCategory.malformed_payload, attempted=True,
                ) from None
            try:
                response_headers = self._response_headers(
                    getattr(response, "headers", {}), status_code=status_code,
                )
            except ArtifactTransportError:
                raise
            except Exception:
                raise ArtifactTransportError(
                    SourceErrorCategory.malformed_payload,
                    attempted=True, status_code=status_code,
                ) from None
            if 300 <= status_code < 400 or status_code < 200 or status_code >= 300:
                return BinaryHttpResponse(status_code, response_headers, b"", 0)

            encoding = response_headers.get("content-encoding", "").strip().lower()
            if encoding not in ("", "identity"):
                raise ArtifactTransportError(
                    SourceErrorCategory.malformed_payload,
                    attempted=True,
                    status_code=status_code,
                ) from None

            raw = getattr(response, "raw", None)
            if raw is None or not callable(getattr(raw, "read", None)):
                raise ArtifactTransportError(
                    SourceErrorCategory.network, attempted=True, status_code=status_code,
                ) from None
            if hasattr(raw, "decode_content"):
                try:
                    raw.decode_content = False
                except Exception:
                    raise ArtifactTransportError(
                        SourceErrorCategory.malformed_payload,
                        attempted=True, status_code=status_code,
                    ) from None

            stream_limit = self.max_response_bytes
            if scope is not None:
                stream_limit = min(stream_limit, scope.byte_limit)
            while True:
                self._check_deadline(
                    deadline, scope, attempted=True, bytes_received=bytes_received,
                    status_code=status_code,
                )
                remaining = stream_limit - bytes_received
                read_size = min(65536, remaining) if remaining > 0 else 1
                try:
                    chunk = raw.read(read_size, decode_content=False)
                except SourceError as exc:
                    raise ArtifactTransportError(
                        exc.category, attempted=True, bytes_received=bytes_received,
                        status_code=status_code,
                    ) from None
                except Exception as exc:
                    category = SourceErrorCategory.timeout if _is_timeout(exc) else SourceErrorCategory.network
                    raise ArtifactTransportError(
                        category, attempted=True, bytes_received=bytes_received,
                        status_code=status_code,
                    ) from None
                if chunk == b"":
                    break
                if not isinstance(chunk, bytes):
                    raise ArtifactTransportError(
                        SourceErrorCategory.malformed_payload,
                        attempted=True, bytes_received=bytes_received, status_code=status_code,
                    ) from None
                bytes_received += len(chunk)
                if scope is not None:
                    try:
                        scope.consume_streamed_bytes(len(chunk))
                    except SourceError as exc:
                        raise ArtifactTransportError(
                            exc.category, attempted=True, bytes_received=bytes_received,
                            status_code=status_code,
                        ) from None
                if bytes_received > stream_limit:
                    raise ArtifactTransportError(
                        SourceErrorCategory.limit_exceeded,
                        attempted=True, bytes_received=bytes_received, status_code=status_code,
                    ) from None
                chunks.append(chunk)

            body = b"".join(chunks)
            return BinaryHttpResponse(status_code, response_headers, body, bytes_received)
        finally:
            _close(response)
            _close(session)

    def _check_deadline(
        self, deadline: float, scope, *, attempted: bool, bytes_received: int,
        status_code: Optional[int] = None,
    ) -> None:
        if scope is not None:
            try:
                scope.ledger.check_deadline(scope.provider, scope.identity)
            except SourceError as exc:
                raise ArtifactTransportError(
                    exc.category, attempted=attempted, bytes_received=bytes_received,
                    status_code=status_code,
                ) from None
        if self._clock() >= deadline:
            raise ArtifactTransportError(
                SourceErrorCategory.timeout, attempted=attempted,
                bytes_received=bytes_received, status_code=status_code,
            ) from None

    @staticmethod
    def _response_headers(headers, *, status_code: Optional[int]) -> dict[str, str]:
        if not isinstance(headers, Mapping):
            raise ArtifactTransportError(
                SourceErrorCategory.malformed_payload, attempted=True,
                status_code=status_code,
            ) from None
        safe: dict[str, str] = {}
        for key, value in headers.items():
            if not isinstance(key, str):
                continue
            normalized = key.lower()
            if normalized in {"location", "content-encoding"}:
                if not isinstance(value, str):
                    raise ArtifactTransportError(
                        SourceErrorCategory.malformed_payload, attempted=True,
                        status_code=status_code,
                    ) from None
                safe[normalized] = value
        return safe


__all__ = [
    "BoundedBinaryGetTransport",
    "normalize_artifact_url",
    "parse_artifact_origin",
]
