"""Live bounded Trello action adapter for card field reading and safe mutation (H2)."""

from datetime import datetime, timezone
import re
from typing import Any, Optional, Set
from zoneinfo import ZoneInfo
import requests

from agente_gestion.ports.task_actions import TaskActionPort
from agente_gestion.ports.source_errors import (
    SourceError,
    SourceErrorCategory,
    SourceProvider,
)

SAFE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
_PRIORITY_ALIASES = {
    "alta": "high", "high": "high", "critical": "high", "critica": "high",
    "media": "medium", "medium": "medium", "baja": "low", "low": "low",
}
_PRIORITY_LEVELS = {"high", "medium", "low"}


def priority_key(value: Any) -> str | None:
    return _PRIORITY_ALIASES.get(value.strip().lower()) if isinstance(value, str) else None


def validate_priority_labels(priority_labels: Optional[dict[str, str]]) -> tuple[dict[str, str], dict[str, str]]:
    """Validate a complete, one-to-one project priority-to-label map."""
    if priority_labels is None or priority_labels == {}:
        return {}, {}
    if not isinstance(priority_labels, dict):
        raise ValueError("Trello priority label mapping must be a mapping")
    labels: dict[str, str] = {}
    names: dict[str, str] = {}
    for name, label_id in priority_labels.items():
        key = priority_key(name)
        if key is None or key in labels:
            raise ValueError("Trello priority label mapping contains an invalid or repeated priority")
        if not isinstance(label_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", label_id) is None:
            raise ValueError("Trello priority label IDs must be safe identifiers")
        labels[key] = label_id
        names[key] = name
    if set(labels) != _PRIORITY_LEVELS or len(set(labels.values())) != len(labels):
        raise ValueError("Trello priority label mapping must define unique high, medium, and low labels")
    return labels, names
DEFAULT_TIMEZONE = ZoneInfo("America/Guayaquil")
DEFAULT_TIMEOUT_SECONDS = 10.0


def _validate_safe_identifier(val: Any) -> str:
    if not isinstance(val, str) or not SAFE_IDENTIFIER_RE.match(val):
        raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration)
    return val


def _parse_trello_datetime(raw: Optional[str]) -> Optional[datetime]:
    if not raw or not isinstance(raw, str):
        return None
    try:
        norm = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
        dt = datetime.fromisoformat(norm)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(DEFAULT_TIMEZONE)
    except Exception:
        return None


