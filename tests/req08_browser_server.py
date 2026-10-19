"""Test-owned real FastAPI server for the REQ-08 browser seam.

The process guard is installed by sitecustomize before this module is imported.
This entry point only serves a fresh app built from the deterministic T2 harness.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import sys
import tempfile


FRONTEND_ORIGIN = "http://127.0.0.1:4173"
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 4174
TEST_OPERATOR_TOKEN = "t2-fixture-operator-token-never-use-in-production"


def _inside(root: Path, candidate: str | Path) -> Path:
    path = Path(candidate).resolve(strict=False)
    if path == root or root not in path.parents:
        raise RuntimeError("A mutable test path is outside the authorized scratch root.")
    return path


def _preflight() -> tuple[Path, Path, Path]:
    if os.environ.get("EXTERNAL_WRITES_ENABLED") != "false":
        raise RuntimeError("EXTERNAL_WRITES_ENABLED must be exactly false.")
    if os.environ.get("PYTHON_DOTENV_DISABLED") != "true":
        raise RuntimeError("PYTHON_DOTENV_DISABLED must be exactly true.")
    if os.environ.get("PYTHONDONTWRITEBYTECODE") != "1":
        raise RuntimeError("Python bytecode output must be disabled in the isolated server.")
    if os.environ.get("REQ08_T6_PYTHON_GUARD_ACTIVE") != "1":
        raise RuntimeError("The Python network guard did not initialize before server imports.")
    if os.environ.get("REQ08_T6_MODE") not in {"real-red", "real-green"}:
        raise RuntimeError("An explicit REQ08 T6 real-browser mode is required.")
    if os.environ.get("AGENTE_CONFIG_PATH"):
        raise RuntimeError("External application configuration is not permitted.")

    root_value = os.environ.get("REQ08_T6_SCRATCH_ROOT")
    run_value = os.environ.get("REQ08_T6_RUN_ROOT")
    checkout_value = os.environ.get("REQ08_T6_CHECKOUT_ROOT")
    if not root_value or not run_value or not checkout_value:
        raise RuntimeError("Explicit T6 scratch, run, and isolated checkout roots are required.")
    root = Path(root_value).resolve(strict=True)
    run_root = _inside(root, run_value)
    checkout = _inside(root, checkout_value)
    if Path(__file__).resolve().parents[1] != checkout:
        raise RuntimeError("The server source must be read from the isolated checkout.")

    for name in (
        "HOME", "TMPDIR", "TMP", "TEMP", "XDG_CACHE_HOME", "XDG_CONFIG_HOME",
        "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "PIP_CACHE_DIR",
        "MPLCONFIGDIR", "npm_config_cache", "npm_config_userconfig", "npm_config_globalconfig",
        "VITE_CACHE_DIR", "NODE_COMPILE_CACHE", "T4_BUILD_OUT_DIR", "T4_VITE_ENV_DIR",
        "PLAYWRIGHT_OUTPUT_DIR", "REQ08_T6_RUN_ROOT", "AGENTE_DB_PATH", "AGENTE_RUNS_DIR", "H5_OUTBOX_DIR",
        "H5_WEB_JOBS_DB_PATH", "BRANCHES_DIR", "REQ08_T6_PYTHON_NETWORK_LOG",
        "REQ08_T6_PYTHON_GUARD_LOG", "REQ08_T6_PYTHON_PROBE_LOG", "REQ08_T6_BROWSER_NETWORK_LOG",
        "REQ08_T6_BROWSER_GUARD_LOG", "ISOLATION_NETWORK_LOG", "ISOLATION_GUARD_LOG",
    ):
        value = os.environ.get(name)
        if not value:
            raise RuntimeError(f"Required isolated path is missing: {name}.")
        _inside(root, value)

    for name in (
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "GROQ_API_KEY",
        "MISTRAL_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN",
        "TRELLO_API_KEY", "TRELLO_TOKEN", "SMTP_USER", "SMTP_PASSWORD", "H5_OPERATOR_TOKEN",
        "H5_ADMIN_TOKEN", "H5_SESSION_SECRET", "MANAGER_LOGIN_PASSWORD",
    ):
        if os.environ.get(name):
            raise RuntimeError(f"Credential environment variable must be absent: {name}.")

    for name in (
        "REQ08_T6_PYTHON_NETWORK_LOG", "REQ08_T6_PYTHON_PROBE_LOG",
        "REQ08_T6_BROWSER_NETWORK_LOG", "ISOLATION_NETWORK_LOG",
    ):
        if Path(os.environ[name]).exists():
            raise RuntimeError(f"Refusing to reuse or continue after a network-attempt log: {name}.")

    if os.environ.get("REQ08_T6_BIND_HOST") != BACKEND_HOST or os.environ.get("REQ08_T6_BIND_PORT") != str(BACKEND_PORT):
        raise RuntimeError("The only authorized API bind is 127.0.0.1:4174.")
    return root, run_root, checkout


def _safe_default_settings(run_root: Path):
    from pydantic import SecretStr
    from agente_gestion.api.settings import ApiSettings, ProjectConfig

    return ApiSettings(
        db_path=str(run_root / "unserved-global-app.sqlite"),
        runs_dir=str(run_root / "unserved-global-runs"),
        outbox_dir=str(run_root / "unserved-global-outbox"),
        job_ledger_path=str(run_root / "unserved-global-jobs.sqlite"),
        branches_dir=str(run_root / "unserved-global-branches"),
        projects=(ProjectConfig(id="P", name="Proyecto fixture", source_mode="fixtures"),),
        analysis_enabled=False,
        governance_enabled=True,
        report_decisions_enabled=False,
        weekly_cycle_enabled=False,
        external_writes_enabled=False,
        manager_email=None,
        manager_login_email=None,
        manager_login_password=None,
        operator_token=SecretStr(TEST_OPERATOR_TOKEN),
        operator_actor="gestora-e2e-fixture",
        operator_project_ids=("P",),
        allowed_origins=(FRONTEND_ORIGIN,),
        allowed_hosts=(BACKEND_HOST,),
    )


def _build_app(root: Path, run_root: Path):
    # Patch the settings loader before importing api.app: its module-level app is
    # constructed at import time, so even that unserved instance gets only these
    # explicit scratch-backed test settings, never project defaults/configuration.
    import agente_gestion.api.settings as settings_module

    safe_defaults = _safe_default_settings(run_root)
    settings_module.load_api_settings = lambda **_kwargs: safe_defaults

    from tests.test_req08_decision_workflow import Req08DecisionWorkflowTests
    import agente_gestion.api.app as app_module

    Req08DecisionWorkflowTests.ORIGIN = FRONTEND_ORIGIN
    fixture_case = Req08DecisionWorkflowTests("runTest")
    base, harness_client, _csrf, run = fixture_case._harness(authenticate=False)
    harness_settings = harness_client.app.state.settings

    if harness_settings.external_writes_enabled is not False:
        raise RuntimeError("The T2 harness did not disable external writes.")
    if harness_settings.projects[0].id != "P" or harness_settings.projects[0].source_mode != "fixtures":
        raise RuntimeError("The T2 harness did not seed the expected synthetic fixture project.")
    if run.project_id != "P":
        raise RuntimeError("The deterministic source/orchestrator harness returned another project.")

    explicit_settings = harness_settings.model_copy(update={
        "allowed_origins": (FRONTEND_ORIGIN,),
        "allowed_hosts": (BACKEND_HOST,),
        "analysis_enabled": False,
        "external_writes_enabled": False,
        "github_token": None,
        "trello_api_key": None,
        "trello_token": None,
        "smtp_user": None,
        "smtp_password": None,
        "manager_login_email": None,
        "manager_login_password": None,
    })
    for name in ("db_path", "runs_dir", "outbox_dir", "job_ledger_path", "branches_dir"):
        _inside(root, getattr(explicit_settings, name))
    if explicit_settings.analysis_config_path or explicit_settings.analysis_env_file:
        raise RuntimeError("The server must not load analysis config or dotenv files.")
    if explicit_settings.external_writes_enabled is not False:
        raise RuntimeError("The explicitly served app must disable external writes.")
    if any(getattr(explicit_settings, name) is not None for name in (
        "github_token", "trello_api_key", "trello_token", "smtp_user", "smtp_password",
    )):
        raise RuntimeError("The explicitly served app must not carry external-service credentials.")

    test_app = app_module.create_app(explicit_settings)
    if test_app is app_module.app:
        raise RuntimeError("Refusing to serve the module-level app.")
    if test_app.state.settings.external_writes_enabled is not False:
        raise RuntimeError("The served app does not report external writes disabled.")

    @test_app.middleware("http")
    async def verify_network_guard(request, call_next):
        _assert_python_guard_intact()
        try:
            return await call_next(request)
        finally:
            _assert_python_guard_intact()

    return fixture_case, base, harness_client, test_app


def _assert_owned_port_available() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        probe.bind((BACKEND_HOST, BACKEND_PORT))


def _assert_python_guard_intact() -> None:
    import sitecustomize

    if not sitecustomize._log_integrity():
        raise RuntimeError("Python network guard method references changed.")


def main() -> int:
    root, run_root, _checkout = _preflight()
    python_temp = run_root / "python-temp"
    python_temp.mkdir(mode=0o700, parents=True, exist_ok=True)
    tempfile.tempdir = str(python_temp)

    fixture_case = None
    try:
        fixture_case, _base, _harness_client, test_app = _build_app(root, run_root)
        _assert_owned_port_available()
        import uvicorn

        print("REQ08 test-owned API app ready: project=P; external_writes_enabled=false; bind=127.0.0.1:4174")
        server = uvicorn.Server(uvicorn.Config(
            test_app,
            host=BACKEND_HOST,
            port=BACKEND_PORT,
            log_level="warning",
            access_log=False,
            reload=False,
            workers=1,
            lifespan="on",
        ))
        _assert_python_guard_intact()
        server.run()
        return 0
    finally:
        if fixture_case is not None:
            fixture_case.doCleanups()


if __name__ == "__main__":
    sys.exit(main())
