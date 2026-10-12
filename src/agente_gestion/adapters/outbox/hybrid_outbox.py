"""Hybrid notification outbox adapter delivering to file and SQLite audit ledger (H3)."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Optional
import uuid

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.domain.notification_outbox import OutboxMessage, OutboxReceipt
from agente_gestion.ports.outbox import NotificationOutboxPort


class HybridNotificationOutboxAdapter(NotificationOutboxPort):
    """
    Outbox adapter persisting notification deliveries to:
    1. Local structured files (.json and .md receipts in configured outbox directory).
    2. Immutable SQLite notification_outbox table for query and audit trail.
    Enforces strict window idempotency: duplicate deliveries for the same window are skipped
    unless force=True is explicitly supplied.
    """

    def __init__(
        self,
        storage: SQLiteStorageAdapter,
        outbox_dir: Path | str = ".runtime/outbox",
    ) -> None:
        self.storage = storage
        self.outbox_dir = Path(outbox_dir)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)

    def deliver(self, message: OutboxMessage, force: bool = False) -> OutboxReceipt:
        # Check delivery idempotency for this project and window
        if not force:
            existing = self.storage.get_outbox_receipt_for_window(
                message.project_id, message.window_id
            )
            if existing is not None:
                return OutboxReceipt(
                    delivery_id=f"skip-{uuid.uuid4().hex[:12]}",
                    message_id=message.message_id,
                    project_id=message.project_id,
                    window_id=message.window_id,
                    channel=message.channel,
                    status="SKIPPED_DUPLICATE",
                    file_path=existing.file_path,
                    dispatched_at=datetime.now(timezone.utc),
                    detail=f"Already delivered in previous dispatch ({existing.delivery_id}). Use force=True to re-dispatch.",
                )

        delivery_id = f"dlv-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        # 1. Write file artifact if channel in ('file', 'all')
        file_path_str: Optional[str] = None
        if message.channel in ("file", "all"):
            safe_win = message.window_id[:16]
            file_name_json = f"{message.project_id}_{safe_win}_{delivery_id}.json"
            file_name_md = f"{message.project_id}_{safe_win}_{delivery_id}.md"

            dest_json = self.outbox_dir / file_name_json
            dest_md = self.outbox_dir / file_name_md

            with open(dest_json, "w", encoding="utf-8") as f:
                f.write(message.model_dump_json(indent=2))

            with open(dest_md, "w", encoding="utf-8") as f:
                f.write(f"# {message.subject}\n\n")
                f.write(f"**Fecha de despacho:** {now.isoformat()}\n")
                f.write(f"**Proyecto:** {message.project_id}\n")
                f.write(f"**Ventana semanal:** `{message.window_id}`\n\n")
                f.write(f"## Resumen Ejecutivo\n\n{message.summary}\n\n")
                f.write(f"## Contenido Detallado\n\n{message.body_markdown}\n")

            file_path_str = str(dest_json)

        # 2. Record receipt in SQLite
        receipt = OutboxReceipt(
            delivery_id=delivery_id,
            message_id=message.message_id,
            project_id=message.project_id,
            window_id=message.window_id,
            channel=message.channel,
            status="DELIVERED",
            file_path=file_path_str,
            dispatched_at=now,
            detail="Delivered to file outbox and SQLite audit ledger",
        )

        self.storage.append_outbox_receipt(receipt, message, force=force)
        return receipt

    def list_receipts(self, project_id: str) -> list[OutboxReceipt]:
        return self.storage.list_outbox_receipts(project_id)

    def get_receipt_for_window(self, project_id: str, window_id: str) -> Optional[OutboxReceipt]:
        return self.storage.get_outbox_receipt_for_window(project_id, window_id)

