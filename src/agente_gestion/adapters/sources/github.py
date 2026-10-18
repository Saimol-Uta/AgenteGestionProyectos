"""Bounded read-only GitHub adapter for commit diffs and evidence collection (T3)."""

import base64
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Optional
from urllib.parse import parse_qs, quote, urlsplit

from agente_gestion.adapters.sources.http_transport import (
    BoundedGetTransport,
    HttpGetTransportPort,
    HttpResponse,
)
from agente_gestion.domain.code import CodeDiffSnippet
from agente_gestion.domain.evidence import (
    EvidenceItem,
    EvidenceProvenance,
    EvidenceSource,
)
from agente_gestion.ports.source_errors import (
    SourceError,
    SourceErrorCategory,
    SourceProvider,
)
from agente_gestion.ports.sources import CodeSourcePort

# Hex SHA pattern: strictly full 40-char (SHA-1) or 64-char (SHA-256) hex hashes
FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$")

# Safe identifier patterns preventing injection in URL paths
SAFE_OWNER_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
SAFE_REPO_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")
SAFE_BRANCH_RE = re.compile(r"^[a-zA-Z0-9_\-\./]+$")

# Strict unified diff hunk header pattern
HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")

# Bounded limits and ceilings
MAX_GITHUB_FILES_CEILING = 3000
DEFAULT_MAX_FILES = 3000
DEFAULT_MAX_PAGES = 30
DEFAULT_MAX_REQUESTS = 32
DEFAULT_MAX_TEXT_LENGTH = 65536
DEFAULT_MAX_LINES = 5000
DEFAULT_MAX_TOTAL_BYTES = 10 * 1024 * 1024  # 10 MiB aggregate
MAX_FULL_FILE_BYTES = 5 * 1024 * 1024
MAX_CACHE_ENTRIES = 20


def _validate_safe_string(val: Any) -> str:
    """Validate that val is a non-empty string without CR/LF/quotes/control characters."""
    if not isinstance(val, str) or not val.strip():
        raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None

    for ch in val:
        code = ord(ch)
        if code < 32 or code == 127 or ch in ('"', "'", "\r", "\n", "\t"):
            raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None
    return val


def _parse_hunk_int(s: Optional[str], default: int = 1) -> int:
    """Parse integer from hunk header regex group with bounded digits to prevent DoS/overflow."""
    if s is None:
        return default
    if len(s) > 12:
        raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
    try:
        val = int(s)
        if val < 0:
            raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
        return val
    except Exception:
        raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None


@dataclass(frozen=True)
class _CachedCollection:
    """Immutable normalized collection containing snippets, evidence, and completeness metadata."""
    project_id: str
    owner: str
    repo: str
    commit_sha: str
    collected_at: datetime
    diffs: tuple[CodeDiffSnippet, ...]
    evidence: tuple[EvidenceItem, ...]
    unavailable_file_paths: tuple[str, ...]
    is_complete: bool


