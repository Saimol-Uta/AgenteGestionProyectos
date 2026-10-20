"""Bounded, opt-in analysis preparation and local durable job tracking."""

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
from threading import RLock
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

from pydantic import SecretStr

from agente_gestion.adapters.sources.local_monitoring_rules import (
    MonitoringRulesError,
    load_monitoring_rules,
)
from agente_gestion.api.errors import ApiProblem
from agente_gestion.api.repository import SQLiteQueryRepository
from agente_gestion.api.schemas import (
    AnalysisPreparation,
    AnalysisPreflight,
    AnalysisJobView,
    PreflightCheck,
    ProviderPreflight,
    RemotePreflight,
    RepositoryPreflight,
)
from agente_gestion.api.security import safe_identifier, sanitize_text
from agente_gestion.api.settings import ApiSettings
from agente_gestion.application.config import Settings as EngineSettings
from agente_gestion.domain.report import DomainProposalRuleSet


@dataclass(frozen=True)
class AnalysisRuntimePlan:
    """Private effective configuration plus its deliberately narrow public summary."""

    fingerprint: str
    preflight: AnalysisPreflight
    remote: RemotePreflight
    execution_mode: str = "sandbox"
    fallback_reasons: tuple[str, ...] = ()
    engine_settings: EngineSettings | None = field(default=None, repr=False, compare=False)
    proposal_rules: DomainProposalRuleSet | None = field(default=None, repr=False, compare=False)
    proposal_rules_status: str = "not_configured"

    @property
    def eligible(self) -> bool:
        return (
            self.preflight.configuration_status == "ready"
            and (not self.remote.required or self.remote.server_allowed)
            and self.engine_settings is not None
        )


def _resolve_proposal_rules(
    project: Any, engine: EngineSettings | None, project_id: str,
) -> tuple[DomainProposalRuleSet | None, str]:
    """Load owner rules for one project through the validated shared loader.

    The explicit per-project path wins over the shared YAML key. The loader
    itself binds the file to ``project_id`` and rejects any other project, so a
    rules file can never serve a project it was not written for.
    """
    explicit = getattr(project, "monitoring_rules_path", None)
    shared = getattr(engine, "monitoring_rules_path", None) if engine is not None else None
    path = explicit or shared
    if not isinstance(path, str) or not path.strip():
        return None, "not_configured"
    try:
        rules = load_monitoring_rules(path, project_id)
    except MonitoringRulesError as error:
        return None, "invalid" if error.reason != "project_mismatch" else "not_configured"
    return rules, "configured"


def _credential_status(secret: SecretStr | None) -> str:
    return "configured" if secret and secret.get_secret_value() else "missing"


def _public_model_name(value: object) -> str:
    if not isinstance(value, str) or len(value) > 80:
        return "unknown"
    cleaned = sanitize_text(value, limit=80)
    if (cleaned != value or re.search(r"(?i)(?:api[_-]?key|token|secret|password|bearer|credential)", value)
            or "://" in value or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}", value)):
        return "unknown"
    return value


def _transport(provider: str) -> str:
    if provider in {"gemini", "groq", "trello", "github", "github_actions"}:
        return "remote"
    if provider in {"fake_simulated", "sandbox"}:
        return "local"
    return "unknown"


def _repository_config_fingerprint(repositories: tuple[Any, ...]) -> list[dict[str, Any]]:
    """Keep configured repository order and every source selector in the digest."""
    return [repository.model_dump(mode="json") for repository in repositories]


