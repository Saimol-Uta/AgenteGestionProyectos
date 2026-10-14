"""Local operator sessions, CSRF controls, and response-side secret filtering."""

from dataclasses import dataclass
import hashlib
import hmac
import re
import secrets
import time
from threading import RLock
from typing import Callable

from pydantic import SecretStr

from agente_gestion.api.settings import ApiSettings, IDENTIFIER_PATTERN


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b([\w-]*(?:api[ _-]?(?:key|token)|access[ _-]?token|refresh[ _-]?token|"
    r"token|secret|password|authorization|credential|private[ _-]?key)[\w-]*)"
    r"\b(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^,;\n]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_PROVIDER_TOKEN = re.compile(
    r"(?i)\b(?:gh[pousr]_|github_pat_|gsk[_-]|sk[_-]|AIza|xox[baprs]-|mistral[_-])"
    r"[A-Za-z0-9_-]{12,}\b"
)
_URL_CREDENTIAL = re.compile(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@")
_LONG_OPAQUE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_-]{48,}(?![A-Za-z0-9])")
_COMMIT_RE = re.compile(r"^(?:[0-9a-fA-F]{7,64})$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")


@dataclass(frozen=True)
class OperatorSession:
    actor: str
    project_ids: tuple[str, ...]
    csrf_token: str
    expires_at: float


class OperatorSessionStore:
    """Process-local expiring/revocable session store; raw cookie IDs are never stored."""

    def __init__(self, settings: ApiSettings, clock: Callable[[], float] = time.monotonic):
        self._settings = settings
        self._clock = clock
        self._sessions: dict[str, OperatorSession] = {}
        self._lock = RLock()
        self._operator_token = (
            settings.operator_token.get_secret_value() if settings.operator_token else None
        )
        configured_project_ids = tuple(project.id for project in settings.projects)
        configured_project_set = set(configured_project_ids)
        requested_project_ids = settings.operator_project_ids or configured_project_ids
        self._project_ids = tuple(
            project_id for project_id in requested_project_ids
            if project_id in configured_project_set
        )

    @property
    def login_available(self) -> bool:
        return bool(self._operator_token)

    def verify_operator_token(self, supplied: str) -> bool:
        return bool(self._operator_token) and hmac.compare_digest(
            self._operator_token.encode("utf-8"), supplied.encode("utf-8"),
        )

    def issue(self) -> tuple[str, OperatorSession]:
        cookie_value = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        session = OperatorSession(
            actor=self._settings.operator_actor,
            project_ids=self._project_ids,
            csrf_token=csrf_token,
            expires_at=self._clock() + self._settings.session_ttl_seconds,
        )
        with self._lock:
            self._discard_expired()
            self._sessions[self._fingerprint(cookie_value)] = session
        return cookie_value, session

    def get(self, cookie_value: str | None) -> OperatorSession | None:
        if not cookie_value:
            return None
        with self._lock:
            self._discard_expired()
            key = self._fingerprint(cookie_value)
            session = self._sessions.get(key)
            if session and session.expires_at > self._clock():
                return session
            self._sessions.pop(key, None)
            return None

    def csrf_is_valid(self, session: OperatorSession, supplied: str | None) -> bool:
        return bool(supplied) and hmac.compare_digest(
            session.csrf_token.encode("utf-8"), supplied.encode("utf-8"),
        )

    def revoke(self, cookie_value: str | None) -> None:
        if cookie_value:
            with self._lock:
                self._sessions.pop(self._fingerprint(cookie_value), None)

    def _discard_expired(self) -> None:
        now = self._clock()
        for key, session in tuple(self._sessions.items()):
            if session.expires_at <= now:
                self._sessions.pop(key, None)

    @staticmethod
    def _fingerprint(cookie_value: str) -> str:
        return hashlib.sha256(cookie_value.encode("utf-8")).hexdigest()


def sanitize_text(value: str | None, *, secrets_to_redact: tuple[str, ...] = (), limit: int = 2000) -> str | None:
    """Bound text length and redact common secret assignments/tokens before DTO output."""
    if value is None:
        return None
    cleaned = "".join(ch if ch in "\n\t" or ord(ch) >= 32 else " " for ch in str(value))[:limit]
    cleaned = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTADO]", cleaned)
    cleaned = _BEARER.sub("Bearer [REDACTADO]", cleaned)
    cleaned = _PROVIDER_TOKEN.sub("[REDACTADO]", cleaned)
    cleaned = _URL_CREDENTIAL.sub(r"\1[REDACTADO]@", cleaned)
    cleaned = _LONG_OPAQUE.sub("[REDACTADO]", cleaned)
    for secret in secrets_to_redact:
        if secret:
            cleaned = cleaned.replace(secret, "[REDACTADO]")
    return cleaned


def safe_identifier(value: object) -> str | None:
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        return None
    return value


def safe_repository(value: object) -> str | None:
    if isinstance(value, str) and _REPOSITORY_RE.fullmatch(value):
        owner, name = value.split("/", 1)
        if owner not in {".", ".."} and name not in {".", ".."}:
            return value
    return None


def safe_commit_sha(value: object) -> str | None:
    if isinstance(value, str) and _COMMIT_RE.fullmatch(value):
        return value.lower()
    return None


def safe_relative_path(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 512:
        return None
    if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value):
        return None
    parts = value.replace("\\", "/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    if any(ord(ch) < 32 for ch in value) or sanitize_text(value, limit=512) != value:
        return None
    if any(character in value for character in ("?", "#")):
        return None
    return "/".join(parts)


def safe_lines(value: object) -> list[int] | None:
    if not isinstance(value, list) or len(value) > 100:
        return None
    if any(not isinstance(line, int) or isinstance(line, bool) or line < 1 or line > 10_000_000
           for line in value):
        return None
    return value
