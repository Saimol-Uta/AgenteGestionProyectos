"""Typed, sanitized errors for external data sources and adapters."""

from enum import Enum


class SourceProvider(str, Enum):
    """Supported external or local data source providers."""
    trello = "trello"
    github = "github"
    github_actions = "github_actions"
    local_policy = "local_policy"


class SourceErrorCategory(str, Enum):
    """High-level failure categories across sources."""
    authentication = "authentication"
    permission_denied = "permission_denied"
    rate_limit = "rate_limit"
    timeout = "timeout"
    network = "network"
    malformed_payload = "malformed_payload"
    not_found = "not_found"
    configuration = "configuration"
    limit_exceeded = "limit_exceeded"


class SourceError(Exception):
    """
    Sanitized typed exception for source adapters.

    Canonical str and repr outputs are fixed messages derived strictly
    from validated SourceProvider and SourceErrorCategory enums.
    When adapters raise SourceError with 'from None', the causing exception
    context is suppressed, though standard Python execution frame locations
    in the traceback remain.
    """

    def __init__(self, provider: SourceProvider | str, category: SourceErrorCategory | str):
        valid_prov = None
        for p in SourceProvider:
            if provider == p or provider == p.value:
                valid_prov = p
                break
        if valid_prov is None:
            raise ValueError("Invalid source provider.")

        valid_cat = None
        for c in SourceErrorCategory:
            if category == c or category == c.value:
                valid_cat = c
                break
        if valid_cat is None:
            raise ValueError("Invalid source error category.")

        self.provider = valid_prov
        self.category = valid_cat
        message = f"Source '{self.provider.value}' failed with error category '{self.category.value}'."
        super().__init__(message)

    def __str__(self) -> str:
        return f"Source '{self.provider.value}' failed with error category '{self.category.value}'."

    def __repr__(self) -> str:
        return f"SourceError(provider={self.provider!r}, category={self.category!r})"