def _fingerprint(
    api_settings: ApiSettings, engine: EngineSettings | None, project_id: str,
    proposal_rules: DomainProposalRuleSet | None = None,
) -> str:
    effective: dict[str, Any] = {
        "project_id": project_id,
        "api_db_path": str(Path(api_settings.db_path).absolute()),
        "runs_dir": api_settings.runs_dir,
        "analysis_enabled": api_settings.analysis_enabled,
    }
    project = next((item for item in api_settings.projects if item.id == project_id), None)
    if project is not None and project.repositories:
        effective["api_repository_collection"] = _repository_config_fingerprint(project.repositories)
    if proposal_rules is not None:
        effective["proposal_rules"] = proposal_rules.model_dump(mode="json")
    if engine is not None:
        engine_values: dict[str, Any] = {}
        for name in EngineSettings.model_fields:
            value = getattr(engine, name)
            if isinstance(value, SecretStr):
                value = hashlib.sha256(value.get_secret_value().encode("utf-8")).hexdigest()
            elif name == "github_repositories" and value is not None:
                value = _repository_config_fingerprint(value)
            elif hasattr(value, "model_dump"):
                value = value.model_dump(mode="json")
            engine_values[name] = value
        effective["engine"] = engine_values
    data = json.dumps(effective, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _safe_repository_field(value: str | None, secrets_to_redact: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    if (value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", value)
            or "://" in value):
        return None
    cleaned = sanitize_text(value, secrets_to_redact=secrets_to_redact, limit=512)
    cleaned = re.sub(r"(?<![A-Za-z0-9:])/(?:[^/\s]+/)+[^/\s,;]+", "[local path omitted]", cleaned)
    cleaned = re.sub(r"(?i)\b[A-Z]:\\[^\s,;]+", "[local path omitted]", cleaned)
    return cleaned


def _repository_preflight(
    repositories: tuple[Any, ...], token: SecretStr | None,
) -> list[RepositoryPreflight]:
    secrets_to_redact = (token.get_secret_value(),) if token is not None else ()
    credential_status = "configured" if token and token.get_secret_value() else "missing"
    return [
        RepositoryPreflight(
            repository_id=repository.id,
            owner=repository.owner,
            repo=repository.repo,
            branch=_safe_repository_field(repository.branch, secrets_to_redact),
            configured_commit_sha=repository.commit_sha,
            quality_workflow_path=_safe_repository_field(
                repository.quality_workflow_path, secrets_to_redact,
            ),
            credential_status=credential_status,
            commit_resolution_status="pending",
            access_status="unknown",
            availability_status="unknown",
        )
        for repository in repositories
    ]


def _unavailable_plan(api_settings: ApiSettings, project_id: str) -> AnalysisRuntimePlan:
    project = next((item for item in api_settings.projects if item.id == project_id), None)
    return AnalysisRuntimePlan(
        fingerprint=_fingerprint(api_settings, None, project_id),
        preflight=AnalysisPreflight(
            source_mode=project.source_mode if project else "unknown",
            configuration_status="incomplete",
            checks=[PreflightCheck(code="configuration_unavailable", status="incomplete")],
            repositories=_repository_preflight(project.repositories, api_settings.github_token)
            if project else [],
        ),
        remote=RemotePreflight(
            required=False, server_allowed=False, providers=[],
        ),
        execution_mode="sandbox",
        fallback_reasons=("Effective analysis configuration is unavailable; no run can be confirmed.",),
    )


def load_analysis_plan(api_settings: ApiSettings, project_id: str) -> AnalysisRuntimePlan:
    """Load one effective config using the same YAML and dotenv layers as API startup."""
    project = next((item for item in api_settings.projects if item.id == project_id), None)
    source_mode = project.source_mode if project else "unknown"
    config_path = api_settings.analysis_config_path
    if (not config_path or Path(config_path).name.startswith(".env")
            or Path(config_path).resolve(strict=False).name.startswith(".env")):
        return _unavailable_plan(api_settings, project_id)

    overrides: dict[str, Any] = {
        "project_id": project_id,
        "source_mode": source_mode,
        "db_path": api_settings.db_path,
        "runs_dir": api_settings.runs_dir,
        "timezone": api_settings.timezone,
        "due_soon_days": api_settings.due_soon_days,
    }
    for name, value in (
        ("github_token", api_settings.github_token),
        ("trello_api_key", api_settings.trello_api_key),
        ("trello_token", api_settings.trello_token),
    ):
        if value is not None:
            overrides[name] = value
    if api_settings.github_repository:
        repository = api_settings.github_repository.split("/", 1)
        if len(repository) == 2:
            overrides.update(github_owner=repository[0], github_repo=repository[1])
    mapped_board = api_settings.trello_board_ids.get(project_id)
    if api_settings.trello_board_bindings_explicit:
        overrides["trello_board_id"] = mapped_board or ""
    elif mapped_board:
        overrides["trello_board_id"] = mapped_board

    try:
        engine = EngineSettings.from_env_and_file(
            config_path=config_path,
            overrides=overrides,
            _env_file=api_settings.analysis_env_file,
        )
        project_repositories = project.repositories if project is not None else ()
        if project_repositories and engine.github_repositories is None:
            # Directly supplied API settings use the already-normalized project collection.
            overrides["github_repositories"] = project_repositories
            engine = EngineSettings.from_env_and_file(
                config_path=config_path,
                overrides=overrides,
                _env_file=api_settings.analysis_env_file,
            )
        repository_collection_mismatch = bool(
            project_repositories and engine.github_repositories is not None
            and tuple(project_repositories) != tuple(engine.github_repositories)
        )
    except Exception:
        return _unavailable_plan(api_settings, project_id)

    proposal_rules, proposal_rules_status = _resolve_proposal_rules(project, engine, project_id)
    configured_repositories = tuple(project_repositories or engine.github_repositories or ())
    repository_preflights = _repository_preflight(configured_repositories, engine.github_token)
    providers: list[ProviderPreflight] = []
    fallback_reasons: list[str] = []
    remote_used = False
    remote_permission = bool(engine.allow_remote_ai and not engine.is_fake)

    def add_model(stage: str, provider: str, model: str, secret: SecretStr | None) -> None:
        nonlocal remote_used
        credential_status = _credential_status(secret)
        if engine.is_fake:
            providers.append(ProviderPreflight(
                stage=stage, provider="fake_simulated", model=f"sandbox-{stage}",
                transport="local", credential_status="not_required",
            ))
            return
        if remote_permission and credential_status == "configured":
            remote_used = True
            providers.append(ProviderPreflight(
                stage=stage, provider=provider, model=_public_model_name(model),
                transport="remote", credential_status="configured",
            ))
            return
        reason = (
            f"{stage}: ALLOW_REMOTE_AI is false; deterministic sandbox selected."
            if not engine.allow_remote_ai else
            f"{stage}: {provider} credentials are missing; deterministic sandbox selected."
        )
        fallback_reasons.append(reason)
        providers.append(ProviderPreflight(
            stage=stage, provider="fake_simulated", model=f"sandbox-{stage}",
            transport="local", credential_status=credential_status,
        ))

    add_model("monitoring", "gemini", engine.gemini_monitoring_model, engine.gemini_api_key)
    # Consent must match the same provider selection used by _build_orchestrator.
    if engine.quality_provider == "gemini":
        add_model(
            "quality", "gemini",
            engine.gemini_quality_model or engine.gemini_monitoring_model,
            engine.gemini_api_key,
        )
    elif engine.quality_provider == "mistral":
        add_model("quality", "mistral", engine.mistral_model, engine.mistral_api_key)
    else:
        add_model("quality", "groq", engine.groq_model, engine.groq_api_key)
    add_model("verifier", "gemini", engine.gemini_model, engine.gemini_api_key)
    add_model("report", "gemini", engine.gemini_report_model, engine.gemini_api_key)

    checks: list[PreflightCheck] = []
    if configured_repositories:
        checks.append(PreflightCheck(
            code="repository_collection_configuration",
            status="incomplete" if repository_collection_mismatch else "ready",
        ))
    source_remote_ready = False
    source_needs_fixture = False
    if engine.source_mode == "fixtures":
        source_needs_fixture = True
    elif engine.source_mode == "real":
        task_ready = bool(engine.trello_board_id and engine.trello_api_key and engine.trello_token)
        repo_ready = bool(engine.github_owner and engine.github_repo and engine.github_token)
        ci_ready = bool(repo_ready and engine.github_commit_sha)

        def add_source(stage: str, provider: str, ready: bool, configured: bool) -> None:
            nonlocal source_remote_ready, source_needs_fixture
            if remote_permission and ready:
                source_remote_ready = True
                providers.append(ProviderPreflight(
                    stage=stage, provider=provider, model="not_applicable",
                    transport="remote", credential_status="configured",
                ))
                return
            source_needs_fixture = True
            if engine.is_fake:
                reason = f"{stage}: explicit fake mode selects the deterministic sandbox."
            elif not engine.allow_remote_ai:
                reason = f"{stage}: ALLOW_REMOTE_AI is false; local fixture source selected."
            else:
                reason = f"{stage}: remote credentials or resource binding are missing; local fixture source selected."
            fallback_reasons.append(reason)
            providers.append(ProviderPreflight(
                stage=stage, provider="sandbox", model="fixture-source", transport="local",
                credential_status="configured" if configured else "missing",
            ))

        def add_repository_source(stage: str, provider: str, repository_id: str) -> None:
            nonlocal source_remote_ready, source_needs_fixture
            credential_configured = bool(
                engine.github_token and engine.github_token.get_secret_value()
            )
            if remote_permission and credential_configured:
                source_remote_ready = True
                providers.append(ProviderPreflight(
                    stage=stage, provider=provider, model="not_applicable",
                    transport="remote", credential_status="configured",
                    repository_id=repository_id,
                ))
                return
            source_needs_fixture = True
            if engine.is_fake:
                reason = f"{stage} ({repository_id}): explicit fake mode selects the deterministic sandbox."
            elif not engine.allow_remote_ai:
                reason = f"{stage} ({repository_id}): ALLOW_REMOTE_AI is false; local fixture source selected."
            else:
                reason = f"{stage} ({repository_id}): GitHub credentials are missing; local fixture source selected."
            fallback_reasons.append(reason)
            providers.append(ProviderPreflight(
                stage=stage, provider="sandbox", model="fixture-source", transport="local",
                credential_status="configured" if credential_configured else "missing",
                repository_id=repository_id,
            ))

        add_source("task_source", "trello", task_ready, bool(engine.trello_api_key and engine.trello_token))
        if configured_repositories:
            for repository in configured_repositories:
                add_repository_source("code_source", "github", repository.id)
                add_repository_source("ci_source", "github_actions", repository.id)
        else:
            add_source("code_source", "github", repo_ready, bool(engine.github_owner and engine.github_repo and engine.github_token))
            add_source("ci_source", "github_actions", ci_ready, bool(engine.github_token and engine.github_commit_sha))

        policy_ready = Path(engine.policies_path).is_file()
        if not policy_ready:
            source_needs_fixture = True
            fallback_reasons.append("policy_source: local policy file is missing; fixture policy source selected.")
        checks.append(PreflightCheck(
            code="policy_configuration",
            status="ready" if policy_ready or Path(engine.fixtures_dir).is_dir() else "incomplete",
        ))
    else:
        checks.append(PreflightCheck(code="source_mode", status="incomplete"))

    if source_needs_fixture:
        fixtures_ready = Path(engine.fixtures_dir).is_dir()
        checks.append(PreflightCheck(
            code="sandbox_source", status="ready" if fixtures_ready else "incomplete",
        ))
    elif engine.source_mode == "fixtures":
        fixtures_ready = Path(engine.fixtures_dir).is_dir()
        checks.append(PreflightCheck(
            code="fixture_source", status="ready" if fixtures_ready else "incomplete",
        ))
    if not checks:
        checks.append(PreflightCheck(code="effective_configuration", status="ready"))
    configuration_status = "ready" if all(check.status == "ready" for check in checks) else "incomplete"

    remote_required = remote_used or source_remote_ready
    if remote_required and fallback_reasons:
        execution_mode = "mixed"
    elif remote_required:
        execution_mode = "live"
    else:
        execution_mode = "sandbox"
    remote = RemotePreflight(
        required=remote_required,
        server_allowed=bool(engine.allow_remote_ai),
        availability="not_checked",
        providers=providers,
    )
    return AnalysisRuntimePlan(
        fingerprint=_fingerprint(api_settings, engine, project_id, proposal_rules),
        preflight=AnalysisPreflight(
            source_mode=engine.source_mode,
            configuration_status=configuration_status,
            checks=checks,
            repositories=repository_preflights,
            proposal_rules_status=proposal_rules_status,
        ),
        remote=remote,
        execution_mode=execution_mode,
        fallback_reasons=tuple(fallback_reasons),
        engine_settings=engine,
        proposal_rules=proposal_rules,
        proposal_rules_status=proposal_rules_status,
    )


@dataclass(frozen=True)
class _PreparedAnalysis:
    public: AnalysisPreparation
    plan: AnalysisRuntimePlan = field(repr=False)
    session_fingerprint: str
    expires_monotonic: float


class AnalysisJobLedger:
    """Separate local job ledger. Reads are read-only; schema creation is explicit POST-only."""

    def __init__(self, path: str, main_database_path: str):
        self.path = Path(path)
        self.main_database_path = Path(main_database_path)
        resolved_path = self.path.resolve(strict=False)
        if (self.path.name.startswith(".env") or resolved_path.name.startswith(".env")
                or ".git" in self.path.parts or ".git" in resolved_path.parts):
            raise ValueError("Job ledger path is not a permitted local database path.")
        if resolved_path == self.main_database_path.resolve(strict=False):
            raise ValueError("Job ledger must be separate from the engine database.")
        if self.path.exists() and self.path.is_dir():
            raise ValueError("Job ledger path must be a file path.")

    def _ensure_schema(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=2.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 2000")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS analysis_jobs (
                job_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                run_id TEXT,
                error_code TEXT,
                remote_consent INTEGER NOT NULL,
                idempotency_hash TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                preparation_id TEXT NOT NULL,
                configuration_fingerprint TEXT NOT NULL,
                execution_mode TEXT NOT NULL DEFAULT 'sandbox',
                fallback_reasons_json TEXT NOT NULL DEFAULT '[]',
                UNIQUE(project_id, idempotency_hash)
            )
        """)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(analysis_jobs)")}
        if "execution_mode" not in columns:
            connection.execute(
                "ALTER TABLE analysis_jobs ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'sandbox'"
            )
        if "fallback_reasons_json" not in columns:
            connection.execute(
                "ALTER TABLE analysis_jobs ADD COLUMN fallback_reasons_json TEXT NOT NULL DEFAULT '[]'"
            )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_jobs_project_created "
            "ON analysis_jobs(project_id, created_at DESC)"
        )
        return connection

    def _read_connection(self) -> sqlite3.Connection | None:
        if not self.path.is_file():
            return None
        uri = self.path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=1.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='analysis_jobs'"
        ).fetchone()
        if table is None:
            connection.close()
            return None
        return connection

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_job(
        self,
        project_id: str,
        idempotency_hash: str,
        request_fingerprint: str,
        preparation_id: str,
        configuration_fingerprint: str,
        remote_consent: bool,
        execution_mode: str,
        fallback_reasons: tuple[str, ...],
        queue_capacity: int,
    ) -> tuple[dict[str, Any], bool]:
        connection = self._ensure_schema()
        try:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT * FROM analysis_jobs WHERE project_id = ? AND idempotency_hash = ?",
                (project_id, idempotency_hash),
            ).fetchone()
            if previous is not None:
                if previous["request_fingerprint"] != request_fingerprint:
                    connection.rollback()
                    raise ApiProblem(409, "idempotencia_conflictiva", "La clave de idempotencia ya se usó con otra solicitud.")
                connection.commit()
                return dict(previous), True
            active = connection.execute(
                "SELECT project_id FROM analysis_jobs WHERE status IN ('queued', 'running') "
                "AND project_id = ? LIMIT 1",
                (project_id,),
            ).fetchone()
            if active is not None:
                connection.rollback()
                raise ApiProblem(409, "analisis_en_curso", "Ya existe un análisis activo para este proyecto.")
            active_count = connection.execute(
                "SELECT count(*) FROM analysis_jobs WHERE status IN ('queued', 'running')"
            ).fetchone()[0]
            if active_count >= queue_capacity:
                connection.rollback()
                raise ApiProblem(429, "cola_de_analisis_llena", "La cola de análisis está llena; inténtelo más tarde.")
            job_id = "webjob-" + secrets.token_hex(12)
            now = self._timestamp()
            connection.execute(
                "INSERT INTO analysis_jobs (job_id, project_id, status, created_at, updated_at, run_id, "
                "error_code, remote_consent, idempotency_hash, request_fingerprint, preparation_id, "
                "configuration_fingerprint, execution_mode, fallback_reasons_json) "
                "VALUES (?, ?, 'queued', ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, project_id, now, now, int(remote_consent), idempotency_hash,
                 request_fingerprint, preparation_id, configuration_fingerprint, execution_mode,
                 json.dumps(list(fallback_reasons), ensure_ascii=False)),
            )
            row = connection.execute("SELECT * FROM analysis_jobs WHERE job_id = ?", (job_id,)).fetchone()
            connection.commit()
            return dict(row), False
        finally:
            connection.close()

    def update(self, job_id: str, *, status: str, run_id: str | None = None,
               error_code: str | None = None) -> None:
        connection = self._ensure_schema()
        try:
            connection.execute(
                "UPDATE analysis_jobs SET status = ?, updated_at = ?, run_id = COALESCE(?, run_id), "
                "error_code = ? WHERE job_id = ?",
                (status, self._timestamp(), run_id, error_code, job_id),
            )
        finally:
            connection.close()

    def recover_orphans(self, active_job_ids: set[str]) -> None:
        if not self.path.is_file():
            return
        connection = sqlite3.connect(str(self.path), timeout=2.0)
        try:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='analysis_jobs'"
            ).fetchone()
            if exists is None:
                return
            if active_job_ids:
                placeholders = ",".join("?" for _ in active_job_ids)
                connection.execute(
                    f"UPDATE analysis_jobs SET status='interrupted', error_code='process_interrupted', updated_at=? "
                    f"WHERE status IN ('queued','running') AND job_id NOT IN ({placeholders})",
                    (self._timestamp(), *sorted(active_job_ids)),
                )
            else:
                connection.execute(
                    "UPDATE analysis_jobs SET status='interrupted', error_code='process_interrupted', updated_at=? "
                    "WHERE status IN ('queued','running')",
                    (self._timestamp(),),
                )
            connection.commit()
        finally:
            connection.close()

    def get_idempotency(self, project_id: str, idempotency_hash: str) -> dict[str, Any] | None:
        connection = self._read_connection()
        if connection is None:
            return None
        try:
            row = connection.execute(
                "SELECT * FROM analysis_jobs WHERE project_id = ? AND idempotency_hash = ?",
                (project_id, idempotency_hash),
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            connection.close()

    def get(self, job_id: str) -> dict[str, Any] | None:
        connection = self._read_connection()
        if connection is None:
            return None
        try:
            row = connection.execute("SELECT * FROM analysis_jobs WHERE job_id = ?", (job_id,)).fetchone()
            return dict(row) if row is not None else None
        finally:
            connection.close()

    def list_for_project(self, project_id: str, page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
        connection = self._read_connection()
        if connection is None:
            return [], 0
        try:
            total = int(connection.execute(
                "SELECT count(*) FROM analysis_jobs WHERE project_id = ?", (project_id,),
            ).fetchone()[0])
            rows = connection.execute(
                "SELECT * FROM analysis_jobs WHERE project_id = ? ORDER BY created_at DESC, job_id DESC "
                "LIMIT ? OFFSET ?",
                (project_id, page_size, (page - 1) * page_size),
            ).fetchall()
            return [dict(row) for row in rows], total
        finally:
            connection.close()


class AnalysisJobService:
    """One lazily-created worker with bounded durable enqueue and server-bound preparation."""

    def __init__(
        self,
        settings: ApiSettings,
        *,
        executor: Callable[[str, AnalysisRuntimePlan, bool], Any] | None = None,
        plan_loader: Callable[[ApiSettings, str], AnalysisRuntimePlan] = load_analysis_plan,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self.executor = executor
        self.plan_loader = plan_loader
        self.clock = clock
        self.ledger = AnalysisJobLedger(settings.job_ledger_path, settings.db_path)
        self._prepared: dict[str, _PreparedAnalysis] = {}
        self._pool: ThreadPoolExecutor | None = None
        self._futures: dict[str, Future[Any]] = {}
        self._active_ids: set[str] = set()
        self._lock = RLock()

    @staticmethod
    def _session_fingerprint(cookie: str | None) -> str:
        return hashlib.sha256((cookie or "").encode("utf-8")).hexdigest()

    @staticmethod
    def _request_fingerprint(project_id: str, configuration_fingerprint: str, remote_consent: bool) -> str:
        value = json.dumps({
            "project_id": project_id,
            "configuration_fingerprint": configuration_fingerprint,
            "remote_consent": remote_consent,
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _recover_orphans(self) -> None:
        with self._lock:
            active = set(self._active_ids)
        self.ledger.recover_orphans(active)

    def prepare(self, project_id: str, session_cookie: str | None) -> AnalysisPreparation:
        self._recover_orphans()
        try:
            plan = self.plan_loader(self.settings, project_id)
        except Exception:
            plan = _unavailable_plan(self.settings, project_id)
        now = self.clock()
        configured_zone = ZoneInfo(self.settings.timezone)
        now_local = datetime.now(configured_zone)
        preparation_id = secrets.token_urlsafe(24)
        public = AnalysisPreparation(
            preparation_id=preparation_id,
            project_id=project_id,
            expires_at=now_local + timedelta(seconds=self.settings.analysis_prepare_ttl_seconds),
            configuration_fingerprint=plan.fingerprint,
            eligible=plan.eligible,
            ready=plan.eligible,
            execution_mode=plan.execution_mode,
            fallback_reasons=list(plan.fallback_reasons),
            capabilities=["analysis:prepare", "analysis:confirm"],
            preflight=plan.preflight,
            remote=plan.remote,
        )
        with self._lock:
            for key, item in tuple(self._prepared.items()):
                if item.expires_monotonic <= now:
                    self._prepared.pop(key, None)
            self._prepared[preparation_id] = _PreparedAnalysis(
                public=public,
                plan=plan,
                session_fingerprint=self._session_fingerprint(session_cookie),
                expires_monotonic=now + self.settings.analysis_prepare_ttl_seconds,
            )
        return public

    def _job_view(self, row: dict[str, Any]) -> AnalysisJobView:
        job_id = str(row.get("job_id", ""))
        project_id = str(row.get("project_id", ""))
        if (not re.fullmatch(r"webjob-[a-f0-9]{24}", job_id)
                or safe_identifier(project_id) is None):
            raise ApiProblem(500, "registro_de_trabajo_invalido", "No se pudo validar el trabajo almacenado.")
        status = str(row.get("status", ""))
        if status not in {"queued", "running", "completed", "failed", "interrupted"}:
            raise ApiProblem(500, "registro_de_trabajo_invalido", "No se pudo validar el trabajo almacenado.")
        with self._lock:
            active = job_id in self._active_ids
        if status in {"queued", "running"} and not active:
            status = "interrupted"
            error_code = "process_interrupted"
        else:
            error_code = row.get("error_code")
        try:
            created_at = datetime.fromisoformat(str(row["created_at"]))
            updated_at = datetime.fromisoformat(str(row["updated_at"]))
            configured_zone = ZoneInfo(self.settings.timezone)
            created_at = (created_at.replace(tzinfo=configured_zone) if created_at.tzinfo is None
                          else created_at.astimezone(configured_zone))
            updated_at = (updated_at.replace(tzinfo=configured_zone) if updated_at.tzinfo is None
                          else updated_at.astimezone(configured_zone))
        except (KeyError, ValueError):
            raise ApiProblem(500, "registro_de_trabajo_invalido", "No se pudo validar el trabajo almacenado.") from None
        run_id = safe_identifier(row.get("run_id")) if row.get("run_id") is not None else None
        execution_mode = row.get("execution_mode", "sandbox")
        if execution_mode not in {"live", "sandbox", "mixed"}:
            raise ApiProblem(500, "registro_de_trabajo_invalido", "No se pudo validar el modo del trabajo almacenado.")
        try:
            fallback_reasons = json.loads(str(row.get("fallback_reasons_json", "[]")))
        except (TypeError, ValueError):
            raise ApiProblem(500, "registro_de_trabajo_invalido", "No se pudieron validar las razones del trabajo almacenado.") from None
        if not isinstance(fallback_reasons, list) or any(not isinstance(item, str) for item in fallback_reasons):
            raise ApiProblem(500, "registro_de_trabajo_invalido", "No se pudieron validar las razones del trabajo almacenado.")
        allowed_errors = {
            "process_interrupted", "pipeline_failed", "analysis_failed", "worker_unavailable",
            "executor_invalid_result",
        }
        if error_code not in allowed_errors:
            error_code = "job_error" if error_code else None
        return AnalysisJobView(
            job_id=job_id,
            project_id=project_id,
            status=status,
            created_at=created_at,
            updated_at=updated_at,
            run_id=run_id,
            error_code=str(error_code) if error_code else None,
            remote_consent=bool(row.get("remote_consent")),
            execution_mode=execution_mode,
            fallback_reasons=fallback_reasons,
            stage_progress_available=self._stage_progress_available(run_id, project_id) if run_id else False,
        )

    def _stage_progress_available(self, run_id: str, project_id: str) -> bool:
        try:
            repository = SQLiteQueryRepository(self.settings)
            if repository.run_detail(run_id).project_id != project_id:
                return False
            stages = repository.run_stages(run_id)
            return any(item.recorded for item in stages.items)
        except (LookupError, OSError, sqlite3.Error, ValueError):
            return False

    def confirm(
        self,
        project_id: str,
        preparation_id: str,
        configuration_fingerprint: str,
        remote_consent: bool,
        session_cookie: str | None,
        idempotency_key: str,
    ) -> tuple[AnalysisJobView, bool]:
        if not idempotency_key or len(idempotency_key) > 128:
            raise ApiProblem(422, "clave_idempotencia_invalida", "La clave de idempotencia no es válida.")
        idempotency_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        replay_fingerprint = self._request_fingerprint(
            project_id, configuration_fingerprint, remote_consent,
        )
        previous = self.ledger.get_idempotency(project_id, idempotency_hash)
        if previous is not None:
            if previous["request_fingerprint"] != replay_fingerprint:
                raise ApiProblem(409, "idempotencia_conflictiva", "La clave de idempotencia ya se usó con otra solicitud.")
            return self._job_view(previous), True
        with self._lock:
            prepared = self._prepared.get(preparation_id)
        if prepared is None or prepared.public.project_id != project_id:
            raise ApiProblem(409, "preparacion_no_disponible", "La preparación expiró o no está disponible.")
        if prepared.expires_monotonic <= self.clock():
            with self._lock:
                self._prepared.pop(preparation_id, None)
            raise ApiProblem(409, "preparacion_expirada", "La preparación expiró; vuelva a revisar la configuración.")
        if not secrets.compare_digest(
            prepared.session_fingerprint, self._session_fingerprint(session_cookie),
        ):
            raise ApiProblem(403, "preparacion_sesion_distinta", "La preparación pertenece a otra sesión local.")
        if not secrets.compare_digest(prepared.public.configuration_fingerprint, configuration_fingerprint):
            raise ApiProblem(409, "configuracion_cambiada", "La configuración cambió desde la preparación.")
        try:
            current_plan = self.plan_loader(self.settings, project_id)
        except Exception:
            raise ApiProblem(409, "configuracion_no_disponible", "No se pudo validar la configuración efectiva.") from None
        if not secrets.compare_digest(prepared.plan.fingerprint, current_plan.fingerprint):
            raise ApiProblem(409, "configuracion_cambiada", "La configuración cambió desde la preparación.")
        if current_plan.remote.required and not current_plan.remote.server_allowed:
            raise ApiProblem(403, "remoto_no_autorizado", "La configuración del servidor no permite el uso remoto requerido.")
        if current_plan.remote.required and not remote_consent:
            raise ApiProblem(409, "consentimiento_remoto_requerido", "Confirme por separado el uso de fuentes o proveedores remotos.")
        if not current_plan.eligible:
            raise ApiProblem(409, "analisis_no_preparado", "La configuración efectiva no está lista para ejecutar el análisis.")
        if prepared.public.eligible is not True:
            raise ApiProblem(409, "analisis_no_preparado", "La preparación no habilitó esta ejecución.")

        request_fingerprint = self._request_fingerprint(
            project_id, current_plan.fingerprint, remote_consent,
        )
        self._recover_orphans()
        row, reused = self.ledger.create_job(
            project_id=project_id,
            idempotency_hash=idempotency_hash,
            request_fingerprint=request_fingerprint,
            preparation_id=preparation_id,
            configuration_fingerprint=current_plan.fingerprint,
            remote_consent=remote_consent,
            execution_mode=current_plan.execution_mode,
            fallback_reasons=current_plan.fallback_reasons,
            queue_capacity=self.settings.analysis_queue_capacity,
        )
        if not reused:
            job_id = str(row["job_id"])
            self._submit(job_id, project_id, current_plan, remote_consent)
            row = self.ledger.get(job_id) or row
        return self._job_view(row), reused

    def _submit(self, job_id: str, project_id: str, plan: AnalysisRuntimePlan, remote_consent: bool) -> None:
        with self._lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="h5-analysis")
            self._active_ids.add(job_id)
            try:
                future = self._pool.submit(self._run_job, job_id, project_id, plan, remote_consent)
            except RuntimeError:
                self._active_ids.discard(job_id)
                self.ledger.update(job_id, status="failed", error_code="worker_unavailable")
                return
            self._futures[job_id] = future

    def _run_job(self, job_id: str, project_id: str, plan: AnalysisRuntimePlan, remote_consent: bool) -> None:
        run_id = None
        try:
            self.ledger.update(job_id, status="running")
            executor = self.executor or self._execute_orchestrator
            result = executor(project_id, plan, remote_consent)
            run_status = None
            raw_run_id = result if isinstance(result, str) else getattr(result, "run_id", None)
            run_id = safe_identifier(raw_run_id)
            if result is not None and not isinstance(result, str):
                raw_status = getattr(result, "status", None)
                run_status = raw_status.value if hasattr(raw_status, "value") else raw_status
            if run_id is None:
                self.ledger.update(job_id, status="failed", error_code="executor_invalid_result")
            elif run_status == "failed":
                self.ledger.update(job_id, status="failed", run_id=run_id, error_code="pipeline_failed")
            else:
                self.ledger.update(job_id, status="completed", run_id=run_id)
        except Exception:
            self.ledger.update(job_id, status="failed", run_id=run_id, error_code="analysis_failed")
        finally:
            with self._lock:
                self._active_ids.discard(job_id)
                self._futures.pop(job_id, None)

    def _execute_orchestrator(
        self, project_id: str, plan: AnalysisRuntimePlan, remote_consent: bool,
    ) -> Any:
        engine = plan.engine_settings
        if engine is None:
            raise RuntimeError("analysis configuration unavailable")
        effective = engine.model_copy(update={
            "project_id": project_id,
            "db_path": self.settings.db_path,
            "runs_dir": self.settings.runs_dir,
            "allow_remote_ai": bool(engine.allow_remote_ai and remote_consent),
        })
        from agente_gestion.cli import _build_orchestrator
        orchestrator, *_ = _build_orchestrator(effective, effective_mode=effective.source_mode)
        execute_options: dict[str, Any] = {}
        if plan.proposal_rules is not None:
            # Same validated rules contract the CLI uses; the orchestrator still
            # owns the decision to publish, and no rule authorizes any action.
            execute_options["proposal_rules"] = plan.proposal_rules
        return orchestrator.execute(project_id=project_id, **execute_options)

    def get_job(self, job_id: str) -> AnalysisJobView:
        row = self.ledger.get(job_id)
        if row is None:
            raise LookupError("job_not_found")
        return self._job_view(row)

    def list_jobs(self, project_id: str, page: int, page_size: int) -> tuple[list[AnalysisJobView], int]:
        rows, total = self.ledger.list_for_project(project_id, page, page_size)
        return [self._job_view(row) for row in rows], total

    def shutdown(self) -> None:
        with self._lock:
            pool = self._pool
            self._pool = None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=False)
