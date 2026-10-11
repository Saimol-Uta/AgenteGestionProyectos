import argparse
import json
import os
import re
import sys
import time
from typing import Any, Optional
from zoneinfo import ZoneInfo

FULL_SHA_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")

from agente_gestion.application.config import Settings, SystemConfig
from agente_gestion.application.orchestrator import ProjectOrchestrator, OrchestratorConfig
from agente_gestion.adapters.sources.factory import (
    create_task_source,
    create_code_source,
    create_ci_source,
    create_policy_source,
    create_composite_source,
)
from agente_gestion.adapters.sources.http_transport import HttpGetTransportPort
from agente_gestion.adapters.sources.fixtures import FixtureSourcesAdapter
from agente_gestion.adapters.sources.local_monitoring_rules import (
    MonitoringRulesError, load_monitoring_rules,
)
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.adapters.storage.artifacts import ArtifactStorageAdapter
from agente_gestion.adapters.llm.ollama import OllamaProvider
from agente_gestion.adapters.llm.gemini import GeminiProvider
from agente_gestion.adapters.llm.groq import GroqProvider
from agente_gestion.adapters.llm.mistral import MistralProvider
from agente_gestion.adapters.llm.fake import FakeProvider
from agente_gestion.domain.report import HumanDecisionState
from agente_gestion.domain.run import RunStatus
from agente_gestion.domain.task_comparison import compare_task_snapshots
from agente_gestion.ports.source_errors import (
    SourceError,
    SourceErrorCategory,
    SourceProvider,
)


def _build_orchestrator(
    config: Settings,
    effective_mode: str = "fixtures",
    transport: Optional[HttpGetTransportPort] = None,
) -> tuple[
    ProjectOrchestrator,
    SQLiteStorageAdapter,
    ArtifactStorageAdapter,
    Any,
    Any,
    Any,
    Any,
]:
    """Factory helper creating orchestrator and storage/provider adapters for a run."""
    sqlite_storage = SQLiteStorageAdapter(db_path=config.db_path)
    artifact_storage = ArtifactStorageAdapter(base_dir=config.runs_dir)

    if effective_mode == "real":
        source_adapter = create_composite_source(
            config,
            commit_sha=config.github_commit_sha,
            transport=transport,
        )
    else:
        source_adapter = FixtureSourcesAdapter(fixtures_dir=config.fixtures_dir)

    if config.is_fake:
        print("INFO: Mode '--fake' activated. Using clearly simulated deterministic provider.")
        elaborador = FakeProvider(model="fake-qwen-2b")
        revisor = FakeProvider(model="fake-qwen-4b")
        verificador = FakeProvider(model="fake-gemini-flash")
        aprobador = FakeProvider(model="fake-qwen-2b")
    else:
        elaborador = OllamaProvider(
            base_url=config.ollama_base_url,
            model=config.ollama_monitoring_model,
        )
        if config.quality_provider == "groq":
            raw_groq_key = (
                config.groq_api_key.get_secret_value()
                if hasattr(config.groq_api_key, "get_secret_value")
                else (str(config.groq_api_key) if config.groq_api_key is not None else "")
            )
            revisor = GroqProvider(
                api_key=raw_groq_key,
                model=config.groq_model,
                base_url=config.groq_base_url,
                allow_remote_ai=config.allow_remote_ai,
                context_tokens=config.quality_context_window or 131072,
                max_output_tokens=config.quality_max_output_tokens or 8192,
            )
        elif config.quality_provider == "mistral":
            raw_mistral_key = (
                config.mistral_api_key.get_secret_value()
                if hasattr(config.mistral_api_key, "get_secret_value")
                else (str(config.mistral_api_key) if config.mistral_api_key is not None else "")
            )
            revisor = MistralProvider(
                api_key=raw_mistral_key,
                model=config.mistral_model,
                base_url=config.mistral_base_url,
                allow_remote_ai=config.allow_remote_ai,
                context_tokens=config.quality_context_window or 131072,
                max_output_tokens=config.quality_max_output_tokens or 8192,
            )
        else:
            revisor = OllamaProvider(
                base_url=config.ollama_base_url,
                model=config.ollama_quality_model,
                context_tokens=config.quality_context_window or 4096,
                max_output_tokens=config.quality_max_output_tokens or 768,
            )
        aprobador = OllamaProvider(
            base_url=config.ollama_base_url,
            model=config.ollama_report_model,
        )

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
        quality_max_output_tokens=config.quality_max_output_tokens,
        quality_context_margin=config.quality_context_margin,
        runs_dir=config.runs_dir,
        fixtures_dir=config.fixtures_dir,
        db_path=config.db_path,
        is_simulated=config.is_fake,
        timezone=config.timezone,
        due_soon_days=config.due_soon_days,
        commit_sha=config.github_commit_sha,
        target_commit_sha=config.github_commit_sha,
    )

    orchestrator = ProjectOrchestrator(
        sqlite_storage=sqlite_storage,
        artifact_storage=artifact_storage,
        elaborador_provider=elaborador,
        revisor_provider=revisor,
        verificador_provider=verificador,
        aprobador_provider=aprobador,
        source_adapter=source_adapter,
        config=orchestrator_config,
    )

    return orchestrator, sqlite_storage, artifact_storage, verificador, elaborador, revisor, aprobador


