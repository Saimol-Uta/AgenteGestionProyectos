"""Configuration loading, validation, and environment defaults using pydantic-settings."""

import math
import os
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Type
from zoneinfo import ZoneInfo
from agente_gestion.domain.tasks import validate_due_soon_days
from agente_gestion.domain.repositories import RepositoryConfiguration
from agente_gestion.domain.source_limits import DEFAULT_MULTIREPO_BUDGETS
from pydantic import Field, SecretStr, field_validator, model_serializer, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from dotenv import dotenv_values

try:
    import yaml
except ImportError:
    yaml = None

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_yaml_file_ctx: ContextVar[tuple[str, ...]] = ContextVar("_yaml_file_ctx", default=())

FULL_SHA_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")


def _load_yaml_file(config_path: Optional[str]) -> dict[str, Any]:
    """Extracts nested and top-level fields from a YAML configuration file."""
    if not config_path or not os.path.exists(config_path) or yaml is None:
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        file_data = yaml.safe_load(f)

    if not isinstance(file_data, dict):
        return {}

    merged: dict[str, Any] = {}

    # Extract nested YAML sections if present (matching ARCHITECTURA.v1 / config.yaml)
    if "project" in file_data and isinstance(file_data["project"], dict):
        proj = file_data["project"]
        if "id" in proj:
            merged["project_id"] = proj["id"]
        if "default_fixture" in proj:
            merged["default_fixture"] = proj["default_fixture"]
        if "timezone" in proj:
            merged["timezone"] = proj["timezone"]
        if "allow_remote_ai" in proj:
            merged["allow_remote_ai"] = proj["allow_remote_ai"]

    if "execution" in file_data and isinstance(file_data["execution"], dict):
        exec_sec = file_data["execution"]
        if "due_soon_days" in exec_sec:
            merged["due_soon_days"] = exec_sec["due_soon_days"]
        if "max_llm_calls_per_run" in exec_sec:
            merged["max_llm_calls_per_run"] = exec_sec["max_llm_calls_per_run"]
        if "quality_timeout_seconds" in exec_sec:
            merged["quality_timeout_seconds"] = exec_sec["quality_timeout_seconds"]
        if "verifier_timeout_seconds" in exec_sec:
            merged["verifier_timeout_seconds"] = exec_sec["verifier_timeout_seconds"]
        if "quality_max_output_tokens" in exec_sec:
            merged["quality_max_output_tokens"] = exec_sec["quality_max_output_tokens"]
        if "quality_context_window" in exec_sec:
            merged["quality_context_window"] = exec_sec["quality_context_window"]

    if "providers" in file_data and isinstance(file_data["providers"], dict):
        prov_sec = file_data["providers"]
        if "quality" in prov_sec and isinstance(prov_sec["quality"], dict):
            q_sec = prov_sec["quality"]
            if "provider" in q_sec:
                merged["quality_provider"] = q_sec["provider"]
            if "max_output_tokens" in q_sec:
                merged["quality_max_output_tokens"] = q_sec["max_output_tokens"]
            if "model" in q_sec:
                merged["groq_model"] = q_sec["model"]
            if "context_window" in q_sec:
                merged["quality_context_window"] = q_sec["context_window"]
            if "quality_context_window" in q_sec:
                merged["quality_context_window"] = q_sec["quality_context_window"]
        if "report" in prov_sec and isinstance(prov_sec["report"], dict):
            rep_sec = prov_sec["report"]
            if "provider" in rep_sec:
                merged["report_provider"] = rep_sec["provider"]
            if "model" in rep_sec:
                merged["gemini_report_model"] = rep_sec["model"]
        if "remote" in prov_sec and isinstance(prov_sec["remote"], dict):
            rem_sec = prov_sec["remote"]
            if "model" in rem_sec:
                merged["gemini_model"] = rem_sec["model"]
    if "external_writes_enabled" in file_data:
        merged["external_writes_enabled"] = file_data["external_writes_enabled"]

    if "roles" in file_data and isinstance(file_data["roles"], dict):
        roles_sec = file_data["roles"]
        if roles_sec.get("elaborador") == "gemini":
            merged["monitoring_provider"] = "gemini"
        if roles_sec.get("revisor") == "groq":
            merged["quality_provider"] = "groq"
        if roles_sec.get("aprobador") == "gemini":
            merged["report_provider"] = "gemini"

    if "sources" in file_data and isinstance(file_data["sources"], dict):
        src_sec = file_data["sources"]
        if "mode" in src_sec:
            merged["source_mode"] = src_sec["mode"]
        if "policies_path" in src_sec:
            merged["policies_path"] = src_sec["policies_path"]
        if "http_timeout_seconds" in src_sec:
            merged["http_timeout_seconds"] = src_sec["http_timeout_seconds"]
        if "http_max_response_bytes" in src_sec:
            merged["http_max_response_bytes"] = src_sec["http_max_response_bytes"]

        if "trello" in src_sec and isinstance(src_sec["trello"], dict):
            tr = src_sec["trello"]
            if "board_id" in tr:
                merged["trello_board_id"] = tr["board_id"]
            if "api_key" in tr:
                merged["trello_api_key"] = tr["api_key"]
            if "token" in tr:
                merged["trello_token"] = tr["token"]
            if "state_strategy" in tr:
                merged["trello_state_strategy"] = tr["state_strategy"]
            elif "trello_state_strategy" in tr:
                merged["trello_state_strategy"] = tr["trello_state_strategy"]
            mapping_sec = tr.get("mapping") if isinstance(tr.get("mapping"), dict) else tr
            if "pending_list_ids" in mapping_sec:
                merged["trello_pending_list_ids"] = mapping_sec["pending_list_ids"]
            if "in_progress_list_ids" in mapping_sec:
                merged["trello_in_progress_list_ids"] = mapping_sec["in_progress_list_ids"]
            if "completed_list_ids" in mapping_sec:
                merged["trello_completed_list_ids"] = mapping_sec["completed_list_ids"]
            if "blocked_labels" in mapping_sec:
                merged["trello_blocked_labels"] = mapping_sec["blocked_labels"]
            if "included_list_ids" in mapping_sec:
                merged["trello_included_list_ids"] = mapping_sec["included_list_ids"]

        if "github" in src_sec and isinstance(src_sec["github"], dict):
            gh = src_sec["github"]
            if "owner" in gh:
                merged["github_owner"] = gh["owner"]
            repo_val = gh.get("repo")
            repo_alias = gh.get("repository")
            if repo_val not in (None, "") and repo_alias not in (None, "") and repo_val != repo_alias:
                raise ValueError(
                    "Contradictory GitHub repository configuration: repo and repository aliases do not match."
                )
            eff_repo = repo_alias if repo_alias not in (None, "") else repo_val
            if eff_repo is not None:
                merged["github_repo"] = eff_repo
                merged["github_repository"] = eff_repo
            if "repositories" in gh:
                repositories = gh["repositories"]
                if not isinstance(repositories, list):
                    raise ValueError("sources.github.repositories must be a non-empty list.")
                merged["github_repositories"] = repositories

            branch_val = gh.get("branch")
            branch_alias = gh.get("ref")
            if branch_val is not None and branch_alias is not None and branch_val != branch_alias:
                raise ValueError("Contradictory GitHub branch configuration: branch and ref aliases do not match.")
            eff_branch = branch_val if branch_val is not None else branch_alias
            if eff_branch is not None:
                merged["github_branch"] = eff_branch
                merged["github_ref"] = eff_branch

            if "token" in gh:
                merged["github_token"] = gh["token"]
            if "commit_sha" in gh:
                merged["github_commit_sha"] = gh["commit_sha"]
            elif "commit" in gh:
                merged["github_commit_sha"] = gh["commit"]
            elif "sha" in gh:
                merged["github_commit_sha"] = gh["sha"]

            budget = gh.get("collection_budget", gh.get("budget"))
            if budget is not None:
                if not isinstance(budget, dict):
                    raise ValueError("sources.github.collection_budget must be a mapping.")
                for key in DEFAULT_MULTIREPO_BUDGETS:
                    if key in budget:
                        merged[f"multirepo_{key}"] = budget[key]

    # Directly specified top-level keys
    for k, v in file_data.items():
        if v is not None and not isinstance(v, dict):
            merged[k] = v

    return merged


