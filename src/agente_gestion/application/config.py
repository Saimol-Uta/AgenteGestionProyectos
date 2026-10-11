"""Configuration loading, validation, and environment defaults using pydantic-settings."""

import math
import os
import re
from contextvars import ContextVar
from typing import Any, Dict, Optional, Tuple, Type
from zoneinfo import ZoneInfo
from agente_gestion.domain.tasks import validate_due_soon_days
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

try:
    import yaml
except ImportError:
    yaml = None

_yaml_file_ctx: ContextVar[Optional[str]] = ContextVar("_yaml_file_ctx", default=None)

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

    if "providers" in file_data and isinstance(file_data["providers"], dict):
        prov_sec = file_data["providers"]
        if "local" in prov_sec and isinstance(prov_sec["local"], dict):
            local_sec = prov_sec["local"]
            if "base_url" in local_sec:
                merged["ollama_base_url"] = local_sec["base_url"]
            if "model" in local_sec:
                merged["ollama_monitoring_model"] = local_sec["model"]
                merged["ollama_report_model"] = local_sec["model"]
        if "quality" in prov_sec and isinstance(prov_sec["quality"], dict):
            q_sec = prov_sec["quality"]
            if "provider" in q_sec:
                merged["quality_provider"] = q_sec["provider"]
            if "model" in q_sec:
                eff_prov = str(q_sec.get("provider", "groq")).lower()
                if eff_prov == "groq":
                    merged["groq_model"] = q_sec["model"]
                elif eff_prov == "mistral":
                    merged["mistral_model"] = q_sec["model"]
                elif eff_prov == "ollama":
                    merged["ollama_quality_model"] = q_sec["model"]

    if "roles" in file_data and isinstance(file_data["roles"], dict):
        roles_sec = file_data["roles"]
        if "revisor" in roles_sec and roles_sec["revisor"] in ("groq", "mistral", "ollama"):
            merged["quality_provider"] = roles_sec["revisor"]

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
            if repo_val is not None and repo_alias is not None and repo_val != repo_alias:
                raise ValueError("Contradictory GitHub repository configuration: repo and repository aliases do not match.")
            eff_repo = repo_val if repo_val is not None else repo_alias
            if eff_repo is not None:
                merged["github_repo"] = eff_repo
                merged["github_repository"] = eff_repo

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

    # Directly specified top-level keys
    for k, v in file_data.items():
        if v is not None and not isinstance(v, dict):
            merged[k] = v

    return merged


class YamlConfigSettingsSource(PydanticBaseSettingsSource):
    """Loads configuration values from a YAML file with lower precedence than dotenv and OS."""

    def get_field_value(
        self, field: Any, field_name: str
    ) -> Tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> Dict[str, Any]:
        yaml_path = _yaml_file_ctx.get()
        if not yaml_path:
            return {}
        return _load_yaml_file(yaml_path)


class AliasNormalizingSettingsSource(PydanticBaseSettingsSource):
    """Normalizes field aliases within each configuration source before layer merging."""

    def __init__(self, settings_cls: Type[BaseSettings], inner: PydanticBaseSettingsSource):
        super().__init__(settings_cls)
        self.inner = inner

    def get_field_value(self, field: Any, field_name: str) -> Tuple[Any, str, bool]:
        return self.inner.get_field_value(field, field_name)

    def __call__(self) -> Dict[str, Any]:
        data = dict(self.inner())
        # Reconcile github_repo and github_repository within this source
        r1 = data.get("github_repo")
        r2 = data.get("github_repository")
        if r1 is not None and r2 is not None and r1 != r2:
            raise ValueError("Contradictory GitHub repository configuration: repo and repository aliases do not match.")
        resolved_repo = r1 if r1 is not None else r2
        if resolved_repo is not None:
            data["github_repo"] = resolved_repo
            data["github_repository"] = resolved_repo

        # Reconcile github_branch and github_ref within this source
        b1 = data.get("github_branch")
        b2 = data.get("github_ref")
        if b1 is not None and b2 is not None and b1 != b2:
            raise ValueError("Contradictory GitHub branch configuration: branch and ref aliases do not match.")
        resolved_branch = b1 if b1 is not None else b2
        if resolved_branch is not None:
            data["github_branch"] = resolved_branch
            data["github_ref"] = resolved_branch

        return data


