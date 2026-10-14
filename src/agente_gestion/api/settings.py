"""Explicit API settings; this module never loads dotenv files or starts services."""

import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator, model_validator

from agente_gestion.domain.tasks import validate_due_soon_days
from agente_gestion.domain.weekly_schedule import WeeklySchedule


IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
DEFAULT_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
DEFAULT_HOSTS = ["localhost", "127.0.0.1", "::1"]


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128, pattern=IDENTIFIER_PATTERN.pattern)
    name: str = Field(min_length=1, max_length=160)
    source_mode: Literal["fixtures", "real", "stored"] = "stored"


class ApiSettings(BaseModel):
    """Settings passed by the server/operator, never accepted from request data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    db_path: str = ".runtime/agente.db"
    runs_dir: str = "runs"
    outbox_dir: str = ".runtime/outbox"
    projects: tuple[ProjectConfig, ...] = ()
    timezone: str = "America/Guayaquil"
    due_soon_days: int = Field(default=7, ge=0)
    analysis_enabled: bool = False
    analysis_config_path: str | None = Field(default=None, repr=False)
    job_ledger_path: str = Field(default=".runtime/h5-web-jobs.sqlite", repr=False)
    analysis_queue_capacity: int = Field(default=3, ge=1, le=10)
    analysis_prepare_ttl_seconds: int = Field(default=300, ge=30, le=900)
    governance_enabled: bool = False
    report_decisions_enabled: bool = False
    weekly_cycle_enabled: bool = False
    weekly_schedule: WeeklySchedule | None = Field(default=None, repr=False)
    weekly_schedule_status: Literal["configured", "not_configured", "incomplete", "invalid"] = "not_configured"

    operator_token: SecretStr | None = Field(default=None, repr=False, exclude=True)
    operator_actor: str = Field(default="operador-local", min_length=1, max_length=128)
    operator_project_ids: tuple[str, ...] = ()
    session_ttl_seconds: int = Field(default=3600, ge=1, le=86400)
    session_cookie_name: str = Field(default="h5_operator_session", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    session_cookie_secure: bool = False

    allowed_origins: tuple[str, ...] = tuple(DEFAULT_ORIGINS)
    allowed_hosts: tuple[str, ...] = tuple(DEFAULT_HOSTS)

    @model_validator(mode="after")
    def validate_schedule_scope(self) -> "ApiSettings":
        if self.weekly_schedule is not None:
            if self.weekly_schedule_status != "configured":
                raise ValueError("A configured weekly schedule must have configured status.")
            if self.weekly_schedule.project_id not in {project.id for project in self.projects}:
                raise ValueError("The weekly schedule must reference a configured project.")
        return self

    @field_validator("projects")
    @classmethod
    def unique_project_ids(cls, value: tuple[ProjectConfig, ...]) -> tuple[ProjectConfig, ...]:
        ids = [project.id for project in value]
        if len(ids) != len(set(ids)):
            raise ValueError("Los identificadores de proyecto configurados deben ser únicos.")
        return value

    @field_validator("due_soon_days", mode="before")
    @classmethod
    def valid_due_soon_days(cls, value: object) -> int:
        if isinstance(value, str):
            try:
                value = int(value)
            except ValueError:
                raise ValueError("due_soon_days debe ser un entero no negativo.") from None
        return validate_due_soon_days(value)

    @field_validator("operator_token")
    @classmethod
    def require_operator_token_entropy(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < 32:
            raise ValueError("La credencial local debe contener al menos 32 caracteres.")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("La zona horaria configurada no es válida.") from None
        return value

    @field_validator("operator_project_ids")
    @classmethod
    def valid_operator_projects(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not IDENTIFIER_PATTERN.fullmatch(item) for item in value):
            raise ValueError("El alcance local del operador contiene un identificador inválido.")
        return tuple(dict.fromkeys(value))

    @field_validator("allowed_origins")
    @classmethod
    def exact_http_origins(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned: list[str] = []
        for origin in value:
            parsed = urlsplit(origin)
            if (origin == "*" or parsed.scheme not in {"http", "https"}
                    or not parsed.netloc or parsed.username or parsed.password
                    or any(ch.isspace() for ch in origin)
                    or parsed.path or parsed.query or parsed.fragment
                    or origin != f"{parsed.scheme}://{parsed.netloc}"):
                raise ValueError("Cada origen permitido debe ser un origen HTTP(S) exacto, sin comodines.")
            if origin not in cleaned:
                cleaned.append(origin)
        if not cleaned:
            raise ValueError("Debe configurarse al menos un origen HTTP(S) exacto.")
        return tuple(cleaned)

    @field_validator("allowed_hosts")
    @classmethod
    def exact_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(dict.fromkeys(item.strip().lower() for item in value))
        if not cleaned or any(not item or item == "*" or "*" in item for item in cleaned):
            raise ValueError("Los hosts permitidos deben ser explícitos y no pueden usar comodines.")
        return cleaned


def _bool_setting(name: str, fallback: bool = False) -> bool:
    supplied = os.environ.get(name)
    if supplied is None:
        return fallback
    normalized = supplied.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"{name} debe ser true o false.")


def _csv_setting(name: str, fallback: list[str]) -> tuple[str, ...]:
    supplied = os.environ.get(name)
    if supplied is None:
        return tuple(fallback)
    return tuple(part.strip() for part in supplied.split(",") if part.strip())


def _configured_project(
    config_path: Path,
) -> tuple[str | None, str | None, str, int, str, str, str] | None:
    """Read only the project/storage values needed by the web query layer."""
    resolved_name = config_path.resolve(strict=False).name
    if config_path.name.startswith(".env") or resolved_name.startswith(".env"):
        return None
    if not config_path.is_file():
        return None
    with config_path.open("r", encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, dict):
        return None
    project = document.get("project") if isinstance(document.get("project"), dict) else {}
    sources = document.get("sources") if isinstance(document.get("sources"), dict) else {}
    execution = document.get("execution") if isinstance(document.get("execution"), dict) else {}
    raw_project_id = project.get("id", document.get("project_id"))
    project_id = str(raw_project_id) if raw_project_id is not None else None
    name = str(project.get("name", project_id)) if project_id is not None else None
    source_mode = str(sources.get("mode", document.get("source_mode", "fixtures")))
    if source_mode not in {"fixtures", "real"}:
        raise ValueError("El modo de datos configurado para la API no es válido.")
    timezone_name = str(project.get("timezone", document.get("timezone", "America/Guayaquil")))
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("La zona horaria configurada para la API no es válida.") from None
    due_soon_days = execution.get("due_soon_days", document.get("due_soon_days", 7))
    if isinstance(due_soon_days, str):
        try:
            due_soon_days = int(due_soon_days)
        except ValueError:
            raise ValueError("La ventana de vencimiento configurada no es válida.") from None
    try:
        due_soon_days = validate_due_soon_days(due_soon_days)
    except ValueError:
        raise ValueError("La ventana de vencimiento configurada no es válida.") from None
    db_path = str(document.get("db_path", ".runtime/agente.db"))
    runs_dir = str(document.get("runs_dir", "runs"))
    return project_id, name, source_mode, due_soon_days, timezone_name, db_path, runs_dir


def _configured_weekly_schedule() -> tuple[WeeklySchedule | None, str]:
    names = {
        "project_id": "H5_WEEKLY_SCHEDULE_PROJECT_ID",
        "weekday": "H5_WEEKLY_SCHEDULE_WEEKDAY",
        "local_time": "H5_WEEKLY_SCHEDULE_LOCAL_TIME",
        "timezone": "H5_WEEKLY_SCHEDULE_TIMEZONE",
        "window_rule": "H5_WEEKLY_SCHEDULE_WINDOW_RULE",
    }
    supplied = {field: os.environ.get(name) for field, name in names.items()}
    if not any(value is not None for value in supplied.values()):
        return None, "not_configured"
    if any(value is None or not value.strip() for value in supplied.values()):
        return None, "incomplete"
    try:
        values: dict[str, object] = dict(supplied)
        values["weekday"] = int(str(values["weekday"]))
        schedule = WeeklySchedule.model_validate(values, strict=True)
    except (TypeError, ValueError, ValidationError):
        return None, "invalid"
    return schedule, "configured"


def load_api_settings() -> ApiSettings:
    """Build production settings without dotenv, provider calls, or DB access."""
    config_path = Path(os.environ.get("AGENTE_CONFIG_PATH", "config/hormigestion.yaml"))
    try:
        configured = _configured_project(config_path)
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        configured = None
    if configured is None:
        project_id = project_name = None
        source_mode, due_soon_days, timezone_name = "fixtures", 7, "America/Guayaquil"
        configured_db_path, runs_dir = ".runtime/agente.db", "runs"
    else:
        (project_id, project_name, source_mode, due_soon_days, timezone_name,
         configured_db_path, runs_dir) = configured
    configured_path = os.environ.get("AGENTE_DB_PATH")
    db_path = configured_path or configured_db_path

    configured_project = None
    if project_id is not None and IDENTIFIER_PATTERN.fullmatch(project_id) and project_name is not None:
        try:
            configured_project = ProjectConfig(
                id=project_id, name=project_name, source_mode=source_mode,
            )
        except ValidationError:
            pass
    operator_token = os.environ.get("H5_OPERATOR_TOKEN")
    weekly_schedule, weekly_schedule_status = _configured_weekly_schedule()
    if weekly_schedule is not None and (
        configured_project is None or weekly_schedule.project_id != configured_project.id
    ):
        weekly_schedule, weekly_schedule_status = None, "invalid"
    return ApiSettings(
        db_path=db_path,
        runs_dir=os.environ.get("AGENTE_RUNS_DIR", runs_dir),
        outbox_dir=os.environ.get("H5_OUTBOX_DIR", ".runtime/outbox"),
        projects=(configured_project,) if configured_project is not None else (),
        timezone=timezone_name,
        due_soon_days=due_soon_days,
        analysis_enabled=_bool_setting("H5_ANALYSIS_ENABLED", False),
        governance_enabled=_bool_setting("H5_GOVERNANCE_ENABLED", False),
        report_decisions_enabled=_bool_setting("H5_REPORT_DECISIONS_ENABLED", False),
        weekly_cycle_enabled=_bool_setting("H5_WEEKLY_CYCLE_ENABLED", False),
        weekly_schedule=weekly_schedule,
        weekly_schedule_status=weekly_schedule_status,
        analysis_config_path=str(config_path),
        job_ledger_path=os.environ.get("H5_WEB_JOBS_DB_PATH", ".runtime/h5-web-jobs.sqlite"),
        operator_token=SecretStr(operator_token) if operator_token else None,
        operator_actor=os.environ.get("H5_OPERATOR_ACTOR", "operador-local"),
        operator_project_ids=(configured_project.id,) if configured_project is not None else (),
        allowed_origins=_csv_setting("H5_ALLOWED_ORIGINS", DEFAULT_ORIGINS),
        allowed_hosts=_csv_setting("H5_ALLOWED_HOSTS", DEFAULT_HOSTS),
        session_ttl_seconds=int(os.environ.get("H5_SESSION_TTL_SECONDS", "3600")),
        session_cookie_secure=os.environ.get("H5_SESSION_COOKIE_SECURE", "false").lower() == "true",
    )
