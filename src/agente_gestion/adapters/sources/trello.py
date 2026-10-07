"""Bounded read-only Trello adapter for task cards and evidence collection (T2b)."""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from agente_gestion.adapters.sources.http_transport import (
    BoundedGetTransport,
    HttpGetTransportPort,
    HttpResponse,
)
from agente_gestion.domain.evidence import (
    EvidenceItem,
    EvidenceProvenance,
    EvidenceSource,
)
from agente_gestion.domain.tasks import (
    DEFAULT_TIMEZONE,
    TaskCard,
    TaskMappingConfig,
    TaskState,
    normalize_datetime_to_tz,
)
from agente_gestion.ports.source_errors import (
    SourceError,
    SourceErrorCategory,
    SourceProvider,
)
from agente_gestion.ports.sources import TaskSourcePort

# Safe identifier pattern for board, list, card, and member IDs to prevent injection
SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")

# Max allowable items per response
MAX_TRELLO_CARDS_CEILING = 1000
DEFAULT_MAX_CARDS = 1000
DEFAULT_MAX_LISTS = 100
DEFAULT_MAX_TEXT_LENGTH = 16384
MAX_CACHE_ENTRIES = 20


def _validate_safe_string(val: Any) -> str:
    """Validate that val is a non-empty string without CR/LF/quotes/control characters."""
    if not isinstance(val, str) or not val.strip():
        raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration) from None

    for ch in val:
        code = ord(ch)
        if code < 32 or code == 127 or ch in ('"', '\r', '\n'):
            raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration) from None
    return val


def _validate_safe_identifier(val: Any) -> str:
    """Validate safe identifier for path interpolation."""
    s = _validate_safe_string(val)
    if not SAFE_IDENTIFIER_RE.match(s):
        raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration) from None
    return s


@dataclass(frozen=True)
class _CachedCollection:
    """Immutable normalized collection containing tasks and evidence."""
    project_id: str
    board_id: str
    collected_at: datetime
    tasks: tuple[TaskCard, ...]
    evidence: tuple[EvidenceItem, ...]


