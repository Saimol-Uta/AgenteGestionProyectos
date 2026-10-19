"""Isolated real FastAPI server for the G1 web proposal rules walkthrough.

The served application uses a temporary SQLite database, a simulated task
collection and a simulated LLM provider. It never contacts an external service
and never performs an external write.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
import tempfile


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FRONTEND_ORIGIN = "http://127.0.0.1:4173"
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 4174
TEST_OPERATOR_TOKEN = "g1-browser-operator-token-never-use-in-production"
PROJECT_ID = "G1WEB"
TASK_ID = "T-G1WEB"
CARD_ID = "card-g1web-1"
PROPOSED_DUE = "2026-10-18T17:00:00"


def _inside(root: Path, candidate: str | Path) -> Path:
    path = Path(candidate).resolve(strict=False)
    if path == root or root not in path.parents:
        raise RuntimeError("A mutable test path is outside the authorized scratch root.")
    return path


def _preflight() -> tuple[Path, Path]:
    if os.environ.get("EXTERNAL_WRITES_ENABLED") != "false":
        raise RuntimeError("EXTERNAL_WRITES_ENABLED must be exactly false.")
    if os.environ.get("PYTHONDONTWRITEBYTECODE") != "1":
        raise RuntimeError("Python bytecode output must be disabled in the isolated server.")
    if os.environ.get("G1_BROWSER_SCRATCH_ROOT"):
        raise RuntimeError("The G1 browser server owns its own temporary scratch root.")
    for name in (
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY",
        "GITHUB_TOKEN", "TRELLO_API_KEY", "TRELLO_TOKEN", "SMTP_USER", "SMTP_PASSWORD",
    ):
        if os.environ.get(name):
            raise RuntimeError(f"Credential environment variable must be absent: {name}.")
    root = Path(tempfile.mkdtemp(prefix="g1-browser-"))
    run_root = root / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    return root, run_root


def _write_configuration(run_root: Path) -> str:
    """Write the isolated project configuration and the owner rules file."""
    rules_path = run_root / "monitoring-rules.json"
    rules_path.write_text(json.dumps({
        "project_id": PROJECT_ID,
        "rules": [
            {
                "rule_id": "rule-extend-due",
                "task_ids": [TASK_ID],
                "field": "due_date",
                "allowed_values": [PROPOSED_DUE],
                "rationale": "La tarea activa necesita una fecha de entrega revisada.",
                "expected_impact": "Reduce el riesgo de vencimiento de la entrega activa.",
            }
        ],
    }), encoding="utf-8")
    config_path = run_root / "config.yaml"
    policies_path = REPO_ROOT / "config" / "policies.yaml"
    fixtures_dir = REPO_ROOT / "fixtures"
    config_path.write_text(
        "\n".join([
            "project:",
            f"  id: {PROJECT_ID}",
            "  timezone: America/Guayaquil",
            "allow_remote_ai: false",
            "external_writes_enabled: false",
            "execution:",
            "  due_soon_days: 7",
            # Absolute paths keep the preflight independent of the server's
            # working directory.
            f"fixtures_dir: {fixtures_dir}",
            "sources:",
            "  mode: fixtures",
            f"  policies_path: {policies_path}",
            f"monitoring_rules_path: {rules_path}",
            "",
        ]),
        encoding="utf-8",
    )
    return str(config_path)


def _build_app(run_root: Path):
    from pydantic import SecretStr

    from agente_gestion.api.settings import ApiSettings, ProjectConfig
    from tests.test_g1_proposal_rules_api import SimulatedCollectionSource, _task_card

    config_path = _write_configuration(run_root)
    settings = ApiSettings(
        db_path=str(run_root / "agente.db"),
        runs_dir=str(run_root / "runs"),
        outbox_dir=str(run_root / "outbox"),
        job_ledger_path=str(run_root / "jobs.sqlite"),
        branches_dir=str(run_root / "branches"),
        analysis_config_path=config_path,
        projects=(ProjectConfig(
            id=PROJECT_ID, name="Proyecto G1 web", source_mode="fixtures",
            monitoring_rules_path=str(run_root / "monitoring-rules.json"),
        ),),
        analysis_enabled=True,
        governance_enabled=True,
        report_decisions_enabled=False,
        weekly_cycle_enabled=False,
        external_writes_enabled=False,
        manager_email=None,
        manager_login_email=None,
        manager_login_password=None,
        operator_token=SecretStr(TEST_OPERATOR_TOKEN),
        operator_actor="gestora-g1-browser",
        operator_project_ids=(PROJECT_ID,),
        allowed_origins=(FRONTEND_ORIGIN,),
        allowed_hosts=(BACKEND_HOST,),
    )

    # The simulated collection replaces the board adapter and the simulated
    # provider replaces the remote model. Every other part of the served stack
    # is the real application.
    import agente_gestion.cli as cli_module

    from tests.test_g1_proposal_rules_api import SelectingFakeProvider

    source = SimulatedCollectionSource(PROJECT_ID, _task_card(TASK_ID, CARD_ID))
    cli_module.FixtureSourcesAdapter = lambda fixtures_dir=None, **kwargs: source
    cli_module.FakeProvider = SelectingFakeProvider

    import agente_gestion.api.app as app_module

    app = app_module.create_app(settings)
    if app is app_module.app:
        raise RuntimeError("Refusing to serve the module-level app.")
    if app.state.settings.external_writes_enabled is not False:
        raise RuntimeError("The served app does not report external writes disabled.")
    if app.state.settings.trello_api_key or app.state.settings.trello_token:
        raise RuntimeError("The served app must not carry external-service credentials.")
    return app


def _assert_owned_port_available() -> None:
    # SO_REUSEADDR only skips sockets left in TIME-WAIT by a previous run; a
    # live listener on the same port still makes this bind fail.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((BACKEND_HOST, BACKEND_PORT))


def main() -> int:
    _root, run_root = _preflight()
    _assert_owned_port_available()
    app = _build_app(run_root)
    import uvicorn

    uvicorn.run(app, host=BACKEND_HOST, port=BACKEND_PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
