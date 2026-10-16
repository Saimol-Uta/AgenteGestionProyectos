"""Shared and repository-scoped budgets for bounded source retrieval."""

from contextlib import contextmanager
from contextvars import ContextVar
import inspect
import time
from typing import Callable, Iterator, Optional

from agente_gestion.domain.repositories import RepositoryIdentity
from agente_gestion.domain.source_limits import SourceBudgetReceipt
from agente_gestion.ports.source_errors import SourceError, SourceErrorCategory, SourceProvider


_active_budget_scope: ContextVar[Optional["BudgetRequestScope"]] = ContextVar(
    "active_source_budget_scope", default=None,
)


def active_budget_scope() -> Optional["BudgetRequestScope"]:
    """Return the active request scope for streamed transports, if any."""
    return _active_budget_scope.get()


class CollectionBudgetLedger:
    """One deterministic project ledger with independent repository sub-ledgers."""

    def __init__(
        self,
        *,
        project_max_http_calls: int,
        repository_max_http_calls: int,
        project_max_files: int,
        repository_max_files: int,
        project_max_response_bytes: int,
        repository_max_response_bytes: int,
        project_timeout_seconds: float,
        repository_timeout_seconds: float,
        project_max_estimated_tokens: int,
        repository_max_estimated_tokens: int,
        default_request_timeout_seconds: float,
        per_response_max_bytes: int,
        clock: Optional[Callable[[], float]] = None,
    ):
        self.project_max_http_calls = project_max_http_calls
        self.repository_max_http_calls = repository_max_http_calls
        self.project_max_files = project_max_files
        self.repository_max_files = repository_max_files
        self.project_max_response_bytes = project_max_response_bytes
        self.repository_max_response_bytes = repository_max_response_bytes
        self.project_timeout_seconds = project_timeout_seconds
        self.repository_timeout_seconds = repository_timeout_seconds
        self.project_max_estimated_tokens = project_max_estimated_tokens
        self.repository_max_estimated_tokens = repository_max_estimated_tokens
        self.default_request_timeout_seconds = default_request_timeout_seconds
        self.per_response_max_bytes = per_response_max_bytes
        self._clock = clock or time.monotonic
        self._project_started = self._clock()
        self._project_calls = 0
        self._project_files = 0
        self._project_bytes = 0
        self._project_tokens = 0
        self._project_exhausted = False
        self._repositories: dict[str, dict[str, float | int | bool]] = {}

    def begin_repository(self, identity: RepositoryIdentity) -> None:
        """Start or reset a repository's counters for this collection execution."""
        self._repositories[identity.id] = {
            "started": self._clock(),
            "http_calls": 0,
            "files": 0,
            "response_bytes": 0,
            "estimated_tokens": 0,
            "exhausted": False,
        }

    def _repo(self, identity: Optional[RepositoryIdentity]) -> Optional[dict[str, float | int | bool]]:
        return self._repositories.get(identity.id) if identity is not None else None

    def _mark_exhausted(self, identity: Optional[RepositoryIdentity]) -> None:
        if identity is None:
            self._project_exhausted = True
            return
        row = self._repo(identity)
        if row is not None:
            row["exhausted"] = True

    def check_deadline(
        self, provider: SourceProvider, identity: Optional[RepositoryIdentity] = None,
    ) -> None:
        """Reject work after either the shared project or repository deadline."""
        now = self._clock()
        if now - self._project_started >= self.project_timeout_seconds:
            self._project_exhausted = True
            self._mark_exhausted(identity)
            raise SourceError(provider, SourceErrorCategory.timeout) from None
        row = self._repo(identity)
        if row is not None and now - float(row["started"]) >= self.repository_timeout_seconds:
            self._mark_exhausted(identity)
            raise SourceError(provider, SourceErrorCategory.timeout) from None

    def remaining_response_bytes(self, identity: Optional[RepositoryIdentity]) -> int:
        project = self.project_max_response_bytes - self._project_bytes
        if identity is None:
            return max(0, min(project, self.per_response_max_bytes))
        row = self._repo(identity)
        if row is None:
            return 0
        repository = self.repository_max_response_bytes - int(row["response_bytes"])
        return max(0, min(project, repository, self.per_response_max_bytes))

    def remaining_files(self, identity: RepositoryIdentity) -> int:
        row = self._repo(identity)
        if row is None:
            return 0
        return max(0, min(
            self.project_max_files - self._project_files,
            self.repository_max_files - int(row["files"]),
        ))

    def reserve_file_work(self, identity: RepositoryIdentity, provider: SourceProvider) -> int:
        """Preflight the changed-file cap before beginning another repository request sequence."""
        row = self._repo(identity)
        project_remaining = self.project_max_files - self._project_files
        repository_remaining = self.repository_max_files - int(row["files"]) if row is not None else 0
        remaining = max(0, min(project_remaining, repository_remaining))
        if remaining <= 0:
            if project_remaining <= 0:
                self._project_exhausted = True
            self._mark_exhausted(identity)
            raise SourceError(provider, SourceErrorCategory.limit_exceeded) from None
        return remaining

    def mark_response_limit(self, identity: Optional[RepositoryIdentity]) -> None:
        """Reflect aggregate ceilings only when observed byte counters actually reached them."""
        self.mark_stream_capacity_exhausted(identity)

    def mark_stream_capacity_exhausted(self, identity: Optional[RepositoryIdentity]) -> None:
        """Mark only aggregate project/repository byte ceilings actually reached."""
        row = self._repo(identity)
        if self._project_bytes >= self.project_max_response_bytes:
            self._project_exhausted = True
        if row is not None and int(row["response_bytes"]) >= self.repository_max_response_bytes:
            row["exhausted"] = True

    def _remaining_timeout(
        self, identity: Optional[RepositoryIdentity], requested: Optional[float],
    ) -> float:
        now = self._clock()
        remaining = [
            self.project_timeout_seconds - (now - self._project_started),
            requested if requested is not None else self.default_request_timeout_seconds,
        ]
        row = self._repo(identity)
        if row is not None:
            remaining.append(self.repository_timeout_seconds - (now - float(row["started"])))
        return min(remaining)

    def reserve_request(
        self,
        provider: SourceProvider,
        identity: Optional[RepositoryIdentity],
        requested_timeout: Optional[float] = None,
    ) -> "BudgetRequestScope":
        """Reserve one GET before network access and return a byte/deadline scope."""
        self.check_deadline(provider, identity)
        row = self._repo(identity)
        if self._project_calls >= self.project_max_http_calls:
            self._project_exhausted = True
            self._mark_exhausted(identity)
            raise SourceError(provider, SourceErrorCategory.limit_exceeded) from None
        if identity is not None and (row is None or int(row["http_calls"]) >= self.repository_max_http_calls):
            self._mark_exhausted(identity)
            raise SourceError(provider, SourceErrorCategory.limit_exceeded) from None
        available_bytes = self.remaining_response_bytes(identity)
        if available_bytes <= 0:
            self.mark_stream_capacity_exhausted(identity)
            raise SourceError(provider, SourceErrorCategory.limit_exceeded) from None
        timeout = self._remaining_timeout(identity, requested_timeout)
        if timeout <= 0.0:
            self._mark_exhausted(identity)
            raise SourceError(provider, SourceErrorCategory.timeout) from None

        self._project_calls += 1
        if row is not None:
            row["http_calls"] = int(row["http_calls"]) + 1
        return BudgetRequestScope(self, provider, identity, timeout, available_bytes)

    def consume_response_bytes(
        self, provider: SourceProvider, identity: Optional[RepositoryIdentity], amount: int,
    ) -> None:
        """Account actual streamed/returned bytes; exceeding a cap stops the request."""
        if type(amount) is not int or amount < 0:
            raise SourceError(provider, SourceErrorCategory.malformed_payload) from None
        if amount == 0:
            return
        self._project_bytes += amount
        row = self._repo(identity)
        if row is not None:
            row["response_bytes"] = int(row["response_bytes"]) + amount
        project_exceeded = self._project_bytes > self.project_max_response_bytes
        repository_exceeded = (
            row is not None and int(row["response_bytes"]) > self.repository_max_response_bytes
        )
        if self._project_bytes >= self.project_max_response_bytes:
            self._project_exhausted = True
        if row is not None and int(row["response_bytes"]) >= self.repository_max_response_bytes:
            row["exhausted"] = True
        if project_exceeded or repository_exceeded:
            raise SourceError(provider, SourceErrorCategory.limit_exceeded) from None

    def consume_files(self, identity: RepositoryIdentity, amount: int) -> bool:
        """Record observed repository files and return whether both caps remain valid."""
        if type(amount) is not int or amount < 0:
            self._mark_exhausted(identity)
            return False
        row = self._repo(identity)
        if row is None:
            self._mark_exhausted(identity)
            return False
        self._project_files += amount
        row["files"] = int(row["files"]) + amount
        if (self._project_files > self.project_max_files
                or int(row["files"]) > self.repository_max_files):
            if self._project_files > self.project_max_files:
                self._project_exhausted = True
            self._mark_exhausted(identity)
            return False
        return True

    def consume_estimated_tokens(self, identity: Optional[RepositoryIdentity], amount: int) -> bool:
        """Record estimated serialized-context token units (never model usage)."""
        if type(amount) is not int or amount < 0:
            self._mark_exhausted(identity)
            return False
        self._project_tokens += amount
        row = self._repo(identity)
        if row is not None:
            row["estimated_tokens"] = int(row["estimated_tokens"]) + amount
        if (self._project_tokens > self.project_max_estimated_tokens
                or (row is not None and int(row["estimated_tokens"]) > self.repository_max_estimated_tokens)):
            if self._project_tokens > self.project_max_estimated_tokens:
                self._project_exhausted = True
            self._mark_exhausted(identity)
            return False
        if self._project_tokens >= self.project_max_estimated_tokens:
            self._project_exhausted = True
        if row is not None and int(row["estimated_tokens"]) >= self.repository_max_estimated_tokens:
            row["exhausted"] = True
        return True

    def token_budget_exhausted(self, identity: RepositoryIdentity) -> bool:
        row = self._repo(identity)
        project_exhausted = self._project_tokens >= self.project_max_estimated_tokens
        repository_exhausted = (
            row is not None and int(row["estimated_tokens"]) >= self.repository_max_estimated_tokens
        )
        if project_exhausted:
            self._project_exhausted = True
            self._mark_exhausted(identity)
        elif repository_exhausted:
            self._mark_exhausted(identity)
        return bool(project_exhausted or repository_exhausted)

    def receipt(self) -> SourceBudgetReceipt:
        return SourceBudgetReceipt(
            http_calls=self._project_calls,
            files=self._project_files,
            response_bytes=self._project_bytes,
            elapsed_seconds=max(0.0, self._clock() - self._project_started),
            estimated_tokens=self._project_tokens,
            exhausted=self._project_exhausted,
        )

    def repository_receipt(self, identity: RepositoryIdentity) -> SourceBudgetReceipt:
        row = self._repo(identity)
        if row is None:
            return SourceBudgetReceipt(exhausted=True)
        return SourceBudgetReceipt(
            http_calls=int(row["http_calls"]),
            files=int(row["files"]),
            response_bytes=int(row["response_bytes"]),
            elapsed_seconds=max(0.0, self._clock() - float(row["started"])),
            estimated_tokens=int(row["estimated_tokens"]),
            exhausted=bool(row["exhausted"]),
        )


