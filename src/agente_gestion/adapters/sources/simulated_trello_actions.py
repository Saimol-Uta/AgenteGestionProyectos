"""Simulated in-memory Trello action adapter for safe offline execution and verification testing."""

from datetime import datetime
from typing import Any, Optional, Set
from zoneinfo import ZoneInfo

from agente_gestion.ports.task_actions import TaskActionPort

DEFAULT_TIMEZONE = ZoneInfo("America/Guayaquil")


class SimulatedTrelloActionAdapter(TaskActionPort):
    """
    In-memory test double representing Trello's native card mutation capabilities.

    Explicit domain field mapping rules for Trello:
    - Supported:
      * 'due_date': maps to native Trello 'due' (datetime)
      * 'start_date': maps to native Trello 'start' (datetime)
      * 'assignee_ids': maps to native Trello 'idMembers' (list[str])
    - Rejected:
      * 'priority': Trello cards have no native priority field.
      * 'state': In phase-based boards like HORMIGESTION, lists represent phases,
        not lifecycle states. Direct mutation of 'state' is rejected.
    """

    SUPPORTED_FIELDS: Set[str] = {"due_date", "start_date", "assignee_ids"}

    def __init__(self, initial_cards: Optional[dict[str, dict[str, Any]]] = None) -> None:
        # card_id -> {field_name: value}
        self._cards: dict[str, dict[str, Any]] = {}
        if initial_cards:
            for cid, fields in initial_cards.items():
                self._cards[cid] = dict(fields)
        self.mutation_log: list[dict[str, Any]] = []
        self.simulate_network_error: bool = False
        self.simulate_read_error: bool = False
        self.simulate_write_failure: bool = False

    def supported_fields(self) -> Set[str]:
        return set(self.SUPPORTED_FIELDS)

    def set_card_field(self, card_id: str, field: str, value: Any) -> None:
        """Helper to seed card state for testing."""
        if card_id not in self._cards:
            self._cards[card_id] = {}
        self._cards[card_id][field] = value

    def read_card_field(self, card_id: str, field: str) -> Any:
        if self.simulate_network_error or self.simulate_read_error:
            raise ConnectionError("Simulated Trello read transport failure")
        if card_id not in self._cards:
            raise KeyError(f"Card {card_id} not found on simulated board")
        return self._cards[card_id].get(field)

    def apply_card_field_update(self, card_id: str, field: str, new_value: Any) -> bool:
        if self.simulate_network_error:
            raise ConnectionError("Simulated Trello write transport failure")
        if self.simulate_write_failure:
            return False
        if field not in self.SUPPORTED_FIELDS:
            raise ValueError(f"Field '{field}' is not a natively supported Trello mutation field")
        if card_id not in self._cards:
            raise KeyError(f"Card {card_id} not found on simulated board")

        old_val = self._cards[card_id].get(field)
        self._cards[card_id][field] = new_value
        self.mutation_log.append({
            "card_id": card_id,
            "field": field,
            "old_value": old_val,
            "new_value": new_value,
            "timestamp": datetime.now(DEFAULT_TIMEZONE).isoformat(),
        })
        return True

