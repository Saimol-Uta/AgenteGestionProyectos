"""Configuration loading, validation, and environment defaults using pydantic-settings."""

import os
from contextvars import ContextVar
from typing import Any, Dict, Optional, Tuple, Type
from pydantic import Field, SecretStr, field_validator
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
        if "max_llm_calls_per_run" in exec_sec:
            merged["max_llm_calls_per_run"] = exec_sec["max_llm_calls_per_run"]
        if "quality_timeout_seconds" in exec_sec:
            merged["quality_timeout_seconds"] = exec_sec["quality_timeout_seconds"]

    if "providers" in file_data and isinstance(file_data["providers"], dict):
        prov_sec = file_data["providers"]
        if "local" in prov_sec and isinstance(prov_sec["local"], dict):
            local_sec = prov_sec["local"]
            if "base_url" in local_sec:
                merged["ollama_base_url"] = local_sec["base_url"]
            if "model" in local_sec:
                merged["ollama_monitoring_model"] = local_sec["model"]
                merged["ollama_report_model"] = local_sec["model"]

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
    quality_timeout_seconds: float = Field(
        default=120.0,
        description="Timeout in seconds for code quality review requests (bounded default 120s per ARCHITECTURA/T4).",
    )
    max_llm_calls_per_run: int = Field(default=8)
    max_transient_retries: int = Field(default=1)
    max_repair_attempts: int = Field(default=1)
    db_path: str = Field(default=".runtime/agente.db")
    runs_dir: str = Field(default="runs")
    fixtures_dir: str = Field(default="fixtures")
    is_fake: bool = Field(default=False)

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
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
        )

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

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def parse_gemini_api_key(cls, v: Any) -> Optional[SecretStr]:
        if v is None:
            return None
        if isinstance(v, SecretStr):
            return v if v.get_secret_value().strip() else None
        if isinstance(v, str):
            cleaned = v.strip()
            return SecretStr(cleaned) if cleaned else None
        return None

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
                if v is not None:
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