class TrelloActionAdapter(TaskActionPort):
    """
    Live adapter executing bounded read and mutation operations against Trello cards.

    Supported domain fields:
    - 'due_date' -> maps to native Trello 'due' (ISO 8601 string / null)
    - 'start_date' -> maps to native Trello 'start' (ISO 8601 string / null)
    - 'assignee_ids' -> maps to native Trello 'idMembers' (list of member IDs)
    - 'priority' -> maps to configured project labels in native Trello 'idLabels'

    Explicitly rejected:
    - 'priority' without a complete project-specific label map.
    - 'state': Trello lists represent phases in HORMIGESTION, not lifecycle states.
    """

    SUPPORTED_FIELDS: Set[str] = {"due_date", "start_date", "assignee_ids"}

    def __init__(
        self,
        board_id: str,
        api_key: str,
        token: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        priority_labels: Optional[dict[str, str]] = None,
        allow_comment_writes: bool = False,
    ) -> None:
        self._board_id = _validate_safe_identifier(board_id)
        if not api_key or not isinstance(api_key, str) or not api_key.strip():
            raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration)
        if not token or not isinstance(token, str) or not token.strip():
            raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration)
        self._api_key = api_key.strip()
        self._token = token.strip()
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._priority_labels, self._priority_names = validate_priority_labels(priority_labels)
        self._allow_comment_writes = allow_comment_writes is True

    def supported_fields(self) -> Set[str]:
        fields = set(self.SUPPORTED_FIELDS)
        if self._priority_labels:
            fields.add("priority")
        return fields

    def supports_comments(self) -> bool:
        return True

    def read_comment_target(self, card_id: str) -> dict:
        data = self._request_card_data(_validate_safe_identifier(card_id), "idBoard,idList,closed,due,dueComplete,idLabels")
        list_id = _validate_safe_identifier(data.get("idList"))
        response = requests.get(f"https://api.trello.com/1/lists/{list_id}", params={"fields": "closed,idBoard"},
                                headers=self._auth_headers(), timeout=self._timeout_seconds, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError("Cannot verify current list inclusion")
        current_list = response.json()
        if current_list.get("idBoard") != self._board_id or current_list.get("closed") is not False:
            raise ValueError("Comment target list is unavailable or archived")
        return {"is_archived": data.get("closed"), "due_complete": data.get("dueComplete"),
                "is_included": True,
                "due_date": data.get("due"), "list_id": data.get("idList"),
                "priority_label_ids": data.get("idLabels", [])}

    def append_card_comment(self, card_id: str, text: str) -> str:
        from agente_gestion.domain.comment_action import comment_text
        if not self._allow_comment_writes:
            raise ValueError("External comment writes are disabled")
        card_id = _validate_safe_identifier(card_id)
        comment_text(text)
        self._request_card_data(card_id, "idBoard")
        # Exactly one POST. No retry and no sandbox fallback after uncertain delivery.
        response = requests.post(f"https://api.trello.com/1/cards/{card_id}/actions/comments",
            headers=self._auth_headers(), data={"text": text}, timeout=self._timeout_seconds, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError("Comment delivery unconfirmed; reconcile before retrying")
        data = response.json()
        return _validate_safe_identifier(data.get("id"))

    def read_card_comment(self, card_id: str, action_id: str) -> str:
        card_id, action_id = _validate_safe_identifier(card_id), _validate_safe_identifier(action_id)
        response = requests.get(f"https://api.trello.com/1/actions/{action_id}", headers=self._auth_headers(),
                                timeout=self._timeout_seconds, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError("Comment read-back unavailable")
        action = response.json()
        data = action.get("data", {})
        if (action.get("id") != action_id or action.get("type") != "commentCard"
                or data.get("card", {}).get("id") != card_id or data.get("board", {}).get("id") != self._board_id):
            raise ValueError("Comment read-back target mismatch")
        return data["text"]

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f'OAuth oauth_consumer_key="{self._api_key}", oauth_token="{self._token}"',
            "Accept": "application/json",
        }

    def __repr__(self) -> str:
        return f"TrelloActionAdapter(board_id={self._board_id!r})"

    def has_write_permission(self) -> bool:
        """Check board membership before mutation; denied access selects sandbox."""
        def read(path: str, params: dict[str, str]):
            try:
                response = requests.get(
                    "https://api.trello.com/1" + path,
                    headers=self._auth_headers(), params=params,
                    timeout=self._timeout_seconds, allow_redirects=False,
                )
            except requests.exceptions.Timeout:
                raise SourceError(SourceProvider.trello, SourceErrorCategory.timeout) from None
            except requests.RequestException:
                raise SourceError(SourceProvider.trello, SourceErrorCategory.network) from None
            if response.status_code in {401, 403, 404}:
                return None
            if response.status_code != 200:
                raise SourceError(SourceProvider.trello, SourceErrorCategory.unexpected_status)
            try:
                return response.json()
            except (TypeError, ValueError):
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None

        member = read("/members/me", {"fields": "id"})
        if member is None:
            return False
        if not isinstance(member, dict) or not isinstance(member.get("id"), str):
            raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload)
        board = read(f"/boards/{self._board_id}", {"fields": "id,closed"})
        if board is None:
            return False
        if (not isinstance(board, dict) or board.get("id") != self._board_id
                or not isinstance(board.get("closed"), bool)):
            raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload)
        if board["closed"]:
            return False
        memberships = read(f"/boards/{self._board_id}/memberships", {})
        if memberships is None:
            return False
        if (not isinstance(memberships, list)
                or any(not isinstance(item, dict) for item in memberships)):
            raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload)
        return any(
            item.get("idMember") == member["id"]
            and item.get("memberType") in {"admin", "normal"}
            and item.get("deactivated") is False
            for item in memberships
        )

    def _request_card_data(self, clean_card_id: str, fields: str) -> dict[str, Any]:
        url = f"https://api.trello.com/1/cards/{clean_card_id}"
        try:
            resp = requests.get(
                url,
                headers=self._auth_headers(),
                params={"fields": fields},
                timeout=self._timeout_seconds,
            )
        except requests.exceptions.Timeout:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.timeout) from None
        except Exception:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.network) from None
        if resp.status_code == 404:
            raise KeyError(f"Card {clean_card_id} not found on Trello")
        if resp.status_code in (401, 403):
            raise SourceError(SourceProvider.trello, SourceErrorCategory.authentication)
        if resp.status_code != 200:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.unexpected_status)
        try:
            data = resp.json()
        except Exception:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload) from None
        if not isinstance(data, dict):
            raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload)
        if data.get("idBoard") != self._board_id:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload)
        return data

    def read_card_field(self, card_id: str, field: str) -> Any:
        clean_card_id = _validate_safe_identifier(card_id)
        if field not in self.supported_fields():
            raise ValueError(f"Field '{field}' is not supported for Trello operations")

        data = self._request_card_data(
            clean_card_id, "due,start,idMembers,idBoard,idLabels",
        )
        if field == "due_date":
            return _parse_trello_datetime(data.get("due"))
        if field == "start_date":
            return _parse_trello_datetime(data.get("start"))
        if field == "assignee_ids":
            members = data.get("idMembers")
            if members is None:
                return []
            if not isinstance(members, list):
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload)
            return [str(member) for member in members]
        if field == "priority":
            labels = data.get("idLabels")
            if not isinstance(labels, list) or any(not isinstance(item, str) for item in labels):
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload)
            selected = [key for key, label_id in self._priority_labels.items() if label_id in labels]
            if len(selected) > 1:
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload)
            return self._priority_names[selected[0]] if selected else None
        return None

    def apply_card_field_update(self, card_id: str, field: str, new_value: Any) -> bool:
        clean_card_id = _validate_safe_identifier(card_id)
        if field not in self.supported_fields():
            raise ValueError(f"Field '{field}' is not supported for Trello operations")

        payload: dict[str, Any] = {}
        if field == "priority":
            priority = priority_key(new_value)
            if priority not in self._priority_labels:
                raise ValueError("Priority value has no configured Trello label")
            card = self._request_card_data(clean_card_id, "idLabels,idBoard")
            labels = card.get("idLabels")
            if not isinstance(labels, list) or any(not isinstance(item, str) for item in labels):
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload)
            configured_ids = set(self._priority_labels.values())
            payload["idLabels"] = [item for item in labels if item not in configured_ids]
            payload["idLabels"].append(self._priority_labels[priority])
        elif field == "due_date":
            if new_value is None:
                payload["due"] = None
            elif isinstance(new_value, datetime):
                # Normalize to UTC ISO format for Trello
                utc_dt = new_value.astimezone(timezone.utc) if new_value.tzinfo else new_value.replace(tzinfo=timezone.utc)
                payload["due"] = utc_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            elif isinstance(new_value, str):
                payload["due"] = new_value
            else:
                raise ValueError(f"Invalid due_date format: {type(new_value)}")
        elif field == "start_date":
            if new_value is None:
                payload["start"] = None
            elif isinstance(new_value, datetime):
                utc_dt = new_value.astimezone(timezone.utc) if new_value.tzinfo else new_value.replace(tzinfo=timezone.utc)
                payload["start"] = utc_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            elif isinstance(new_value, str):
                payload["start"] = new_value
            else:
                raise ValueError(f"Invalid start_date format: {type(new_value)}")
        elif field == "assignee_ids":
            if not isinstance(new_value, (list, tuple)):
                raise ValueError(f"assignee_ids must be a sequence of member IDs, got {type(new_value)}")
            payload["idMembers"] = list(new_value)

        url = f"https://api.trello.com/1/cards/{clean_card_id}"
        headers = self._auth_headers()
        headers["Content-Type"] = "application/json"

        try:
            resp = requests.put(
                url,
                headers=headers,
                json=payload,
                timeout=self._timeout_seconds,
            )
        except requests.exceptions.Timeout:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.timeout) from None
        except Exception:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.network) from None

        if resp.status_code == 404:
            raise KeyError(f"Card {card_id} not found on Trello")
        if resp.status_code in (401, 403):
            raise SourceError(SourceProvider.trello, SourceErrorCategory.authentication)
        if resp.status_code >= 500:
            # The provider may have applied a write before returning an error.
            # Surface an uncertain outcome; callers must never retry as sandbox success.
            raise SourceError(SourceProvider.trello, SourceErrorCategory.unexpected_status)
        if resp.status_code != 200:
            return False

        return True