def _normalize_github_aliases(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize GitHub aliases within one source, rejecting ambiguous resources."""
    normalized = dict(data)
    repo = normalized.get("github_repo")
    repository = normalized.get("github_repository")
    if repo not in (None, "") and repository not in (None, "") and repo != repository:
        raise ValueError(
            "Contradictory GitHub repository configuration: repo and repository aliases do not match."
        )
    resolved_repo = repository if repository not in (None, "") else repo
    if resolved_repo is not None:
        normalized["github_repo"] = resolved_repo
        normalized["github_repository"] = resolved_repo

    branch = normalized.get("github_branch")
    ref = normalized.get("github_ref")
    if branch is not None and ref is not None and branch != ref:
        raise ValueError("Contradictory GitHub branch configuration: branch and ref aliases do not match.")
    resolved_branch = branch if branch is not None else ref
    if resolved_branch is not None:
        normalized["github_branch"] = resolved_branch
        normalized["github_ref"] = resolved_branch
    return normalized


class YamlConfigSettingsSource(PydanticBaseSettingsSource):
    """Loads configuration values from a YAML file with lower precedence than dotenv and OS."""

    def get_field_value(
        self, field: Any, field_name: str
    ) -> Tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}
        for yaml_path in _yaml_file_ctx.get():
            layer = _normalize_github_aliases(_load_yaml_file(yaml_path))
            merged.update(layer)
        return merged


class AliasNormalizingSettingsSource(PydanticBaseSettingsSource):
    """Normalizes field aliases within each configuration source before layer merging."""

    def __init__(self, settings_cls: Type[BaseSettings], inner: PydanticBaseSettingsSource):
        super().__init__(settings_cls)
        self.inner = inner

    def get_field_value(self, field: Any, field_name: str) -> Tuple[Any, str, bool]:
        return self.inner.get_field_value(field, field_name)

    def __call__(self) -> Dict[str, Any]:
        return _normalize_github_aliases(dict(self.inner()))


class Settings(BaseSettings):
    """Centralized configuration governing project analysis runs and adapter integrations."""

    project_id: str = Field(default="proyecto-demo")
    default_fixture: str = Field(default="fixture_1_standard")
    timezone: str = Field(default="America/Guayaquil")
    allow_remote_ai: bool = Field(
        default=True,
        description="Server permission for remote AI and source adapters; each run also requires consent.",
    )
    monitoring_provider: str = Field(default="gemini")
    gemini_monitoring_model: str = Field(default="gemini-3.5-flash-lite")
    monitoring_max_generation_tokens: Optional[int] = Field(default=None, gt=0, le=8192)
    report_provider: str = Field(
        default="gemini",
        description="LLM provider for ReportAgent. The deterministic sandbox is used when credentials are missing.",
    )
    gemini_report_model: str = Field(
        default="gemini-3.5-flash-lite",
        description="Gemini model identifier for report synthesis.",
    )
    gemini_api_key: Optional[SecretStr] = Field(
        default=None,
        repr=False,
        exclude=True,
        description="Google Gemini API key. SecretStr excluded from repr, dumps, and fingerprints.",
    )
    gemini_model: str = Field(default="gemini-2.5-flash")
    quality_provider: str = Field(
        default="groq",
        description="LLM provider for CodeQualityAgent. The deterministic sandbox is used when credentials are missing.",
    )
    groq_api_key: Optional[SecretStr] = Field(
        default=None,
        repr=False,
        exclude=True,
        description="Groq API key. SecretStr excluded from repr, dumps, and fingerprints.",
    )
    groq_model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Groq model identifier for code quality review.",
    )
    groq_base_url: str = Field(
        default="https://api.groq.com/openai/v1",
        description="Groq API base URL.",
    )
    quality_context_window: Optional[int] = Field(
        default=None,
        description="Optional explicit context window tokens for quality provider.",
    )
    quality_max_output_tokens: Optional[int] = Field(
        default=None,
        description="Optional explicit max output tokens for quality provider.",
    )
    quality_context_margin: int = Field(
        default=64,
        gt=0,
        description="Safety margin tokens for quality context budgeting.",
    )
    quality_timeout_seconds: float = Field(
        default=120.0,
        description="Timeout in seconds for code quality review requests (bounded default 120s per ARCHITECTURA/T4).",
    )
    verifier_timeout_seconds: float = Field(
        default=120.0,
        gt=0.0,
        description="Timeout in seconds for independent verification requests (bounded default 120s; set to 220s to retain legacy budget).",
    )
    due_soon_days: int = Field(default=7, ge=0)
    max_llm_calls_per_run: int = Field(default=8)
    max_transient_retries: int = Field(default=1)
    max_repair_attempts: int = Field(default=1)
    db_path: str = Field(default=".runtime/agente.db")
    runs_dir: str = Field(default="runs")
    fixtures_dir: str = Field(default="fixtures")
    is_fake: bool = Field(default=False)

    # Data sources configuration (T5b)
    source_mode: str = Field(
        default="fixtures",
        description="Data source collection mode ('fixtures' or 'real').",
    )
    monitoring_rules_path: Optional[str] = Field(
        default=None,
        description="Optional local JSON owner constraints; not quality policies or authorization.",
    )
    policies_path: str = Field(
        default="config/policies.yaml",
        description="Path to local quality policies YAML file.",
    )
    trello_api_key: Optional[SecretStr] = Field(
        default=None,
        repr=False,
        exclude=True,
        description="Trello API consumer key. SecretStr excluded from repr and dumps.",
    )
    trello_token: Optional[SecretStr] = Field(
        default=None,
        repr=False,
        exclude=True,
        description="Trello OAuth token. SecretStr excluded from repr and dumps.",
    )
    trello_board_id: Optional[str] = Field(
        default=None,
        description="Target Trello board identifier.",
    )
    trello_pending_list_ids: list[str] = Field(default_factory=list)
    trello_in_progress_list_ids: list[str] = Field(default_factory=list)
    trello_completed_list_ids: list[str] = Field(default_factory=list)
    trello_blocked_labels: list[str] = Field(
        default_factory=lambda: ["bloqueada", "blocked"]
    )
    trello_included_list_ids: Optional[list[str]] = Field(default=None)
    trello_state_strategy: str = Field(
        default="list_mapping",
        description="Task state determination strategy for Trello ('list_mapping' or 'date_fields').",
    )

    github_token: Optional[SecretStr] = Field(
        default=None,
        repr=False,
        exclude=True,
        description="GitHub personal access token. SecretStr excluded from repr and dumps.",
    )
    github_owner: Optional[str] = Field(
        default=None,
        description="Target GitHub organization or repository owner.",
    )
    github_repo: Optional[str] = Field(
        default=None,
        description="Target GitHub repository name.",
    )
    github_repository: Optional[str] = Field(
        default=None,
        description="Alias for github_repo.",
    )
    github_branch: Optional[str] = Field(
        default=None,
        description="Target repository branch to resolve commit tip.",
    )
    github_ref: Optional[str] = Field(
        default=None,
        description="Alias for github_branch.",
    )
    github_commit_sha: Optional[str] = Field(
        default=None,
        description="Optional full 40-char or 64-char hexadecimal commit SHA.",
    )
    github_repositories: Optional[tuple[RepositoryConfiguration, ...]] = Field(
        default=None,
        description="Configured GitHub repositories; authoritative when present, without a primary repository.",
    )
    http_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description="Socket/connect HTTP transport timeout in seconds.",
    )
    http_max_response_bytes: int = Field(
        default=10 * 1024 * 1024,
        gt=0,
        description="Maximum allowed HTTP response bytes.",
    )
    multirepo_project_max_http_calls: int = Field(
        default=DEFAULT_MULTIREPO_BUDGETS["project_max_http_calls"], gt=0,
        description="Shared HTTP call cap across one MultiRepo project collection.",
    )
    multirepo_repository_max_http_calls: int = Field(
        default=DEFAULT_MULTIREPO_BUDGETS["repository_max_http_calls"], gt=0,
        description="HTTP call cap for one repository inside a MultiRepo collection.",
    )
    multirepo_project_max_files: int = Field(
        default=DEFAULT_MULTIREPO_BUDGETS["project_max_files"], gt=0,
        description="Shared changed-file cap across one MultiRepo project collection.",
    )
    multirepo_repository_max_files: int = Field(
        default=DEFAULT_MULTIREPO_BUDGETS["repository_max_files"], gt=0,
        description="Changed-file cap for one repository inside a MultiRepo collection.",
    )
    multirepo_project_max_response_bytes: int = Field(
        default=DEFAULT_MULTIREPO_BUDGETS["project_max_response_bytes"], gt=0,
        description="Actual streamed response-byte cap across one MultiRepo project collection.",
    )
    multirepo_repository_max_response_bytes: int = Field(
        default=DEFAULT_MULTIREPO_BUDGETS["repository_max_response_bytes"], gt=0,
        description="Actual streamed response-byte cap for one repository.",
    )
    multirepo_project_timeout_seconds: float = Field(
        default=DEFAULT_MULTIREPO_BUDGETS["project_timeout_seconds"], gt=0,
        description="Wall-clock deadline for one MultiRepo project collection.",
    )
    multirepo_repository_timeout_seconds: float = Field(
        default=DEFAULT_MULTIREPO_BUDGETS["repository_timeout_seconds"], gt=0,
        description="Wall-clock deadline for one repository in a MultiRepo collection.",
    )
    multirepo_project_max_estimated_tokens: int = Field(
        default=DEFAULT_MULTIREPO_BUDGETS["project_max_estimated_tokens"], gt=0,
        description="Estimated source-context token-unit cap across the project; not model usage.",
    )
    multirepo_repository_max_estimated_tokens: int = Field(
        default=DEFAULT_MULTIREPO_BUDGETS["repository_max_estimated_tokens"], gt=0,
        description="Estimated source-context token-unit cap for one repository; not model usage.",
    )

    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    def __init__(
        self,
        *args: Any,
        _yaml_file: Optional[str] = None,
        _yaml_files: Optional[tuple[str, ...]] = None,
        yaml_file: Optional[str] = None,
        config_path: Optional[str] = None,
        **values: Any,
    ):
        target_yaml = _yaml_file or yaml_file or config_path
        target_yamls = _yaml_files if _yaml_files is not None else ((target_yaml,) if target_yaml else ())
        token = _yaml_file_ctx.set(tuple(target_yamls))
        try:
            super().__init__(*args, **values)
        finally:
            _yaml_file_ctx.reset(token)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        # Precedence order: explicit overrides (init) > OS environment > .env > YAML > defaults
        yaml_src = YamlConfigSettingsSource(settings_cls)
        return tuple(
            AliasNormalizingSettingsSource(settings_cls, s)
            for s in (init_settings, env_settings, dotenv_settings, yaml_src)
        )

    @field_validator(
        "multirepo_project_max_http_calls", "multirepo_repository_max_http_calls",
        "multirepo_project_max_files", "multirepo_repository_max_files",
        "multirepo_project_max_response_bytes", "multirepo_repository_max_response_bytes",
        "multirepo_project_max_estimated_tokens", "multirepo_repository_max_estimated_tokens",
        mode="before",
    )
    @classmethod
    def parse_multirepo_integer_budget(cls, value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError("MultiRepo integer budgets must be positive integers.")
        if isinstance(value, str):
            try:
                if not re.fullmatch(r"[0-9]+", value.strip()):
                    raise ValueError
                value = int(value.strip())
            except ValueError:
                raise ValueError("MultiRepo integer budgets must be positive integers.") from None
        if type(value) is not int or value <= 0:
            raise ValueError("MultiRepo integer budgets must be positive integers.")
        return value

    @field_validator(
        "multirepo_project_timeout_seconds", "multirepo_repository_timeout_seconds", mode="before",
    )
    @classmethod
    def parse_multirepo_time_budget(cls, value: Any) -> float:
        if isinstance(value, bool):
            raise ValueError("MultiRepo timeout budgets must be positive finite numbers.")
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError("MultiRepo timeout budgets must be positive finite numbers.") from None
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError("MultiRepo timeout budgets must be positive finite numbers.")
        return value

    @field_validator("due_soon_days", mode="before")
    @classmethod
    def parse_due_soon_days(cls, value: Any) -> int:
        # Environment values are strings; reject float/bool coercion in YAML/overrides.
        if isinstance(value, str):
            try:
                value = int(value)
            except ValueError:
                raise ValueError("due_soon_days must be a nonnegative integer")
        return validate_due_soon_days(value)

    @field_validator("timezone", mode="before")
    @classmethod
    def parse_timezone(cls, v: Any) -> str:
        if v is None:
            return "America/Guayaquil"
        if not isinstance(v, str) or not v.strip():
            raise ValueError("timezone must be a non-empty string.")
        cleaned = v.strip()
        try:
            ZoneInfo(cleaned)
        except Exception:
            raise ValueError(f"Invalid timezone: '{cleaned}'.")
        return cleaned

    @field_validator("source_mode", mode="before")
    @classmethod
    def parse_source_mode(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("source_mode must be a string")
        cleaned = v.strip().lower()
        if cleaned not in ("fixtures", "real"):
            raise ValueError(f"Invalid source_mode: '{v}'. Must be 'fixtures' or 'real'.")
        return cleaned

    @field_validator("trello_state_strategy", mode="before")
    @classmethod
    def parse_trello_state_strategy(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("trello_state_strategy must be a string")
        cleaned = v.strip().lower()
        if not cleaned:
            raise ValueError("trello_state_strategy cannot be empty")
        if cleaned not in ("list_mapping", "date_fields"):
            raise ValueError(f"Invalid trello_state_strategy: '{v}'. Must be 'list_mapping' or 'date_fields'.")
        return cleaned

    @field_validator("allow_remote_ai", mode="before")
    @classmethod
    def parse_allow_remote_ai(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            val = v.strip().lower()
            if val in ("false", "0", "no", "off", "f", "n", ""):
                return False
            if val in ("true", "1", "yes", "on", "t", "y"):
                return True
            raise ValueError(f"Invalid boolean value for allow_remote_ai: {v!r}")
        return bool(v)

    @field_validator("monitoring_provider", mode="before")
    @classmethod
    def parse_monitoring_provider(cls, value: Any) -> str:
        if not isinstance(value, str) or value.strip().lower() != "gemini":
            raise ValueError("monitoring_provider must be 'gemini'")
        return "gemini"

    @field_validator("quality_provider", mode="before")
    @classmethod
    def parse_quality_provider(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("quality_provider must be a string")
        cleaned = v.strip().lower()
        if cleaned != "groq":
            raise ValueError(f"Invalid quality_provider: '{v}'. Must be 'groq'.")
        return "groq"

    @field_validator(
        "gemini_api_key",
        "groq_api_key",
        "trello_api_key",
        "trello_token",
        "github_token",
        mode="before",
    )
    @classmethod
    def parse_secret_fields(cls, v: Any) -> Optional[SecretStr]:
        if v is None:
            return None
        if isinstance(v, SecretStr):
            return v if v.get_secret_value().strip() else None
        if isinstance(v, str):
            cleaned = v.strip()
            return SecretStr(cleaned) if cleaned else None
        return None

    @field_validator("github_repo", "github_repository", mode="before")
    @classmethod
    def normalize_github_repository_value(cls, value: Any) -> str | None:
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise ValueError("GitHub repository must be a repository name or owner/repository.")
        cleaned = value.strip()
        if (not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?", cleaned)
                or cleaned in {".", ".."} or any(part in {".", ".."} for part in cleaned.split("/"))):
            raise ValueError("GitHub repository must be a repository name or owner/repository.")
        return cleaned

    @field_validator("github_owner", mode="before")
    @classmethod
    def normalize_github_owner_value(cls, value: Any) -> str | None:
        if value is None or value == "":
            return None
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value.strip()):
            raise ValueError("GitHub owner is not valid.")
        return value.strip()

    @field_validator("github_repositories")
    @classmethod
    def validate_github_repositories(
        cls, value: Optional[tuple[RepositoryConfiguration, ...]],
    ) -> Optional[tuple[RepositoryConfiguration, ...]]:
        if value is None:
            return None
        if not value:
            raise ValueError("github_repositories must contain at least one repository.")
        ids = [repository.id for repository in value]
        if len(ids) != len(set(ids)):
            raise ValueError("GitHub repository ids must be unique.")
        return value

    @model_validator(mode="after")
    def validate_sources_consistency(self) -> "Settings":
        if (self.github_repo and self.github_repository
                and self.github_repo != self.github_repository):
            raise ValueError(
                "Contradictory GitHub repository configuration: repo and repository aliases do not match."
            )
        selected_repo = self.github_repository or self.github_repo
        if selected_repo:
            if "/" in selected_repo:
                owner, repository_name = selected_repo.split("/", 1)
                if (self.github_repositories is not None and self.github_owner
                        and self.github_owner != owner):
                    raise ValueError(
                        "Contradictory GitHub owner and repository configuration: owner does not match repository path."
                    )
                object.__setattr__(self, "github_owner", owner)
            else:
                repository_name = selected_repo
            object.__setattr__(self, "github_repo", repository_name)
            object.__setattr__(self, "github_repository", repository_name)

        # Reconcile github_branch and github_ref aliases
        branch = self.github_branch
        ref = self.github_ref
        if branch and ref and branch != ref:
            raise ValueError(
                "Contradictory GitHub branch configuration: branch and ref aliases do not match."
            )
        if ref and not branch:
            object.__setattr__(self, "github_branch", ref)
        elif branch and not ref:
            object.__setattr__(self, "github_ref", branch)

        # Validate commit SHA format if provided
        if self.github_commit_sha:
            raw_sha = self.github_commit_sha.strip()
            if not FULL_SHA_RE.match(raw_sha):
                raise ValueError(
                    "Invalid GitHub commit SHA: must be a full 40-character or 64-character hexadecimal SHA."
                )
            object.__setattr__(self, "github_commit_sha", raw_sha.lower())

        # Legacy singleton values may corroborate one collection entry, but never
        # select, distribute to, or override entries in the authoritative list.
        if self.github_repositories is not None and any((
            self.github_owner, self.github_repo, self.github_branch, self.github_commit_sha,
        )):
            legacy_values = {
                "owner": self.github_owner,
                "repo": self.github_repo,
                "branch": self.github_branch,
                "commit_sha": self.github_commit_sha,
            }
            candidates = [
                repository for repository in self.github_repositories
                if all(value is None or getattr(repository, key) == value
                       for key, value in legacy_values.items())
            ]
            if len(candidates) != 1:
                raise ValueError(
                    "Legacy GitHub configuration must match exactly one configured repository; "
                    "it cannot override or distribute values across the repository collection."
                )

        # Validate budgets
        if not math.isfinite(self.http_timeout_seconds) or self.http_timeout_seconds <= 0.0:
            raise ValueError("http_timeout_seconds must be a positive finite number.")
        if self.http_max_response_bytes <= 0:
            raise ValueError("http_max_response_bytes must be a positive integer.")

        return self

    @model_serializer(mode="wrap")
    def serialize_legacy_shape(self, handler):
        data = handler(self)
        if self.github_repositories is None:
            data.pop("github_repositories", None)
            for key in DEFAULT_MULTIREPO_BUDGETS:
                data.pop(f"multirepo_{key}", None)
        return data

    def get_trello_mapping(self) -> Any:
        from agente_gestion.domain.tasks import TaskMappingConfig
        return TaskMappingConfig(
            pending_list_ids=list(self.trello_pending_list_ids),
            in_progress_list_ids=list(self.trello_in_progress_list_ids),
            completed_list_ids=list(self.trello_completed_list_ids),
            blocked_labels=list(self.trello_blocked_labels),
            included_list_ids=list(self.trello_included_list_ids) if self.trello_included_list_ids is not None else None,
        )

    def get_known_secret_values(self) -> tuple[str, ...]:
        secrets: list[str] = []
        for fld in (
            "gemini_api_key",
            "groq_api_key",
            "trello_api_key",
            "trello_token",
            "github_token",
        ):
            val = getattr(self, fld, None)
            if isinstance(val, SecretStr):
                sec = val.get_secret_value().strip()
                if sec:
                    secrets.append(sec)
        return tuple(secrets)

    @classmethod
    def from_env_and_file(
        cls,
        config_path: Optional[str] = None,
        overrides: Optional[dict[str, Any]] = None,
        env_file: Optional[str] = None,
        _env_file: Any = ...,
    ) -> "Settings":
        """Load base/project/explicit YAML, then dotenv and process overrides."""
        kwargs: dict[str, Any] = {}
        if overrides:
            for aliases, label in (
                (("repo", "repository", "github_repo", "github_repository"), "GitHub repository"),
                (("branch", "ref", "github_branch", "github_ref"), "GitHub branch"),
            ):
                supplied = [overrides[key] for key in aliases if key in overrides and overrides[key] not in (None, "")]
                if supplied and any(value != supplied[0] for value in supplied[1:]):
                    raise ValueError(f"Contradictory {label} aliases in explicit configuration.")
            for k, v in overrides.items():
                if v is None:
                    continue
                if k in ("sources", "source_mode"):
                    kwargs["source_mode"] = v
                elif k in ("commit", "sha", "commit_sha", "github_commit_sha"):
                    kwargs["github_commit_sha"] = v
                elif k in ("branch", "ref", "github_branch", "github_ref"):
                    kwargs["github_branch"] = v
                    kwargs["github_ref"] = v
                elif k in ("repo", "repository", "github_repo", "github_repository"):
                    kwargs["github_repo"] = v
                    kwargs["github_repository"] = v
                else:
                    kwargs[k] = v

        if _env_file is not ...:
            dotenv_path = _env_file
            kwargs["_env_file"] = _env_file
        elif env_file is not None:
            dotenv_path = env_file
            kwargs["_env_file"] = env_file
        else:
            dotenv_path = PROJECT_ROOT / ".env"
            kwargs["_env_file"] = str(dotenv_path)

        dotenv_values_by_name: dict[str, str] = {}
        if dotenv_path is not None:
            candidate = Path(dotenv_path)
            if not candidate.is_absolute() and dotenv_path == ".env":
                candidate = PROJECT_ROOT / candidate
            if candidate.is_file():
                dotenv_values_by_name = {
                    key: value for key, value in dotenv_values(candidate, interpolate=False).items()
                    if value is not None
                }

        selected_config = (
            config_path or os.environ.get("AGENTE_CONFIG_PATH")
            or dotenv_values_by_name.get("AGENTE_CONFIG_PATH")
        )
        selected_path = Path(selected_config) if selected_config else None
        if selected_path is not None and not selected_path.is_absolute():
            selected_path = PROJECT_ROOT / selected_path
        kwargs["_yaml_files"] = tuple(
            str(path) for path in _yaml_configuration_paths(PROJECT_ROOT, selected_path)
        )

        return cls(**kwargs)


def _yaml_project_id(config_path: Path) -> str | None:
    """Read the identity declared by a YAML file without treating dotenv as YAML."""
    if config_path.name.startswith(".env") or config_path.resolve(strict=False).name.startswith(".env"):
        return None
    project_id = _load_yaml_file(str(config_path)).get("project_id")
    return str(project_id) if project_id is not None else None


def _yaml_configuration_paths(project_root: Path, selected_config: Path | None) -> tuple[Path, ...]:
    """Layer base defaults, the default project, then an optional scoped YAML file."""
    base_config = project_root / "config" / "config.yaml"
    project_config = project_root / "config" / "hormigestion.yaml"
    paths = [base_config]

    explicit_is_yaml = selected_config is not None and not (
        selected_config.name.startswith(".env")
        or selected_config.resolve(strict=False).name.startswith(".env")
    )
    explicit_project_id = _yaml_project_id(selected_config) if explicit_is_yaml else None
    default_project_id = _yaml_project_id(project_config)
    if not (explicit_project_id and default_project_id and explicit_project_id != default_project_id):
        paths.append(project_config)

    if explicit_is_yaml and all(
        selected_config.resolve(strict=False) != path.resolve(strict=False) for path in paths
    ):
        paths.append(selected_config)
    return tuple(paths)


# SystemConfig is an alias for backwards compatibility
SystemConfig = Settings

__all__ = ["Settings", "SystemConfig"]