class Settings(BaseSettings):
    """Centralized configuration governing project analysis runs and adapter integrations."""

    project_id: str = Field(default="proyecto-demo")
    default_fixture: str = Field(default="fixture_1_standard")
    timezone: str = Field(default="America/Guayaquil")
    allow_remote_ai: bool = Field(
        default=False,
        description="Whether remote AI providers (e.g. Gemini) are permitted. Must be explicitly enabled.",
    )
    ollama_base_url: str = Field(
        default="http://127.0.0.1:11435",
        description="Ollama server base URL. Default port 11435 per ARCHITECTURA.v1.",
    )
    ollama_monitoring_model: str = Field(default="qwen3.5:2b-q4_K_M")
    ollama_quality_model: str = Field(default="qwen3.5:4b-q4_K_M")
    ollama_report_model: str = Field(default="qwen3.5:2b-q4_K_M")
    gemini_api_key: Optional[SecretStr] = Field(
        default=None,
        repr=False,
        exclude=True,
        description="Google Gemini API key. SecretStr excluded from repr, dumps, and fingerprints.",
    )
    gemini_model: str = Field(default="gemini-2.5-flash")
    quality_provider: str = Field(
        default="ollama",
        description="LLM provider for CodeQualityAgent ('ollama', 'groq', 'mistral'). Default: ollama.",
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
    mistral_api_key: Optional[SecretStr] = Field(
        default=None,
        repr=False,
        exclude=True,
        description="Mistral API key. SecretStr excluded from repr, dumps, and fingerprints.",
    )
    mistral_model: str = Field(
        default="mistral-large-latest",
        description="Mistral model identifier for code quality review.",
    )
    mistral_base_url: str = Field(
        default="https://api.mistral.ai/v1",
        description="Mistral API base URL.",
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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    def __init__(
        self,
        *args: Any,
        _yaml_file: Optional[str] = None,
        yaml_file: Optional[str] = None,
        config_path: Optional[str] = None,
        **values: Any,
    ):
        target_yaml = _yaml_file or yaml_file or config_path
        token = _yaml_file_ctx.set(target_yaml)
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

    @field_validator("quality_provider", mode="before")
    @classmethod
    def parse_quality_provider(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("quality_provider must be a string")
        cleaned = v.strip().lower()
        if cleaned not in ("ollama", "groq", "mistral"):
            raise ValueError(
                f"Invalid quality_provider: '{v}'. Must be 'ollama', 'groq', or 'mistral'."
            )
        return cleaned

    @field_validator(
        "gemini_api_key",
        "groq_api_key",
        "mistral_api_key",
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

    @model_validator(mode="after")
    def validate_sources_consistency(self) -> "Settings":
        # Reconcile github_repo and github_repository aliases
        repo = self.github_repo
        repository = self.github_repository
        if repo and repository and repo != repository:
            raise ValueError(
                "Contradictory GitHub repository configuration: repo and repository aliases do not match."
            )
        if repository and not repo:
            object.__setattr__(self, "github_repo", repository)
        elif repo and not repository:
            object.__setattr__(self, "github_repository", repo)

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

        # Validate budgets
        if not math.isfinite(self.http_timeout_seconds) or self.http_timeout_seconds <= 0.0:
            raise ValueError("http_timeout_seconds must be a positive finite number.")
        if self.http_max_response_bytes <= 0:
            raise ValueError("http_max_response_bytes must be a positive integer.")

        return self

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
            "mistral_api_key",
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
        """Loads configuration with precedence: overrides > OS env > .env > YAML > defaults."""
        kwargs: dict[str, Any] = {}
        if overrides:
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
            kwargs["_env_file"] = _env_file
        elif env_file is not None:
            kwargs["_env_file"] = env_file

        if config_path:
            kwargs["_yaml_file"] = config_path

        return cls(**kwargs)


# SystemConfig is an alias for backwards compatibility
SystemConfig = Settings

__all__ = ["Settings", "SystemConfig"]