class BudgetRequestScope:
    """Per-request accounting hook used by bounded JSON and future binary transports."""

    def __init__(
        self,
        ledger: CollectionBudgetLedger,
        provider: SourceProvider,
        identity: Optional[RepositoryIdentity],
        timeout_seconds: float,
        byte_limit: int,
    ):
        self.ledger = ledger
        self.provider = provider
        self.identity = identity
        self.timeout_seconds = timeout_seconds
        self.byte_limit = byte_limit
        self.bytes_accounted = 0
        self._eof_probe_used = False

    def ensure_stream_capacity(self, *, allow_eof_probe: bool = False) -> None:
        """Check deadlines and byte capacity, allowing one positive EOF probe at the exact cap."""
        self.ledger.check_deadline(self.provider, self.identity)
        if (self.bytes_accounted < self.byte_limit
                and self.ledger.remaining_response_bytes(self.identity) > 0):
            return
        if (allow_eof_probe and self.bytes_accounted == self.byte_limit
                and not self._eof_probe_used):
            return
        self.ledger.mark_stream_capacity_exhausted(self.identity)
        raise SourceError(self.provider, SourceErrorCategory.limit_exceeded) from None

    def consume_streamed_bytes(self, amount: int) -> None:
        self.ledger.consume_response_bytes(self.provider, self.identity, amount)
        self.bytes_accounted += amount
        if amount > 0 and self.bytes_accounted > self.byte_limit:
            self._eof_probe_used = True


