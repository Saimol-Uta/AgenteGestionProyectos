"""Abstract port for task execution actions and external provider state mutation."""

from abc import ABC, abstractmethod
from typing import Any, Set


class TaskActionPort(ABC):
    """Port for read-before-write, field mutation, and post-verification on external task trackers."""

    @abstractmethod
    def supported_fields(self) -> Set[str]:
        """Set of domain field names natively supported for direct mutation by this provider."""
        raise NotImplementedError

    @abstractmethod
    def read_card_field(self, card_id: str, field: str) -> Any:
        """Read current live value of specified field directly from provider."""
        raise NotImplementedError

    @abstractmethod
    def apply_card_field_update(self, card_id: str, field: str, new_value: Any) -> bool:
        """Apply mutation on external card field; returns True on success, False on failure."""
        raise NotImplementedError

