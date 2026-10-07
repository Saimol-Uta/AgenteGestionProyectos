"""Command Line Interface for AgenteGestionProyectos."""

import argparse
import os
import sys
from typing import Optional

from agente_gestion.application.config import Settings, SystemConfig
from agente_gestion.application.orchestrator import ProjectOrchestrator, OrchestratorConfig
from agente_gestion.adapters.sources.fixtures import FixtureSourcesAdapter
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.adapters.storage.artifacts import ArtifactStorageAdapter
from agente_gestion.adapters.llm.ollama import OllamaProvider
from agente_gestion.adapters.llm.gemini import GeminiProvider
from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.domain.report import HumanDecisionState
from agente_gestion.domain.run import RunStatus


def run_command(args: argparse.Namespace) -> int:
    """Executes the analysis pipeline on the target project."""
    # Build SystemConfig from args and environment
    overrides = {}
    if args.project:
        overrides["project_id"] = args.project
    if args.allow_remote_ai:
        overrides["allow_remote_ai"] = True
    if args.ollama_base_url:
        overrides["ollama_base_url"] = args.ollama_base_url
    if args.db_path:
        overrides["db_path"] = args.db_path
    if args.output_dir:
        overrides["runs_dir"] = args.output_dir
    if args.fake:
        overrides["is_fake"] = True

    config = Settings.from_env_and_file(
        config_path=args.config,
        overrides=overrides,
        env_file=getattr(args, "env_file", None),
    )

    # Initialize persistence adapters
    sqlite_storage = SQLiteStorageAdapter(db_path=config.db_path)
    artifact_storage = ArtifactStorageAdapter(base_dir=config.runs_dir)

    # Initialize source adapter
    fixtures_adapter = FixtureSourcesAdapter(fixtures_dir=config.fixtures_dir)

    # Select LLM providers
    if config.is_fake:
        print("INFO: Mode '--fake' activated. Using clearly simulated deterministic provider.")
        elaborador = FakeProvider(model="fake-qwen-2b")
        revisor = FakeProvider(model="fake-qwen-4b")
        verificador = FakeProvider(model="fake-gemini-flash")
        aprobador = FakeProvider(model="fake-qwen-2b")
    else:
        # Real local Ollama default
        elaborador = OllamaProvider(
            base_url=config.ollama_base_url,
            model=config.ollama_monitoring_model,
        )
        revisor = OllamaProvider(
            base_url=config.ollama_base_url,
            model=config.ollama_quality_model,
        )
        aprobador = OllamaProvider(
            base_url=config.ollama_base_url,
            model=config.ollama_report_model,
        )

        # Verifier: Gemini if remote AI enabled and key present; else None or disabled Gemini provider.
        # User explicitly mandates Gemini verification: NO automatic fallback to local Ollama!
        if config.allow_remote_ai and config.gemini_api_key:
            raw_key = (
                config.gemini_api_key.get_secret_value()
                if hasattr(config.gemini_api_key, "get_secret_value")
                else str(config.gemini_api_key)
            )
            verificador = GeminiProvider(
                api_key=raw_key,
                model=config.gemini_model,
                allow_remote_ai=True,
            )
        elif config.allow_remote_ai and not config.gemini_api_key:
            verificador = GeminiProvider(
                api_key="",
                model=config.gemini_model,
                allow_remote_ai=True,
            )
        else:
            verificador = None

    orchestrator_config = OrchestratorConfig(
        project_id=config.project_id,
        default_fixture=config.default_fixture,
        allow_remote_ai=config.allow_remote_ai,
        max_llm_calls_per_run=config.max_llm_calls_per_run,
        quality_timeout_seconds=config.quality_timeout_seconds,
        verifier_timeout_seconds=config.verifier_timeout_seconds,
        runs_dir=config.runs_dir,
        fixtures_dir=config.fixtures_dir,
        db_path=config.db_path,
        is_simulated=config.is_fake,
        timezone=config.timezone,
    )

    orchestrator = ProjectOrchestrator(
        sqlite_storage=sqlite_storage,
        artifact_storage=artifact_storage,
        elaborador_provider=elaborador,
        revisor_provider=revisor,
        verificador_provider=verificador,
        aprobador_provider=aprobador,
        source_adapter=fixtures_adapter,
        config=orchestrator_config,
    )

    requested_project = args.project or config.project_id
    fixture_name = args.fixture
    run = orchestrator.execute(project_id=requested_project, fixture_name=fixture_name)

    # Print required execution summary:
    # Run ID / status / agents / models / findings / escalations / verifications / report / duration
    findings_stored = sqlite_storage.get_findings(run.run_id)
    verifs_stored = sqlite_storage.get_verifications(run.run_id)
    escalations_count = sum(1 for f in findings_stored if f.get("should_escalate"))
    report_path = os.path.join(config.runs_dir, run.run_id, "report.md")

    verificador_summary = (
        getattr(verificador, "model", "unavailable")
        if (
            verificador is not None
            and (
                config.is_fake
                or (config.allow_remote_ai and bool(config.gemini_api_key))
            )
        )
        else "unavailable"
    )

    print("\n" + "=" * 60)
    print("RESUMEN DE EJECUCIÓN (Pipeline Execution Summary)")
    print("=" * 60)
    print(f"Run ID:         {run.run_id}")
    print(f"Status:         {run.status.value}")
    print(f"Agents:         Elaborador, Revisor, Verificador, Aprobador")
    print(f"Models:         Elaborador={getattr(elaborador, 'model', 'default')}, "
          f"Revisor={getattr(revisor, 'model', 'default')}, "
          f"Verificador={verificador_summary}, "
          f"Aprobador={getattr(aprobador, 'model', 'default')}")
    print(f"Findings:       {len(findings_stored)} candidate finding(s)")
    print(f"Escalations:    {escalations_count} finding(s) escalated to verifier")
    print(f"Verifications:  {len(verifs_stored)} finding(s) independently verified")
    print(f"Report:         {report_path}")
    print(f"Duration:       {run.duration_seconds:.2f} s")
    print("=" * 60 + "\n")

    return 0 if run.status in (RunStatus.awaiting_human, RunStatus.completed) else 1


