#!/usr/bin/env python3
"""Serve a source-complete local fixture without reading live project configuration."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_OPERATOR_TOKEN = "h5-fixture-operator-only-never-use-in-production"
FIXTURE_CLOCK = datetime(2026, 1, 22, 18, 0, tzinfo=timezone.utc)


def local_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("el puerto debe estar entre 1024 y 65535") from None
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("el puerto debe estar entre 1024 y 65535")
    return port


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description=(
            "Demostración local H5-TEST con datos unificados y fuente completa. "
            "Usa proveedores fake, escribe solo en SQLite/artifacts locales y no llama servicios externos."
        ),
    )
    command.add_argument(
        "--dataset", choices=("fixture",), default="fixture",
        help="fixture: base nueva aislada; no se ofrece el histórico legacy.",
    )
    command.add_argument(
        "--fixture-db", type=Path,
        help=(
            "Usar una base regenerada por tests.web_fixtures.seed_unified_demo_data; "
            "requiere artefactos frescos <db-stem>-runs y <db-stem>-outbox junto a la base. "
            "El ledger de jobs y las ramas permanecen aislados en un directorio temporal."
        ),
    )
    command.add_argument(
        "--port", type=local_port, default=8000,
        help="Puerto local de 127.0.0.1; predeterminado: 8000.",
    )
    return command


@contextmanager
def demo_settings(dataset: str = "fixture", *, fixture_db_path: Path | None = None):
    """Build a matching writable H5-TEST config around a fresh isolated fixture database."""
    if dataset != "fixture":
        raise ValueError("Only the fresh unified fixture dataset is supported.")

    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "tests"))
    from agente_gestion.api.settings import ApiSettings, ProjectConfig
    from agente_gestion.domain.weekly_schedule import WeeklySchedule
    from web_fixtures import seed_unified_demo_data, validate_unified_demo_database

    with TemporaryDirectory(prefix="h5-unified-web-") as temporary:
        root = Path(temporary)
        fixtures_dir = root / "fixtures"
        fixtures_dir.mkdir()
        branches_dir = root / "branches"
        job_ledger_path = root / "analysis-jobs.sqlite"
        analysis_config = root / "analysis-fixture.yaml"
        analysis_config.write_text(
            "is_fake: true\nallow_remote_ai: false\nsource_mode: fixtures\n"
            f"fixtures_dir: {json.dumps(str(fixtures_dir))}\n",
            encoding="utf-8",
        )

        if fixture_db_path is None:
            database_path = root / "fixture.sqlite"
            runs_dir = root / "fixture-runs"
            outbox_dir = root / "fixture-outbox"
            seed_unified_demo_data(database_path, runs_dir, outbox_dir)
        else:
            database_path = Path(fixture_db_path).expanduser().resolve(strict=False)
            runs_dir = database_path.with_name(f"{database_path.stem}-runs")
            outbox_dir = database_path.with_name(f"{database_path.stem}-outbox")
            validate_unified_demo_database(database_path, runs_dir, outbox_dir)

        project_id = "H5-TEST"
        yield ApiSettings(
            db_path=str(database_path),
            runs_dir=str(runs_dir),
            outbox_dir=str(outbox_dir),
            projects=(ProjectConfig(
                id=project_id,
                name="HORMIGESTION · fixture unificada",
                source_mode="fixtures",
            ),),
            operator_token=FIXTURE_OPERATOR_TOKEN,
            operator_actor="operador-fixture",
            operator_project_ids=(project_id,),
            allowed_origins=[
                "http://localhost:8000", "http://127.0.0.1:8000",
                "http://localhost:5173", "http://127.0.0.1:5173",
            ],
            allowed_hosts=["localhost", "127.0.0.1", "testserver"],
            analysis_enabled=True,
            governance_enabled=True,
            report_decisions_enabled=True,
            weekly_cycle_enabled=True,
            weekly_schedule=WeeklySchedule(
                project_id=project_id,
                weekday=3,
                local_time="13:00",
                timezone="America/Guayaquil",
                window_rule="previous_scheduled_week",
            ),
            weekly_schedule_status="configured",
            analysis_config_path=str(analysis_config),
            job_ledger_path=str(job_ledger_path),
            branches_dir=str(branches_dir),
            external_writes_enabled=False,
        )


def fixture_executor(project_id, plan, _remote_consent):
    """Execute one real orchestrator pipeline with fixed data and simulated providers only."""
    from web_fixtures import run_fixture_analysis

    return run_fixture_analysis(project_id, plan.engine_settings, captured_at=FIXTURE_CLOCK)


def main(argv: list[str] | None = None) -> int:
    command = parser()
    args = command.parse_args(argv)
    # Imports occur after argument validation; --help never opens a DB or starts a service.
    import uvicorn

    from agente_gestion.api.app import create_app

    try:
        with demo_settings(args.dataset, fixture_db_path=args.fixture_db) as settings:
            print(
                f"Dataset: fixture unificada H5-TEST; base aislada={args.fixture_db is None}; "
                "escrituras externas deshabilitadas.",
                flush=True,
            )
            application = create_app(
                settings,
                analysis_executor=fixture_executor,
                schedule_clock=(lambda: FIXTURE_CLOCK),
            )
            print(
                f"Reloj fijo de pruebas: {FIXTURE_CLOCK.isoformat()}; "
                "cada análisis ejecuta el pipeline simulado y persiste una nueva corrida local.",
                flush=True,
            )
            uvicorn.run(application, host="127.0.0.1", port=args.port, log_level="warning")
    except (FileNotFoundError, ValueError) as exc:
        command.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