class GitHubCodeSourceAdapter(CodeSourcePort):
    """
    Read-only adapter retrieving code diff snippets and evidence for a pinned commit from GitHub API.

    Enforces:
    - Exactly one pinned commit per retrieval cycle; floating refs (branch tips) are resolved once.
    - Strict unified patch parsing mapping context and added lines to real new-file line numbers.
    - Omission of deleted '-' lines from NEW snippets without fake contiguous line gaps.
    - Normalized evidence tracking for removed files and binary/large unavailable patches.
    - Rejection of credential echoes in paths, identifiers, or source code (never fabricated/redacted).
    - Local cache published atomically only after all pages and patches validate cleanly.
    """

    def __init__(
        self,
        owner: str,
        repo: str,
        token: Optional[str] = None,
        branch: Optional[str] = None,
        transport: Optional[HttpGetTransportPort] = None,
        max_files: int = DEFAULT_MAX_FILES,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        max_text_length: int = DEFAULT_MAX_TEXT_LENGTH,
        max_lines: int = DEFAULT_MAX_LINES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    ):
        self._owner = _validate_safe_string(owner)
        if not SAFE_OWNER_RE.match(self._owner):
            raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None

        self._repo = _validate_safe_string(repo)
        if not SAFE_REPO_RE.match(self._repo) or self._repo in (".", ".."):
            raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None

        self._token: Optional[str] = None
        if token is not None:
            self._token = _validate_safe_string(token)

        self._branch: Optional[str] = None
        if branch is not None:
            b = _validate_safe_string(branch)
            if not SAFE_BRANCH_RE.match(b) or ".." in b or b.startswith("/") or b.endswith("/"):
                raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None
            self._branch = b

        # Prevent credential leaks in configuration values
        if self._token:
            if self._token in self._owner or self._token in self._repo:
                raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None
            if self._branch and self._token in self._branch:
                raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None

        if transport is not None and not isinstance(transport, HttpGetTransportPort):
            raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None
        self._transport = transport or BoundedGetTransport(SourceProvider.github)

        # Validate budget limits
        if type(max_files) is not int or max_files <= 0 or max_files > MAX_GITHUB_FILES_CEILING:
            raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None
        self._max_files = max_files

        if type(max_pages) is not int or max_pages <= 0 or max_pages > DEFAULT_MAX_PAGES:
            raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None
        self._max_pages = max_pages

        if type(max_requests) is not int or max_requests <= 0 or max_requests > 50:
            raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None
        self._max_requests = max_requests

        if type(max_text_length) is not int or max_text_length <= 0:
            raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None
        self._max_text_length = max_text_length

        if type(max_lines) is not int or max_lines <= 0:
            raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None
        self._max_lines = max_lines

        if type(max_total_bytes) is not int or max_total_bytes <= 0:
            raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None
        self._max_total_bytes = max_total_bytes

        self._cache: OrderedDict[str, _CachedCollection] = OrderedDict()
        self._last_resolved_commit_sha: Optional[str] = None
        self._last_collection_complete: bool = False
        self._unavailable_file_paths: tuple[str, ...] = ()

    @property
    def owner(self) -> str:
        """Configured repository owner."""
        return self._owner

    @property
    def repo(self) -> str:
        """Configured repository name."""
        return self._repo

    @property
    def branch(self) -> Optional[str]:
        """Configured branch, if any."""
        return self._branch

    @property
    def last_resolved_commit_sha(self) -> Optional[str]:
        """The commit SHA resolved and published by the most recent successful retrieval."""
        return self._last_resolved_commit_sha

    @property
    def last_collection_complete(self) -> bool:
        """True if all changed files in the last successful collection had diff patches available."""
        return self._last_collection_complete

    @property
    def unavailable_file_paths(self) -> list[str]:
        """List of changed file paths whose diff patches were unavailable (e.g. binary or large diffs)."""
        return list(self._unavailable_file_paths)

    def __repr__(self) -> str:
        """Safe representation hiding credentials."""
        return f"GitHubCodeSourceAdapter(owner={self._owner!r}, repo={self._repo!r}, branch={self._branch!r})"

    def _assert_no_credential_echo(self, text: Any, is_config: bool = False) -> None:
        """
        Reject strings echoing configured token to prevent secret leaks or fabricated code.
        Source code containing the secret is rejected safely, NOT redacted into fabricated code.
        """
        if self._token and isinstance(text, str) and self._token in text:
            cat = SourceErrorCategory.configuration if is_config else SourceErrorCategory.malformed_payload
            raise SourceError(SourceProvider.github, cat) from None

    def _validate_safe_relative_path(self, path: Any) -> str:
        """Validate safe relative path (no traversal, no absolute, no control characters)."""
        if not isinstance(path, str) or not path.strip():
            raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

        self._assert_no_credential_echo(path)

        for ch in path:
            code = ord(ch)
            if code < 32 or code == 127 or ch in ("\r", "\n", "\t"):
                raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

        if path.startswith("/") or path.startswith("\\"):
            raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

        if ":" in path:
            raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

        parts = path.replace("\\", "/").split("/")
        for part in parts:
            if part == "..":
                raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

        return path

    def _auth_headers(self) -> dict[str, str]:
        """Generate API headers using documented GitHub REST API version 2022-11-28."""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def invalidate_cache(self, project_id: Optional[str] = None) -> None:
        """Clear cached collection for a specific project or all projects."""
        if project_id is not None:
            self._cache.pop(project_id, None)
        else:
            self._cache.clear()
            self._last_resolved_commit_sha = None
            self._last_collection_complete = False
            self._unavailable_file_paths = ()

    def refresh(self, project_id: str, commit_sha: Optional[str] = None) -> list[CodeDiffSnippet]:
        """Explicitly refresh the cached collection for the project."""
        self.invalidate_cache(project_id)
        return self.get_diffs(project_id, commit_sha=commit_sha)

    def resolve_commit_sha(self) -> str:
        """Resolve this adapter's configured branch without collecting or caching its diff."""
        if self._branch is None:
            raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None
        self._assert_no_credential_echo(self._branch, is_config=True)
        url = (
            f"https://api.github.com/repos/{self._owner}/{self._repo}/commits/"
            f"{quote(self._branch, safe='')}"
        )
        try:
            response = self._transport.get(url=url, headers=self._auth_headers())
        except SourceError as exc:
            if exc.provider != SourceProvider.github:
                raise SourceError(SourceProvider.github, exc.category) from None
            raise
        except Exception:
            raise SourceError(SourceProvider.github, SourceErrorCategory.network) from None
        if (response.status_code != 200 or not isinstance(response.body, dict)
                or type(response.bytes_received) is not int or response.bytes_received < 0
                or response.bytes_received > self._max_total_bytes):
            category = (SourceErrorCategory.limit_exceeded
                        if getattr(response, "bytes_received", 0) > self._max_total_bytes
                        else SourceErrorCategory.malformed_payload)
            raise SourceError(SourceProvider.github, category) from None
        resolved_sha = response.body.get("sha")
        if not isinstance(resolved_sha, str) or not FULL_SHA_RE.fullmatch(resolved_sha):
            raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
        self._assert_no_credential_echo(resolved_sha)
        return resolved_sha.lower()

    def _parse_link_header(self, link_str: str) -> dict[str, str]:
        """Parse HTTP Link header into rel -> url mapping."""
        links: dict[str, str] = {}
        entries = link_str.split(",")
        for entry in entries:
            parts = entry.strip().split(";")
            if len(parts) < 2:
                continue
            url_part = parts[0].strip()
            if url_part.startswith("<") and url_part.endswith(">"):
                url_part = url_part[1:-1].strip()
            rel_part = None
            for p in parts[1:]:
                p = p.strip()
                if p.startswith("rel="):
                    val = p[4:].strip().strip('"').strip("'")
                    rel_part = val
            if rel_part and url_part:
                links[rel_part] = url_part
        return links

    def _validate_next_link(self, next_url: str, expected_page: int) -> None:
        """Strictly validate rel=next URL from Link header."""
        self._assert_no_credential_echo(next_url)
        try:
            parts = urlsplit(next_url)
        except Exception:
            raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

        if parts.scheme.lower() != "https":
            raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
        if (parts.hostname or "").lower() != "api.github.com":
            raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
        if parts.username or parts.password:
            raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
        if parts.port not in (None, 443):
            raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
        if parts.fragment:
            raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

        # Check path
        path = parts.path
        if not (path.startswith("/repos/") or path.startswith("/repositories/")):
            raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

        # Validate query parameters
        qs = parse_qs(parts.query, keep_blank_values=True)
        for k in qs:
            if k not in ("page", "per_page"):
                raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

        if "page" in qs:
            try:
                pg = int(qs["page"][0])
                if pg != expected_page:
                    raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
            except (ValueError, TypeError):
                raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

        if "per_page" in qs:
            try:
                ppg = int(qs["per_page"][0])
                if ppg != 100:
                    raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
            except (ValueError, TypeError):
                raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

    def _resolve_default_branch(self, requests_count: list[int], total_bytes: list[int]) -> str:
        """Resolve repository default branch via GET /repos/{owner}/{repo}."""
        if requests_count[0] >= self._max_requests:
            raise SourceError(SourceProvider.github, SourceErrorCategory.limit_exceeded) from None

        url = f"https://api.github.com/repos/{self._owner}/{self._repo}"
        try:
            resp: HttpResponse = self._transport.get(url=url, headers=self._auth_headers())
        except SourceError as e:
            if e.provider != SourceProvider.github:
                raise SourceError(SourceProvider.github, e.category) from None
            raise
        except Exception:
            raise SourceError(SourceProvider.github, SourceErrorCategory.network) from None

        requests_count[0] += 1
        total_bytes[0] += resp.bytes_received
        if total_bytes[0] > self._max_total_bytes:
            raise SourceError(SourceProvider.github, SourceErrorCategory.limit_exceeded) from None

        if not isinstance(resp.body, dict):
            raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

        default_branch = resp.body.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch.strip():
            raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

        self._assert_no_credential_echo(default_branch)
        return default_branch

    def _parse_unified_patch(self, patch: str) -> list[tuple[int, str]]:
        """
        Parse strict unified diff patch.

        Maps context and added lines to real 1-based new-file line numbers.
        Omits '-' old lines from NEW snippets.
        Validates hunk counters and ranges; rejects malformed/combined diffs.
        """
        if not patch:
            return []

        # Split on actual LF to preserve Unicode line separators (\x0b, \x0c, \x85, \u2028)
        # Strip only the final newline delimiter artifact if present
        if patch.endswith("\n"):
            lines = patch[:-1].split("\n")
        else:
            lines = patch.split("\n")

        recorded_lines: list[tuple[int, str]] = []
        i = 0
        n = len(lines)
        last_new_end = 0

        while i < n:
            line = lines[i]
            if line.startswith("@@@"):
                # Combined diff (e.g. merge commit): reject as unsupported/malformed
                raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
            if not line.startswith("@@"):
                raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

            m = HUNK_HEADER_RE.match(line)
            if not m:
                raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

            old_start = _parse_hunk_int(m.group(1))
            old_count = _parse_hunk_int(m.group(2), default=1)
            new_start = _parse_hunk_int(m.group(3))
            new_count = _parse_hunk_int(m.group(4), default=1)

            if old_start == 0 and old_count != 0:
                raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
            if new_start == 0 and new_count != 0:
                raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

            # Enforce non-overlapping/increasing hunks
            if new_start < last_new_end:
                raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

            current_new_line = new_start
            hunk_old_seen = 0
            hunk_new_seen = 0

            i += 1
            while i < n:
                cur = lines[i]
                if cur.startswith("@@"):
                    # Next hunk header
                    break
                if cur.startswith("+"):
                    if hunk_new_seen >= new_count:
                        raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
                    content = cur[1:]
                    recorded_lines.append((current_new_line, content))
                    current_new_line += 1
                    hunk_new_seen += 1
                    i += 1
                elif cur.startswith("-"):
                    if hunk_old_seen >= old_count:
                        raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
                    hunk_old_seen += 1
                    i += 1
                elif cur.startswith(" ") or cur == "":
                    # Context line
                    if hunk_new_seen >= new_count or hunk_old_seen >= old_count:
                        raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
                    content = cur[1:] if cur.startswith(" ") else ""
                    recorded_lines.append((current_new_line, content))
                    current_new_line += 1
                    hunk_new_seen += 1
                    hunk_old_seen += 1
                    i += 1
                elif cur in ("\\ No newline at end of file", "\\ No newline at end of file\r"):
                    # Exact standard \ No newline at end of file marker
                    i += 1
                else:
                    raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

            if hunk_new_seen != new_count or hunk_old_seen != old_count:
                raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

            last_new_end = current_new_line

        return recorded_lines

    def _publish_collection_metadata(self, collection: _CachedCollection) -> None:
        """Synchronize public last-retrieval state with the validated collection."""
        self._last_resolved_commit_sha = collection.commit_sha
        self._last_collection_complete = collection.is_complete
        self._unavailable_file_paths = tuple(collection.unavailable_file_paths)

    def _capture_full_file(
        self, filename: str, pinned_sha: str, entry: dict[str, Any],
        requests_count: list[int], total_bytes: list[int],
    ) -> CodeDiffSnippet | None:
        """Read one complete file only at the already resolved commit SHA."""
        if requests_count[0] >= self._max_requests:
            return None
        encoded_path = "/".join(quote(part, safe="") for part in filename.split("/"))
        url = (
            f"https://api.github.com/repos/{self._owner}/{self._repo}/contents/{encoded_path}"
            f"?ref={quote(pinned_sha, safe='')}"
        )
        requests_count[0] += 1
        try:
            response = self._transport.get(url=url, headers=self._auth_headers())
        except SourceError as exc:
            if exc.provider != SourceProvider.github:
                raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
            if exc.category in {
                SourceErrorCategory.not_found,
                SourceErrorCategory.network,
                SourceErrorCategory.timeout,
                SourceErrorCategory.rate_limit,
            }:
                return None
            raise SourceError(SourceProvider.github, exc.category) from None
        total_bytes[0] += response.bytes_received
        if total_bytes[0] > self._max_total_bytes:
            raise SourceError(SourceProvider.github, SourceErrorCategory.limit_exceeded) from None

        if response.status_code == 404:
            return None
        if response.status_code == 401:
            raise SourceError(SourceProvider.github, SourceErrorCategory.authentication) from None
        if response.status_code == 403:
            headers = {key.lower(): str(value) for key, value in response.headers.items()}
            if headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers:
                return None
            raise SourceError(SourceProvider.github, SourceErrorCategory.permission_denied) from None
        if response.status_code == 429 or 500 <= response.status_code < 600:
            return None
        if response.status_code != 200:
            raise SourceError(SourceProvider.github, SourceErrorCategory.network) from None

        body = response.body
        if not isinstance(body, dict):
            raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
        if not body:
            return None
        encoding = body.get("encoding")
        if encoding == "none":
            return None
        if encoding != "base64":
            raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
        encoded = body.get("content")
        blob_sha = body.get("sha")
        if (not isinstance(encoded, str) or not isinstance(blob_sha, str)
                or not FULL_SHA_RE.fullmatch(blob_sha)):
            raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
        expected_blob_sha = entry.get("sha")
        if expected_blob_sha is not None:
            if not isinstance(expected_blob_sha, str) or not FULL_SHA_RE.fullmatch(expected_blob_sha):
                raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
            if expected_blob_sha.lower() != blob_sha.lower():
                raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
        encoded = encoded.replace("\r\n", "").replace("\n", "")
        try:
            content_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeError:
            return None
        self._assert_no_credential_echo(content)
        size = body.get("size")
        if size is not None and (type(size) is not int or size != len(content_bytes)):
            raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
        if len(content_bytes) > min(MAX_FULL_FILE_BYTES, self._max_total_bytes):
            return None
        if len(content.splitlines()) > self._max_lines:
            return None
        line_count = len(content.splitlines())
        return CodeDiffSnippet(
            repository=f"{self._owner}/{self._repo}",
            pull_request_id=None,
            commit_sha=pinned_sha,
            file_path=filename,
            diff_side="NEW",
            evidence_scope="full_file",
            is_complete=True,
            lines=list(range(1, line_count + 1)),
            content=content,
            total_lines=line_count,
            source_sha256=hashlib.sha256(content_bytes).hexdigest(),
            source_blob_sha=blob_sha.lower(),
        )

    def _ensure_collection(
        self,
        project_id: str,
        commit_sha: Optional[str] = None,
    ) -> _CachedCollection:
        """Fetch, validate, and normalize commit diffs into a consistent cached collection."""
        if not isinstance(project_id, str) or not project_id.strip():
            raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None

        self._assert_no_credential_echo(project_id, is_config=True)

        norm_explicit_sha: Optional[str] = None
        if commit_sha is not None:
            if not isinstance(commit_sha, str) or not FULL_SHA_RE.match(commit_sha):
                raise SourceError(SourceProvider.github, SourceErrorCategory.configuration) from None
            self._assert_no_credential_echo(commit_sha, is_config=True)
            norm_explicit_sha = commit_sha.lower()

        # Check existing cache
        if project_id in self._cache:
            cached = self._cache[project_id]
            if norm_explicit_sha is None or cached.commit_sha == norm_explicit_sha:
                self._cache.move_to_end(project_id)
                self._publish_collection_metadata(cached)
                return cached

        # Begin collection cycle
        requests_count = [0]
        total_bytes = [0]
        collection_time = datetime.now(timezone.utc)

        # Determine target ref for page 1
        if norm_explicit_sha is not None:
            initial_ref = norm_explicit_sha
        elif self._branch is not None:
            initial_ref = quote(self._branch, safe="")
        else:
            resolved_default = self._resolve_default_branch(requests_count, total_bytes)
            initial_ref = quote(resolved_default, safe="")

        all_files: list[dict[str, Any]] = []
        seen_filenames: set[str] = set()
        pinned_sha: Optional[str] = None
        current_page = 1

        while True:
            if requests_count[0] >= self._max_requests:
                raise SourceError(SourceProvider.github, SourceErrorCategory.limit_exceeded) from None

            if current_page == 1:
                url = f"https://api.github.com/repos/{self._owner}/{self._repo}/commits/{initial_ref}"
            else:
                url = f"https://api.github.com/repos/{self._owner}/{self._repo}/commits/{pinned_sha}"

            params = {"per_page": 100, "page": current_page}

            try:
                resp: HttpResponse = self._transport.get(
                    url=url,
                    params=params,
                    headers=self._auth_headers(),
                )
            except SourceError as e:
                if e.provider != SourceProvider.github:
                    raise SourceError(SourceProvider.github, e.category) from None
                raise
            except Exception:
                raise SourceError(SourceProvider.github, SourceErrorCategory.network) from None

            requests_count[0] += 1
            total_bytes[0] += resp.bytes_received
            if total_bytes[0] > self._max_total_bytes:
                raise SourceError(SourceProvider.github, SourceErrorCategory.limit_exceeded) from None

            if not isinstance(resp.body, dict):
                raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

            resp_sha_raw = resp.body.get("sha")
            if not isinstance(resp_sha_raw, str) or not FULL_SHA_RE.match(resp_sha_raw):
                raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
            self._assert_no_credential_echo(resp_sha_raw)

            page_sha = resp_sha_raw.lower()

            if current_page == 1:
                if norm_explicit_sha is not None and page_sha != norm_explicit_sha:
                    raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
                pinned_sha = page_sha
            else:
                if page_sha != pinned_sha:
                    raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

            raw_files = resp.body.get("files")
            if raw_files is None:
                if current_page == 1:
                    raw_files = []
                else:
                    raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

            if not isinstance(raw_files, list):
                raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

            if current_page > 1 and len(raw_files) == 0:
                # Intermediate empty page is malformed
                raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

            for entry in raw_files:
                if not isinstance(entry, dict):
                    raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None

                filename = self._validate_safe_relative_path(entry.get("filename"))
                if filename in seen_filenames:
                    raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
                seen_filenames.add(filename)

                status = entry.get("status")
                if not isinstance(status, str) or not status.strip():
                    raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
                self._assert_no_credential_echo(status)

                prev_filename = entry.get("previous_filename")
                if prev_filename is not None:
                    self._validate_safe_relative_path(prev_filename)

                patch = entry.get("patch")
                if patch is not None:
                    if not isinstance(patch, str):
                        raise SourceError(SourceProvider.github, SourceErrorCategory.malformed_payload) from None
                    if len(patch) > self._max_text_length:
                        raise SourceError(SourceProvider.github, SourceErrorCategory.limit_exceeded) from None
                    self._assert_no_credential_echo(patch)

                all_files.append(entry)

                if len(all_files) >= MAX_GITHUB_FILES_CEILING or len(all_files) > self._max_files:
                    raise SourceError(SourceProvider.github, SourceErrorCategory.limit_exceeded) from None

            # Evaluate pagination continuation
            if len(raw_files) < 100:
                break

            link_header = resp.headers.get("link")
            if link_header:
                links = self._parse_link_header(link_header)
                if "next" in links:
                    next_page = current_page + 1
                    self._validate_next_link(links["next"], next_page)
                    current_page = next_page
                    if current_page > self._max_pages:
                        raise SourceError(SourceProvider.github, SourceErrorCategory.limit_exceeded) from None
                    continue
                else:
                    if len(all_files) >= MAX_GITHUB_FILES_CEILING:
                        raise SourceError(SourceProvider.github, SourceErrorCategory.limit_exceeded) from None
                    break
            else:
                if len(all_files) >= MAX_GITHUB_FILES_CEILING:
                    raise SourceError(SourceProvider.github, SourceErrorCategory.limit_exceeded) from None
                break

        # Build normalized snippets and evidence
        assert pinned_sha is not None
        canonical_commit_url = f"https://api.github.com/repos/{self._owner}/{self._repo}/commits/{pinned_sha}"

        snippets: list[CodeDiffSnippet] = []
        evidence: list[EvidenceItem] = []
        unavailable_paths: list[str] = []
        is_complete = True

        # Commit-level evidence item
        commit_ev_id = f"github-commit-{pinned_sha[:16]}"
        commit_payload: dict[str, Any] = {
            "repository": f"{self._owner}/{self._repo}",
            "commit_sha": pinned_sha,
            "file_count": len(all_files),
            "url": canonical_commit_url,
        }
        evidence.append(
            EvidenceItem(
                provenance=EvidenceProvenance(
                    id=commit_ev_id,
                    source=EvidenceSource.github,
                    project_id=project_id,
                    repository=f"{self._owner}/{self._repo}",
                    commit_sha=pinned_sha,
                    timestamp=collection_time,
                    payload=commit_payload,
                ),
                category="commit",
                summary=f"GitHub commit {self._owner}/{self._repo}@{pinned_sha[:7]} ({len(all_files)} files)",
            )
        )

        for entry in all_files:
            filename = entry["filename"]
            status = entry["status"]
            patch = entry.get("patch")
            full_file = None

            if patch is None:
                if status == "removed":
                    patch_available = True
                    reason = "file_removed"
                else:
                    patch_available = False
                    reason = "patch_unavailable"
                    unavailable_paths.append(filename)
                    is_complete = False

                lines_list = []
            else:
                parsed = self._parse_unified_patch(patch)
                if len(parsed) > self._max_lines:
                    raise SourceError(SourceProvider.github, SourceErrorCategory.limit_exceeded) from None

                lines_list = [l_num for l_num, _ in parsed]
                if lines_list:
                    content_str = "\n".join(c_text for _, c_text in parsed) + "\n"
                    self._assert_no_credential_echo(content_str)
                    snip = CodeDiffSnippet(
                        repository=f"{self._owner}/{self._repo}",
                        pull_request_id=None,
                        commit_sha=pinned_sha,
                        file_path=filename,
                        diff_side="NEW",
                        evidence_scope="diff",
                        is_complete=False,
                        lines=lines_list,
                        content=content_str,
                    )
                    snippets.append(snip)

                patch_available = True
                reason = "file_removed" if status == "removed" else None

            if status != "removed":
                full_file = self._capture_full_file(
                    filename, pinned_sha, entry, requests_count, total_bytes,
                )
                if full_file is not None:
                    snippets.append(full_file)

            # File diff evidence item
            stable_hash = hashlib.sha256(
                f"github:{self._owner}/{self._repo}:{pinned_sha}:{filename}".encode("utf-8")
            ).hexdigest()[:16]
            diff_ev_id = f"github-diff-{stable_hash}"

            diff_payload: dict[str, Any] = {
                "repository": f"{self._owner}/{self._repo}",
                "commit_sha": pinned_sha,
                "file_path": filename,
                "status": status,
                "patch_available": patch_available,
                "full_file_available": full_file is not None,
                "lines": lines_list,
                "url": canonical_commit_url,
            }
            if full_file is not None:
                diff_payload["source_sha256"] = full_file.source_sha256
                diff_payload["source_blob_sha"] = full_file.source_blob_sha
            if reason:
                diff_payload["reason"] = reason

            evidence.append(
                EvidenceItem(
                    provenance=EvidenceProvenance(
                        id=diff_ev_id,
                        source=EvidenceSource.github,
                        project_id=project_id,
                        repository=f"{self._owner}/{self._repo}",
                        commit_sha=pinned_sha,
                        file_path=filename,
                        diff_side="NEW",
                        lines=lines_list,
                        external_id=entry.get("sha"),
                        timestamp=collection_time,
                        payload=diff_payload,
                    ),
                    category="diff",
                    summary=f"GitHub diff {self._owner}/{self._repo}@{pinned_sha[:7]}:{filename} ({status})",
                )
            )

        collection = _CachedCollection(
            project_id=project_id,
            owner=self._owner,
            repo=self._repo,
            commit_sha=pinned_sha,
            collected_at=collection_time,
            diffs=tuple(snippets),
            evidence=tuple(evidence),
            unavailable_file_paths=tuple(unavailable_paths),
            is_complete=is_complete,
        )

        if len(self._cache) >= MAX_CACHE_ENTRIES:
            self._cache.popitem(last=False)

        self._cache[project_id] = collection
        self._publish_collection_metadata(collection)

        return collection

    def get_diffs(self, project_id: str, commit_sha: Optional[str] = None) -> list[CodeDiffSnippet]:
        """
        Retrieve normalized CodeDiffSnippet objects for the pinned commit.

        Returns deep copies from the cached collection to protect against caller mutation.
        """
        collection = self._ensure_collection(project_id, commit_sha=commit_sha)
        return [d.model_copy(deep=True) for d in collection.diffs]

    def get_evidence(self, project_id: str, commit_sha: Optional[str] = None) -> list[EvidenceItem]:
        """
        Retrieve normalized EvidenceItem objects corresponding to the pinned commit collection.

        Returns deep copies from the cached collection to protect against caller mutation.
        """
        if project_id in self._cache:
            cached = self._cache[project_id]
            if commit_sha is None or cached.commit_sha == commit_sha.lower():
                self._cache.move_to_end(project_id)
                self._publish_collection_metadata(cached)
                return [e.model_copy(deep=True) for e in cached.evidence]

        if commit_sha is not None:
            collection = self._ensure_collection(project_id, commit_sha=commit_sha)
            return [e.model_copy(deep=True) for e in collection.evidence]

        raise SourceError(SourceProvider.github, SourceErrorCategory.not_found) from None
