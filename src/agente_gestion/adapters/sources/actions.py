"""Bounded read-only GitHub Actions adapter for CI runs and evidence collection (T4)."""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

from agente_gestion.adapters.sources.http_transport import (
    BoundedGetTransport,
    HttpGetTransportPort,
    HttpResponse,
)
from agente_gestion.domain.code import CIRunResult
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
from agente_gestion.ports.sources import CISourcePort

# Hex SHA pattern: strictly full 40-char (SHA-1) or 64-char (SHA-256) hex hashes
FULL_SHA_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")

# Safe identifier patterns preventing injection in URL paths
SAFE_OWNER_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
SAFE_REPO_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")

# Bounded limits and ceilings
MAX_ACTIONS_RUNS_CEILING = 1000
DEFAULT_MAX_RUNS = 1000
DEFAULT_MAX_PAGES = 10
DEFAULT_MAX_REQUESTS = 12
DEFAULT_MAX_TEXT_LENGTH = 65536
DEFAULT_MAX_TOTAL_BYTES = 10 * 1024 * 1024  # 10 MiB aggregate
MAX_CACHE_ENTRIES = 20


def _validate_safe_string(val: Any) -> str:
    """Validate that val is a non-empty string without CR/LF/quotes/control characters."""
    if not isinstance(val, str) or not val.strip():
        raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None

    for ch in val:
        code = ord(ch)
        if code < 32 or code == 127 or ch in ('"', "'", "\r", "\n", "\t"):
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
    return val


def _validate_remote_safe_string(val: Any) -> str:
    """Validate that remote string is non-empty without CR/LF/quotes/control characters."""
    if not isinstance(val, str) or not val.strip():
        raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

    for ch in val:
        code = ord(ch)
        if code < 32 or code == 127 or ch in ('"', "'", "\r", "\n", "\t"):
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None
    return val


def _parse_iso_utc_datetime(val: Any) -> datetime:
    """Parse UTC-aware ISO 8601 timestamp string. Rejects naive timestamps."""
    if not isinstance(val, str) or not val.strip():
        raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None
    try:
        norm = val.replace("Z", "+00:00") if val.endswith("Z") else val
        dt = datetime.fromisoformat(norm)
    except Exception:
        raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None
    if dt.tzinfo is None:
        raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class _CachedCollection:
    """Immutable normalized collection containing CI runs, evidence, and completeness metadata."""
    project_id: str
    owner: str
    repo: str
    target_commit_sha: str
    collected_at: datetime
    runs: tuple[CIRunResult, ...]
    evidence: tuple[EvidenceItem, ...]
    is_complete: bool


