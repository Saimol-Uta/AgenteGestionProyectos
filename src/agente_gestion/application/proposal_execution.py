"""Governed, idempotent Trello proposal execution service."""

from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

from agente_gestion.adapters.sources.sandbox_task_actions import SandboxTaskActions
from agente_gestion.ports.source_errors import SourceError
from agente_gestion.adapters.sources.trello_action_adapter import (
    TrelloActionAdapter,
    priority_key,
    validate_priority_labels,
)
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.application.proposal_auto_review import ProposalAutoReviewService
from agente_gestion.domain.action_execution import (
    ExecutionActor,
    ExecutionAuthorization,
    ExecutionRequest,
    ExecutionResult,
    execute_proposal_action,
)
from agente_gestion.domain.action_proposal import ActionProposal
from agente_gestion.domain.proposal_review import DecisionEvent, OfflineActorClaim
from agente_gestion.domain.security_rbac import SecurityAuditEntry, hash_token_fingerprint


class ProposalExecutionError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


class ProposalExecutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["approve_and_execute", "modify_and_execute", "reject"]
    modified_due_date: StrictStr | None = Field(default=None, max_length=40)
    modified_priority: StrictStr | None = Field(default=None, max_length=40)
    modified_assignee_ids: list[StrictStr] | None = Field(default=None, max_length=100)
    modified_comment: StrictStr | None = Field(default=None, min_length=1, max_length=2000, exclude_if=lambda v: v is None)
    reason: StrictStr = Field(max_length=1000)

    @field_validator("modified_comment")
    @classmethod
    def valid_comment(cls, value):
        from agente_gestion.domain.comment_action import comment_text
        return comment_text(value) if value is not None else None

    @field_validator("modified_due_date", "modified_priority")
    @classmethod
    def nonblank_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Optional text values cannot be blank")
        return value.strip() if value is not None else None

    @field_validator("reason")
    @classmethod
    def preserve_nonblank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A meaningful human decision reason is required")
        return value

    @field_validator("modified_assignee_ids")
    @classmethod
    def validate_assignees(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = [item.strip() for item in value]
        if (any(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item) is None for item in normalized)
                or len(set(normalized)) != len(normalized)):
            raise ValueError("Assignee identifiers must be unique safe identifiers")
        return normalized

    @model_validator(mode="after")
    def validate_choice(self) -> "ProposalExecutionInput":
        if self.decision == "modify_and_execute":
            modifications = sum(value is not None for value in (
                self.modified_due_date, self.modified_priority, self.modified_assignee_ids, self.modified_comment,
            ))
            if modifications != 1:
                raise ValueError("A modified proposal requires exactly one changed field and a reason")
        elif any(value is not None for value in (
            self.modified_due_date, self.modified_priority, self.modified_assignee_ids, self.modified_comment,
        )):
            raise ValueError("Modified values require modify_and_execute")
        return self


class ProposalExecutionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: str
    original_proposal_id: str
    decision: Literal["approve_and_execute", "modify_and_execute", "reject"]
    execution: dict[str, Any]
    execution_mode: Literal["live", "sandbox"]
    proposal: dict[str, Any] | None = None
    technical_review: dict[str, Any] | None = None
    replayed: bool = False


