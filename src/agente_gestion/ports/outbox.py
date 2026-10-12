"""Port interface for notification outbox dispatch and inspection (H3)."""

from abc import ABC, abstractmethod
from typing import Optional

from agente_gestion.domain.notification_outbox import OutboxMessage, OutboxReceipt


class NotificationOutboxPort(ABC):
    """Port for dispatching scheduled notifications and recording delivery receipts."""

    @abstractmethod
    def deliver(self, message: OutboxMessage, force: bool = False) -> OutboxReceipt:
        """Deliver message through configured channels, respecting delivery idempotency unless force=True."""
        raise NotImplementedError

    @abstractmethod
    def list_receipts(self, project_id: str) -> list[OutboxReceipt]:
        """List historical delivery receipts for the specified project."""
        raise NotImplementedError

    @abstractmethod
    def get_receipt_for_window(self, project_id: str, window_id: str) -> Optional[OutboxReceipt]:
        """Check if an outbox delivery was already dispatched for the given project and window."""
        raise NotImplementedError

