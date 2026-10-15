"""Explicit SMTP configuration and stdlib transport for manager notifications.

This module never loads dotenv files. Environment reads happen only when callers
explicitly invoke ``SMTPConfig.from_env``.
"""

from dataclasses import dataclass, field
from email.message import EmailMessage
import os
import re
import smtplib
import ssl
from typing import Callable

from pydantic import SecretStr


_EMAIL = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$"
)


class SMTPDeliveryError(RuntimeError):
    """Transport failure with deliberately secret-free public details."""


@dataclass(frozen=True)
class SMTPConfig:
    manager_email: str | None = None
    host: str | None = None
    port: int = 587
    user: str | None = field(default=None, repr=False)
    password: str | SecretStr | None = field(default=None, repr=False)
    use_tls: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise ValueError("El puerto SMTP configurado no es válido.")
        if self.manager_email is not None:
            validate_email_address(self.manager_email)
        if self.host is not None and (not self.host.strip() or any(c in self.host for c in "\r\n\t ")):
            raise ValueError("El servidor SMTP configurado no es válido.")
        if not isinstance(self.use_tls, bool):
            raise ValueError("La opción TLS de SMTP no es válida.")

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.user and self._password())

    def _password(self) -> str | None:
        if isinstance(self.password, SecretStr):
            return self.password.get_secret_value()
        return self.password

    @classmethod
    def from_env(cls) -> "SMTPConfig":
        """Read the documented SMTP environment variables, without dotenv loading."""
        port_raw = os.environ.get("SMTP_PORT", "587")
        try:
            port = int(port_raw)
        except (TypeError, ValueError):
            raise ValueError("SMTP_PORT debe ser un puerto TCP válido.") from None
        tls_raw = os.environ.get("SMTP_USE_TLS", "true").strip().lower()
        if tls_raw in {"true", "1", "yes", "on"}:
            use_tls = True
        elif tls_raw in {"false", "0", "no", "off"}:
            use_tls = False
        else:
            raise ValueError("SMTP_USE_TLS debe ser true o false.")
        return cls(
            manager_email=os.environ.get("MANAGER_EMAIL") or None,
            host=os.environ.get("SMTP_HOST") or None,
            port=port,
            user=os.environ.get("SMTP_USER") or None,
            password=os.environ.get("SMTP_PASSWORD") or None,
            use_tls=use_tls,
        )

    @classmethod
    def from_settings(cls, settings: object) -> "SMTPConfig":
        """Build an explicit transport config from server-owned settings."""
        user = getattr(settings, "smtp_user", None)
        password = getattr(settings, "smtp_password", None)
        return cls(
            manager_email=getattr(settings, "manager_email", None),
            host=getattr(settings, "smtp_host", None),
            port=getattr(settings, "smtp_port", 587),
            user=user.get_secret_value() if isinstance(user, SecretStr) else user,
            password=password if isinstance(password, SecretStr) else password,
            use_tls=getattr(settings, "smtp_use_tls", True),
        )


def validate_email_address(value: str) -> str:
    """Accept one bare mailbox only; reject controls and header syntax."""
    if (not isinstance(value, str) or len(value) > 254 or value != value.strip()
            or "\r" in value or "\n" in value or not _EMAIL.fullmatch(value)):
        raise ValueError("La dirección de correo no es válida.")
    return value


def safe_header(value: str, *, max_length: int = 300) -> str:
    """Validate untrusted text before assigning it to an RFC email header."""
    if (not isinstance(value, str) or not value or len(value) > max_length
            or "\r" in value or "\n" in value or any(ord(ch) < 32 for ch in value)):
        raise ValueError("El encabezado del correo no es válido.")
    return value


def send_email(
    config: SMTPConfig,
    *,
    recipient: str,
    subject: str,
    plain_text: str,
    html_text: str,
    smtp_factory: Callable[..., object] | None = None,
) -> None:
    """Submit a multipart message; success means only that the relay accepted it."""
    if not config.is_configured:
        raise SMTPDeliveryError("La configuración SMTP está incompleta.")
    receiver = validate_email_address(recipient)
    sender = validate_email_address(config.user or "")
    safe_subject = safe_header(subject)
    client = None
    try:
        factory = smtp_factory or smtplib.SMTP
        client = factory(config.host, config.port, timeout=15)
        if config.use_tls:
            client.starttls(context=ssl.create_default_context())
        client.login(sender, config._password())
        message = EmailMessage()
        message["From"] = sender
        message["To"] = receiver
        message["Subject"] = safe_subject
        message.set_content(plain_text)
        message.add_alternative(html_text, subtype="html")
        client.send_message(message)
    except Exception:
        # Do not include SMTP exception text: libraries may echo credentials or headers.
        raise SMTPDeliveryError("El servidor SMTP no confirmó el envío.") from None
    finally:
        if client is not None:
            try:
                client.quit()
            except Exception:
                # DATA acceptance already occurred; QUIT failure must not cause a duplicate retry.
                pass