def decide_command(args: argparse.Namespace) -> int:
    """Registers human manager decision against a report version."""
    sqlite_storage = SQLiteStorageAdapter(db_path=args.db_path)

    try:
        decision_enum = HumanDecisionState(args.decision)
    except ValueError:
        print(f"ERROR: Invalid decision '{args.decision}'. "
              f"Choices: {[e.value for e in HumanDecisionState]}")
        return 2

    try:
        updated = sqlite_storage.register_human_decision(
            report_id=args.report_or_run_id,
            decision=decision_enum,
            reason=args.reason,
            run_id=args.report_or_run_id,
        )
        print("\n" + "=" * 60)
        print("DECISIÓN HUMANA REGISTRADA (Human Manager Decision Registered)")
        print("=" * 60)
        print(f"Report ID:      {updated.report_id}")
        print(f"Decisión:       {updated.human_decision.value}")
        if updated.decision_reason:
            print(f"Motivo:         {updated.decision_reason}")
        print(f"Timestamp:      {updated.decision_timestamp}")
        print("=" * 60 + "\n")
        return 0
    except Exception as e:
        print(f"ERROR: Failed to register decision: {str(e)}")
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agente_gestion",
        description="Agente de Gestión de Proyectos - Pipeline CLI",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # run command
    parser_run = subparsers.add_parser("run", help="Run project weekly analysis pipeline")
    parser_run.add_argument("project", nargs="?", default="proyecto-demo", help="Project identifier or fixture")
    parser_run.add_argument("--config", default="config/config.yaml", help="Path to config YAML file")
    parser_run.add_argument("--fixture", help="Name of JSON fixture in fixtures/ directory")
    parser_run.add_argument("--fake", action="store_true", help="Clearly simulated deterministic execution without real LLMs")
    parser_run.add_argument("--allow-remote-ai", action="store_true", help="Explicitly permit remote AI providers (e.g. Gemini)")
    parser_run.add_argument("--ollama-base-url", default=None, help="Ollama server URL (defaults to http://127.0.0.1:11435)")
    parser_run.add_argument("--db-path", default=None, help="Path to SQLite database file")
    parser_run.add_argument("--output-dir", default=None, help="Directory to save runs artifacts")
    parser_run.add_argument("--env-file", default=None, help="Path to custom .env file (defaults to .env)")

    # decide command
    parser_decide = subparsers.add_parser("decide", help="Register human manager decision on a report")
    parser_decide.add_argument("report_or_run_id", help="Report ID or Run ID being evaluated")
    parser_decide.add_argument(
        "--decision",
        required=True,
        choices=["approved", "rejected", "changes_requested", "postponed"],
        help="Manager decision",
    )
    parser_decide.add_argument("--reason", default=None, help="Optional explanatory rationale for decision")
    parser_decide.add_argument("--db-path", default=".runtime/agente.db", help="Path to SQLite database file")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.subcommand == "run":
        return run_command(args)
    elif args.subcommand == "decide":
        return decide_command(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