class GitHubActionsSourceAdapter(CISourcePort):
    """
    Read-only adapter retrieving GitHub Actions workflow runs and evidence for a pinned commit.

    Enforces:
    - Exactly one pinned target commit per retrieval cycle; no floating branches/HEAD.
    - Full 40-char or 64-char hex commit SHA strictly required before issuing network requests.
    - Preservation of ACTUAL returned run.head_sha (mismatches are never overwritten or dropped).
    - Honest CI metrics: total_tests, failed_tests, and coverage_percentage are ALWAYS None.
    - Timestamp integrity: CIRunResult.timestamp is None; actual UTC timestamps preserved in evidence.
    - Deterministic sorting newest first by updated_at with run ID tiebreaker.
    - Strict Link header validation and self-constructed pagination targets.
    - Rejection of credential echoes in paths, identifiers, or remote payload texts.
    - Local cache published atomically only after all runs and pages validate cleanly.
    """

    def __init__(
        self,
        owner: str,
        repo: str,
        token: Optional[str] = None,
        commit_sha: Optional[str] = None,
        target_commit_sha: Optional[str] = None,
        transport: Optional[HttpGetTransportPort] = None,
        max_runs: int = DEFAULT_MAX_RUNS,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        max_text_length: int = DEFAULT_MAX_TEXT_LENGTH,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    ):
        self._owner = _validate_safe_string(owner)
        if not SAFE_OWNER_RE.match(self._owner):
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None

        self._repo = _validate_safe_string(repo)
        if not SAFE_REPO_RE.match(self._repo) or self._repo in (".", ".."):
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None

        self._token: Optional[str] = None
        if token is not None:
            self._token = _validate_safe_string(token)

        # Independently validate each supplied constructor SHA alias
        def _validate_constructor_sha(sha_val: Any) -> Optional[str]:
            if sha_val is None:
                return None
            if not isinstance(sha_val, str) or not sha_val:
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
            if not FULL_SHA_RE.match(sha_val):
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
            s = _validate_safe_string(sha_val)
            if self._token and self._token in s:
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
            return s.lower()

        resolved_c_sha = _validate_constructor_sha(commit_sha)
        resolved_t_sha = _validate_constructor_sha(target_commit_sha)

        if resolved_c_sha is not None and resolved_t_sha is not None:
            if resolved_c_sha != resolved_t_sha:
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
            self._configured_target_sha = resolved_c_sha
        elif resolved_c_sha is not None:
            self._configured_target_sha = resolved_c_sha
        elif resolved_t_sha is not None:
            self._configured_target_sha = resolved_t_sha
        else:
            self._configured_target_sha = None

        # Prevent credential leaks in configuration values
        if self._token:
            if self._token in self._owner or self._token in self._repo:
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
            if self._configured_target_sha and self._token in self._configured_target_sha:
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None

        if transport is not None and not isinstance(transport, HttpGetTransportPort):
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
        self._transport = transport or BoundedGetTransport(SourceProvider.github_actions)

        # Validate budget limits
        if type(max_runs) is not int or max_runs <= 0 or max_runs > MAX_ACTIONS_RUNS_CEILING:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
        self._max_runs = max_runs

        if type(max_pages) is not int or max_pages <= 0 or max_pages > DEFAULT_MAX_PAGES:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
        self._max_pages = max_pages

        if type(max_requests) is not int or max_requests <= 0 or max_requests > 50:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
        self._max_requests = max_requests

        if type(max_text_length) is not int or max_text_length <= 0:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
        self._max_text_length = max_text_length

        if type(max_total_bytes) is not int or max_total_bytes <= 0:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
        self._max_total_bytes = max_total_bytes

        self._cache: OrderedDict[tuple[str, str], _CachedCollection] = OrderedDict()
        self._last_target_commit_sha: Optional[str] = None
        self._last_collection_complete: bool = False

    @property
    def owner(self) -> str:
        """Configured repository owner."""
        return self._owner

    @property
    def repo(self) -> str:
        """Configured repository name."""
        return self._repo

    @property
    def commit_sha(self) -> Optional[str]:
        """Configured target commit SHA, if any."""
        return self._configured_target_sha

    @property
    def target_commit_sha(self) -> Optional[str]:
        """Alias for configured target commit SHA."""
        return self._configured_target_sha

    @property
    def last_target_commit_sha(self) -> Optional[str]:
        """The target commit SHA used in the most recent successful collection."""
        return self._last_target_commit_sha

    @property
    def last_resolved_commit_sha(self) -> Optional[str]:
        """Alias for last_target_commit_sha for port convention consistency."""
        return self._last_target_commit_sha

    @property
    def last_collection_complete(self) -> bool:
        """True if the last retrieval completed and published successfully."""
        return self._last_collection_complete

    def __repr__(self) -> str:
        """Safe representation hiding credentials."""
        return f"GitHubActionsSourceAdapter(owner={self._owner!r}, repo={self._repo!r})"

    def _assert_no_credential_echo(self, text: Any, is_config: bool = False) -> None:
        """
        Reject strings echoing configured token to prevent secret leaks.
        Remote payloads containing the secret are rejected conservatively.
        """
        if self._token and isinstance(text, str) and self._token in text:
            cat = SourceErrorCategory.configuration if is_config else SourceErrorCategory.malformed_payload
            raise SourceError(SourceProvider.github_actions, cat) from None

    def _validate_variable_text(
        self,
        raw_val: Any,
        is_required: bool = False,
        default: Optional[str] = None,
    ) -> Optional[str]:
        """Validate consumed/emitted variable text metadata and enforce max_text_length."""
        if raw_val is None:
            if is_required:
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None
            return default
        if not isinstance(raw_val, str):
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None
        if not raw_val.strip() and is_required:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None
        self._assert_no_credential_echo(raw_val)
        if len(raw_val) > self._max_text_length:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.limit_exceeded) from None
        return _validate_remote_safe_string(raw_val)

    def _parse_and_validate_date(self, raw_val: Any, is_required: bool = True) -> Optional[datetime]:
        """Validate and parse UTC-aware date string, checking credential echo before and after."""
        if raw_val is None:
            if is_required:
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None
            return None
        if not isinstance(raw_val, str) or not raw_val.strip():
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None
        self._assert_no_credential_echo(raw_val)
        dt = _parse_iso_utc_datetime(raw_val)
        self._assert_no_credential_echo(dt.isoformat())
        return dt

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
            keys_to_remove = [k for k in self._cache if k[0] == project_id]
            for k in keys_to_remove:
                self._cache.pop(k, None)
        else:
            self._cache.clear()
            self._last_target_commit_sha = None
            self._last_collection_complete = False

    def refresh(self, project_id: str, commit_sha: Optional[str] = None) -> list[CIRunResult]:
        """Explicitly refresh the cached collection for the project."""
        self.invalidate_cache(project_id)
        return self.get_ci_results(project_id, commit_sha=commit_sha)

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

    def _validate_next_link(self, next_url: str, expected_page: int, target_sha: str) -> None:
        """Strictly validate rel=next URL from Link header."""
        self._assert_no_credential_echo(next_url)
        try:
            parts = urlsplit(next_url)
        except Exception:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

        if parts.scheme.lower() != "https":
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

        if (parts.hostname or "").lower() != "api.github.com":
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

        if parts.username or parts.password or parts.fragment:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

        try:
            port = parts.port
        except Exception:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None
        if port is not None and port != 443:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

        expected_path = f"/repos/{self._owner}/{self._repo}/actions/runs"
        if parts.path != expected_path:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

        try:
            qs = parse_qs(parts.query, keep_blank_values=True)
        except Exception:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

        allowed_keys = {"head_sha", "page", "per_page"}
        if not set(qs.keys()).issubset(allowed_keys):
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

        if qs.get("head_sha") != [target_sha]:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

        if qs.get("page") != [str(expected_page)]:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

        if qs.get("per_page") != ["100"]:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

    def _publish_collection_metadata(self, collection: _CachedCollection) -> None:
        """Synchronize public last-retrieval state with the validated collection."""
        self._last_target_commit_sha = collection.target_commit_sha
        self._last_collection_complete = collection.is_complete

    def _ensure_collection(
        self,
        project_id: str,
        commit_sha: Optional[str] = None,
    ) -> _CachedCollection:
        """Fetch, validate, and normalize workflow runs into an immutable cached collection."""
        if not isinstance(project_id, str) or not project_id.strip():
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
        self._assert_no_credential_echo(project_id, is_config=True)

        # Resolve pinned target SHA
        resolved_sha: Optional[str] = None
        if commit_sha is not None:
            s_sha = _validate_safe_string(commit_sha)
            if not FULL_SHA_RE.match(s_sha):
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
            self._assert_no_credential_echo(s_sha, is_config=True)
            resolved_sha = s_sha.lower()
        elif self._configured_target_sha is not None:
            resolved_sha = self._configured_target_sha
        else:
            # Invariant: Never float or guess SHA without explicit pinned target
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None

        cache_key = (project_id, resolved_sha)
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            self._cache.move_to_end(cache_key)
            self._publish_collection_metadata(cached)
            return cached

        collection_time = datetime.now(timezone.utc)
        current_page = 1
        requests_count = 0
        total_bytes = 0
        all_run_rows: list[dict[str, Any]] = []
        seen_run_ids: set[int] = set()
        declared_total_count: Optional[int] = None

        while True:
            if requests_count >= self._max_requests:
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.limit_exceeded) from None
            if current_page > self._max_pages:
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.limit_exceeded) from None

            url = f"https://api.github.com/repos/{self._owner}/{self._repo}/actions/runs"
            params = {
                "head_sha": resolved_sha,
                "per_page": 100,
                "page": current_page,
            }

            try:
                resp: HttpResponse = self._transport.get(
                    url=url,
                    params=params,
                    headers=self._auth_headers(),
                )
            except SourceError as e:
                if e.provider != SourceProvider.github_actions:
                    raise SourceError(SourceProvider.github_actions, e.category) from None
                raise
            except Exception:
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.network) from None

            requests_count += 1
            total_bytes += resp.bytes_received
            if total_bytes > self._max_total_bytes:
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.limit_exceeded) from None

            if not isinstance(resp.body, dict):
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

            raw_total = resp.body.get("total_count")
            if not isinstance(raw_total, int) or isinstance(raw_total, bool) or raw_total < 0:
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

            if raw_total >= 1000 or raw_total > self._max_runs:
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.limit_exceeded) from None

            if declared_total_count is None:
                declared_total_count = raw_total
            elif raw_total != declared_total_count:
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

            raw_runs = resp.body.get("workflow_runs")
            if not isinstance(raw_runs, list):
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

            if declared_total_count == 0:
                if len(raw_runs) > 0:
                    raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None
                link_header = resp.headers.get("link")
                if link_header:
                    links = self._parse_link_header(link_header)
                    if "next" in links:
                        raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None
                break

            if len(raw_runs) == 0:
                # Empty intermediate page before reaching declared total count
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

            for row in raw_runs:
                if not isinstance(row, dict):
                    raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

                r_id = row.get("id")
                if not isinstance(r_id, int) or isinstance(r_id, bool) or r_id <= 0:
                    raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

                if r_id in seen_run_ids:
                    raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None
                seen_run_ids.add(r_id)

                all_run_rows.append(row)
                if len(all_run_rows) >= 1000 or len(all_run_rows) > self._max_runs:
                    raise SourceError(SourceProvider.github_actions, SourceErrorCategory.limit_exceeded) from None

            if len(all_run_rows) >= declared_total_count:
                link_header = resp.headers.get("link")
                if link_header:
                    links = self._parse_link_header(link_header)
                    if "next" in links:
                        raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None
                break

            if len(raw_runs) < 100:
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

            link_header = resp.headers.get("link")
            if not link_header:
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

            links = self._parse_link_header(link_header)
            if "next" not in links:
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

            next_page = current_page + 1
            self._validate_next_link(links["next"], next_page, resolved_sha)
            current_page = next_page

        if declared_total_count is not None and len(all_run_rows) != declared_total_count:
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None

        # Process, normalize and validate each run
        parsed_entries: list[tuple[CIRunResult, EvidenceItem, tuple[datetime, int]]] = []

        for row in all_run_rows:
            r_id = row["id"]
            run_id_str = str(r_id)
            self._assert_no_credential_echo(run_id_str)
            if len(run_id_str) > self._max_text_length:
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.limit_exceeded) from None

            workflow_name = self._validate_variable_text(row.get("name"), is_required=False, default="workflow")
            assert workflow_name is not None

            raw_head_sha = row.get("head_sha")
            if not isinstance(raw_head_sha, str) or not FULL_SHA_RE.match(raw_head_sha):
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None
            self._assert_no_credential_echo(raw_head_sha)
            actual_head_sha = raw_head_sha.lower()

            head_branch = self._validate_variable_text(row.get("head_branch"), is_required=False, default=None)
            event_name = self._validate_variable_text(row.get("event"), is_required=False, default=None)
            status_str = self._validate_variable_text(row.get("status"), is_required=False, default=None)
            conclusion_str = self._validate_variable_text(row.get("conclusion"), is_required=False, default=None)

            wf_id = row.get("workflow_id")
            if wf_id is not None:
                if not isinstance(wf_id, int) or isinstance(wf_id, bool) or wf_id <= 0:
                    raise SourceError(SourceProvider.github_actions, SourceErrorCategory.malformed_payload) from None
                wf_id_str = str(wf_id)
                self._assert_no_credential_echo(wf_id_str)
                if len(wf_id_str) > self._max_text_length:
                    raise SourceError(SourceProvider.github_actions, SourceErrorCategory.limit_exceeded) from None

            dt_updated = self._parse_and_validate_date(row.get("updated_at"), is_required=True)
            assert dt_updated is not None
            dt_created = self._parse_and_validate_date(row.get("created_at"), is_required=False)
            dt_started = self._parse_and_validate_date(row.get("run_started_at"), is_required=False)

            # Status normalization: conclusion takes precedence if present, otherwise status
            if conclusion_str is not None and conclusion_str.strip():
                norm_status = conclusion_str.strip().lower()
            elif status_str is not None and status_str.strip():
                norm_status = status_str.strip().lower()
            else:
                norm_status = "unknown"

            # CIRunResult entity: test counts and coverage are ALWAYS None
            # Completion timestamp is ALWAYS None (updated_at is not completion proof)
            ci_run = CIRunResult(
                id=run_id_str,
                workflow_name=workflow_name,
                commit_sha=actual_head_sha,
                status=norm_status,
                total_tests=None,
                failed_tests=None,
                coverage_percentage=None,
                timestamp=None,
            )

            canonical_run_url = f"https://api.github.com/repos/{self._owner}/{self._repo}/actions/runs/{run_id_str}"
            is_match = (actual_head_sha == resolved_sha)

            payload: dict[str, Any] = {
                "run_id": run_id_str,
                "workflow_id": wf_id,
                "workflow_name": workflow_name,
                "status": status_str,
                "conclusion": conclusion_str,
                "head_branch": head_branch,
                "head_sha": actual_head_sha,
                "requested_target_sha": resolved_sha,
                "is_target_match": is_match,
                "event": event_name,
                "created_at": dt_created.isoformat() if dt_created else None,
                "updated_at": dt_updated.isoformat(),
                "run_started_at": dt_started.isoformat() if dt_started else None,
                "url": canonical_run_url,
            }

            ev_item = EvidenceItem(
                provenance=EvidenceProvenance(
                    id=f"github_actions_{run_id_str}",
                    source=EvidenceSource.github_actions,
                    project_id=project_id,
                    repository=f"{self._owner}/{self._repo}",
                    commit_sha=actual_head_sha,
                    external_id=run_id_str,
                    timestamp=collection_time,
                    payload=payload,
                ),
                category="ci_run",
                summary=(
                    f"GitHub Actions run {run_id_str} ({workflow_name}): "
                    f"status={status_str}, conclusion={conclusion_str}, head_sha={actual_head_sha}"
                ),
            )

            sort_key = (dt_updated, r_id)
            parsed_entries.append((ci_run, ev_item, sort_key))

        # Deterministic newest-first sort by (updated_at, run_id)
        parsed_entries.sort(key=lambda x: x[2], reverse=True)
        final_runs = tuple(x[0] for x in parsed_entries)
        final_evidence = tuple(x[1] for x in parsed_entries)

        collection = _CachedCollection(
            project_id=project_id,
            owner=self._owner,
            repo=self._repo,
            target_commit_sha=resolved_sha,
            collected_at=collection_time,
            runs=final_runs,
            evidence=final_evidence,
            is_complete=True,
        )

        if len(self._cache) >= MAX_CACHE_ENTRIES:
            self._cache.popitem(last=False)

        self._cache[cache_key] = collection
        self._publish_collection_metadata(collection)

        return collection

    def get_ci_results(self, project_id: str, commit_sha: Optional[str] = None) -> list[CIRunResult]:
        """
        Retrieve normalized CIRunResult objects for the target commit.

        Returns deep copies from the cached collection to protect against caller mutation.
        """
        collection = self._ensure_collection(project_id, commit_sha=commit_sha)
        return [r.model_copy(deep=True) for r in collection.runs]

    def get_evidence(self, project_id: str, commit_sha: Optional[str] = None) -> list[EvidenceItem]:
        """
        Retrieve normalized EvidenceItem objects corresponding to the target commit collection.

        Returns deep copies from the cached collection to protect against caller mutation.
        """
        if not isinstance(project_id, str) or not project_id.strip():
            raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
        self._assert_no_credential_echo(project_id, is_config=True)

        target_sha: Optional[str] = None
        if commit_sha is not None:
            s_sha = _validate_safe_string(commit_sha)
            if not FULL_SHA_RE.match(s_sha):
                raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
            self._assert_no_credential_echo(s_sha, is_config=True)
            target_sha = s_sha.lower()
        elif self._configured_target_sha is not None:
            target_sha = self._configured_target_sha

        if target_sha is not None:
            cache_key = (project_id, target_sha)
            if cache_key in self._cache:
                cached = self._cache[cache_key]
                self._cache.move_to_end(cache_key)
                self._publish_collection_metadata(cached)
                return [e.model_copy(deep=True) for e in cached.evidence]
            collection = self._ensure_collection(project_id, commit_sha=commit_sha)
            return [e.model_copy(deep=True) for e in collection.evidence]

        # If target SHA omitted, look for any cached collection for this project
        for (pid, _), cached in reversed(list(self._cache.items())):
            if pid == project_id:
                self._cache.move_to_end((cached.project_id, cached.target_commit_sha))
                self._publish_collection_metadata(cached)
                return [e.model_copy(deep=True) for e in cached.evidence]

        raise SourceError(SourceProvider.github_actions, SourceErrorCategory.configuration) from None
