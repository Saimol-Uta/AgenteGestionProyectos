"""API configuration with project-root dotenv defaults and explicit environment overrides."""

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv import dotenv_values
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_serializer,
    model_validator,
)

from agente_gestion.domain.tasks import validate_due_soon_days
from agente_gestion.domain.weekly_schedule import WeeklySchedule
from agente_gestion.domain.repositories import RepositoryConfiguration


PROJECT_ROOT = Path(__file__).resolve().parents[3]


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
    repositories: tuple[RepositoryConfiguration, ...] = Field(default_factory=tuple)

    @field_validator("repositories")
    @classmethod
    def unique_repository_ids(
        cls, value: tuple[RepositoryConfiguration, ...],
    ) -> tuple[RepositoryConfiguration, ...]:
        ids = [repository.id for repository in value]
        if len(ids) != len(set(ids)):
            raise ValueError("Los identificadores de repositorio configurados deben ser únicos.")
        return value

    @model_serializer(mode="wrap")
    def serialize_legacy_shape(self, handler):
        data = handler(self)
        if not self.repositories:
            data.pop("repositories", None)
        return data


class ApiSettings(BaseModel):
    """Settings passed by the server/operator, never accepted from request data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    db_path: str = ".runtime/agente.db"
    runs_dir: str = "runs"
    outbox_dir: str = ".runtime/outbox"
    projects: tuple[ProjectConfig, ...] = ()
    timezone: str = "America/Guayaquil"
    due_soon_days: int = Field(default=7, ge=0)
    analysis_enabled: bool = True
    analysis_config_path: str | None = Field(default=None, repr=False)
    analysis_env_file: str | None = Field(default=None, repr=False, exclude=True)
    job_ledger_path: str = Field(default=".runtime/h5-web-jobs.sqlite", repr=False)
    analysis_queue_capacity: int = Field(default=3, ge=1, le=10)
    analysis_prepare_ttl_seconds: int = Field(default=300, ge=30, le=900)
    governance_enabled: bool = True
    report_decisions_enabled: bool = True
    weekly_cycle_enabled: bool = True
    weekly_schedule: WeeklySchedule | None = Field(default=None, repr=False)
    weekly_schedule_status: Literal["configured", "not_configured", "incomplete", "invalid"] = "not_configured"

    # External mutation permission is a server default; adapters still require live credentials and binding.
    external_writes_enabled: bool = True
    github_token: SecretStr | None = Field(default=None, repr=False, exclude=True)
    github_repository: str | None = None
    github_base_branch: str = "main"
    branches_dir: str = ".runtime/branches"
    trello_api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    trello_token: SecretStr | None = Field(default=None, repr=False, exclude=True)
    trello_priority_labels: dict[str, dict[str, str]] = Field(default_factory=dict)
    trello_board_ids: dict[str, str] = Field(default_factory=dict)
    trello_board_bindings_explicit: bool = Field(default=False, repr=False, exclude=True)
    manager_email: str | None = None
    manager_login_email: str | None = "gestor@hormigestion.ec"
    manager_login_password: SecretStr | None = Field(
        default=SecretStr("HormiGestion2026!"), repr=False, exclude=True,
    )
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: SecretStr | None = Field(default=None, repr=False, exclude=True)
    smtp_password: SecretStr | None = Field(default=None, repr=False, exclude=True)
    smtp_use_tls: bool = True

    @field_validator("manager_email")
    @classmethod
    def valid_manager_email(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value):
            raise ValueError("El correo del gestor no es válido.")
        return value

    @field_validator("manager_login_email")
    @classmethod
    def valid_manager_login_email(cls, value: str | None) -> str | None:
        if value not in {None, ""} and not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value):
            raise ValueError("El correo de acceso del gestor no es válido.")
        return value

    @field_validator("github_repository")
    @classmethod
    def valid_github_repository(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", value):
            raise ValueError("El repositorio debe tener el formato owner/repository.")
        if value is not None and value.split("/")[1] in {".", ".."}:
            raise ValueError("El repositorio no es válido.")
        return value

    @field_validator("trello_board_ids")
    @classmethod
    def valid_board_bindings(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not IDENTIFIER_PATTERN.fullmatch(project_id)
               or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", board_id)
               for project_id, board_id in value.items()):
            raise ValueError("El mapa de tableros Trello no es válido.")
        return value

    @field_validator("trello_priority_labels")
    @classmethod
    def valid_priority_labels(cls, value: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        for project_id, priorities in value.items():
            if not IDENTIFIER_PATTERN.fullmatch(project_id):
                raise ValueError("El proyecto del mapa de prioridades no es válido.")
            for priority, label_id in priorities.items():
                if priority not in {"alta", "media", "baja", "high", "medium", "low", "critical", "critica"}:
                    raise ValueError("La prioridad configurada no es válida.")
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", label_id):
                    raise ValueError("El ID de etiqueta de prioridad no es válido.")
        return value

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


def _bool_setting(name: str, fallback: bool = False, *, environ: Mapping[str, str]) -> bool:
    supplied = environ.get(name)
    if supplied is None:
        return fallback
    normalized = supplied.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"{name} debe ser true o false.")


def _csv_setting(name: str, fallback: list[str], *, environ: Mapping[str, str]) -> tuple[str, ...]:
    supplied = environ.get(name)
    if supplied is None:
        return tuple(fallback)
    return tuple(part.strip() for part in supplied.split(",") if part.strip())


class _ConfigurationAliasError(ValueError):
    pass


def _is_repository_collection_error(error: BaseException) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in (
        "github_repositories",
        "github repository collection",
        "legacy github configuration",
        "github owner and repository configuration",
        "sources.github.repositories",
    ))


def _validate_alias_pair(
    values: Mapping[str, object], first: str, second: str, description: str,
) -> None:
    first_value, second_value = values.get(first), values.get(second)
    if (first_value not in (None, "") and second_value not in (None, "")
            and first_value != second_value):
        raise _ConfigurationAliasError(f"Contradictory {description} aliases: {first} and {second} do not match.")


def _validate_environment_aliases(values: Mapping[str, str]) -> None:
    normalized = {key.upper(): value for key, value in values.items()}
    _validate_alias_pair(normalized, "GITHUB_REPO", "GITHUB_REPOSITORY", "GitHub repository")
    _validate_alias_pair(normalized, "GITHUB_BRANCH", "GITHUB_REF", "GitHub branch")


def _validate_yaml_aliases(document: Mapping[str, object]) -> None:
    _validate_alias_pair(document, "github_repo", "github_repository", "GitHub repository")
    _validate_alias_pair(document, "github_branch", "github_ref", "GitHub branch")
    sources = document.get("sources")
    github = sources.get("github") if isinstance(sources, Mapping) else None
    if isinstance(github, Mapping):
        _validate_alias_pair(github, "repo", "repository", "GitHub repository")
        _validate_alias_pair(github, "branch", "ref", "GitHub branch")


def _merge_yaml_documents(config_paths: tuple[Path, ...]) -> dict[str, object]:
    """Merge project YAML layers without ever treating dotenv files as YAML."""
    def merge(left: dict[str, object], right: Mapping[str, object]) -> dict[str, object]:
        result = dict(left)
        for key, value in right.items():
            previous = result.get(key)
            if isinstance(previous, dict) and isinstance(value, Mapping):
                result[key] = merge(previous, value)
            else:
                result[key] = value
        return result

    document: dict[str, object] = {}
    for config_path in config_paths:
        resolved_name = config_path.resolve(strict=False).name
        if config_path.name.startswith(".env") or resolved_name.startswith(".env"):
            continue
        if not config_path.is_file():
            continue
        with config_path.open("r", encoding="utf-8") as stream:
            file_data = yaml.safe_load(stream)
        if isinstance(file_data, dict):
            _validate_yaml_aliases(file_data)
            document = merge(document, file_data)
    return document


def _configured_project(
    config_paths: tuple[Path, ...],
) -> tuple[str | None, str | None, str, int, str, str, str] | None:
    """Read layered project/storage values needed by the web query layer."""
    document = _merge_yaml_documents(config_paths)
    if not document:
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


def _configured_weekly_schedule(environ: Mapping[str, str]) -> tuple[WeeklySchedule | None, str]:
    names = {
        "project_id": "H5_WEEKLY_SCHEDULE_PROJECT_ID",
        "weekday": "H5_WEEKLY_SCHEDULE_WEEKDAY",
        "local_time": "H5_WEEKLY_SCHEDULE_LOCAL_TIME",
        "timezone": "H5_WEEKLY_SCHEDULE_TIMEZONE",
        "window_rule": "H5_WEEKLY_SCHEDULE_WINDOW_RULE",
    }
    supplied = {field: environ.get(name) for field, name in names.items()}
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


def load_api_settings(*, env_file: str | Path | None = ".env") -> ApiSettings:
    """Load the project's YAML layers, root dotenv defaults, then process overrides."""
    dotenv_path: Path | None = None
    file_values: dict[str, str] = {}
    if env_file is not None:
        dotenv_path = Path(env_file)
        if not dotenv_path.is_absolute():
            dotenv_path = PROJECT_ROOT / dotenv_path
        if dotenv_path.is_file():
            file_values = {
                key: value for key, value in dotenv_values(dotenv_path).items()
                if value is not None
            }
    _validate_environment_aliases(file_values)
    _validate_environment_aliases(os.environ)
    environ = {**file_values, **os.environ}

    selected_config = environ.get("AGENTE_CONFIG_PATH")
    selected_path = Path(selected_config) if selected_config else None
    if selected_path is not None and not selected_path.is_absolute():
        selected_path = PROJECT_ROOT / selected_path
    project_path = PROJECT_ROOT / "config" / "hormigestion.yaml"
    document: dict[str, object] = {}
    try:
        from agente_gestion.application.config import (
            Settings as EngineSettings,
            _yaml_configuration_paths,
        )

        layered_paths = _yaml_configuration_paths(PROJECT_ROOT, selected_path)
        document = _merge_yaml_documents(layered_paths)
        source_section = document.get("sources")
        github_section = source_section.get("github") if isinstance(source_section, Mapping) else None
        if isinstance(github_section, Mapping) and "repositories" in github_section:
            if not isinstance(github_section["repositories"], list):
                raise ValueError("Invalid GitHub repository collection configuration.")
        configured = _configured_project(layered_paths)
        engine = EngineSettings.from_env_and_file(
            config_path=str(selected_path) if selected_path is not None else None,
            _env_file=str(dotenv_path) if env_file is not None and dotenv_path is not None else None,
        )
    except _ConfigurationAliasError:
        raise
    except (OSError, TypeError, ValueError, ValidationError, yaml.YAMLError) as exc:
        if _is_repository_collection_error(exc):
            raise ValueError("Invalid GitHub repository collection configuration.") from None
        document = {}
        configured = None
        engine = None

    if configured is None or engine is None:
        project_id = project_name = None
        source_mode, due_soon_days, timezone_name = "fixtures", 7, "America/Guayaquil"
        configured_db_path, runs_dir = ".runtime/agente.db", "runs"
    else:
        (_yaml_project_id, yaml_project_name, _yaml_source_mode, _yaml_due_days,
         _yaml_timezone, _yaml_db_path, _yaml_runs_dir) = configured
        project_id = engine.project_id
        project = document.get("project") if isinstance(document.get("project"), dict) else {}
        project_name = str(project.get("name", yaml_project_name or project_id))
        if project.get("id") not in (None, project_id):
            project_name = project_id
        source_mode = engine.source_mode
        due_soon_days = engine.due_soon_days
        timezone_name = engine.timezone
        configured_db_path, runs_dir = engine.db_path, engine.runs_dir

    configured_project = None
    if project_id is not None and IDENTIFIER_PATTERN.fullmatch(project_id) and project_name is not None:
        try:
            configured_project = ProjectConfig(
                id=project_id,
                name=project_name,
                source_mode=source_mode,
                repositories=engine.github_repositories if engine is not None and engine.github_repositories else (),
            )
        except ValidationError:
            configured_project = None

    if engine is not None:
        github_token = engine.github_token
        repository = (
            f"{engine.github_owner}/{engine.github_repo}"
            if engine.github_owner and engine.github_repo else None
        )
        trello_api_key, trello_token = engine.trello_api_key, engine.trello_token
        board_id = engine.trello_board_id
    else:
        github_token = SecretStr(environ["GITHUB_TOKEN"]) if environ.get("GITHUB_TOKEN") else None
        repository = environ.get("GITHUB_REPOSITORY") or environ.get("GITHUB_REPO") or None
        trello_api_key = SecretStr(environ["TRELLO_API_KEY"]) if environ.get("TRELLO_API_KEY") else None
        trello_token = SecretStr(environ["TRELLO_TOKEN"]) if environ.get("TRELLO_TOKEN") else None
        board_id = None

    mapping_explicit = "TRELLO_BOARD_IDS" in environ or "trello_board_ids" in document
    if "TRELLO_BOARD_IDS" in environ:
        trello_board_ids = json.loads(environ["TRELLO_BOARD_IDS"])
    else:
        raw_mapping = document.get("trello_board_ids", {})
        if not isinstance(raw_mapping, dict):
            raise ValueError("TRELLO_BOARD_IDS debe ser un objeto JSON o mapa YAML.")
        trello_board_ids = dict(raw_mapping)
    if not mapping_explicit and board_id and project_id:
        trello_board_ids = {project_id: board_id}

    if "TRELLO_PRIORITY_LABELS" in environ:
        trello_priority_labels = json.loads(environ["TRELLO_PRIORITY_LABELS"])
    else:
        raw_labels = document.get("trello_priority_labels", {})
        if not isinstance(raw_labels, dict):
            raise ValueError("TRELLO_PRIORITY_LABELS debe ser un objeto JSON o mapa YAML.")
        trello_priority_labels = dict(raw_labels)

    external_writes_default = document.get("external_writes_enabled", True)
    if isinstance(external_writes_default, str):
        normalized_external_writes = external_writes_default.strip().lower()
        if normalized_external_writes in {"true", "1", "yes", "on"}:
            external_writes_default = True
        elif normalized_external_writes in {"false", "0", "no", "off"}:
            external_writes_default = False
        else:
            raise ValueError("external_writes_enabled debe ser true o false.")
    if not isinstance(external_writes_default, bool):
        raise ValueError("external_writes_enabled debe ser true o false.")

    operator_token = environ.get("H5_OPERATOR_TOKEN")
    weekly_schedule, weekly_schedule_status = _configured_weekly_schedule(environ)
    if weekly_schedule is not None and (
        configured_project is None or weekly_schedule.project_id != configured_project.id
    ):
        weekly_schedule, weekly_schedule_status = None, "invalid"

    selected_analysis_config = selected_path or project_path
    configured_db_override = environ.get("AGENTE_DB_PATH")
    db_path = configured_db_override.strip() if configured_db_override and configured_db_override.strip() else configured_db_path
    return ApiSettings(
        db_path=db_path,
        runs_dir=environ.get("AGENTE_RUNS_DIR", runs_dir),
        outbox_dir=environ.get("H5_OUTBOX_DIR", ".runtime/outbox"),
        projects=(configured_project,) if configured_project is not None else (),
        timezone=timezone_name,
        due_soon_days=due_soon_days,
        analysis_enabled=_bool_setting("H5_ANALYSIS_ENABLED", True, environ=environ),
        governance_enabled=_bool_setting("H5_GOVERNANCE_ENABLED", True, environ=environ),
        report_decisions_enabled=_bool_setting("H5_REPORT_DECISIONS_ENABLED", True, environ=environ),
        weekly_cycle_enabled=_bool_setting("H5_WEEKLY_CYCLE_ENABLED", True, environ=environ),
        weekly_schedule=weekly_schedule,
        weekly_schedule_status=weekly_schedule_status,
        external_writes_enabled=_bool_setting(
            "EXTERNAL_WRITES_ENABLED", external_writes_default, environ=environ,
        ),
        github_token=github_token,
        github_repository=repository,
        github_base_branch=environ.get("GITHUB_BASE_BRANCH", "main"),
        branches_dir=environ.get("BRANCHES_DIR", ".runtime/branches"),
        trello_api_key=trello_api_key,
        trello_token=trello_token,
        trello_priority_labels=trello_priority_labels,
        trello_board_ids=trello_board_ids,
        trello_board_bindings_explicit=mapping_explicit,
        manager_email=environ.get("MANAGER_EMAIL") or document.get("manager_email") or None,
        manager_login_email=environ.get(
            "MANAGER_LOGIN_EMAIL", str(document.get("manager_login_email", "gestor@hormigestion.ec")),
        ),
        manager_login_password=SecretStr(
            environ.get("MANAGER_LOGIN_PASSWORD", str(document.get("manager_login_password", "HormiGestion2026!")))
        ),
        smtp_host=environ.get("SMTP_HOST") or None,
        smtp_port=int(environ.get("SMTP_PORT", "587")),
        smtp_user=SecretStr(environ["SMTP_USER"]) if environ.get("SMTP_USER") else None,
        smtp_password=SecretStr(environ["SMTP_PASSWORD"]) if environ.get("SMTP_PASSWORD") else None,
        smtp_use_tls=_bool_setting("SMTP_USE_TLS", True, environ=environ),
        analysis_config_path=str(selected_analysis_config),
        analysis_env_file=str(dotenv_path) if env_file is not None and dotenv_path is not None else None,
        job_ledger_path=environ.get("H5_WEB_JOBS_DB_PATH", ".runtime/h5-web-jobs.sqlite"),
        operator_token=SecretStr(operator_token) if operator_token else None,
        operator_actor=environ.get("H5_OPERATOR_ACTOR", "operador-local"),
        operator_project_ids=(configured_project.id,) if configured_project is not None else (),
        allowed_origins=_csv_setting("H5_ALLOWED_ORIGINS", DEFAULT_ORIGINS, environ=environ),
        allowed_hosts=_csv_setting("H5_ALLOWED_HOSTS", DEFAULT_HOSTS, environ=environ),
        session_ttl_seconds=int(environ.get("H5_SESSION_TTL_SECONDS", "3600")),
        session_cookie_secure=environ.get("H5_SESSION_COOKIE_SECURE", "false").lower() == "true",
    )