class ProposalExecutionService:
    """Coordinate fresh technical checks, human authorization and verified writes."""

    def __init__(self, storage: SQLiteStorageAdapter):
        self.storage = storage

    @staticmethod
    def validate_priority_mapping(priority_labels: dict[str, str]) -> dict[str, str]:
        try:
            normalized, _names = validate_priority_labels(priority_labels)
        except (TypeError, ValueError):
            raise ProposalExecutionError(
                422, "mapa_prioridad_invalido",
                "Configure etiquetas Trello únicas para alta, media y baja.",
            ) from None
        if not normalized:
            raise ProposalExecutionError(
                422, "mapa_prioridad_requerido",
                "Configure las etiquetas alta, media y baja antes de cambiar prioridad.",
            )
        return normalized

    @staticmethod
    def execution_mode_for(
        *, source_mode: str, external_writes_enabled: bool,
        trello_api_key: str | None, trello_token: str | None,
    ) -> Literal["live", "sandbox"]:
        if (source_mode == "real" and external_writes_enabled
                and trello_api_key and trello_api_key.strip()
                and trello_token and trello_token.strip()):
            return "live"
        return "sandbox"

    @staticmethod
    def _fingerprint(values: dict[str, Any]) -> str:
        raw = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _session_fingerprint(session_cookie: str | None) -> str:
        return hashlib.sha256((session_cookie or "").encode("utf-8")).hexdigest()

    @staticmethod
    def _execution_id(identity: str) -> str:
        return "web-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _event_id(*parts: str) -> str:
        return "web-exec-" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _parse_due_date(value: str, timezone_name: str) -> datetime:
        zone = ZoneInfo(timezone_name)
        try:
            if len(value) == 10:
                parsed = datetime.fromisoformat(value)
            else:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ProposalExecutionError(422, "fecha_invalida", "La fecha propuesta no es válida.") from None
        return parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed

    def _derived_proposal_id(self, proposal: ActionProposal, request: ProposalExecutionInput) -> str:
        identity = {
            "original_proposal_id": proposal.proposal_id,
            "decision": request.model_dump(mode="json"),
            "capture_id": proposal.observed_capture_id,
        }
        return "proposal-mod-" + self._fingerprint(identity)[:32]

    def _modified_proposal(
        self, proposal: ActionProposal, request: ProposalExecutionInput, timezone_name: str,
        priority_labels: dict[str, str],
    ) -> ActionProposal:
        fields = {
            "due_date": request.modified_due_date,
            "priority": request.modified_priority,
            "assignee_ids": request.modified_assignee_ids,
            "comment": request.modified_comment,
        }
        requested = [(field, value) for field, value in fields.items() if value is not None]
        if len(requested) != 1 or requested[0][0] != proposal.field:
            raise ProposalExecutionError(
                422, "campo_modificado_incompatible",
                "El campo modificado debe coincidir con el de la propuesta original.",
            )
        field, value = requested[0]
        if field == "due_date":
            proposed_value: Any = self._parse_due_date(value, timezone_name)
        elif field == "priority":
            normalized = self.validate_priority_mapping(priority_labels)
            aliases = {"alta": "high", "high": "high", "critical": "high", "critica": "high",
                       "media": "medium", "medium": "medium", "baja": "low", "low": "low"}
            key = aliases.get(value.strip().lower())
            if key not in normalized:
                raise ProposalExecutionError(422, "prioridad_invalida", "La prioridad modificada no está configurada.")
            proposed_value = value.strip().lower()
        else:
            proposed_value = value
        return proposal.model_copy(update={
            "proposal_id": self._derived_proposal_id(proposal, request),
            "proposed_value": proposed_value,
            "rationale": request.reason,
        })

    def _initialize_execution_ledgers(self, connection: sqlite3.Connection) -> None:
        connection.execute("""CREATE TABLE IF NOT EXISTS proposal_execution_idempotency (
            project_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            proposal_id TEXT NOT NULL,
            request_sha256 TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            session_sha256 TEXT NOT NULL,
            execution_id TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY(project_id, idempotency_key)
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS proposal_execution_claims (
            project_id TEXT NOT NULL,
            proposal_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            execution_id TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY(project_id, proposal_id)
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS proposal_execution_outcomes (
            execution_id TEXT PRIMARY KEY,
            request_sha256 TEXT NOT NULL,
            response_json TEXT NOT NULL,
            response_sha256 TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS proposal_execution_resource_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            project_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            field TEXT NOT NULL,
            request_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('reserved', 'complete', 'uncertain')),
            recorded_at TEXT NOT NULL
        )""")
        connection.execute("""CREATE INDEX IF NOT EXISTS idx_proposal_execution_resource
            ON proposal_execution_resource_events(project_id, task_id, field, sequence)""")
        for table in (
            "proposal_execution_idempotency", "proposal_execution_claims", "proposal_execution_outcomes",
            "proposal_execution_resource_events",
        ):
            for operation in ("UPDATE", "DELETE"):
                connection.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{operation}
                    BEFORE {operation} ON {table}
                    BEGIN SELECT RAISE(ABORT, 'Proposal execution ledger is append-only'); END""")

    def _existing_result(
        self, *, project_id: str, proposal_id: str, idempotency_key: str,
        request_sha256: str, actor_id: str, session_sha256: str,
    ) -> ProposalExecutionReceipt | None:
        """Read an existing receipt before current settings can obstruct an exact replay."""
        connection = self.storage._get_connection()
        try:
            with connection:
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='proposal_execution_idempotency'",
                ).fetchone()
                if exists is None:
                    return None
                row = connection.execute(
                    "SELECT * FROM proposal_execution_idempotency WHERE project_id=? AND idempotency_key=?",
                    (project_id, idempotency_key),
                ).fetchone()
                if row is None:
                    return None
                if (row["proposal_id"], row["request_sha256"], row["actor_id"], row["session_sha256"]) != (
                    proposal_id, request_sha256, actor_id, session_sha256,
                ):
                    raise ProposalExecutionError(
                        409, "idempotencia_conflictiva", "La clave de idempotencia ya se usó con otra solicitud.",
                    )
                outcome = connection.execute(
                    "SELECT request_sha256, response_json, response_sha256 "
                    "FROM proposal_execution_outcomes WHERE execution_id=?",
                    (row["execution_id"],),
                ).fetchone()
                if outcome is None:
                    raise ProposalExecutionError(
                        409, "resultado_de_ejecucion_incierto",
                        "La solicitud anterior quedó incierta; no se repetirá ninguna escritura externa.",
                    )
                if (outcome["request_sha256"] != request_sha256
                        or hashlib.sha256(outcome["response_json"].encode("utf-8")).hexdigest()
                        != outcome["response_sha256"]):
                    raise ProposalExecutionError(
                        503, "registro_de_ejecucion_invalido", "El recibo de ejecución no superó la verificación.",
                    )
                receipt = ProposalExecutionReceipt.model_validate_json(outcome["response_json"])
                return receipt.model_copy(update={"replayed": True})
        finally:
            connection.close()

    def _reserve(
        self, *, project_id: str, proposal_id: str, idempotency_key: str,
        request_sha256: str, actor_id: str, session_sha256: str, execution_id: str,
        resource_task_id: str | None, resource_field: str | None,
    ) -> ProposalExecutionReceipt | None:
        connection = self.storage._get_connection()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                self._initialize_execution_ledgers(connection)
                row = connection.execute(
                    "SELECT * FROM proposal_execution_idempotency WHERE project_id=? AND idempotency_key=?",
                    (project_id, idempotency_key),
                ).fetchone()
                if row is not None:
                    if (row["proposal_id"], row["request_sha256"], row["actor_id"], row["session_sha256"]) != (
                        proposal_id, request_sha256, actor_id, session_sha256,
                    ):
                        raise ProposalExecutionError(
                            409, "idempotencia_conflictiva", "La clave de idempotencia ya se usó con otra solicitud.",
                        )
                    outcome = connection.execute(
                        "SELECT request_sha256, response_json, response_sha256 "
                        "FROM proposal_execution_outcomes WHERE execution_id=?",
                        (row["execution_id"],),
                    ).fetchone()
                    if outcome is None:
                        raise ProposalExecutionError(
                            409, "resultado_de_ejecucion_incierto",
                            "La solicitud anterior quedó incierta; no se repetirá ninguna escritura externa.",
                        )
                    if (outcome["request_sha256"] != request_sha256
                            or hashlib.sha256(outcome["response_json"].encode("utf-8")).hexdigest()
                            != outcome["response_sha256"]):
                        raise ProposalExecutionError(
                            503, "registro_de_ejecucion_invalido", "El recibo de ejecución no superó la verificación.",
                        )
                    receipt = ProposalExecutionReceipt.model_validate_json(outcome["response_json"])
                    return receipt.model_copy(update={"replayed": True})

                claim = connection.execute(
                    "SELECT idempotency_key FROM proposal_execution_claims WHERE project_id=? AND proposal_id=?",
                    (project_id, proposal_id),
                ).fetchone()
                if claim is not None:
                    raise ProposalExecutionError(
                        409, "propuesta_ya_intentada",
                        "La propuesta ya tiene una ejecución registrada o incierta; use su misma clave para reproducirla.",
                    )
                if resource_task_id is not None and resource_field is not None:
                    if resource_field.startswith("comment:"):
                        uncertain_comment = connection.execute("""SELECT 1 FROM proposal_execution_resource_events e
                            WHERE e.project_id=? AND e.task_id=? AND e.field LIKE 'comment:%'
                            AND e.sequence=(SELECT MAX(prior.sequence) FROM proposal_execution_resource_events prior
                                WHERE prior.project_id=e.project_id AND prior.task_id=e.task_id AND prior.field=e.field)
                            AND e.state IN ('reserved','uncertain') LIMIT 1""", (project_id, resource_task_id)).fetchone()
                        if uncertain_comment:
                            raise ProposalExecutionError(409, "comentario_requiere_reconciliacion",
                                "Existe un comentario concurrente o incierto en esta tarjeta; reconcilie antes de publicar otro.")
                    latest = connection.execute(
                        "SELECT state FROM proposal_execution_resource_events "
                        "WHERE project_id=? AND task_id=? AND field=? ORDER BY sequence DESC LIMIT 1",
                        (project_id, resource_task_id, resource_field),
                    ).fetchone()
                    if latest is not None and (latest["state"] in {"reserved", "uncertain"} or resource_field.startswith("comment:")):
                        raise ProposalExecutionError(
                            409, "recurso_de_tarea_incierto",
                            "Ya existe una escritura concurrente o incierta para este campo; no se repetirá.",
                        )
                stamp = datetime.now(timezone.utc).isoformat()
                connection.execute("""INSERT INTO proposal_execution_idempotency
                    (project_id, idempotency_key, proposal_id, request_sha256, actor_id,
                     session_sha256, execution_id, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (
                        project_id, idempotency_key, proposal_id, request_sha256, actor_id,
                        session_sha256, execution_id, stamp,
                    ))
                connection.execute("""INSERT INTO proposal_execution_claims
                    (project_id, proposal_id, idempotency_key, execution_id, recorded_at)
                    VALUES (?, ?, ?, ?, ?)""", (project_id, proposal_id, idempotency_key, execution_id, stamp))
                if resource_task_id is not None and resource_field is not None:
                    connection.execute("""INSERT INTO proposal_execution_resource_events
                        (event_id, project_id, task_id, field, request_id, state, recorded_at)
                        VALUES (?, ?, ?, ?, ?, 'reserved', ?)""", (
                            self._event_id(project_id, resource_task_id, resource_field, execution_id, "reserved"),
                            project_id, resource_task_id, resource_field, execution_id, stamp,
                        ))
                return None
        finally:
            connection.close()

    def _append_audit(
        self, connection: sqlite3.Connection, entry: SecurityAuditEntry, human_reason: str,
    ) -> None:
        row = connection.execute(
            "SELECT project_id, actor_id, role, permission_checked, resource_id, decision, reason, "
            "token_fingerprint, human_reason FROM security_audit_log WHERE event_id=?", (entry.event_id,),
        ).fetchone()
        expected = (
            entry.project_id, entry.actor_id, entry.role, entry.permission_checked,
            entry.resource_id, entry.decision, entry.reason, entry.token_fingerprint, human_reason,
        )
        if row is not None:
            if tuple(row) != expected:
                raise ValueError("Security audit event conflict")
            return
        connection.execute("""INSERT INTO security_audit_log
            (event_id, project_id, actor_id, role, permission_checked, resource_id,
             decision, reason, token_fingerprint, recorded_at, human_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                entry.event_id, entry.project_id, entry.actor_id, entry.role,
                entry.permission_checked, entry.resource_id, entry.decision, entry.reason,
                entry.token_fingerprint, entry.recorded_at.isoformat(), human_reason,
            ))

    def _finalize(
        self,
        *,
        request: ExecutionRequest,
        result: ExecutionResult,
        receipt: ProposalExecutionReceipt,
        request_sha256: str,
        actor_id: str,
        session_cookie: str | None,
        human_reason: str,
        task_id: str | None,
        field: str | None,
    ) -> ProposalExecutionReceipt:
        if result.execution_mode is None:
            result = result.model_copy(update={"execution_mode": receipt.execution_mode})
        response_json = json.dumps(
            receipt.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        response_sha256 = hashlib.sha256(response_json.encode("utf-8")).hexdigest()
        audit = SecurityAuditEntry(
            event_id=self._event_id(request.project_id, request.proposal_id, request.request_id),
            project_id=request.project_id,
            actor_id=actor_id,
            role="local_operator",
            permission_checked="web:proposal_execute",
            resource_id=request.proposal_id,
            decision="granted" if result.status in {"EXECUTED", "ALREADY_APPLIED", "CONFLICT"} else "denied",
            reason="request_sha256=" + request_sha256,
            token_fingerprint=hash_token_fingerprint(session_cookie or ""),
        )
        connection = self.storage._get_connection()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                self._initialize_execution_ledgers(connection)
                connection.execute("""INSERT INTO proposal_executions
                    (execution_id, request_id, project_id, proposal_id, status,
                     verification_status, actor_id, field, recorded_at, request_json, result_json, human_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                        result.execution_id, request.request_id, request.project_id,
                        request.proposal_id, result.status, result.verification_status,
                        result.actor_id, result.field, result.recorded_at.isoformat(),
                        request.model_dump_json(), result.model_dump_json(), human_reason,
                    ))
                self._append_audit(connection, audit, human_reason)
                if task_id is not None and field is not None:
                    uncertain = (
                        receipt.execution_mode == "live"
                        and result.status == "FAILED"
                        and result.verification_status == "unknown"
                        and any(reason.startswith(("mutation_error:", "post_verification_read_error:"))
                                for reason in result.reasons)
                    )
                    terminal_state = "uncertain" if uncertain else "complete"
                    connection.execute("""INSERT INTO proposal_execution_resource_events
                        (event_id, project_id, task_id, field, request_id, state, recorded_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""", (
                            self._event_id(request.project_id, task_id, field, request.request_id, terminal_state),
                            request.project_id, task_id, field, request.request_id,
                            terminal_state, datetime.now(timezone.utc).isoformat(),
                        ))
                connection.execute("""INSERT INTO proposal_execution_outcomes
                    (execution_id, request_sha256, response_json, response_sha256, recorded_at)
                    VALUES (?, ?, ?, ?, ?)""", (
                        request.request_id, request_sha256, response_json, response_sha256,
                        datetime.now(timezone.utc).isoformat(),
                    ))
            return receipt
        finally:
            connection.close()

    def _deny_result(
        self, request: ExecutionRequest, proposal: ActionProposal, *reasons: str,
    ) -> ExecutionResult:
        return ExecutionResult(
            execution_id=f"exec-{request.request_id}",
            request_id=request.request_id,
            proposal_id=proposal.proposal_id,
            status="DENIED",
            reasons=tuple(reasons),
            field=proposal.field,
            value_proposed=proposal.proposed_value,
            verification_status="unknown",
            actor_id=request.authorization.actor.actor_id,
        )

    @staticmethod
    def _execution_view(result: ExecutionResult, mode: Literal["live", "sandbox"]) -> dict[str, Any]:
        status = result.status
        if mode == "sandbox" and result.status in {"EXECUTED", "ALREADY_APPLIED"}:
            status = "SIMULATED"
        return {
            "execution_id": result.execution_id,
            "status": status,
            "result_status": result.status,
            "verification_status": result.verification_status,
            "reasons": list(result.reasons),
            "field": result.field,
            "value_before": result.value_before,
            "value_proposed": result.value_proposed,
            "value_observed_after": result.value_observed_after,
            "actor_id": result.actor_id,
            "execution_mode": mode,
            "live_state_changed": mode == "live" and result.status == "EXECUTED",
            **({"native_action_id": result.native_action_id} if result.native_action_id is not None else {}),
        }

    def execute(
        self,
        *,
        project_id: str,
        proposal_id: str,
        body: ProposalExecutionInput,
        actor_id: str,
        session_cookie: str | None,
        idempotency_key: str,
        source_mode: str,
        external_writes_enabled: bool,
        trello_api_key: str | None,
        trello_token: str | None,
        trello_priority_labels: dict[str, str],
        timezone_name: str = "America/Guayaquil",
        trello_board_id: str | None = None,
    ) -> ProposalExecutionReceipt:
        if not idempotency_key or not idempotency_key.strip() or len(idempotency_key) > 128:
            raise ProposalExecutionError(422, "clave_idempotencia_invalida", "La clave de idempotencia no es válida.")
        idempotency_key = idempotency_key.strip()
        stored = self.storage.get_action_proposal(proposal_id)
        if stored is None or stored[0].project_id != project_id:
            raise ProposalExecutionError(404, "propuesta_no_encontrada", "No se encontró la propuesta del proyecto.")
        original, evidence = stored
        selected_proposal_id = (
            self._derived_proposal_id(original, body)
            if body.decision == "modify_and_execute" else original.proposal_id
        )
        session_sha256 = self._session_fingerprint(session_cookie)
        request_sha256 = self._fingerprint({
            "project_id": project_id,
            "original_proposal_id": proposal_id,
            "proposal_id": selected_proposal_id,
            "decision": body.model_dump(mode="json"),
            "actor_id": actor_id,
            "session_sha256": session_sha256,
        })
        execution_identity = "|".join((project_id, selected_proposal_id, idempotency_key, session_sha256))
        request_id = self._execution_id(execution_identity)
        replay = self._existing_result(
            project_id=project_id, proposal_id=selected_proposal_id,
            idempotency_key=idempotency_key, request_sha256=request_sha256,
            actor_id=actor_id, session_sha256=session_sha256,
        )
        if replay is not None:
            return replay

        mode: Literal["live", "sandbox"] = "sandbox" if body.decision == "reject" else self.execution_mode_for(
            source_mode=source_mode, external_writes_enabled=external_writes_enabled,
            trello_api_key=trello_api_key, trello_token=trello_token,
        )
        if mode == "live" and (trello_board_id is None or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", trello_board_id) is None):
            mode = "sandbox"
        proposal = original
        if body.decision == "modify_and_execute":
            proposal = self._modified_proposal(original, body, timezone_name, trello_priority_labels)
        if body.decision != "reject" and proposal.field == "priority":
            self.validate_priority_mapping(trello_priority_labels)
            if (priority_key(proposal.proposed_value) not in {"high", "medium", "low"}
                    or (proposal.current_value is not None
                        and priority_key(proposal.current_value) not in {"high", "medium", "low"})):
                raise ProposalExecutionError(
                    422, "prioridad_no_configurada", "La prioridad actual o propuesta no tiene una etiqueta configurada.",
                )

        live_action_port = None
        if mode == "live":
            live_action_port = TrelloActionAdapter(
                board_id=trello_board_id or "", api_key=trello_api_key or "",
                token=trello_token or "",
                priority_labels=trello_priority_labels if proposal.field == "priority" else None,
                allow_comment_writes=proposal.operation == "append_comment",
            )
            try:
                if not live_action_port.has_write_permission():
                    mode = "sandbox"
            except (SourceError, KeyError):
                raise ProposalExecutionError(
                    503, "permiso_trello_no_verificado",
                    "No se pudo verificar el permiso de Trello; no se intentó ninguna escritura.",
                ) from None

        resource_field = "comment:" + original.proposal_id if proposal.operation == "append_comment" else proposal.field
        replay = self._reserve(
            project_id=project_id, proposal_id=selected_proposal_id,
            idempotency_key=idempotency_key, request_sha256=request_sha256,
            actor_id=actor_id, session_sha256=session_sha256,
            execution_id=request_id,
            resource_task_id=(proposal.task_id if body.decision != "reject" else None),
            resource_field=(resource_field if body.decision != "reject" else None),
        )
        if replay is not None:
            return replay

        if proposal.proposal_id != original.proposal_id:
            self.storage.save_action_proposal(proposal, evidence)
        review_state = self.storage.get_proposal_review_state(project_id, proposal.proposal_id)
        if review_state is None:
            raise ProposalExecutionError(503, "registro_revision_no_disponible", "No se pudo verificar el registro de revisión.")
        proposal_digest, events, projection = review_state
        capture = self.storage.get_snapshot_capture(proposal.observed_capture_id)
        captures = self.storage.list_snapshot_captures(project_id)
        if capture is None:
            raise ProposalExecutionError(409, "captura_no_disponible", "La captura inmutable de la propuesta no está disponible.")
        if proposal.proposal_id != original.proposal_id:
            review_state = self.storage.get_proposal_review_state(project_id, proposal.proposal_id)
            proposal_digest, events, projection = review_state
        technical_review = ProposalAutoReviewService(self.storage).review_and_record(
            proposal, capture, evidence, project_captures=captures,
        )

        internal_actor = ExecutionActor(
            actor_id=actor_id,
            role="project_manager",
            auth_mechanism="server_session",
            auth_token="session-sha256:" + self._session_fingerprint(session_cookie),
        )
        authorization = ExecutionAuthorization(
            grant_id="grant-" + hashlib.sha256(request_id.encode()).hexdigest()[:32],
            proposal_id=proposal.proposal_id,
            proposal_body_sha256=proposal_digest,
            expected_version=projection.version,
            actor=internal_actor,
            grant_token="execution:" + request_id,
        )
        action_request = ExecutionRequest(
            request_id=request_id,
            project_id=project_id,
            proposal_id=proposal.proposal_id,
            proposal_body_sha256=proposal_digest,
            capture_id=capture.capture_id,
            expected_version=projection.version,
            authorization=authorization,
        )

        if body.decision == "reject":
            if projection.review_status != "rejected":
                event = DecisionEvent(
                    event_id=self._event_id(project_id, proposal.proposal_id, request_id, "reject"),
                    project_id=project_id, proposal_id=proposal.proposal_id,
                    capture_id=capture.capture_id, proposal_body_sha256=proposal_digest,
                    expected_version=projection.version, actor=OfflineActorClaim(actor_id=actor_id),
                    decision="reject",
                )
                self.storage.append_proposal_decision(event)
            result = self._deny_result(action_request, proposal, "rejected_by_manager")
        elif technical_review.technical_status != "ready" or technical_review.dry_run.status != "SIMULATED":
            result = self._deny_result(
                action_request, proposal, "technical_review_blocked", *technical_review.technical_errors,
            )
        else:
            if projection.review_status == "pending":
                approval = DecisionEvent(
                    event_id=self._event_id(project_id, proposal.proposal_id, request_id, "approve"),
                    project_id=project_id, proposal_id=proposal.proposal_id,
                    capture_id=capture.capture_id, proposal_body_sha256=proposal_digest,
                    expected_version=projection.version, actor=OfflineActorClaim(actor_id=actor_id),
                    decision="approve_offline",
                )
                self.storage.append_proposal_decision(approval)
                review_state = self.storage.get_proposal_review_state(project_id, proposal.proposal_id)
                proposal_digest, events, projection = review_state
                authorization = authorization.model_copy(update={
                    "proposal_body_sha256": proposal_digest,
                    "expected_version": projection.version,
                })
                action_request = action_request.model_copy(update={
                    "proposal_body_sha256": proposal_digest,
                    "expected_version": projection.version,
                    "authorization": authorization,
                })
            if projection.review_status != "approved_offline":
                result = self._deny_result(action_request, proposal, "human_review_not_approved")
            else:
                if mode == "live":
                    action_port = live_action_port
                else:
                    card = next((item for item in capture.snapshot.tasks if item.id == proposal.task_id), None)
                    if card is None:
                        result = self._deny_result(action_request, proposal, "target_not_found")
                        return self._finalize(
                            request=action_request,
                            result=result,
                            receipt=ProposalExecutionReceipt(
                                proposal_id=proposal.proposal_id, original_proposal_id=original.proposal_id,
                                decision=body.decision,
                                execution=self._execution_view(result, mode), execution_mode=mode,
                                proposal=proposal.model_dump(mode="json"),
                                technical_review=technical_review.model_dump(mode="json"),
                            ),
                            request_sha256=request_sha256, actor_id=actor_id, session_cookie=session_cookie,
                            human_reason=body.reason, task_id=proposal.task_id, field=resource_field,
                        )
                    initial = {
                        **(card.model_dump(mode="json") if proposal.operation == "append_comment" else {}),
                        **({"priority_label_ids": [proposal.comment_context.priority_label_id]} if proposal.operation == "append_comment" else {}),
                        "due_date": card.due_date,
                        "start_date": card.start_date,
                        "assignee_ids": list(card.assignee_ids or []),
                        "priority": card.priority,
                        "idLabels": [],
                    }
                    action_port = SandboxTaskActions(
                        {card.id: initial},
                        priority_labels=trello_priority_labels if proposal.field == "priority" else None,
                    )
                result = execute_proposal_action(
                    request=action_request, proposal=proposal, capture=capture,
                    evidence_items=evidence, proposal_body_sha256=proposal_digest,
                    events=events, project_captures=captures, action_port=action_port,
                ).model_copy(update={"execution_mode": mode})

        receipt = ProposalExecutionReceipt(
            proposal_id=proposal.proposal_id,
            original_proposal_id=original.proposal_id,
            decision=body.decision,
            execution=self._execution_view(result, mode),
            execution_mode=mode,
            proposal=proposal.model_dump(mode="json"),
            technical_review=technical_review.model_dump(mode="json"),
        )
        return self._finalize(
            request=action_request,
            result=result,
            receipt=receipt,
            request_sha256=request_sha256,
            actor_id=actor_id,
            session_cookie=session_cookie,
            human_reason=body.reason,
            task_id=proposal.task_id if body.decision != "reject" else None,
            field=resource_field if body.decision != "reject" else None,
        )
