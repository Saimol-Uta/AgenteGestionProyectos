"""Hybrid notification outbox with append-only receipts and optional SMTP delivery."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import tempfile
from typing import Callable, Optional
import uuid

from agente_gestion.adapters.outbox.smtp import (
    SMTPConfig,
    SMTPDeliveryError,
    safe_header,
    send_email,
    validate_email_address,
)
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.domain.notification_outbox import OutboxMessage, OutboxReceipt
from agente_gestion.ports.outbox import NotificationOutboxPort


class OutboxIdempotencyConflict(ValueError):
    """An email idempotency key was reused for a different request."""


class OutboxAttemptStateError(RuntimeError):
    """A durable email attempt record could not be safely interpreted."""


class HybridNotificationOutboxAdapter(NotificationOutboxPort):
    """Persist local artifacts and immutable receipts; optionally submit manager email.

    Legacy messages keep their original window-based duplicate behavior. Email messages
    opt in through a SHA-256 ``delivery_key`` and use a durable per-request attempt state,
    independent of the report window. SMTP attempts are never automatically repeated
    after the transport has entered its ambiguous ``sending`` state.
    """

    def __init__(
        self,
        storage: SQLiteStorageAdapter,
        outbox_dir: Path | str = ".runtime/outbox",
        *,
        smtp_config: SMTPConfig | None = None,
        smtp_factory: Callable[..., object] | None = None,
    ) -> None:
        self.storage = storage
        self.outbox_dir = Path(outbox_dir)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        self.smtp_config = smtp_config or SMTPConfig()
        self.smtp_factory = smtp_factory

    def deliver(self, message: OutboxMessage, force: bool = False) -> OutboxReceipt:
        email_message = self._email_message(message)
        if email_message is not None:
            return self._deliver_email(email_message, force=force)

        # Preserve the original project/window idempotency for every legacy channel.
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
                    detail=(f"Already delivered in previous dispatch ({existing.delivery_id}). "
                            "Use force=True to re-dispatch."),
                )

        delivery_id = f"dlv-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        file_path = self._write_legacy_artifacts(message, delivery_id, now)
        receipt = OutboxReceipt(
            delivery_id=delivery_id,
            message_id=message.message_id,
            project_id=message.project_id,
            window_id=message.window_id,
            channel=message.channel,
            status="DELIVERED",
            file_path=file_path,
            dispatched_at=now,
            detail="Delivered to file outbox and SQLite audit ledger",
        )
        self.storage.append_outbox_receipt(receipt, message, force=force)
        return receipt

    def lookup_email_attempt(
        self, project_id: str, delivery_key: str, delivery_fingerprint: str,
    ) -> OutboxReceipt | None:
        """Return a durable replay, resolving crash ambiguity without another SMTP send."""
        attempt_dir, state_path, lock_path = self._attempt_paths(project_id, delivery_key)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        with self._locked(lock_path):
            state = self._read_state(state_path)
            if state is None:
                return None
            self._check_fingerprint(state, project_id, delivery_key, delivery_fingerprint)
            if state["status"] == "prepared":
                return None
            if state["status"] == "sending":
                receipt = self._ambiguous_receipt(state)
                state.update(status="ambiguous", receipt_json=receipt.model_dump_json())
                self._save_state(state_path, state)
                self._append_once(receipt, state)
                return receipt
            if state["status"] not in {"sent", "failed", "ambiguous", "complete"}:
                raise OutboxAttemptStateError("The stored email attempt state is invalid.")
            receipt = self._receipt_from_state(state)
            self._append_once(receipt, state)
            return receipt

    def _email_message(self, message: OutboxMessage) -> OutboxMessage | None:
        if message.delivery_key is not None:
            return message
        recipient = message.recipient
        if self.smtp_config.manager_email and recipient in {None, "gestor_proyecto"}:
            recipient = self.smtp_config.manager_email
        if not recipient or "@" not in recipient:
            return None
        recipient = validate_email_address(recipient)
        keyed = message.model_copy(update={"recipient": recipient})
        key = hashlib.sha256(
            f"outbox-email\\0{message.project_id}\\0{message.message_id}".encode("utf-8")
        ).hexdigest()
        fingerprint = self._message_fingerprint(keyed)
        return keyed.model_copy(update={
            "delivery_key": key,
            "delivery_fingerprint": fingerprint,
        })

    def _deliver_email(self, message: OutboxMessage, *, force: bool) -> OutboxReceipt:
        delivery_key = message.delivery_key
        if delivery_key is None:
            raise ValueError("An email delivery key is required.")
        fingerprint = message.delivery_fingerprint or self._message_fingerprint(message)
        recipient = validate_email_address(message.recipient or "")
        safe_header(message.subject)
        html_body = message.body_html or self._plain_html(message)
        attempt_dir, state_path, lock_path = self._attempt_paths(message.project_id, delivery_key)
        attempt_dir.mkdir(parents=True, exist_ok=True)

        with self._locked(lock_path):
            state = self._read_state(state_path)
            if state is not None:
                self._check_fingerprint(state, message.project_id, delivery_key, fingerprint)
                status = state["status"]
                if status == "sending":
                    receipt = self._ambiguous_receipt(state)
                    state.update(status="ambiguous", receipt_json=receipt.model_dump_json())
                    self._save_state(state_path, state)
                    self._append_once(receipt, state)
                    return receipt
                if status in {"sent", "failed", "ambiguous", "complete"}:
                    receipt = self._receipt_from_state(state)
                    self._append_once(receipt, state)
                    return receipt
                if status != "prepared":
                    raise OutboxAttemptStateError("The stored email attempt state is invalid.")
            else:
                delivery_id = f"dlv-{uuid.uuid4().hex[:12]}"
                now = datetime.now(timezone.utc)
                state = {
                    "version": 1,
                    "project_id": message.project_id,
                    "delivery_key": delivery_key,
                    "delivery_fingerprint": fingerprint,
                    "delivery_id": delivery_id,
                    "message_id": message.message_id,
                    "window_id": message.window_id,
                    "channel": message.channel,
                    "status": "prepared",
                    "file_path": None,
                    "dispatched_at": now.isoformat(),
                    "receipt_json": None,
                    "message_json": message.model_dump_json(),
                }
                self._save_state(state_path, state)

            delivery_id = state["delivery_id"]
            file_path = self._write_legacy_artifacts(message, delivery_id, datetime.fromisoformat(state["dispatched_at"]))
            html_path = self.outbox_dir / f"{delivery_id}.html"
            self._write_text(html_path, html_body)
            state["file_path"] = file_path
            self._save_state(state_path, state)

            if self.smtp_config.is_configured:
                state["status"] = "sending"
                self._save_state(state_path, state)
                try:
                    send_email(
                        self.smtp_config,
                        recipient=recipient,
                        subject=message.subject,
                        plain_text=message.body_markdown,
                        html_text=html_body,
                        smtp_factory=self.smtp_factory,
                    )
                except SMTPDeliveryError:
                    receipt = self._make_receipt(
                        state,
                        status="FAILED",
                        delivery_channel="smtp_email_failed",
                        detail=("SMTP did not confirm the message. The outcome may be ambiguous; "
                                "use a new explicit idempotency key for an intentional retry."),
                    )
                    state.update(status="failed", receipt_json=receipt.model_dump_json())
                    self._save_state(state_path, state)
                    self._append_once(receipt, state)
                    return receipt
                receipt = self._make_receipt(
                    state,
                    status="DELIVERED",
                    delivery_channel="smtp_email_sent",
                    detail="SMTP relay accepted the message; final mailbox delivery is not confirmed.",
                )
            else:
                receipt = self._make_receipt(
                    state,
                    status="DELIVERED",
                    delivery_channel="local_outbox_simulated_email",
                    detail="SMTP is disabled or incomplete; HTML email was stored in the local outbox.",
                )

            # Persist relay/local acceptance before SQLite append. A retry repairs a
            # missing receipt from this state instead of submitting the email again.
            state.update(status="sent", receipt_json=receipt.model_dump_json())
            self._save_state(state_path, state)
            self._append_once(receipt, state)
            return receipt

    def _write_legacy_artifacts(
        self, message: OutboxMessage, delivery_id: str, now: datetime,
    ) -> Optional[str]:
        if message.channel not in ("file", "all"):
            return None
        safe_win = message.window_id[:16]
        dest_json = self.outbox_dir / f"{message.project_id}_{safe_win}_{delivery_id}.json"
        dest_md = self.outbox_dir / f"{message.project_id}_{safe_win}_{delivery_id}.md"
        self._write_text(dest_json, message.model_dump_json(indent=2))
        markdown = (
            f"# {message.subject}\n\n"
            f"**Fecha de despacho:** {now.isoformat()}\n"
            f"**Proyecto:** {message.project_id}\n"
            f"**Ventana semanal:** `{message.window_id}`\n\n"
            f"## Resumen Ejecutivo\n\n{message.summary}\n\n"
            f"## Contenido Detallado\n\n{message.body_markdown}\n"
        )
        self._write_text(dest_md, markdown)
        return str(dest_json)

    def _make_receipt(
        self,
        state: dict,
        *,
        status: str,
        delivery_channel: str,
        detail: str,
    ) -> OutboxReceipt:
        return OutboxReceipt(
            delivery_id=state["delivery_id"],
            message_id=state["message_id"],
            project_id=state["project_id"],
            window_id=state["window_id"],
            channel=state["channel"],
            status=status,
            file_path=state.get("file_path"),
            dispatched_at=datetime.fromisoformat(state["dispatched_at"]),
            detail=detail,
            delivery_channel=delivery_channel,
        )

    def _ambiguous_receipt(self, state: dict) -> OutboxReceipt:
        return self._make_receipt(
            state,
            status="FAILED",
            delivery_channel="smtp_email_failed",
            detail=("Process interrupted during SMTP submission; relay acceptance is unknown. "
                    "No automatic resend was attempted."),
        )

    def _receipt_from_state(self, state: dict) -> OutboxReceipt:
        raw = state.get("receipt_json")
        if not isinstance(raw, str):
            raise OutboxAttemptStateError("The stored email receipt is unavailable.")
        try:
            return OutboxReceipt.model_validate_json(raw)
        except ValueError:
            raise OutboxAttemptStateError("The stored email receipt is invalid.") from None

    def _append_once(self, receipt: OutboxReceipt, state: dict) -> None:
        existing = next(
            (item for item in self.storage.list_outbox_receipts(receipt.project_id)
             if item.delivery_id == receipt.delivery_id),
            None,
        )
        if existing is None:
            # Stored messages are immutable audit material. Reconstructing the message
            # from the receipt's request is unnecessary: each state also stores its JSON.
            message_raw = state.get("message_json")
            if not isinstance(message_raw, str):
                raise OutboxAttemptStateError("The stored email message is unavailable.")
            try:
                message = OutboxMessage.model_validate_json(message_raw)
            except ValueError:
                raise OutboxAttemptStateError("The stored email message is invalid.") from None
            self.storage.append_outbox_receipt(receipt, message, force=True)
        if state.get("status") in {"sent", "failed", "ambiguous"}:
            state["status"] = "complete"
            self._save_state(self._state_path_for_state(state), state)

    @staticmethod
    def _message_fingerprint(message: OutboxMessage) -> str:
        values = message.model_dump(mode="json", exclude={"created_at", "delivery_fingerprint"})
        canonical = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _attempt_paths(self, project_id: str, delivery_key: str) -> tuple[Path, Path, Path]:
        digest = hashlib.sha256(f"{project_id}\0{delivery_key}".encode("utf-8")).hexdigest()
        attempt_dir = self.outbox_dir / ".email-attempts"
        return attempt_dir, attempt_dir / f"{digest}.json", attempt_dir / f"{digest}.lock"

    def _state_path_for_state(self, state: dict) -> Path:
        return self._attempt_paths(state["project_id"], state["delivery_key"])[1]

    @contextmanager
    def _locked(self, lock_path: Path):
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _read_state(path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise OutboxAttemptStateError("The durable email attempt state is unreadable.") from None
        if not isinstance(state, dict) or state.get("version") != 1:
            raise OutboxAttemptStateError("The durable email attempt state is invalid.")
        return state

    @staticmethod
    def _check_fingerprint(
        state: dict, project_id: str, delivery_key: str, delivery_fingerprint: str,
    ) -> None:
        if state.get("project_id") != project_id or state.get("delivery_key") != delivery_key:
            raise OutboxAttemptStateError("The durable email attempt scope is invalid.")
        if state.get("delivery_fingerprint") != delivery_fingerprint:
            raise OutboxIdempotencyConflict("The email idempotency key was used for another request.")

    @staticmethod
    def _save_state(path: Path, state: dict) -> None:
        encoded = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            raise OutboxAttemptStateError("The durable email attempt state could not be stored.") from None

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _plain_html(message: OutboxMessage) -> str:
        return (
            "<!doctype html><html lang=\"es\"><meta charset=\"utf-8\"><body>"
            f"<h1>{html.escape(message.subject)}</h1>"
            f"<p>{html.escape(message.summary)}</p>"
            f"<pre>{html.escape(message.body_markdown)}</pre></body></html>"
        )

    def list_receipts(self, project_id: str) -> list[OutboxReceipt]:
        return self.storage.list_outbox_receipts(project_id)

    def get_receipt_for_window(self, project_id: str, window_id: str) -> Optional[OutboxReceipt]:
        return self.storage.get_outbox_receipt_for_window(project_id, window_id)