class BudgetedHttpGetTransport:
    """Per-request budget view preserving the caller-injected GET transport contract."""

    def __init__(
        self,
        transport,
        ledger: CollectionBudgetLedger,
        provider: SourceProvider,
        identity: Optional[RepositoryIdentity] = None,
    ):
        self._transport = transport
        self._ledger = ledger
        self._provider = provider
        self._identity = identity

    def get(self, url, params=None, headers=None, timeout_seconds=None):
        scope = self._ledger.reserve_request(self._provider, self._identity, timeout_seconds)
        token = _active_budget_scope.set(scope)
        try:
            kwargs = {"url": url, "params": params, "headers": headers}
            try:
                signature = inspect.signature(self._transport.get)
                supports_timeout = "timeout_seconds" in signature.parameters or any(
                    parameter.kind == inspect.Parameter.VAR_KEYWORD
                    for parameter in signature.parameters.values()
                )
            except (TypeError, ValueError, AttributeError):
                supports_timeout = False
            if supports_timeout:
                kwargs["timeout_seconds"] = scope.timeout_seconds
            response = self._transport.get(**kwargs)
            response_bytes = getattr(response, "bytes_received", 0)
            if type(response_bytes) is not int or response_bytes < 0:
                raise SourceError(self._provider, SourceErrorCategory.malformed_payload) from None
            if scope.bytes_accounted == 0:
                scope.consume_streamed_bytes(response_bytes)
            elif response_bytes > scope.bytes_accounted:
                scope.consume_streamed_bytes(response_bytes - scope.bytes_accounted)
            if response_bytes > self._ledger.per_response_max_bytes:
                raise SourceError(self._provider, SourceErrorCategory.limit_exceeded) from None
            self._ledger.check_deadline(self._provider, self._identity)
            return response
        finally:
            _active_budget_scope.reset(token)


__all__ = [
    "BudgetRequestScope",
    "BudgetedHttpGetTransport",
    "CollectionBudgetLedger",
    "active_budget_scope",
]
