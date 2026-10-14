#!/usr/bin/env python3
"""Serve authentic history read-only or disposable web-test data, without remote calls."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_OPERATOR_TOKEN = "h5-fixture-operator-only-never-use-in-production"
FIXTURE_CLOCK = datetime(2026, 1, 8, 18, 0, tzinfo=timezone.utc)


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
            "Demostración web local en 127.0.0.1: historial auténtico de solo lectura "
            "o datos controlados desechables. No llama proveedores ni escribe en Trello."
        ),
    )
    command.add_argument(
        "--dataset", choices=("historical", "fixture"), default="historical",
        help="historical: consulta sin privilegios; fixture: base temporal de pruebas.",
    )
    command.add_argument("--port", type=local_port, default=8000, help="Puerto local; predeterminado: 8000.")
    return command


@contextmanager
def demo_settings(dataset: str):
    """Never make a writable copy of the authentic DB or alter historical artifacts."""
    sys.path.insert(0, str(ROOT / "src"))
    from agente_gestion.api.settings import ApiSettings, ProjectConfig, load_api_settings

    if dataset == "historical":
        settings = load_api_settings()
        if not Path(settings.db_path).is_file():
            raise FileNotFoundError("No existe una base histórica; use --dataset fixture para pruebas aisladas.")
        # This demonstration mode cannot gain privileges from an inherited operator token.
        yield settings.model_copy(update={
            "operator_token": None, "operator_project_ids": (), "analysis_enabled": False,
            "governance_enabled": False, "report_decisions_enabled": False, "weekly_cycle_enabled": False,
        })
        return

    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="h5-web-fixture-", dir=runtime) as temporary:
        root = Path(temporary)
        sys.path.insert(0, str(ROOT / "tests"))
        from web_fixtures import PROJECT_ID, create_web_fixture_db
        from agente_gestion.domain.weekly_schedule import WeeklySchedule

        database = root / "fixture.sqlite"
        create_web_fixture_db(database)
        config = root / "analysis-fixture.yaml"
        config.write_text("is_fake: true\nallow_remote_ai: false\n", encoding="utf-8")
        yield ApiSettings(
            db_path=str(database),
            runs_dir=str(root / "runs"),
            outbox_dir=str(root / "outbox"),
            projects=(ProjectConfig(
                id=PROJECT_ID, name="HORMIGESTION · fixture controlada", source_mode="fixtures",
            ),),
            operator_token=FIXTURE_OPERATOR_TOKEN,
            operator_actor="operador-fixture",
            operator_project_ids=(PROJECT_ID,),
            analysis_enabled=True,
            governance_enabled=True,
            report_decisions_enabled=True,
            weekly_cycle_enabled=True,
            weekly_schedule=WeeklySchedule(
                project_id=PROJECT_ID, weekday=3, local_time="13:00",
                timezone="America/Guayaquil", window_rule="previous_scheduled_week",
            ),
            weekly_schedule_status="configured",
            analysis_config_path=str(config),
            job_ledger_path=str(root / "jobs.sqlite"),
        )


def fixture_executor(project_id, plan, _remote_consent):
    """Exercise job transport only; deliberately do not invoke an agent or source adapter."""
    if project_id != "H5-TEST" or plan.engine_settings is None or not plan.engine_settings.is_fake:
        raise RuntimeError("fixture_executor_refused_nonfake_plan")
    print("Fixture job: existing simulated run returned; no pipeline or provider invoked.", flush=True)
    return "run-current"


def main(argv: list[str] | None = None) -> int:
    command = parser()
    args = command.parse_args(argv)
    # Imports occur after argument validation; --help never opens a DB or starts a service.
    import uvicorn

    from agente_gestion.api.app import create_app

    try:
        with demo_settings(args.dataset) as settings:
            print(f"Dataset: {args.dataset}; escrituras externas deshabilitadas.", flush=True)
            application = create_app(
                settings, analysis_executor=fixture_executor if args.dataset == "fixture" else None,
                schedule_clock=(lambda: FIXTURE_CLOCK) if args.dataset == "fixture" else None,
            )
            if args.dataset == "fixture":
                print(f"Reloj fijo de pruebas: {FIXTURE_CLOCK.isoformat()}; ciclo manual local SQLite, sin IA.", flush=True)
            uvicorn.run(application, host="127.0.0.1", port=args.port, log_level="warning")
    except FileNotFoundError as exc:
        command.error(str(exc))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))
    raise SystemExit(main())
