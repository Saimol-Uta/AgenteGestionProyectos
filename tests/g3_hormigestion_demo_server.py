"""Controlled G3 validation over an isolated copy of the real HORMIGESTION data.

The served application never touches the project database. It reuses the G1
demo harness to copy the last stored HORMIGESTION capture into a temporary
SQLite database, and adds the only thing G3 needs: the demonstration rule
declares the risk TYPE it answers, so the replayed proposal carries a real
reference to a real deterministic risk of that capture.

The rules file is a demonstration case, not an approved operational
correction, and it lives only inside the temporary scratch root.
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

from tests.g1_browser_server import FRONTEND_ORIGIN  # noqa: E402  (path bootstrap first)
from tests.g1_hormigestion_demo_server import (  # noqa: E402
    DEMO_CURRENT_DUE,
    DEMO_PROPOSED_DUE,
    DEMO_TASK_ID,
    PROJECT_ID,
    SOURCE_DB,
    StoredHormigestionSource,
    _latest_capture,
    _latest_task_evidence,
    _read_only_connection,
    _source_digest,
)

BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 4175
TEST_OPERATOR_TOKEN = "g3-demo-operator-token-never-use-in-production"
DEMO_RULE_ID = "demo-g3-riesgo-proxima-vencimiento"
DEMO_RISK_KIND = "due_soon"
DEMO_MARKER = "DEMOSTRACION G3 - CASO DE PRUEBA, NO ES UNA CORRECCION OPERATIVA"


def _preflight() -> tuple[Path, Path]:
    if os.environ.get("EXTERNAL_WRITES_ENABLED") != "false":
        raise RuntimeError("EXTERNAL_WRITES_ENABLED must be exactly false.")
    if os.environ.get("PYTHONDONTWRITEBYTECODE") != "1":
        raise RuntimeError("Python bytecode output must be disabled in the isolated server.")
    for name in (
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY",
        "GITHUB_TOKEN", "TRELLO_API_KEY", "TRELLO_TOKEN", "SMTP_USER", "SMTP_PASSWORD",
    ):
        if os.environ.get(name):
            raise RuntimeError(f"Credential environment variable must be absent: {name}.")
    if not SOURCE_DB.is_file():
        raise RuntimeError("The source project database is missing; the demo cannot run.")
    root = Path(tempfile.mkdtemp(prefix="g3-demo-"))
    run_root = root / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    return root, run_root


def _assert_owned_port_available() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((BACKEND_HOST, BACKEND_PORT))


def _write_demo_rules(run_root: Path) -> str:
    """Write the demonstration rules file, now declaring the risk type it answers."""
    path = run_root / "monitoring-rules-DEMO-g3.json"
    path.write_text(json.dumps({
        "project_id": PROJECT_ID,
        "rules": [
            {
                "rule_id": DEMO_RULE_ID,
                "task_ids": [DEMO_TASK_ID],
                "field": "due_date",
                "allowed_values": [DEMO_PROPOSED_DUE],
                "risk_kind": DEMO_RISK_KIND,
                "rationale": (
                    f"[{DEMO_MARKER}] La tarjeta {DEMO_TASK_ID} sigue en curso con fecha limite "
                    f"{DEMO_CURRENT_DUE}. La regla declara el tipo de riesgo "
                    f"'{DEMO_RISK_KIND}' y solo existe para verificar el vinculo riesgo, tarea y "
                    "propuesta con datos reales; no afirma que la prorroga sea la correccion "
                    "operativa correcta."
                ),
                "expected_impact": (
                    f"[{DEMO_MARKER}] Verifica que la propuesta queda enlazada al riesgo "
                    "determinista de la captura y que su revision y decision siguen el mismo "
                    "circuito auditable. No modifica el tablero de Trello ni el plan del proyecto."
                ),
            }
        ],
    }), encoding="utf-8")
    return str(path)


def _write_demo_config(run_root: Path, rules_path: str) -> str:
    path = run_root / "config.yaml"
    path.write_text(
        "\n".join([
            "project:",
            f"  id: {PROJECT_ID}",
            "  timezone: America/Guayaquil",
            "allow_remote_ai: false",
            "external_writes_enabled: false",
            "execution:",
            "  due_soon_days: 7",
            f"fixtures_dir: {REPO_ROOT / 'fixtures'}",
            "sources:",
            "  mode: fixtures",
            f"  policies_path: {REPO_ROOT / 'config' / 'policies.yaml'}",
            f"monitoring_rules_path: {rules_path}",
            "",
        ]),
        encoding="utf-8",
    )
    return str(path)


def _build_app(run_root: Path):
    from pydantic import SecretStr

    from agente_gestion.api.settings import ApiSettings, ProjectConfig
    from tests.test_g1_proposal_rules_api import SelectingFakeProvider

    rules_path = _write_demo_rules(run_root)
    config_path = _write_demo_config(run_root, rules_path)

    settings = ApiSettings(
        db_path=str(run_root / "demo-agente.db"),
        runs_dir=str(run_root / "runs"),
        outbox_dir=str(run_root / "outbox"),
        job_ledger_path=str(run_root / "demo-jobs.sqlite"),
        branches_dir=str(run_root / "branches"),
        analysis_config_path=config_path,
        projects=(ProjectConfig(
            id=PROJECT_ID, name="HORMIGESTION (demo G3)", source_mode="fixtures",
            monitoring_rules_path=rules_path,
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
        operator_actor="gestora-g3-demo",
        operator_project_ids=(PROJECT_ID,),
        allowed_origins=(FRONTEND_ORIGIN,),
        allowed_hosts=(BACKEND_HOST,),
    )

    import agente_gestion.cli as cli_module

    cli_module.FixtureSourcesAdapter = lambda fixtures_dir=None, **kwargs: StoredHormigestionSource()
    cli_module.FakeProvider = SelectingFakeProvider

    import agente_gestion.api.app as app_module

    app = app_module.create_app(settings)
    if app is app_module.app:
        raise RuntimeError("Refusing to serve the module-level app.")
    if app.state.settings.external_writes_enabled is not False:
        raise RuntimeError("The served app does not report external writes disabled.")
    if app.state.settings.trello_api_key or app.state.settings.trello_token:
        raise RuntimeError("The served app must not carry external-service credentials.")

    with _read_only_connection() as connection:
        row = _latest_capture(connection)
        capture_id = row[0]
        task_evidence = len(_latest_task_evidence(connection))
    manifest = {
        "source_db": str(SOURCE_DB),
        "source_db_sha256": _source_digest(),
        "capture_id": capture_id,
        "task_evidence_copied": task_evidence,
        "rules_path": rules_path,
        "demo_db": str(run_root / "demo-agente.db"),
        "demo_rule_id": DEMO_RULE_ID,
        "demo_task_id": DEMO_TASK_ID,
        "demo_risk_kind": DEMO_RISK_KIND,
        "demo_current_due": DEMO_CURRENT_DUE,
        "demo_proposed_due": DEMO_PROPOSED_DUE,
        "demo_marker": DEMO_MARKER,
    }
    (run_root / "demo-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return app


def main() -> int:
    _root, run_root = _preflight()
    _assert_owned_port_available()
    app = _build_app(run_root)
    import uvicorn

    uvicorn.run(app, host=BACKEND_HOST, port=BACKEND_PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