class TrelloSourceAdapter(TaskSourcePort):
    """
    Read-only adapter retrieving cards and lists from Trello API via bounded HTTP transport.

    Maintains a consistent, cached snapshot per project/run to ensure tasks and evidence
    originate from a single retrieval cycle without partial publication on failure.
    Remote GET calls do not claim transactional atomicity across HTTP requests; however,
    local cache commit is strictly atomic and publication is all-or-nothing.
    """

    def __init__(
        self,
        board_id: str,
        api_key: str,
        token: str,
        mapping_config: Optional[TaskMappingConfig] = None,
        transport: Optional[HttpGetTransportPort] = None,
        max_cards: int = DEFAULT_MAX_CARDS,
        max_lists: int = DEFAULT_MAX_LISTS,
        max_text_length: int = DEFAULT_MAX_TEXT_LENGTH,
        timezone_name: str = "America/Guayaquil",
        state_strategy: str = "list_mapping",
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self._api_key = _validate_safe_string(api_key)
        self._token = _validate_safe_string(token)
        self._board_id = _validate_safe_identifier(board_id)

        if self._api_key in self._board_id or self._token in self._board_id:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration) from None

        if state_strategy not in ("list_mapping", "date_fields"):
            raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration) from None
        self._state_strategy = state_strategy

        if clock is not None and not callable(clock):
            raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration) from None
        self._clock = clock

        if mapping_config is not None and not isinstance(mapping_config, TaskMappingConfig):
            raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration) from None
        self._mapping_config = mapping_config or TaskMappingConfig()

        if transport is not None and not isinstance(transport, HttpGetTransportPort):
            raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration) from None
        self._transport = transport or BoundedGetTransport(SourceProvider.trello)

        if type(max_cards) is not int or max_cards <= 0 or max_cards > MAX_TRELLO_CARDS_CEILING:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration) from None
        self._max_cards = max_cards

        if type(max_lists) is not int or max_lists <= 0:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration) from None
        self._max_lists = max_lists

        if type(max_text_length) is not int or max_text_length <= 0:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration) from None
        self._max_text_length = max_text_length

        try:
            self._tz = ZoneInfo(timezone_name)
        except Exception:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration) from None

        self._cache: OrderedDict[str, _CachedCollection] = OrderedDict()

    @property
    def board_id(self) -> str:
        """Configured Trello board identifier."""
        return self._board_id

    @property
    def mapping_config(self) -> TaskMappingConfig:
        """Current task state and list mapping configuration."""
        return self._mapping_config

    @property
    def state_strategy(self) -> str:
        """Configured task state determination strategy ('list_mapping' or 'date_fields')."""
        return self._state_strategy

    def __repr__(self) -> str:
        """Safe representation hiding credentials."""
        return f"TrelloSourceAdapter(board_id={self._board_id!r})"

    def _sanitize_text(self, text: Optional[str]) -> Optional[str]:
        """Redact configured credentials if echoed in remote text."""
        if text is None:
            return None
        res = text
        if self._api_key and self._api_key in res:
            res = res.replace(self._api_key, "[REDACTED]")
        if self._token and self._token in res:
            res = res.replace(self._token, "[REDACTED]")
        return res

    def _assert_no_credential_echo(self, ident: str) -> None:
        """Reject identity strings echoing configured key or token to prevent identity corruption/leakage."""
        if self._api_key in ident or self._token in ident:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None

    def invalidate_cache(self, project_id: Optional[str] = None) -> None:
        """Clear cached collection for a specific project or all projects."""
        if project_id is not None:
            self._cache.pop(project_id, None)
        else:
            self._cache.clear()

    def refresh(self, project_id: str) -> list[TaskCard]:
        """Explicitly refresh the cached collection for the project."""
        self.invalidate_cache(project_id)
        return self.get_tasks(project_id)

    def _auth_headers(self) -> dict[str, str]:
        """Generate Authorization header using documented Trello OAuth format."""
        return {
            "Authorization": f'OAuth oauth_consumer_key="{self._api_key}", oauth_token="{self._token}"',
            "Accept": "application/json",
        }

    def _fetch_lists(self) -> list[dict[str, Any]]:
        """Fetch and validate board lists."""
        url = f"https://api.trello.com/1/boards/{self._board_id}/lists/all"
        try:
            resp: HttpResponse = self._transport.get(url=url, headers=self._auth_headers())
        except SourceError as e:
            if e.provider != SourceProvider.trello:
                raise SourceError(SourceProvider.trello, e.category) from None
            raise
        except Exception:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.network) from None

        if not isinstance(resp.body, list):
            raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None

        if len(resp.body) > self._max_lists:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.limit_exceeded) from None

        seen_list_ids: set[str] = set()
        validated_lists: list[dict[str, Any]] = []

        for item in resp.body:
            if not isinstance(item, dict):
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None

            list_id = item.get("id")
            if not isinstance(list_id, str) or not SAFE_IDENTIFIER_RE.match(list_id):
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
            self._assert_no_credential_echo(list_id)

            if list_id in seen_list_ids:
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
            seen_list_ids.add(list_id)

            board_id = item.get("idBoard")
            if not isinstance(board_id, str) or board_id != self._board_id:
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
            self._assert_no_credential_echo(board_id)

            name = item.get("name")
            if not isinstance(name, str) or len(name) > self._max_text_length:
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None

            closed = item.get("closed")
            if type(closed) is not bool:
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None

            validated_lists.append({
                "id": list_id,
                "name": name,
                "closed": closed,
                "idBoard": board_id,
            })

        return validated_lists

    def _fetch_cards(self) -> list[dict[str, Any]]:
        """Fetch and validate board cards."""
        url = f"https://api.trello.com/1/boards/{self._board_id}/cards/all"
        try:
            resp: HttpResponse = self._transport.get(url=url, headers=self._auth_headers())
        except SourceError as e:
            if e.provider != SourceProvider.trello:
                raise SourceError(SourceProvider.trello, e.category) from None
            raise
        except Exception:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.network) from None

        if not isinstance(resp.body, list):
            raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None

        # Fail conservatively on ceiling or configured limit
        if len(resp.body) >= MAX_TRELLO_CARDS_CEILING or len(resp.body) > self._max_cards:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.limit_exceeded) from None

        seen_card_ids: set[str] = set()
        validated_cards: list[dict[str, Any]] = []

        for item in resp.body:
            if not isinstance(item, dict):
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None

            card_id = item.get("id")
            if not isinstance(card_id, str) or not SAFE_IDENTIFIER_RE.match(card_id):
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
            self._assert_no_credential_echo(card_id)

            if card_id in seen_card_ids:
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
            seen_card_ids.add(card_id)

            board_id = item.get("idBoard")
            if not isinstance(board_id, str) or board_id != self._board_id:
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
            self._assert_no_credential_echo(board_id)

            name = item.get("name")
            if not isinstance(name, str) or len(name) > self._max_text_length:
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None

            closed = item.get("closed")
            if type(closed) is not bool:
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None

            list_id = item.get("idList")
            if not isinstance(list_id, str) or not SAFE_IDENTIFIER_RE.match(list_id):
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
            self._assert_no_credential_echo(list_id)

            desc = item.get("desc")
            if desc is not None:
                if not isinstance(desc, str) or len(desc) > self._max_text_length:
                    raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None

            # Validate due date
            due = item.get("due")
            if due is not None:
                if not isinstance(due, str):
                    raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
                try:
                    parsed_due = datetime.fromisoformat(due)
                except Exception:
                    raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
                if parsed_due.tzinfo is None:
                    # Naive datetimes are strictly rejected
                    raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None

            # Validate dateLastActivity
            date_last_activity = item.get("dateLastActivity")
            act_dt_str: Optional[str] = None
            if date_last_activity is not None:
                if not isinstance(date_last_activity, str):
                    raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
                try:
                    parsed_act = datetime.fromisoformat(date_last_activity)
                except Exception:
                    raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
                if parsed_act.tzinfo is None:
                    raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
                act_dt_str = parsed_act.isoformat()

            # Validate labels
            labels_raw = item.get("labels", [])
            if not isinstance(labels_raw, list):
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
            card_labels: list[str] = []
            for lbl in labels_raw:
                if isinstance(lbl, dict):
                    lname = lbl.get("name")
                    if not isinstance(lname, str):
                        raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
                    card_labels.append(lname)
                elif isinstance(lbl, str):
                    card_labels.append(lbl)
                else:
                    raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None

            # Validate idMembers
            members_raw = item.get("idMembers", [])
            if not isinstance(members_raw, list):
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
            member_ids: list[str] = []
            for mem in members_raw:
                if not isinstance(mem, str) or not SAFE_IDENTIFIER_RE.match(mem):
                    raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
                self._assert_no_credential_echo(mem)
                member_ids.append(mem)

            # Validate dueComplete (optional; if present, strictly bool)
            due_complete: Optional[bool] = None
            if "dueComplete" in item:
                dc_raw = item["dueComplete"]
                if type(dc_raw) is not bool:
                    raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
                due_complete = dc_raw

            # Validate start date (optional; missing or None allowed; if present, non-empty ISO string with tz)
            start_raw: Optional[str] = None
            parsed_start: Optional[datetime] = None
            if "start" in item:
                st_val = item["start"]
                if st_val is not None:
                    if not isinstance(st_val, str) or not st_val.strip():
                        raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
                    self._assert_no_credential_echo(st_val)
                    try:
                        parsed_start = datetime.fromisoformat(st_val)
                    except Exception:
                        raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
                    if parsed_start.tzinfo is None:
                        raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
                    start_raw = st_val

            validated_cards.append({
                "id": card_id,
                "name": name,
                "desc": desc,
                "idList": list_id,
                "idBoard": board_id,
                "closed": closed,
                "due": due,
                "last_activity": act_dt_str,
                "labels": card_labels,
                "idMembers": member_ids,
                "dueComplete": due_complete,
                "start": start_raw,
                "start_dt": parsed_start,
            })

        return validated_cards

    def _ensure_collection(self, project_id: str) -> _CachedCollection:
        """Fetch and normalize cards and lists into a consistent cached collection."""
        if not isinstance(project_id, str) or not project_id.strip():
            raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration) from None

        if self._api_key in project_id or self._token in project_id:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration) from None

        if project_id in self._cache:
            self._cache.move_to_end(project_id)
            return self._cache[project_id]

        if self._clock is not None:
            collection_time = self._clock()
            if collection_time.tzinfo is None:
                raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration) from None
            collection_time = collection_time.astimezone(timezone.utc)
        else:
            collection_time = datetime.now(timezone.utc)

        # Retrieve both responses; fail-fast without modifying cache on any error
        raw_lists = self._fetch_lists()
        raw_cards = self._fetch_cards()

        lists_by_id = {l["id"]: l for l in raw_lists}

        # Check that every card's idList belongs to a known list on this board
        for c in raw_cards:
            if c["idList"] not in lists_by_id:
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None

        tasks: list[TaskCard] = []
        evidence: list[EvidenceItem] = []

        cfg = self._mapping_config
        blocked_labels_set = {b.strip().lower() for b in cfg.blocked_labels if b.strip()}

        for c in raw_cards:
            card_id = c["id"]
            list_id = c["idList"]
            list_obj = lists_by_id[list_id]
            list_name = list_obj["name"]

            # Sanitize texts against echoed secrets
            sanitized_title = self._sanitize_text(c["name"]) or ""
            sanitized_detail = self._sanitize_text(c["desc"])
            sanitized_list_name = self._sanitize_text(list_name)
            sanitized_labels = [self._sanitize_text(lbl) or "" for lbl in c["labels"]]

            has_blocked_label = any(lbl.strip().lower() in blocked_labels_set for lbl in sanitized_labels)

            # State mapping based on configured strategy
            if self._state_strategy == "date_fields":
                due_complete = c["dueComplete"]
                start_dt = c["start_dt"]
                is_future_start = (start_dt is not None and start_dt > collection_time)

                if due_complete is True:
                    state = TaskState.completed
                    has_contradiction = (has_blocked_label or is_future_start)
                elif due_complete is False:
                    if has_blocked_label:
                        state = TaskState.blocked
                        has_contradiction = False
                    elif start_dt is not None:
                        if is_future_start:
                            state = TaskState.pending
                            has_contradiction = False
                        else:
                            state = TaskState.in_progress
                            has_contradiction = False
                    else:
                        state = TaskState.unknown
                        has_contradiction = False
                else:
                    state = TaskState.unknown
                    has_contradiction = False
            else:
                is_completed_list = list_id in cfg.completed_list_ids
                is_in_progress_list = list_id in cfg.in_progress_list_ids
                is_pending_list = list_id in cfg.pending_list_ids

                if is_completed_list:
                    state = TaskState.completed
                    has_contradiction = has_blocked_label
                elif is_in_progress_list:
                    if has_blocked_label:
                        state = TaskState.blocked
                        has_contradiction = False
                    else:
                        state = TaskState.in_progress
                        has_contradiction = False
                elif is_pending_list:
                    if has_blocked_label:
                        state = TaskState.blocked
                        has_contradiction = False
                    else:
                        state = TaskState.pending
                        has_contradiction = False
                else:
                    state = TaskState.unknown
                    has_contradiction = False

            # Included status: closed cards and cards in closed lists are excluded from progress
            if c["closed"] or list_obj["closed"]:
                is_included = False
            elif cfg.included_list_ids is not None:
                is_included = (list_id in cfg.included_list_ids)
            else:
                is_included = True

            # Due date conversion
            due_dt: Optional[datetime] = None
            if c["due"] is not None:
                parsed = datetime.fromisoformat(c["due"])
                due_dt = normalize_datetime_to_tz(parsed, self._tz)

            task_card = TaskCard(
                id=card_id,
                title=sanitized_title,
                list_id=list_id,
                list_name=sanitized_list_name,
                labels=sanitized_labels,
                state=state,
                due_date=due_dt,
                detail=sanitized_detail,
                external_id=card_id,
                is_included=is_included,
                has_contradiction=has_contradiction,
            )
            tasks.append(task_card)

            # Canonical safe URL
            canonical_url = f"https://api.trello.com/1/cards/{card_id}"

            payload: dict[str, Any] = {
                "card_id": card_id,
                "title": sanitized_title,
                "state": state.value,
                "list_id": list_id,
                "list_name": sanitized_list_name,
                "labels": sanitized_labels,
                "member_ids": c["idMembers"],
                "due_date": due_dt.isoformat() if due_dt else None,
                "is_archived": c["closed"],
                "is_included": is_included,
                "has_contradiction": has_contradiction,
                "detail": sanitized_detail,
                "url": canonical_url,
            }
            if c.get("last_activity") is not None:
                payload["last_activity"] = c["last_activity"]
            if c.get("dueComplete") is not None:
                payload["due_complete"] = c["dueComplete"]
            if c.get("start") is not None:
                norm_start = normalize_datetime_to_tz(c["start_dt"], self._tz) if c.get("start_dt") else None
                payload["start_date"] = norm_start.isoformat() if norm_start else None
                payload["start_raw"] = self._sanitize_text(c["start"])

            provenance = EvidenceProvenance(
                id=f"trello-task-{card_id}",
                source=EvidenceSource.trello,
                project_id=project_id,
                external_id=card_id,
                timestamp=collection_time,
                payload=payload,
            )

            evidence_item = EvidenceItem(
                provenance=provenance,
                category="task",
                summary=f"Trello card {card_id}: {sanitized_title}",
            )
            evidence.append(evidence_item)

        collection = _CachedCollection(
            project_id=project_id,
            board_id=self._board_id,
            collected_at=collection_time,
            tasks=tuple(tasks),
            evidence=tuple(evidence),
        )

        # Enforce bounded memory cache
        if len(self._cache) >= MAX_CACHE_ENTRIES:
            self._cache.popitem(last=False)

        self._cache[project_id] = collection
        return collection

    def get_tasks(self, project_id: str) -> list[TaskCard]:
        """
        Retrieve normalized TaskCard items for the configured board.

        Returns deep copies from the cached collection to protect against caller mutation.
        """
        collection = self._ensure_collection(project_id)
        return [t.model_copy(deep=True) for t in collection.tasks]

    def get_evidence(self, project_id: str) -> list[EvidenceItem]:
        """
        Retrieve normalized EvidenceItem items corresponding to the collected tasks.

        Returns deep copies from the cached collection to protect against caller mutation.
        """
        collection = self._ensure_collection(project_id)
        return [e.model_copy(deep=True) for e in collection.evidence]
