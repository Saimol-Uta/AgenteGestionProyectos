"""Idempotent application service for human-governed finding decisions."""

import hashlib
import json
from datetime import datetime, timezone

from agente_gestion.adapters.storage.sqlite import (
    FindingDecisionStorageError,
    SQLiteStorageAdapter,
)
from agente_gestion.domain.code import validate_source_syntax
from agente_gestion.domain.code_action import BranchExecutionResult, CodeSourceSnapshot
from agente_gestion.domain.findings import CandidateFinding
from agente_gestion.domain.security_rbac import SecurityAuditEntry, hash_token_fingerprint
from agente_gestion.ports.code_action_port import CodeActionPort


class FindingDecisionError(RuntimeError):
    """Stable HTTP-safe error returned by the finding decision use case."""

    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


class FindingDecisionService:
    """Durably reserve a request before executing a code branch side effect."""

    def __init__(self, storage: SQLiteStorageAdapter, code_action: CodeActionPort):
        self.storage = storage
        self.code_action = code_action

    @staticmethod
    def _audit_entry(
        *, event_id: str, project_id: str, finding_id: str, actor_id: str,
        session_cookie: str | None, reason: str,
    ) -> SecurityAuditEntry:
        return SecurityAuditEntry(
            event_id=event_id,
            project_id=project_id,
            actor_id=actor_id,
            role="project_manager_session",
            permission_checked="web:finding_decision",
            resource_id=finding_id,
            decision="granted",
            reason=reason,
            token_fingerprint=hash_token_fingerprint(session_cookie or ""),
        )

    @staticmethod
    def _storage_error(exc: FindingDecisionStorageError) -> FindingDecisionError:
        errors = {
            "idempotency_conflict": (409, "idempotencia_conflictiva", "La clave de idempotencia ya se usó con una solicitud distinta."),
            "decision_in_progress": (409, "decision_en_curso", "Ya existe una decisión concurrente para este hallazgo."),
            "approval_already_recorded": (409, "aprobacion_ya_registrada", "Este hallazgo ya tiene una aprobación registrada."),
            "finding_already_approved": (409, "hallazgo_ya_aprobado", "Este hallazgo ya fue aprobado."),
            "decision_conflict": (409, "decision_conflictiva", "La decisión entra en conflicto con una solicitud anterior."),
            "decision_receipt_unavailable": (503, "decision_no_disponible", "No se pudo finalizar de forma segura el registro de la decisión."),
            "finding_not_found": (404, "hallazgo_no_encontrado", "No se encontró el hallazgo solicitado."),
        }
        status, code, message = errors.get(
            exc.code, (503, "decision_no_disponible", "No se pudo registrar la decisión de forma segura."),
        )
        return FindingDecisionError(status, code, message)

    @staticmethod
    def _ids(project_id: str, run_id: str, finding_id: str, key: str) -> tuple[str, str]:
        scope = json.dumps([project_id, run_id, finding_id, key], separators=(",", ":"))
        digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()
        request_id = f"fdreq-{digest[:32]}"
        event_id = f"finding-{digest[:24]}"
        return request_id, event_id

    @staticmethod
    def _target_branch(finding_id: str, idempotency_key: str, supplied: str | None) -> str:
        if supplied:
            return supplied
        suffix = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:10]
        return f"quality/{finding_id}-{suffix}"

    def decide(
        self,
        *,
        project_id: str,
        run_id: str,
        finding_id: str,
        candidate: CandidateFinding,
        decision: str,
        idempotency_key: str,
        actor_id: str,
        session_cookie: str | None,
        original_excerpt: str,
        source: CodeSourceSnapshot | None,
        repository: str | None,
        base_branch: str,
        suggested_code_override: str | None = None,
        target_branch: str | None = None,
        reason: str | None = None,
    ) -> dict[str, object]:
        if candidate.id != finding_id:
            raise FindingDecisionError(404, "hallazgo_no_encontrado", "No se encontró el hallazgo solicitado.")
        if decision not in {"approve", "reject", "request_changes"}:
            raise FindingDecisionError(422, "decision_invalida", "La decisión enviada no es válida.")
        if suggested_code_override is not None and not suggested_code_override.strip():
            raise FindingDecisionError(422, "codigo_sugerido_invalido", "El código alternativo no puede estar vacío.")
        effective_code = suggested_code_override if suggested_code_override is not None else candidate.suggested_code
        selected_branch = self._target_branch(finding_id, idempotency_key, target_branch)
        if decision == "approve" and selected_branch == base_branch:
            raise FindingDecisionError(422, "rama_destino_invalida", "La rama destino debe ser distinta de la rama base.")
        normalized_request = {
            "decision": decision,
            "suggested_code_override": suggested_code_override,
            "target_branch": target_branch,
            "reason": reason.strip() if reason else None,
            "actor_id": actor_id,
        }
        request_json = json.dumps(normalized_request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        request_sha256 = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        request_id, event_id = self._ids(project_id, run_id, finding_id, idempotency_key)
        now = datetime.now(timezone.utc).isoformat()

        if decision == "approve" and not effective_code.strip():
            raise FindingDecisionError(409, "codigo_sugerido_requerido", "El hallazgo no contiene código sugerido para aprobar.")
        if decision == "approve":
            if (source is None or not source.is_complete
                    or source.repository != repository
                    or source.file_path != candidate.file_path
                    or source.commit_sha != candidate.commit_sha
                    or not source.source_sha256
                    or hashlib.sha256(source.content.encode("utf-8")).hexdigest() != source.source_sha256
                    or source.total_lines != len(source.content.splitlines())):
                raise FindingDecisionError(
                    409, "fuente_no_verificable",
                    "Se requiere el archivo completo, íntegro y fijado a la evidencia para aprobar.",
                )
            if not original_excerpt or source.content.count(original_excerpt) != 1:
                raise FindingDecisionError(
                    409, "parche_obsoleto_o_ambiguo",
                    "El fragmento original no coincide exactamente una sola vez con la fuente capturada.",
                )
            patched = source.content.replace(original_excerpt, effective_code, 1)
            syntax_status = validate_source_syntax(candidate.file_path, patched)
            if syntax_status == "unsupported":
                raise FindingDecisionError(
                    422, "validacion_sintaxis_no_soportada",
                    "No hay un validador seguro disponible para la sintaxis de este tipo de archivo.",
                )
            if syntax_status == "invalid":
                raise FindingDecisionError(
                    422, "codigo_sugerido_invalido",
                    "El código sugerido no conserva la sintaxis del archivo completo.",
                )

        try:
            reservation, existing = self.storage.reserve_finding_decision(
                project_id=project_id, run_id=run_id, finding_id=finding_id,
                idempotency_key=idempotency_key, request_id=request_id, event_id=event_id,
                request_sha256=request_sha256, request_json=request_json, decision=decision,
                actor_id=actor_id, recorded_at=now,
            )
        except FindingDecisionStorageError as exc:
            raise self._storage_error(exc) from None

        if reservation == "replay":
            if existing["state"] == "failed":
                raise FindingDecisionError(
                    int(existing["error_status"] or 503),
                    str(existing["error_code"] or "decision_no_disponible"),
                    str(existing["error_message"] or "La decisión anterior no pudo completarse."),
                )
            try:
                result = json.loads(existing["result_json"])
            except (ValueError, TypeError):
                raise FindingDecisionError(503, "recibo_invalido", "El recibo durable de la decisión no es válido.") from None
            result["reused"] = True
            return result

        branch_result: BranchExecutionResult | None = None
        try:
            if decision == "approve":
                if not repository:
                    raise FindingDecisionError(409, "repositorio_no_disponible", "No se conoce el repositorio de origen del hallazgo.")
                branch_result = self.code_action.create_verification_branch(
                    repo=repository,
                    base_branch=base_branch,
                    target_branch=selected_branch,
                    file_path=candidate.file_path,
                    original_excerpt=original_excerpt,
                    suggested_code=effective_code,
                    finding_id=finding_id,
                    actor=actor_id,
                )
                if branch_result.execution_mode not in {"live", "sandbox"}:
                    raise FindingDecisionError(503, "resultado_de_rama_invalido", "El adaptador no confirmó el modo de ejecución.")
            decision_status = {
                "approve": "approved", "reject": "rejected", "request_changes": "changes_requested",
            }[decision]
            result: dict[str, object] = {
                "reused": False,
                "audit_event_id": f"audit-{event_id}",
                "decision_status": decision_status,
                "branch_name": branch_result.branch_name if branch_result else None,
                "branch_url": branch_result.branch_url if branch_result else None,
                "commit_sha_applied": branch_result.commit_sha_applied if branch_result else None,
                "suggested_code": effective_code,
                "improvement_summary": candidate.improvement_summary,
                "execution_mode": branch_result.execution_mode if branch_result else "sandbox",
            }
            result_json = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            recorded_at = datetime.now(timezone.utc).isoformat()
            audit = self._audit_entry(
                event_id=f"audit-{event_id}", project_id=project_id, finding_id=finding_id,
                actor_id=actor_id, session_cookie=session_cookie,
                reason=f"Finding decision {decision_status} recorded in {result['execution_mode']} mode.",
            )
            self.storage.complete_finding_decision(
                request_id=request_id, result_json=result_json, decision_status=decision_status,
                suggested_code=effective_code, improvement_summary=candidate.improvement_summary,
                branch_name=branch_result.branch_name if branch_result else None,
                branch_url=branch_result.branch_url if branch_result else None,
                commit_sha_applied=branch_result.commit_sha_applied if branch_result else None,
                audit_entry=audit, recorded_at=recorded_at,
            )
            return result
        except FindingDecisionError as exc:
            failure = exc
        except Exception as exc:
            status = getattr(exc, "status_code", 503)
            code = getattr(exc, "code", "code_action_failed")
            message = getattr(exc, "message", None)
            if not isinstance(status, int) or not 400 <= status <= 599:
                status = 503
            if not isinstance(code, str) or not code:
                code = "code_action_failed"
            if not isinstance(message, str) or not message:
                message = "La acción de código no se completó; no se confirmó una rama remota."
            failure = FindingDecisionError(status, code, message)

        failure_json = json.dumps({
            "decision_status": "failed", "error_code": failure.code,
            "error_message": failure.message, "status_code": failure.status_code,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        recorded_at = datetime.now(timezone.utc).isoformat()
        audit = self._audit_entry(
            event_id=f"audit-{event_id}", project_id=project_id, finding_id=finding_id,
            actor_id=actor_id, session_cookie=session_cookie,
            reason=f"Finding decision failed with code {failure.code}.",
        )
        try:
            self.storage.fail_finding_decision(
                request_id=request_id, result_json=failure_json, error_status=failure.status_code,
                error_code=failure.code, error_message=failure.message, suggested_code=effective_code,
                improvement_summary=candidate.improvement_summary, audit_entry=audit,
                recorded_at=recorded_at,
            )
        except FindingDecisionStorageError as exc:
            raise self._storage_error(exc) from None
        raise failure
