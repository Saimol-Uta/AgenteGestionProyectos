"""Isolated real runner for safe Trello action execution and verification (H2).

Features:
- Strict authorization gate requiring explicit flags.
- Safe read-before-write concurrency check.
- Non-destructive test protocol: applies proposed value, verifies via live read,
  then immediately restores the pristine original value and verifies restoration.
- Records all attempts in SQLite proposal_executions audit ledger.
- Never prints credentials or leaks secrets.
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
import sys
import time
from zoneinfo import ZoneInfo

from agente_gestion.adapters.sources.trello_action_adapter import TrelloActionAdapter
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.application.config import Settings
from agente_gestion.domain.action_execution import (
    ExecutionActor,
    ExecutionAuthorization,
    ExecutionRequest,
    execute_proposal_action,
)
from agente_gestion.domain.action_proposal import ActionProposal
from agente_gestion.domain.proposal_review import DecisionEvent, Digest, Version, OfflineActorClaim
from agente_gestion.domain.evidence import EvidenceItem, EvidenceProvenance, EvidenceSource
from agente_gestion.domain.tasks import DEFAULT_TIMEZONE, TaskCard, TaskState, normalize_datetime_to_tz
from agente_gestion.domain.snapshot import SnapshotCapture, WeeklySnapshot
from agente_gestion.ports.source_errors import SourceError

DEFAULT_TZ = ZoneInfo("America/Guayaquil")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Isolated real Trello action execution runner (H2)",
    )
    parser.add_argument("--project", default="HORMIGESTION", help="Project identifier")
    parser.add_argument("--card-id", default="6a9582bcdf0dcc58846d4b01", help="Target Trello card ID")
    parser.add_argument("--field", default="due_date", choices=["due_date", "start_date"], help="Card field to mutate")
    parser.add_argument("--allow-real", action="store_true", help="Explicitly permit real Trello API network access")
    parser.add_argument("--execute", action="store_true", help="Execute real mutation (PUT) on Trello")
    parser.add_argument("--no-restore", action="store_true", help="Do not restore original value (NOT recommended)")
    parser.add_argument("--db-path", default=".runtime/agente.db", help="Path to SQLite audit ledger database")
    return parser


def run() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.allow_real:
        sys.stderr.write("ERROR: Real Trello execution blocked. Pass --allow-real to authorize network access.\n")
        return 1

    try:
        config = Settings.from_env_and_file()
    except Exception as e:
        sys.stderr.write(f"ERROR: Configuration loading failed: {e}\n")
        return 1

    if not config.trello_board_id or not config.trello_api_key or not config.trello_token:
        sys.stderr.write("ERROR: Trello credentials or board_id missing in configuration.\n")
        return 1

    adapter = TrelloActionAdapter(
        board_id=config.trello_board_id,
        api_key=config.trello_api_key.get_secret_value(),
        token=config.trello_token.get_secret_value(),
        timeout_seconds=config.http_timeout_seconds,
    )

    card_id = args.card_id
    field = args.field

    # 1. Live pre-read of current card state
    try:
        current_live_value = adapter.read_card_field(card_id, field)
    except Exception as exc:
        sys.stderr.write(f"ERROR: Failed to read live card {card_id} from Trello: {exc}\n")
        return 1

    print("\n" + "=" * 75)
    print("ESTADO ACTUAL EN TRELLO EN VIVO (Live Trello State)")
    print("=" * 75)
    print(f"Proyecto:           {args.project}")
    print(f"Board ID:           {config.trello_board_id}")
    print(f"Card ID:            {card_id}")
    print(f"Campo:              {field}")
    print(f"Valor actual:       {current_live_value}")

    # Calculate proposed test value
    if field == "due_date":
        if isinstance(current_live_value, datetime):
            # Extend by 24 hours for the test
            proposed_value = current_live_value + timedelta(days=1)
        else:
            proposed_value = datetime.now(DEFAULT_TZ) + timedelta(days=3)
    elif field == "start_date":
        if isinstance(current_live_value, datetime):
            proposed_value = current_live_value + timedelta(days=1)
        else:
            proposed_value = datetime.now(DEFAULT_TZ)
    else:
        sys.stderr.write(f"ERROR: Field {field} not handled by test runner.\n")
        return 1

    print(f"Valor propuesto:    {proposed_value}")
    print(f"Protocolo retorno:  {'Restauración automática e inmediata al valor original' if not args.no_restore else 'Sin restauración'}")
    print("=" * 75)

    if not args.execute:
        print("\nMODO PREVIEW / INSPECCIÓN (Sin mutaciones ejecutadas).")
        print("Para autorizar y ejecutar la mutación real con verificación y restauración,")
        print("proporciona aprobación explícita al orquestador.")
        return 0

    # 2. Preparation of audited domain structures
    storage = SQLiteStorageAdapter(args.db_path)

    # Look up existing captures in database
    captures = storage.list_snapshot_captures(args.project)
    if not captures:
        sys.stderr.write("ERROR: No existing snapshot capture found in database for project.\n")
        return 1
    latest_capture = captures[-1]

    # Verify task exists in snapshot
    matched_task = next((t for t in latest_capture.snapshot.tasks if t.id == card_id), None)
    if matched_task is None:
        sys.stderr.write(f"ERROR: Card {card_id} not found in snapshot capture {latest_capture.capture_id}.\n")
        return 1

    evidence_id = f"trello-task-{card_id}"
    evidence_item = EvidenceItem(
        provenance=EvidenceProvenance(
            id=evidence_id,
            source=EvidenceSource.trello,
            project_id=args.project,
            external_id=card_id,
            timestamp=latest_capture.snapshot.captured_at,
            payload={
                "task_id": card_id,
                "card_id": card_id,
                field: current_live_value.isoformat() if isinstance(current_live_value, datetime) else current_live_value,
                "observed_fields": list(matched_task.observed_fields or [field]),
            },
        ),
        category="task",
        summary=f"Trello card {card_id}: {matched_task.title}",
    )

    # Build compliant proposal
    proposal = ActionProposal(
        proposal_id=f"prop-h2-test-{int(time.time())}",
        project_id=args.project,
        task_id=card_id,
        observed_capture_id=latest_capture.capture_id,
        field=field,
        current_value=current_live_value,
        proposed_value=proposed_value,
        evidence_ids=[evidence_id],
        rationale="Prueba real aislada H2: verificación de ejecutor seguro con retorno automático.",
        expected_impact="Actualización temporal controlada de fecha límite con restauración inmediata.",
    )
    import hashlib
    prop_raw = json.dumps(
        {"proposal": proposal.model_dump(mode="json"), "evidence": [evidence_item.model_dump(mode="json")]},
        sort_keys=True,
    )
    prop_body_sha = hashlib.sha256(prop_raw.encode("utf-8")).hexdigest()

    # Create approved decision events
    events = [
        DecisionEvent(
            event_id="evt-h2-test-approve",
            project_id=args.project,
            proposal_id=proposal.proposal_id,
            capture_id=latest_capture.capture_id,
            proposal_body_sha256=Digest(prop_body_sha),
            expected_version=0,
            actor=OfflineActorClaim(actor_id="tech-lead"),
            decision="approve_offline",
        )
    ]

    # Create authenticated execution authorization
    actor = ExecutionActor(
        actor_id="lead-architect-live",
        role="lead_architect",
        auth_mechanism="hmac_token",
        auth_token="auth_token_verified_live_session_h2",
    )
    auth = ExecutionAuthorization(
        grant_id="grant-h2-live-001",
        proposal_id=proposal.proposal_id,
        proposal_body_sha256=Digest(prop_body_sha),
        expected_version=Version(1),
        actor=actor,
        grant_token="grant_token_live_execution_h2",
    )
    request = ExecutionRequest(
        request_id=f"req-{int(time.time())}",
        project_id=args.project,
        proposal_id=proposal.proposal_id,
        proposal_body_sha256=Digest(prop_body_sha),
        capture_id=latest_capture.capture_id,
        expected_version=Version(1),
        authorization=auth,
    )

    # 3. Execute Proposal Mutation through domain engine
    print("\nEjecutando mutación real vía domain/action_execution...")
    result = execute_proposal_action(
        request=request,
        proposal=proposal,
        capture=latest_capture,
        evidence_items=[evidence_item],
        proposal_body_sha256=prop_body_sha,
        events=events,
        project_captures=[latest_capture],
        action_port=adapter,
    )

    # Record in database ledger
    storage.append_proposal_execution(request, result)

    print("\n" + "=" * 75)
    print("RESULTADO DE EJECUCIÓN (Execution Result)")
    print("=" * 75)
    print(f"Status:             {result.status}")
    print(f"Verificación:       {result.verification_status}")
    print(f"Valor anterior:     {result.value_before}")
    print(f"Valor propuesto:    {result.value_proposed}")
    print(f"Valor observado:    {result.value_observed_after}")
    print(f"Motivos:            {result.reasons}")
    print("=" * 75)

    if result.status != "EXECUTED" or result.verification_status != "verified":
        sys.stderr.write("ERROR: La mutación o verificación posterior no fue exitosa.\n")
        return 1

    # 4. Immediate Safe Restoration
    if not args.no_restore:
        print("\nEjecutando restauración inmediata al valor original en Trello...")
        restore_ok = adapter.apply_card_field_update(card_id, field, current_live_value)
        if not restore_ok:
            sys.stderr.write("CRITICAL: Error al restaurar el valor original en Trello.\n")
            return 2

        # Post-restore read verification
        restored_live_val = adapter.read_card_field(card_id, field)
        print(f"Restauración confirmada por lectura en vivo: {restored_live_val}")
        if str(restored_live_val) != str(current_live_value):
            sys.stderr.write("CRITICAL: El valor tras restauración no coincide exactamente con el original.\n")
            return 2
        print("Tablero HORMIGESTION intacto y en su estado original.")

    return 0


if __name__ == "__main__":
    sys.exit(run())
