"""Abstract port for task execution actions and external provider state mutation."""

from abc import ABC, abstractmethod
from typing import Any, Set


class TaskActionPort(ABC):
    def supports_comments(self) -> bool:
        return False

    def append_card_comment(self, card_id: str, text: str) -> str:
        """Append once; a missing receipt is uncertain, never permission to retry."""
        raise NotImplementedError

    def read_card_comment(self, card_id: str, action_id: str) -> str:
        raise NotImplementedError

    def read_comment_target(self, card_id: str) -> dict:
        raise NotImplementedError

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