initialize_pipeline = _build_orchestrator


def run_command(args: argparse.Namespace, transport: Optional[HttpGetTransportPort] = None) -> int:
    """Executes the analysis pipeline on the target project."""
    # Validate conflicting explicit CLI options
    explicit_sources_real = getattr(args, "sources", None) == "real"
    explicit_fixture_passed = getattr(args, "fixture", None) is not None

    if explicit_sources_real and explicit_fixture_passed:
        sys.stderr.write(
            "ERROR: Contradictory arguments: '--sources real' cannot be combined with explicit '--fixture'.\n"
        )
        return 2

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
    if getattr(args, "sources", None):
        overrides["source_mode"] = args.sources
    if getattr(args, "commit", None):
        overrides["github_commit_sha"] = args.commit
    if getattr(args, "branch", None):
        overrides["github_branch"] = args.branch
    if getattr(args, "quality_provider", None):
        overrides["quality_provider"] = args.quality_provider
    if getattr(args, "due_soon_days", None) is not None:
        overrides["due_soon_days"] = args.due_soon_days

    if getattr(args, "monitoring_rules", None) is not None:
        overrides["monitoring_rules_path"] = args.monitoring_rules

    try:
        config = Settings.from_env_and_file(
            config_path=args.config,
            overrides=overrides,
            env_file=getattr(args, "env_file", None),
        )
    except Exception:
        sys.stderr.write("ERROR: Configuration validation failed.\n")
        return 2

    requested_project = args.project or config.project_id
    rules_path = getattr(args, "monitoring_rules", None)
    if rules_path is None:
        rules_path = config.monitoring_rules_path
    proposal_rules = None
    if rules_path is not None:
        try:
            proposal_rules = load_monitoring_rules(rules_path, requested_project)
        except MonitoringRulesError:
            sys.stderr.write("ERROR: Monitoring rules input invalid.\n")
            return 2

    # Backward compatibility: explicit --fixture forces fixtures mode
    if explicit_fixture_passed:
        effective_mode = "fixtures"
    else:
        effective_mode = config.source_mode

    try:
        (
            orchestrator,
            sqlite_storage,
            artifact_storage,
            verificador,
            elaborador,
            revisor,
            aprobador,
        ) = _build_orchestrator(config, effective_mode=effective_mode, transport=transport)
    except SourceError as se:
        sys.stderr.write(f"ERROR: Source configuration failed for '{se.provider.value}': {se.category.value}\n")
        return 2
    except Exception:
        sys.stderr.write("ERROR: Configuration validation failed.\n")
        return 2

    fixture_name = args.fixture
    execute_options = {"proposal_rules": proposal_rules} if proposal_rules is not None else {}
    run = orchestrator.execute(project_id=requested_project, fixture_name=fixture_name,
                               **execute_options)

    # Print required execution summary:
    findings_stored = sqlite_storage.get_findings(run.run_id)
    verifs_stored = sqlite_storage.get_verifications(run.run_id)
    escalations_count = sum(1 for f in findings_stored if f.get("should_escalate"))
    report_path = os.path.join(config.runs_dir, run.run_id, "report.md")

    def _format_model_tag(provider, default_provider_name: str) -> str:
        if provider is None:
            return "unavailable"
        is_sim = getattr(provider, "is_simulated", False) or config.is_fake
        if is_sim:
            return getattr(provider, "model", "simulated")
        p_name = getattr(provider, "provider_name", default_provider_name)
        p_mod = getattr(provider, "model", "unknown")
        if "/" in str(p_mod):
            return str(p_mod)
        return f"{p_name}/{p_mod}"

    verificador_summary = (
        _format_model_tag(verificador, "gemini")
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
    print(f"Models:         Elaborador={_format_model_tag(elaborador, 'ollama')}, "
          f"Revisor={_format_model_tag(revisor, 'ollama')}, "
          f"Verificador={verificador_summary}, "
          f"Aprobador={_format_model_tag(aprobador, 'ollama')}")
    print(f"Findings:       {len(findings_stored)} candidate finding(s)")
    print(f"Escalations:    {escalations_count} finding(s) escalated to verifier")
    print(f"Verifications:  {len(verifs_stored)} finding(s) independently verified")
    print(f"Report:         {report_path}")
    print(f"Duration:       {run.duration_seconds:.2f} s")
    print("=" * 60 + "\n")

    return 0 if run.status in (RunStatus.awaiting_human, RunStatus.completed) else 1


def _is_safe_sha(sha_val: Optional[str], known_secrets: tuple[str, ...]) -> bool:
    """Verifies that sha_val matches strict full-length hex pattern and does not echo any known secret."""
    if not sha_val or sha_val in ("unknown", "N/A", "-"):
        return False
    if not FULL_SHA_RE.match(sha_val):
        return False
    sha_lower = sha_val.lower()
    for sec in known_secrets:
        if sec and sec.lower() in sha_lower:
            return False
    return True


def source_check_command(args: argparse.Namespace) -> int:
    """Executes read-only diagnostic source checks and prints permitted metadata only."""
    overrides = {}
    if getattr(args, "project", None):
        overrides["project_id"] = args.project
    if getattr(args, "sources", None):
        overrides["source_mode"] = args.sources
    if getattr(args, "commit", None):
        overrides["github_commit_sha"] = args.commit
    if getattr(args, "branch", None):
        overrides["github_branch"] = args.branch

    try:
        config = Settings.from_env_and_file(
            config_path=getattr(args, "config", None),
            overrides=overrides,
            env_file=getattr(args, "env_file", None),
        )
    except Exception:
        sys.stderr.write("ERROR: Configuration validation failed.\n")
        return 2

    provider_arg = args.provider.lower()
    project_id = args.project or config.project_id
    commit_override = args.commit or config.github_commit_sha
    known_secrets = config.get_known_secret_values()

    results = []
    overall_success = True

    # 1. Trello check
    if provider_arg in ("trello", "all"):
        t0 = time.perf_counter()
        try:
            task_adapter = create_task_source(config)
            tasks = task_adapter.get_tasks(project_id)
            duration = max(0.0, time.perf_counter() - t0)
            results.append({
                "provider": "trello",
                "status": "success",
                "count": len(tasks),
                "target_or_actual_sha": "N/A",
                "collection_complete": True,
                "duration_seconds": f"{duration:.3f}",
                "canonical_error": "none",
            })
        except Exception as exc:
            overall_success = False
            duration = max(0.0, time.perf_counter() - t0)
            cat = exc.category.value if isinstance(exc, SourceError) else "unknown"
            results.append({
                "provider": "trello",
                "status": "failed",
                "count": "unknown",
                "target_or_actual_sha": "N/A",
                "collection_complete": False,
                "duration_seconds": f"{duration:.3f}",
                "canonical_error": cat,
            })

    # 2. GitHub check
    resolved_github_sha: Optional[str] = None
    if provider_arg in ("github", "all"):
        t0 = time.perf_counter()
        try:
            code_adapter = create_code_source(config)
            diffs = code_adapter.get_diffs(project_id, commit_sha=commit_override)
            duration = max(0.0, time.perf_counter() - t0)
            resolved_sha = getattr(code_adapter, "last_resolved_commit_sha", None) or commit_override or "unknown"
            is_comp = getattr(code_adapter, "last_collection_complete", True)
            if resolved_sha != "unknown" and not _is_safe_sha(resolved_sha, known_secrets):
                overall_success = False
                results.append({
                    "provider": "github",
                    "status": "failed",
                    "count": "unknown",
                    "target_or_actual_sha": "-",
                    "collection_complete": False,
                    "duration_seconds": f"{duration:.3f}",
                    "canonical_error": "malformed_payload",
                })
                resolved_github_sha = None
            else:
                resolved_github_sha = resolved_sha if resolved_sha != "unknown" else None
                if not is_comp:
                    overall_success = False
                results.append({
                    "provider": "github",
                    "status": "success" if is_comp else "incomplete",
                    "count": len(diffs),
                    "target_or_actual_sha": resolved_sha,
                    "collection_complete": is_comp,
                    "duration_seconds": f"{duration:.3f}",
                    "canonical_error": "none",
                })
        except Exception as exc:
            overall_success = False
            duration = max(0.0, time.perf_counter() - t0)
            cat = exc.category.value if isinstance(exc, SourceError) else "unknown"
            results.append({
                "provider": "github",
                "status": "failed",
                "count": "unknown",
                "target_or_actual_sha": "-",
                "collection_complete": False,
                "duration_seconds": f"{duration:.3f}",
                "canonical_error": cat,
            })

    # 3. GitHub Actions check
    if provider_arg in ("github-actions", "all"):
        t0 = time.perf_counter()
        target_ci_sha = commit_override or resolved_github_sha
        if target_ci_sha and not _is_safe_sha(target_ci_sha, known_secrets):
            overall_success = False
            results.append({
                "provider": "github-actions",
                "status": "failed",
                "count": "unknown",
                "target_or_actual_sha": "-",
                "collection_complete": False,
                "duration_seconds": "0.000",
                "canonical_error": "malformed_payload",
            })
        elif config.source_mode != "fixtures" and provider_arg == "all" and not target_ci_sha and resolved_github_sha is None:
            # Code provider failed or did not resolve SHA => Actions dependency blocked
            overall_success = False
            results.append({
                "provider": "github-actions",
                "status": "blocked",
                "count": "unknown",
                "target_or_actual_sha": "-",
                "collection_complete": False,
                "duration_seconds": "0.000",
                "canonical_error": "dependency_blocked",
            })
        else:
            try:
                ci_adapter = create_ci_source(config, commit_sha=target_ci_sha)
                runs = ci_adapter.get_ci_results(project_id, commit_sha=target_ci_sha)
                duration = max(0.0, time.perf_counter() - t0)
                actual_sha = getattr(ci_adapter, "last_target_commit_sha", None) or target_ci_sha or "N/A"
                if actual_sha not in ("N/A", "-", "unknown") and not _is_safe_sha(actual_sha, known_secrets):
                    overall_success = False
                    results.append({
                        "provider": "github-actions",
                        "status": "failed",
                        "count": "unknown",
                        "target_or_actual_sha": "-",
                        "collection_complete": False,
                        "duration_seconds": f"{duration:.3f}",
                        "canonical_error": "malformed_payload",
                    })
                else:
                    is_comp = getattr(ci_adapter, "last_collection_complete", True)
                    if not is_comp:
                        overall_success = False
                    results.append({
                        "provider": "github-actions",
                        "status": "success" if is_comp else "incomplete",
                        "count": len(runs),
                        "target_or_actual_sha": actual_sha,
                        "collection_complete": is_comp,
                        "duration_seconds": f"{duration:.3f}",
                        "canonical_error": "none",
                    })
            except Exception as exc:
                overall_success = False
                duration = max(0.0, time.perf_counter() - t0)
                cat = exc.category.value if isinstance(exc, SourceError) else "unknown"
                results.append({
                    "provider": "github-actions",
                    "status": "failed",
                    "count": "unknown",
                    "target_or_actual_sha": "-",
                    "collection_complete": False,
                    "duration_seconds": f"{duration:.3f}",
                    "canonical_error": cat,
                })

    # 4. Optional local policy check
    if provider_arg in ("policy", "local-policy"):
        t0 = time.perf_counter()
        try:
            pol_adapter = create_policy_source(config)
            policies = pol_adapter.get_policies(project_id)
            duration = max(0.0, time.perf_counter() - t0)
            results.append({
                "provider": "local_policy",
                "status": "success",
                "count": len(policies),
                "target_or_actual_sha": "N/A",
                "collection_complete": True,
                "duration_seconds": f"{duration:.3f}",
                "canonical_error": "none",
            })
        except Exception as exc:
            overall_success = False
            duration = max(0.0, time.perf_counter() - t0)
            cat = exc.category.value if isinstance(exc, SourceError) else "unknown"
            results.append({
                "provider": "local_policy",
                "status": "failed",
                "count": "unknown",
                "target_or_actual_sha": "N/A",
                "collection_complete": False,
                "duration_seconds": f"{duration:.3f}",
                "canonical_error": cat,
            })

    # Print structured metadata table
    print("\n" + "=" * 80)
    print("DIAGNÓSTICO DE FUENTES DE DATOS (Data Source Diagnostics)")
    print("=" * 80)
    print(f"{'Provider':<16} {'Status':<12} {'Count':<10} {'Target/Actual SHA':<42} {'Duration':<10} {'Error'}")
    print("-" * 80)
    for r in results:
        sha_str = str(r["target_or_actual_sha"])
        for sec in known_secrets:
            if sec and sec.lower() in sha_str.lower():
                sha_str = "-"
        print(f"{r['provider']:<16} {r['status']:<12} {str(r['count']):<10} {sha_str:<42} {r['duration_seconds']:<10} {r['canonical_error']}")
    print("=" * 80 + "\n")

    return 0 if overall_success else 1


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


def history_command(args: argparse.Namespace) -> int:
    """Read persisted observations without configuration, migration or composition."""
    try:
        storage = SQLiteStorageAdapter(db_path=args.db_path, read_only=True)
        if args.history_operation == "list":
            result = []
            for capture in storage.list_snapshot_captures(args.project):
                snapshot = capture.snapshot
                result.append({
                    "capture_id": capture.capture_id,
                    "content_hash": capture.content_hash,
                    "snapshot_id": snapshot.id,
                    "project_id": snapshot.project_id,
                    "captured_at": snapshot.captured_at.isoformat(),
                    "cutoff_datetime": snapshot.cutoff_datetime.isoformat(),
                    "task_source_identity": snapshot.task_source_identity,
                    "tasks_complete": snapshot.tasks_complete,
                    "is_complete": snapshot.is_complete,
                    "task_count": len(snapshot.tasks),
                })
        else:
            previous = storage.get_snapshot_capture(args.previous_capture)
            current = storage.get_snapshot_capture(args.current_capture)
            if previous is None or current is None:
                sys.stderr.write("ERROR: History capture not found.\n")
                return 1
            if any(c.snapshot.project_id != args.project for c in (previous, current)):
                sys.stderr.write("ERROR: History capture belongs to another project.\n")
                return 1
            # Evidence is run-scoped, not capture-scoped: never invent associations.
            comparison = compare_task_snapshots(previous.snapshot, current.snapshot)
            result = comparison.model_dump(mode="json")
            result.update(previous_capture_id=previous.capture_id,
                          current_capture_id=current.capture_id)
        output = json.dumps(result)
    except NotImplementedError:
        sys.stderr.write("ERROR: History unavailable in legacy database; no migration performed.\n")
        return 1
    except Exception:
        # Validation/SQLite exceptions may contain entire payloads or private facts.
        sys.stderr.write("ERROR: History read or comparison failed; check database integrity, "
                         "capture chronology and source compatibility.\n")
        return 1
    print(output)
    return 0


def proposals_command(args: argparse.Namespace) -> int:
    """Browse verified proposals only; frozen supporting evidence is not exported."""
    metadata_keys = (
        "proposal_id", "schema_version", "project_id", "task_id", "observed_capture_id",
        "field", "review_status", "authorization_status", "execution_status",
    )
    detail_keys = metadata_keys + (
        "current_value", "proposed_value", "rationale", "expected_impact", "evidence_ids",
    )
    try:
        storage = SQLiteStorageAdapter(db_path=args.db_path, read_only=True)
        if args.proposals_operation == "list":
            result = []
            for proposal, _ in storage.list_action_proposals(args.project):
                data = proposal.model_dump(mode="json")
                row = {key: data[key] for key in metadata_keys}
                row["supporting_evidence_count"] = len(proposal.evidence_ids)
                result.append(row)
        else:
            record = storage.get_action_proposal(args.proposal_id)
            if record is None or record[0].project_id != args.project:
                sys.stderr.write("ERROR: Proposal not found.\n")
                return 1
            data = record[0].model_dump(mode="json")
            result = {key: data[key] for key in detail_keys}
        output = json.dumps(result)
    except NotImplementedError:
        sys.stderr.write("ERROR: Proposal catalogue unavailable; no migration performed.\n")
        return 1
    except Exception:
        # SQLite/model errors can contain private submission or capture content.
        sys.stderr.write("ERROR: Proposal read failed; check database integrity.\n")
        return 1
    print(output)
    return 0


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
    parser_run.add_argument(
        "--sources",
        choices=["fixtures", "real"],
        default=None,
        help="Source collection mode ('fixtures' or 'real', defaults to fixtures)",
    )
    parser_run.add_argument("--commit", default=None, help="Target commit SHA (full 40 or 64 hex characters)")
    parser_run.add_argument("--branch", default=None, help="Target branch name")
    parser_run.add_argument("--fake", action="store_true", help="Clearly simulated deterministic execution without real LLMs")
    parser_run.add_argument("--allow-remote-ai", action="store_true", help="Explicitly permit remote AI providers (e.g. Gemini)")
    parser_run.add_argument("--ollama-base-url", default=None, help="Ollama server URL (defaults to http://127.0.0.1:11435)")
    parser_run.add_argument("--db-path", default=None, help="Path to SQLite database file")
    parser_run.add_argument("--output-dir", default=None, help="Directory to save runs artifacts")
    parser_run.add_argument("--quality-provider", choices=["ollama", "groq", "mistral"], default=None, help="LLM provider for CodeQualityAgent ('ollama', 'groq', 'mistral')")
    parser_run.add_argument("--env-file", default=None, help="Path to custom .env file (defaults to .env)")
    parser_run.add_argument("--due-soon-days", type=int, default=None, help="Inclusive near-due window in days (nonnegative; default 7)")
    parser_run.add_argument("--monitoring-rules", default=None, metavar="PATH",
                            help="Local JSON owner constraints (not action authorization)")

    # source-check command
    parser_check = subparsers.add_parser("source-check", help="Read-only data source diagnostic checks")
    parser_check.add_argument(
        "provider",
        choices=["trello", "github", "github-actions", "all", "local-policy"],
        help="Target provider to diagnose",
    )
    parser_check.add_argument("--project", default=None, help="Target project identifier")
    parser_check.add_argument("--config", default="config/config.yaml", help="Path to config YAML file")
    parser_check.add_argument(
        "--sources",
        choices=["fixtures", "real"],
        default=None,
        help="Source collection mode ('fixtures' or 'real')",
    )
    parser_check.add_argument("--commit", default=None, help="Target commit SHA (full 40 or 64 hex characters)")
    parser_check.add_argument("--branch", default=None, help="Target branch name")
    parser_check.add_argument("--env-file", default=None, help="Path to custom .env file (defaults to .env)")

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

    parser_history = subparsers.add_parser("history", help="Read persisted capture history as JSON")
    history_operations = parser_history.add_subparsers(dest="history_operation", required=True)
    for operation in ("list", "compare"):
        history_parser = history_operations.add_parser(operation)
        history_parser.add_argument("project", help="Project identifier")
        if operation == "compare":
            history_parser.add_argument("previous_capture", help="Previous capture ID (not snapshot ID)")
            history_parser.add_argument("current_capture", help="Current capture ID (not snapshot ID)")
        history_parser.add_argument("--db-path", required=True, help="Existing SQLite database; read-only")

    parser_proposals = subparsers.add_parser("proposals", help="Read persisted proposals as JSON")
    proposal_operations = parser_proposals.add_subparsers(dest="proposals_operation", required=True)
    for operation in ("list", "show"):
        proposal_parser = proposal_operations.add_parser(operation)
        proposal_parser.add_argument("project", help="Project identifier")
        if operation == "show":
            proposal_parser.add_argument("proposal_id", help="Proposal ID")
        proposal_parser.add_argument("--db-path", required=True, help="Existing SQLite database; read-only")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.subcommand == "run":
        return run_command(args)
    elif args.subcommand == "source-check":
        return source_check_command(args)
    elif args.subcommand == "decide":
        return decide_command(args)
    elif args.subcommand == "history":
        return history_command(args)
    elif args.subcommand == "proposals":
        return proposals_command(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
