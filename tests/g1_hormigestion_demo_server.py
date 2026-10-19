"""Controlled G1 validation over an isolated copy of the real HORMIGESTION data.

The served application never touches the project database. It opens the source
database read-only, copies the last stored HORMIGESTION capture into a temporary
SQLite database, and replays the real capture and the real task evidence through
a simulated provider. The rules file is a demonstration case, not an approved
operational correction, and it lives only inside the temporary scratch root.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.g1_browser_server import (  # noqa: E402  (path bootstrap must run first)
    BACKEND_HOST,
    BACKEND_PORT,
    FRONTEND_ORIGIN,
    _assert_owned_port_available,
)

SOURCE_DB = REPO_ROOT / ".runtime" / "agente.db"
PROJECT_ID = "HORMIGESTION"
TEST_OPERATOR_TOKEN = "g1-demo-operator-token-never-use-in-production"
DEMO_RULE_ID = "demo-prorroga-dashboard-reporteria"
DEMO_TASK_ID = "6a9582c16a2c16f6106609e2"
DEMO_CURRENT_DUE = "2026-10-19T17:00:00-05:00"
DEMO_PROPOSED_DUE = "2026-10-20T17:00:00-05:00"
DEMO_MARKER = "DEMOSTRACION G1 - CASO DE PRUEBA, NO ES UNA CORRECCION OPERATIVA"


def _preflight() -> tuple[Path, Path]:
    if os.environ.get("EXTERNAL_WRITES_ENABLED") != "false":
        raise RuntimeError("EXTERNAL_WRITES_ENABLED must be exactly false.")
    if os.environ.get("PYTHONDONTWRITEBYTECODE") != "1":
        raise RuntimeError("Python bytecode output must be disabled in the isolated server.")
    if os.environ.get("G1_DEMO_SCRATCH_ROOT"):
        raise RuntimeError("The G1 demo server owns its own temporary scratch root.")
    for name in (
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY",
        "GITHUB_TOKEN", "TRELLO_API_KEY", "TRELLO_TOKEN", "SMTP_USER", "SMTP_PASSWORD",
    ):
        if os.environ.get(name):
            raise RuntimeError(f"Credential environment variable must be absent: {name}.")
    if not SOURCE_DB.is_file():
        raise RuntimeError("The source project database is missing; the demo cannot run.")
    root = Path(tempfile.mkdtemp(prefix="g1-demo-"))
    run_root = root / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    return root, run_root


def _source_digest() -> str:
    digest = hashlib.sha256()
    with open(SOURCE_DB, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_only_connection() -> sqlite3.Connection:
    # SQLite itself refuses any write on this handle.
    return sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)


def _latest_capture(connection: sqlite3.Connection):
    row = connection.execute(
        "SELECT capture_id, project_id, cutoff_datetime, captured_at, data_json, content_hash "
        "FROM snapshot_captures WHERE project_id = ? ORDER BY captured_at DESC LIMIT 1",
        (PROJECT_ID,),
    ).fetchone()
    if row is None:
        raise RuntimeError("The source database has no HORMIGESTION capture to copy.")
    return row


def _latest_task_evidence(connection: sqlite3.Connection) -> list[dict]:
    run_id = connection.execute(
        "SELECT run_id FROM runs WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
        (PROJECT_ID,),
    ).fetchone()
    if run_id is None:
        raise RuntimeError("The source database has no HORMIGESTION run to copy evidence from.")
    rows = connection.execute(
        "SELECT data_json FROM evidence WHERE run_id = ?", (run_id[0],),
    ).fetchall()
    items = [json.loads(item[0]) for item in rows]
    return [item for item in items if item.get("category") == "task"]


def _write_demo_rules(run_root: Path) -> str:
    """Write the demonstration rules file inside the temporary scratch root."""
    path = run_root / "monitoring-rules-DEMO-g1.json"
    path.write_text(json.dumps({
        "project_id": PROJECT_ID,
        "rules": [
            {
                "rule_id": DEMO_RULE_ID,
                "task_ids": [DEMO_TASK_ID],
                "field": "due_date",
                "allowed_values": [DEMO_PROPOSED_DUE],
                "rationale": (
                    f"[{DEMO_MARKER}] La tarjeta {DEMO_TASK_ID} sigue en curso con fecha limite "
                    f"{DEMO_CURRENT_DUE}. La regla solo existe para verificar el circuito completo "
                    "de web a auditoria con datos reales; no afirma que la prorroga sea la "
                    "correccion operativa correcta ni que el solapamiento con la siguiente "
                    "actividad sea aceptable."
                ),
                "expected_impact": (
                    f"[{DEMO_MARKER}] Verifica que una fecha alternativa produce una propuesta "
                    "revisable, ejecutable solo en sandbox y auditable. No modifica el tablero "
                    "de Trello ni el plan del proyecto."
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


class StoredHormigestionSource:
    """Replays the stored HORMIGESTION capture and its task evidence.

    The source database is opened read-only for every collection, so the demo
    replays the real persisted data without ever writing to it.
    """

    def __init__(self) -> None:
        self._snapshot = None
        self._evidence: list = []

    def collect_snapshot(self, project_id, cutoff_datetime, commit_sha=None):
        from agente_gestion.domain.evidence import EvidenceItem
        from agente_gestion.domain.snapshot import WeeklySnapshot
        from agente_gestion.ports.source_collection import SourceCollectionResult

        with _read_only_connection() as connection:
            row = _latest_capture(connection)
            evidence_items = _latest_task_evidence(connection)
        snapshot = WeeklySnapshot.model_validate(json.loads(row[4]))
        self._snapshot = snapshot
        self._evidence = [EvidenceItem.model_validate(item) for item in evidence_items]
        return SourceCollectionResult(snapshot=snapshot, evidence=list(self._evidence))


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
            id=PROJECT_ID, name="HORMIGESTION (demo G1)", source_mode="fixtures",
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
        operator_actor="gestora-g1-demo",
        operator_project_ids=(PROJECT_ID,),
        allowed_origins=(FRONTEND_ORIGIN,),
        allowed_hosts=(BACKEND_HOST,),
    )

    # The simulated source and provider replace the board adapter and the remote
    # model. Every other part of the served stack is the real application.
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
