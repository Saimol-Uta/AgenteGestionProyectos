"""Explicit in-memory TaskActionPort for fixture and offline proposal execution."""

from datetime import datetime
from typing import Any, Optional, Set

from agente_gestion.adapters.sources.trello_action_adapter import priority_key, validate_priority_labels
from agente_gestion.ports.task_actions import TaskActionPort


class SandboxTaskActions(TaskActionPort):
    """Simulate bounded task updates without making provider calls or claiming live writes."""

    BASE_FIELDS: Set[str] = {"due_date", "start_date", "assignee_ids"}

    def __init__(
        self,
        initial_cards: Optional[dict[str, dict[str, Any]]] = None,
        *,
        priority_labels: Optional[dict[str, str]] = None,
    ) -> None:
        self._cards = {card_id: dict(values) for card_id, values in (initial_cards or {}).items()}
        self._priority_labels, self._priority_names = validate_priority_labels(priority_labels)
        self.mutation_log: list[dict[str, Any]] = []
        self.simulate_read_error = False
        self.simulate_write_failure = False

    def supported_fields(self) -> Set[str]:
        return self.BASE_FIELDS | ({"priority"} if self._priority_labels else set())

    def set_card_field(self, card_id: str, field: str, value: Any) -> None:
        self._cards.setdefault(card_id, {})[field] = value

    def read_card_field(self, card_id: str, field: str) -> Any:
        if self.simulate_read_error:
            raise ConnectionError("Sandbox action read failed")
        if field not in self.supported_fields():
            raise ValueError(f"Field '{field}' is not supported in sandbox execution")
        if card_id not in self._cards:
            raise KeyError(f"Card {card_id} not found in sandbox")
        return self._cards[card_id].get(field)

    def apply_card_field_update(self, card_id: str, field: str, new_value: Any) -> bool:
        if self.simulate_write_failure:
            return False
        if field not in self.supported_fields():
            raise ValueError(f"Field '{field}' is not supported in sandbox execution")
        if card_id not in self._cards:
            raise KeyError(f"Card {card_id} not found in sandbox")
        old_value = self._cards[card_id].get(field)
        record: dict[str, Any] = {
            "card_id": card_id, "field": field,
            "old_value": old_value, "new_value": new_value,
            "timestamp": datetime.now().astimezone().isoformat(),
        }
        if field == "priority":
            normalized = priority_key(new_value)
            if normalized not in self._priority_labels:
                raise ValueError("Priority value has no configured Trello label")
            labels = self._cards[card_id].get("idLabels", [])
            if not isinstance(labels, list) or any(not isinstance(item, str) for item in labels):
                raise ValueError("Sandbox label state is invalid")
            configured_ids = set(self._priority_labels.values())
            updated_labels = [item for item in labels if item not in configured_ids]
            updated_labels.append(self._priority_labels[normalized])
            self._cards[card_id]["idLabels"] = updated_labels
            self._cards[card_id][field] = self._priority_names[normalized]
            record["preserved_label_ids"] = [item for item in labels if item not in configured_ids]
            record["resulting_label_ids"] = updated_labels
        else:
            self._cards[card_id][field] = list(new_value) if field == "assignee_ids" and isinstance(new_value, (tuple, list)) else new_value
        self.mutation_log.append(record)
        return True
