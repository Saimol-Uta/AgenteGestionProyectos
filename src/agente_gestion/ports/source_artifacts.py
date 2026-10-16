"""Ports and finite limits for bounded, read-only CI artifact retrieval."""

from dataclasses import dataclass, field
import math
from typing import Mapping, Optional, Protocol, runtime_checkable

from agente_gestion.ports.source_errors import SourceError, SourceErrorCategory, SourceProvider


DEFAULT_GITHUB_ARTIFACT_STORAGE_ORIGINS = tuple(
    f"https://productionresultssa{index}.blob.core.windows.net" for index in range(5)
)


@dataclass(frozen=True, repr=False)
class BinaryHttpResponse:
    """One-hop response; body and headers are hidden from accidental repr/logging."""

    status_code: int
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)
    bytes_received: int


class ArtifactTransportError(SourceError):
    """Sanitized transport failure with bounded local receipt facts only."""

    def __init__(
        self,
        category: SourceErrorCategory | str,
        *,
        attempted: bool,
        bytes_received: int = 0,
        status_code: Optional[int] = None,
    ):
        if type(attempted) is not bool or type(bytes_received) is not int or bytes_received < 0:
            raise ValueError("Invalid artifact transport receipt.")
        if status_code is not None and (type(status_code) is not int or not 100 <= status_code <= 599):
            raise ValueError("Invalid artifact transport status.")
        super().__init__(SourceProvider.github_actions, category)
        self.attempted = attempted
        self.bytes_received = bytes_received
        self.status_code = status_code


@runtime_checkable
class BinaryGetTransportPort(Protocol):
    """Single-hop GET port returning bounded raw bytes without following redirects."""

    def get(
        self,
        url: str,
        params=None,
        headers: Optional[Mapping[str, str]] = None,
        timeout_seconds: Optional[float] = None,
    ) -> BinaryHttpResponse:
        ...


@dataclass(frozen=True)
class CiArtifactLimits:
    """Small explicit ceilings for artifact download, redirects, ZIP, and JSON."""

    max_compressed_bytes: int = 8 * 1024 * 1024
    max_expanded_bytes: int = 1024 * 1024
    max_json_bytes: int = 1024 * 1024
    max_archive_entries: int = 16
    max_redirects: int = 3
    timeout_seconds: float = 15.0
    allowed_storage_origins: tuple[str, ...] = field(
        default_factory=lambda: DEFAULT_GITHUB_ARTIFACT_STORAGE_ORIGINS
    )

    def __post_init__(self) -> None:
        integer_limits = (
            self.max_compressed_bytes,
            self.max_expanded_bytes,
            self.max_json_bytes,
            self.max_archive_entries,
            self.max_redirects,
        )
        if any(type(value) is not int or value <= 0 for value in integer_limits):
            raise ValueError("Artifact limits must be positive integers.")
        if (self.max_compressed_bytes > 64 * 1024 * 1024
                or self.max_expanded_bytes > 64 * 1024 * 1024
                or self.max_json_bytes > 16 * 1024 * 1024
                or self.max_archive_entries > 128 or self.max_redirects > 5):
            raise ValueError("Artifact limits exceed the supported resource ceiling.")
        if self.max_json_bytes > self.max_expanded_bytes:
            raise ValueError("The JSON byte limit cannot exceed the expanded archive byte limit.")
        if (isinstance(self.timeout_seconds, bool)
                or not isinstance(self.timeout_seconds, (int, float))
                or not math.isfinite(self.timeout_seconds)
                or self.timeout_seconds <= 0 or self.timeout_seconds > 30):
            raise ValueError("Artifact timeout must be finite, positive, and at most 30 seconds.")
        if (not isinstance(self.allowed_storage_origins, tuple)
                or len(self.allowed_storage_origins) > 8
                or any(not isinstance(origin, str) for origin in self.allowed_storage_origins)):
            raise ValueError("Allowed storage origins must be a tuple of origin strings.")


__all__ = [
    "ArtifactTransportError",
    "BinaryGetTransportPort",
    "BinaryHttpResponse",
    "CiArtifactLimits",
    "DEFAULT_GITHUB_ARTIFACT_STORAGE_ORIGINS",
]
