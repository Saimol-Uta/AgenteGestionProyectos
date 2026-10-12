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

    Explicitly rejected:
    - 'priority': Trello cards have no native priority field.
    - 'state': Trello lists represent phases in HORMIGESTION, not lifecycle states.
    """

    SUPPORTED_FIELDS: Set[str] = {"due_date", "start_date", "assignee_ids"}

    def __init__(
        self,
        board_id: str,
        api_key: str,
        token: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._board_id = _validate_safe_identifier(board_id)
        if not api_key or not isinstance(api_key, str) or not api_key.strip():
            raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration)
        if not token or not isinstance(token, str) or not token.strip():
            raise SourceError(SourceProvider.trello, SourceErrorCategory.configuration)
        self._api_key = api_key.strip()
        self._token = token.strip()
        self._timeout_seconds = max(1.0, float(timeout_seconds))

    def supported_fields(self) -> Set[str]:
        return set(self.SUPPORTED_FIELDS)

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f'OAuth oauth_consumer_key="{self._api_key}", oauth_token="{self._token}"',
            "Accept": "application/json",
        }

    def __repr__(self) -> str:
        return f"TrelloActionAdapter(board_id={self._board_id!r})"

    def read_card_field(self, card_id: str, field: str) -> Any:
        clean_card_id = _validate_safe_identifier(card_id)
        if field not in self.SUPPORTED_FIELDS:
            raise ValueError(f"Field '{field}' is not supported for Trello operations")

        url = f"https://api.trello.com/1/cards/{clean_card_id}"
        try:
            resp = requests.get(
                url,
                headers=self._auth_headers(),
                params={"fields": "due,start,idMembers,idBoard"},
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
        if resp.status_code != 200:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.unexpected_status)

        try:
            data = resp.json()
        except Exception:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload)

        if not isinstance(data, dict):
            raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload)

        # Cross-check board binding
        if data.get("idBoard") and data.get("idBoard") != self._board_id:
            raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload)

        if field == "due_date":
            return _parse_trello_datetime(data.get("due"))
        elif field == "start_date":
            return _parse_trello_datetime(data.get("start"))
        elif field == "assignee_ids":
            members = data.get("idMembers")
            if members is None:
                return []
            if not isinstance(members, list):
                raise SourceError(SourceProvider.trello, SourceErrorCategory.malformed_payload)
            return [str(m) for m in members]

        return None

    def apply_card_field_update(self, card_id: str, field: str, new_value: Any) -> bool:
        clean_card_id = _validate_safe_identifier(card_id)
        if field not in self.SUPPORTED_FIELDS:
            raise ValueError(f"Field '{field}' is not supported for Trello operations")

        payload: dict[str, Any] = {}
        if field == "due_date":
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
        if resp.status_code != 200:
            return False

        return True

