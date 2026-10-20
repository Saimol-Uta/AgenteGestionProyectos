"""Idempotent application service for human-governed finding decisions."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from agente_gestion.adapters.storage.sqlite import (
    FindingDecisionStorageError,
    SQLiteStorageAdapter,
)
from agente_gestion.domain.code import CodeDiffSnippet, validate_source_syntax
from agente_gestion.domain.code_action import BranchExecutionResult, CodeSourceSnapshot, FindingActionOrigin
from agente_gestion.domain.findings import CandidateFinding
from agente_gestion.domain.patch_review import effective_patch
from agente_gestion.application.patch_review import (
    BLOCK_PATCH_NOT_IDENTIFIABLE,
    BLOCK_SUGGESTED_CODE_REQUIRED,
    ApprovalEligibility,
    confirmed_defect_verification,
    evaluate_approval_eligibility,
    match_patch_review,
)
from agente_gestion.application.finding_publication import (
    PUBLICATION_BASE_BRANCH,
    PUBLICATION_NOT_REQUESTED,
    PUBLICATION_PARTIAL,
    PublicationError,
    PublicationOutcome,
    latest_finding_publication,
    mark_publication_partial,
    mark_publication_published,
    publish_pull_request,
    record_branch_checkpoint,
)
from agente_gestion.domain.security_rbac import SecurityAuditEntry, hash_token_fingerprint
from agente_gestion.ports.code_action_port import CodeActionPort


APPROVAL_REVIEW_REQUIRED_CODE = "revision_independiente_requerida"
APPROVAL_REVIEW_REQUIRED_MESSAGE = (
    "No se puede aprobar ni ejecutar esta corrección de GitHub: requiere una revisión "
    "independiente favorable y vigente, vinculada al parche exacto que se aplicaría. "
    "Un defecto confirmado, una sintaxis válida o unas pruebas aprobadas no la sustituyen."
)


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
    def _review_note(eligibility: ApprovalEligibility | None) -> str:
        """The identity of the review that authorized the decision, if any."""
        if eligibility is None or eligibility.review is None:
            return ""
        review = eligibility.review
        return (
            f" Review {review.review_id} sha256:{review.patch_sha256}"
            f" run:{review.run_id} origin:{review.commit_sha}"
        )

    @staticmethod
    def _publication_audit_id(event_id: str, attempts: int) -> str:
        """One deterministic audit identifier per publication attempt."""
        return f"audit-{event_id}-publication-{attempts}"

    @staticmethod
    def _publication_audit_entry(
        *, event_id: str, project_id: str, finding_id: str, actor_id: str,
        session_cookie: str | None, reason: str, granted: bool,
    ) -> SecurityAuditEntry:
        """The audit record of one publication attempt."""
        return SecurityAuditEntry(
            event_id=event_id,
            project_id=project_id,
            actor_id=actor_id,
            role="project_manager_session",
            permission_checked="web:finding_publication",
            resource_id=finding_id,
            decision="granted" if granted else "denied",
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
        request_id = f"fdreq-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]}"
        event_id = f"finding-{digest[:24]}"
        return request_id, event_id

    @staticmethod
    def _legacy_request_id(project_id: str, run_id: str, finding_id: str, key: str) -> str:
        scope = json.dumps([project_id, run_id, finding_id, key], separators=(",", ":"))
        digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()
        return f"fdreq-{digest[:32]}"

    @staticmethod
    def _target_branch(
        finding_id: str, idempotency_key: str, supplied: str | None,
        origin: FindingActionOrigin | None = None,
    ) -> str:
        if supplied:
            return supplied
        if ":" in finding_id:
            scope = [
                origin.project_id if origin else None,
                origin.run_id if origin else None,
                finding_id,
                origin.repository_identity.model_dump(mode="json") if origin and origin.repository_identity else None,
                origin.repository if origin else None,
                origin.commit_sha if origin else None,
                idempotency_key,
            ]
            digest = hashlib.sha256(json.dumps(
                scope, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            return f"quality/scoped-{digest[:32]}"
        suffix = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:10]
        if origin is not None and (origin.repository_identity is not None or origin.multi_repository):
            scope = [
                origin.project_id, origin.run_id, finding_id,
                origin.repository_identity.model_dump(mode="json") if origin.repository_identity else None,
                origin.repository, origin.commit_sha, idempotency_key,
            ]
            scope_digest = hashlib.sha256(json.dumps(
                scope, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()[:12]
            return f"quality/{finding_id}-{suffix}-{scope_digest}"
        return f"quality/{finding_id}-{suffix}"

    @staticmethod
    def _same_repository_identity(left, right) -> bool:
        return bool(
            left is not None and right is not None and left.id == right.id
            and left.owner.casefold() == right.owner.casefold()
            and left.repo.casefold() == right.repo.casefold()
        )

    def _validate_persisted_context(
        self, *, origin: FindingActionOrigin | None, project_id: str, run_id: str,
        finding_id: str, candidate: CandidateFinding, original_excerpt: str,
        source: CodeSourceSnapshot | None, repository: str | None, base_branch: str,
    ) -> None:
        def refuse() -> None:
            raise FindingDecisionError(
                409, "fuente_no_verificable",
                "El origen del hallazgo no coincide con la fuente y el alcance almacenados.",
            )

        run = self.storage.get_run(run_id)
        if run is None or run.project_id != project_id or run.run_id != run_id:
            refuse()
        report = self.storage.get_report(run.report_id) if run.report_id else None
        if report is None or report.project_id != project_id or report.run_id != run_id:
            refuse()
        stored_candidates = [item for item in report.candidate_findings if item.id == finding_id]
        if not stored_candidates:
            stored_candidates = [
                item for item in [
                    *report.confirmed_findings, *report.rejected_findings,
                    *report.insufficient_findings, *report.pending_findings,
                ] if item.id == finding_id
            ]
        if len(stored_candidates) != 1:
            refuse()
        stored_candidate = stored_candidates[0]
        if (stored_candidate.commit_sha.lower() != candidate.commit_sha.lower()
                or stored_candidate.file_path != candidate.file_path
                or stored_candidate.line != candidate.line
                or stored_candidate.evidence != candidate.evidence
                or stored_candidate.evidence_ids != candidate.evidence_ids
                or (stored_candidate.provenance is not None
                    and stored_candidate.provenance.certifies_target is False)):
            refuse()
        stored_identity = stored_candidate.repository_identity or (
            stored_candidate.provenance.repository_identity
            if stored_candidate.provenance is not None else None
        )
        supplied_identity = candidate.repository_identity or (
            candidate.provenance.repository_identity if candidate.provenance is not None else None
        )
        if ((stored_identity is None) != (supplied_identity is None)
                or stored_identity is not None
                and not self._same_repository_identity(stored_identity, supplied_identity)):
            refuse()
        if stored_candidate.provenance is not None and (
            stored_candidate.provenance.commit_sha not in {None, stored_candidate.commit_sha.lower()}
            or stored_candidate.provenance.repository_identity != (
                candidate.provenance.repository_identity if candidate.provenance is not None else None
            )
        ):
            refuse()
        stored_row = next((item for item in self.storage.get_findings(run_id)
                           if item.get("id", item.get("finding_id")) == finding_id), None)
        if (stored_row is None
                or stored_row.get("commit_sha") not in {None, candidate.commit_sha.lower()}
                or stored_row.get("file_path") not in {None, candidate.file_path}
                or stored_row.get("evidence") != stored_candidate.evidence):
            refuse()
        if original_excerpt != (stored_candidate.evidence or ""):
            refuse()

        snapshot = self.storage.get_snapshot(run.snapshot_id) if run.snapshot_id else None
        if snapshot is None or snapshot.project_id != project_id:
            refuse()
        report_states = []
        for result in report.repository_results:
            grouped = [
                *result.confirmed_findings, *result.rejected_findings,
                *result.insufficient_findings, *result.pending_findings,
            ]
            reported = next((item for item in grouped if item.id == finding_id), None)
            if reported is None:
                continue
            state = result.repository_state
            if (reported.commit_sha.lower() != candidate.commit_sha.lower()
                    or reported.file_path != candidate.file_path
                    or reported.evidence != stored_candidate.evidence
                    or state.target_commit_sha is not None
                    and state.target_commit_sha.lower() != candidate.commit_sha.lower()):
                refuse()
            if stored_identity is not None and not self._same_repository_identity(state.identity, stored_identity):
                refuse()
            if stored_identity is None:
                stored_identity = state.identity
            report_states.append(state)

        multi_repository = bool(
            len(report.repository_results) > 1 or len(snapshot.repository_results) > 1
        )
        if origin is not None and origin.multi_repository != multi_repository:
            refuse()
        if origin is not None and (
            origin.project_id != project_id or origin.run_id != run_id
            or origin.finding_id != finding_id
            or origin.commit_sha != candidate.commit_sha.lower()
            or origin.file_path != candidate.file_path
            or origin.excerpt_sha256 != (
                hashlib.sha256(stored_candidate.evidence.encode("utf-8")).hexdigest()
                if stored_candidate.evidence else None
            )
        ):
            refuse()
        if origin is not None and stored_identity is not None and not self._same_repository_identity(
            origin.repository_identity, stored_identity,
        ):
            refuse()
        entries: dict[str, tuple[CodeDiffSnippet, list[object]]] = {}
        raw_entries = [(item, None) for item in snapshot.code_diffs]
        for result in snapshot.repository_results:
            raw_entries.extend((item, result.repository_state) for item in result.code_diffs)
        for raw, state in raw_entries:
            try:
                snippet = raw if isinstance(raw, CodeDiffSnippet) else CodeDiffSnippet.model_validate(raw)
            except (TypeError, ValueError):
                refuse()
            if (snippet.commit_sha.lower() != candidate.commit_sha.lower()
                    or snippet.file_path != candidate.file_path):
                continue
            entry = entries.setdefault(snippet.model_dump_json(), (snippet, []))
            if state is not None and all(
                existing.model_dump_json() != state.model_dump_json() for existing in entry[1]
            ):
                entry[1].append(state)

        persisted_repository_names = {snippet.repository.casefold() for snippet, _states in entries.values()}
        if (origin is not None and origin.repository is None
                and (stored_identity is not None or persisted_repository_names)):
            refuse()
        if (origin is not None and origin.repository is not None and stored_identity is None
                and not persisted_repository_names):
            refuse()
        if (origin is not None and origin.repository is not None and persisted_repository_names
                and origin.repository.casefold() not in persisted_repository_names):
            refuse()
        source_matches = []
        source_states = []
        matching_identities = []
        for snippet, states in entries.values():
            snippet_repository = snippet.repository.casefold()
            if origin is not None and origin.repository is not None and (
                    snippet_repository != origin.repository.casefold()):
                continue
            if stored_identity is not None and snippet_repository != (
                    f"{stored_identity.owner}/{stored_identity.repo}".casefold()):
                continue
            snippet_identity = snippet.repository_identity or (
                snippet.provenance.repository_identity if snippet.provenance is not None else None
            )
            if snippet.provenance is not None and (
                snippet.provenance.certifies_target is False
                or snippet.provenance.commit_sha not in {None, candidate.commit_sha.lower()}
            ):
                refuse()
            if snippet_identity is not None:
                matching_identities.append(snippet_identity)
                if stored_identity is not None and not self._same_repository_identity(
                    snippet_identity, stored_identity,
                ):
                    refuse()
                if origin is not None and origin.repository_identity is not None and not self._same_repository_identity(
                    snippet_identity, origin.repository_identity,
                ):
                    refuse()
            for state in states:
                matching_identities.append(state.identity)
                if stored_identity is not None and not self._same_repository_identity(state.identity, stored_identity):
                    refuse()
                if origin is not None and origin.repository_identity is not None and not self._same_repository_identity(
                    state.identity, origin.repository_identity,
                ):
                    refuse()
                if state.target_commit_sha is not None and state.target_commit_sha.lower() != candidate.commit_sha.lower():
                    refuse()
                source_states.append(state)
            if not snippet.is_complete or snippet.truncated or snippet.evidence_scope != "full_file":
                continue
            if (not snippet.source_sha256
                    or hashlib.sha256(snippet.content.encode("utf-8")).hexdigest() != snippet.source_sha256
                    or snippet.total_lines != len(snippet.content.splitlines())
                    or candidate.line not in snippet.lines):
                refuse()
            source_matches.append((snippet, states))

        persisted_branches = {state.branch for state in [*report_states, *source_states]}
        if origin is not None and origin.base_branch is not None and persisted_branches and (
                persisted_branches != {origin.base_branch}):
            refuse()
        if persisted_branches and len(persisted_branches) == 1 and base_branch not in persisted_branches:
            refuse()
        known_repositories = {snippet.repository.casefold() for snippet, _state in source_matches}
        if len(known_repositories) > 1:
            refuse()
        if origin is not None and matching_identities and origin.repository_identity is None:
            refuse()
        if origin is not None and any(
            not self._same_repository_identity(item, origin.repository_identity)
            for item in matching_identities
        ):
            refuse()
        if origin is not None and origin.repository_identity is not None:
            if stored_identity is not None and not self._same_repository_identity(
                origin.repository_identity, stored_identity,
            ):
                refuse()
            if stored_identity is None and not any(
                self._same_repository_identity(item, origin.repository_identity)
                for item in matching_identities
            ):
                refuse()
        if origin is not None:
            if origin.repository is not None and stored_identity is not None and origin.repository.casefold() != (
                    f"{stored_identity.owner}/{stored_identity.repo}".casefold()):
                refuse()
            if origin.repository is not None and known_repositories and known_repositories != {
                    origin.repository.casefold()}:
                refuse()
            if origin.repository is not None and source_matches and len(source_matches) != 1:
                refuse()
            if source_matches and origin.source_sha256 != source_matches[0][0].source_sha256:
                refuse()
            if origin.source_sha256 is not None and (
                    len(source_matches) != 1 or source_matches[0][0].source_sha256 != origin.source_sha256):
                refuse()
            if origin.source_blob_sha is not None and (
                    len(source_matches) != 1 or source_matches[0][0].source_blob_sha != origin.source_blob_sha):
                refuse()
        if source is not None:
            matching_source = [snippet for snippet, _state in source_matches
                               if snippet.repository.casefold() == (repository or "").casefold()]
            if (len(matching_source) != 1
                    or source.content != matching_source[0].content
                    or source.repository is None
                    or source.repository.casefold() != matching_source[0].repository.casefold()
                    or source.commit_sha is None
                    or source.commit_sha.lower() != matching_source[0].commit_sha.lower()
                    or source.file_path != matching_source[0].file_path
                    or source.source_sha256 != matching_source[0].source_sha256
                    or source.source_blob_sha != matching_source[0].source_blob_sha):
                refuse()

    def _validate_origin(
        self, *, origin: FindingActionOrigin | None, project_id: str, run_id: str, finding_id: str,
        candidate: CandidateFinding, original_excerpt: str, source: CodeSourceSnapshot | None,
        repository: str | None, base_branch: str,
    ) -> None:
        self._validate_persisted_context(
            origin=origin, project_id=project_id, run_id=run_id, finding_id=finding_id,
            candidate=candidate, original_excerpt=original_excerpt, source=source,
            repository=repository, base_branch=base_branch,
        )

        def refuse() -> None:
            raise FindingDecisionError(
                409, "fuente_no_verificable",
                "El origen del hallazgo no coincide con la fuente y el alcance almacenados.",
            )

        candidate_sha = candidate.commit_sha.lower()
        candidate_identity = candidate.repository_identity or (
            candidate.provenance.repository_identity if candidate.provenance is not None else None
        )
        if (candidate.provenance is not None and candidate.provenance.commit_sha is not None
                and candidate.provenance.commit_sha != candidate_sha):
            refuse()
        if origin is None:
            if (candidate_identity is not None and repository is not None
                    and repository.casefold() != f"{candidate_identity.owner}/{candidate_identity.repo}".casefold()):
                refuse()
            if source is not None and (
                source.repository is not None and repository is not None
                and source.repository.casefold() != repository.casefold()
                or source.commit_sha is not None and source.commit_sha.lower() != candidate_sha
                or source.file_path is not None and source.file_path != candidate.file_path
            ):
                refuse()
            return
        if (origin.project_id != project_id or origin.run_id != run_id or origin.finding_id != finding_id
                or candidate.id != origin.finding_id
                or origin.commit_sha is not None and origin.commit_sha != candidate_sha
                or origin.file_path is not None and origin.file_path != candidate.file_path
                or origin.repository is not None and (
                    repository is None or repository.casefold() != origin.repository.casefold()
                )
                or origin.base_branch is not None and base_branch != origin.base_branch):
            refuse()
        if candidate_identity is not None and (
            origin.repository_identity is None
            or origin.repository_identity.id != candidate_identity.id
            or origin.repository_identity.owner.casefold() != candidate_identity.owner.casefold()
            or origin.repository_identity.repo.casefold() != candidate_identity.repo.casefold()
        ):
            refuse()
        if origin.repository_identity is not None and origin.repository is not None and (
            origin.repository.casefold() != (
                f"{origin.repository_identity.owner}/{origin.repository_identity.repo}".casefold()
            )
        ):
            refuse()
        if origin.excerpt_sha256 is not None and hashlib.sha256(
            original_excerpt.encode("utf-8"),
        ).hexdigest() != origin.excerpt_sha256:
            refuse()
        if source is not None:
            if (source.repository is not None and origin.repository is not None
                    and source.repository.casefold() != origin.repository.casefold()
                    or source.commit_sha is not None and origin.commit_sha is not None
                    and source.commit_sha.lower() != origin.commit_sha
                    or source.file_path is not None and origin.file_path is not None
                    and source.file_path != origin.file_path
                    or source.source_sha256 is not None and origin.source_sha256 is not None
                    and source.source_sha256 != origin.source_sha256
                    or source.source_blob_sha is not None and origin.source_blob_sha is not None
                    and source.source_blob_sha != origin.source_blob_sha):
                refuse()
            if source.is_complete and (
                source.commit_sha is None or source.commit_sha.lower() != candidate_sha
                or source.file_path != candidate.file_path
                or not source.source_sha256
                or hashlib.sha256(source.content.encode("utf-8")).hexdigest() != source.source_sha256
                or source.total_lines != len(source.content.splitlines())
            ):
                refuse()

    def _approval_context_is_valid(
        self, *, origin: FindingActionOrigin | None, project_id: str, run_id: str,
        finding_id: str, candidate: CandidateFinding, original_excerpt: str,
        source: CodeSourceSnapshot | None, repository: str | None, base_branch: str,
    ) -> bool:
        """Whether the persisted context still supports this exact correction."""
        if candidate.id != finding_id:
            return False
        if origin is None or not origin.configured_for_code_action:
            return False
        try:
            self._validate_origin(
                origin=origin, project_id=project_id, run_id=run_id, finding_id=finding_id,
                candidate=candidate, original_excerpt=original_excerpt, source=source,
                repository=repository, base_branch=base_branch,
            )
        except FindingDecisionError:
            return False
        except Exception:
            return False
        return True

    def _approval_eligibility(
        self, *, origin: FindingActionOrigin | None, project_id: str, run_id: str,
        finding_id: str, candidate: CandidateFinding, original_excerpt: str,
        source: CodeSourceSnapshot | None, repository: str | None, base_branch: str,
        suggested_code_override: str | None = None,
    ) -> ApprovalEligibility:
        """Gather the facts of the authorization chain and apply the shared policy."""
        effective_code = (
            suggested_code_override if suggested_code_override is not None else candidate.suggested_code
        )
        patch_sha256: str | None = None
        patch_blocked_reason: str | None = None
        if not effective_code.strip():
            patch_blocked_reason = BLOCK_SUGGESTED_CODE_REQUIRED
        elif source is None or not source.is_complete or not source.content or not original_excerpt:
            patch_blocked_reason = BLOCK_PATCH_NOT_IDENTIFIABLE
        else:
            try:
                _patched, patch_sha256 = effective_patch(
                    source.content, original_excerpt, effective_code,
                )
            except ValueError:
                patch_blocked_reason = BLOCK_PATCH_NOT_IDENTIFIABLE
        defect = confirmed_defect_verification(
            self.storage, run_id=run_id, finding_id=finding_id,
        )
        match = None
        if patch_sha256 is not None and source is not None and repository and source.commit_sha:
            match = match_patch_review(
                self.storage,
                project_id=project_id, run_id=run_id, finding_id=finding_id,
                patch_sha256=patch_sha256,
                original_sha256=hashlib.sha256(source.content.encode("utf-8")).hexdigest(),
                repository=repository, commit_sha=source.commit_sha,
                file_path=candidate.file_path,
            )
        return evaluate_approval_eligibility(
            context_valid=self._approval_context_is_valid(
                origin=origin, project_id=project_id, run_id=run_id, finding_id=finding_id,
                candidate=candidate, original_excerpt=original_excerpt, source=source,
                repository=repository, base_branch=base_branch,
            ),
            defect_confirmed=defect is not None,
            patch_sha256=patch_sha256,
            matched_review=match.review if match is not None else None,
            review_blocked_reason=match.blocked_reason if match is not None else None,
            patch_blocked_reason=patch_blocked_reason,
        )

    def approval_eligibility(
        self,
        *,
        project_id: str,
        run_id: str,
        finding_id: str,
        candidate: CandidateFinding,
        original_excerpt: str,
        source: CodeSourceSnapshot | None,
        repository: str | None,
        base_branch: str,
        origin: FindingActionOrigin | None,
        suggested_code_override: str | None = None,
    ) -> dict[str, object]:
        """The authorization fact of one finding, read-only and side-effect free.

        This is the same fact the decision gate uses, so an interface can
        enable an approval only when the backend says the chain is complete.
        It never records a decision and never authorizes anything by itself.
        """
        eligibility = self._approval_eligibility(
            origin=origin, project_id=project_id, run_id=run_id, finding_id=finding_id,
            candidate=candidate, original_excerpt=original_excerpt, source=source,
            repository=repository, base_branch=base_branch,
            suggested_code_override=suggested_code_override,
        )
        return {
            "eligible": eligibility.eligible,
            "authorized": eligibility.authorized,
            "context_valid": eligibility.context_valid,
            "defect_confirmed": eligibility.defect_confirmed,
            "blocked_reason": eligibility.blocked_reason,
            "authorization_reason": eligibility.authorization_reason,
            "patch_sha256": eligibility.patch_sha256,
            "review_id": eligibility.review_id,
            "review": (
                eligibility.review.model_dump(mode="json")
                if eligibility.review is not None else None
            ),
        }

    def _publish_approved_branch(
        self, *, project_id: str, run_id: str, finding_id: str, request_id: str,
        event_id: str, actor_id: str, idempotency_key: str, origin: FindingActionOrigin,
        branch_result: BranchExecutionResult, eligibility: ApprovalEligibility,
        session_cookie: str | None,
    ) -> tuple[PublicationOutcome, SecurityAuditEntry]:
        """Persist the branch checkpoint, publish one pull request and audit it.

        The checkpoint of the branch and of the review that authorized it is
        written before the publication side effect, so an interrupted
        publication can be recovered without creating a second branch.
        """
        repository = origin.repository
        patch_sha256 = eligibility.patch_sha256 or ""
        checkpoint = record_branch_checkpoint(
            self.storage,
            project_id=project_id, run_id=run_id, finding_id=finding_id,
            request_id=request_id, event_id=event_id, actor_id=actor_id,
            idempotency_key=idempotency_key, repository=repository,
            base_branch=PUBLICATION_BASE_BRANCH, head_branch=branch_result.branch_name,
            head_commit_sha=branch_result.commit_sha_applied,
            branch_url=branch_result.branch_url,
            branch_execution_mode=branch_result.execution_mode,
            patch_sha256=patch_sha256, patch_review_id=eligibility.review_id,
        )
        publication_id = checkpoint["publication_id"]
        try:
            outcome = publish_pull_request(
                code_action=self.code_action, repository=repository,
                branch_result=branch_result, finding_id=finding_id, actor=actor_id,
                patch_sha256=patch_sha256,
            )
        except PublicationError as exc:
            partial = mark_publication_partial(
                self.storage, publication_id=publication_id, error_code=exc.code,
                error_message=exc.message,
            )
            audit = self._publication_audit_entry(
                event_id=self._publication_audit_id(event_id, partial["attempts"]),
                project_id=project_id, finding_id=finding_id, actor_id=actor_id,
                session_cookie=session_cookie,
                reason=f"Pull request publication refused with code {exc.code}. {exc.message}",
                granted=False,
            )
            return PublicationOutcome(
                status=PUBLICATION_PARTIAL, error_code=exc.code, error_message=exc.message,
            ), audit
        published = mark_publication_published(
            self.storage, publication_id=publication_id,
            pull_request_number=outcome.pull_request_number or 0,
            pull_request_url=outcome.pull_request_url or "",
            pull_request_state=outcome.pull_request_state or "",
            pull_request_simulated=bool(outcome.pull_request_simulated),
        )
        audit = self._publication_audit_entry(
            event_id=self._publication_audit_id(event_id, published["attempts"]),
            project_id=project_id, finding_id=finding_id, actor_id=actor_id,
            session_cookie=session_cookie,
            reason=(
                f"Pull request {outcome.pull_request_number} published against"
                f" {PUBLICATION_BASE_BRANCH} in {branch_result.execution_mode} mode."
            ),
            granted=True,
        )
        return outcome, audit

    def _recover_publication(
        self, *, result: dict[str, object], existing: dict[str, Any], project_id: str,
        run_id: str, finding_id: str, origin: FindingActionOrigin | None,
        eligibility: ApprovalEligibility | None, actor_id: str, session_cookie: str | None,
    ) -> dict[str, object]:
        """Recover one interrupted publication without a second human decision.

        The authorization is revalidated against the current source and the
        current review before anything is published, and the persisted branch
        checkpoint is reused as it is: no branch is created again and no second
        decision is recorded.
        """
        if eligibility is None or not eligibility.authorized:
            raise FindingDecisionError(
                409, APPROVAL_REVIEW_REQUIRED_CODE, APPROVAL_REVIEW_REQUIRED_MESSAGE,
            )
        checkpoint = latest_finding_publication(
            self.storage, project_id=project_id, run_id=run_id, finding_id=finding_id,
        )
        if checkpoint is None or not checkpoint["branch_url"]:
            raise FindingDecisionError(
                503, "publicacion_no_disponible",
                "No hay un punto de control durable de la rama publicada.",
            )
        if (origin is None or not origin.repository
                or checkpoint["repository"].casefold() != origin.repository.casefold()
                or checkpoint["patch_sha256"] != eligibility.patch_sha256
                or checkpoint["patch_review_id"] != eligibility.review_id):
            raise FindingDecisionError(
                409, APPROVAL_REVIEW_REQUIRED_CODE, APPROVAL_REVIEW_REQUIRED_MESSAGE,
            )
        try:
            branch_result = BranchExecutionResult(
                execution_mode=checkpoint["execution_mode"],
                branch_name=checkpoint["branch_name"],
                branch_url=checkpoint["branch_url"],
                commit_sha_applied=checkpoint["commit_sha_applied"],
            )
        except ValueError:
            raise FindingDecisionError(
                503, "publicacion_no_disponible",
                "El punto de control durable de la rama publicada no es utilizable.",
            ) from None
        publication_id = checkpoint["publication_id"]
        event_id = existing["event_id"]
        try:
            outcome = publish_pull_request(
                code_action=self.code_action, repository=checkpoint["repository"],
                branch_result=branch_result, finding_id=finding_id, actor=actor_id,
                patch_sha256=checkpoint["patch_sha256"],
            )
        except PublicationError as exc:
            partial = mark_publication_partial(
                self.storage, publication_id=publication_id, error_code=exc.code,
                error_message=exc.message,
            )
            self.storage.append_security_audit_event(self._publication_audit_entry(
                event_id=self._publication_audit_id(event_id, partial["attempts"]),
                project_id=project_id, finding_id=finding_id, actor_id=actor_id,
                session_cookie=session_cookie,
                reason=f"Pull request publication refused with code {exc.code}. {exc.message}",
                granted=False,
            ))
            result.update(PublicationOutcome(
                status=PUBLICATION_PARTIAL, error_code=exc.code, error_message=exc.message,
            ).result_fields())
            self._refresh_receipt(existing, result)
            return result
        published = mark_publication_published(
            self.storage, publication_id=publication_id,
            pull_request_number=outcome.pull_request_number or 0,
            pull_request_url=outcome.pull_request_url or "",
            pull_request_state=outcome.pull_request_state or "",
            pull_request_simulated=bool(outcome.pull_request_simulated),
        )
        self.storage.append_security_audit_event(self._publication_audit_entry(
            event_id=self._publication_audit_id(event_id, published["attempts"]),
            project_id=project_id, finding_id=finding_id, actor_id=actor_id,
            session_cookie=session_cookie,
            reason=(
                f"Pull request {outcome.pull_request_number} recovered against"
                f" {PUBLICATION_BASE_BRANCH} in {branch_result.execution_mode} mode."
            ),
            granted=True,
        ))
        result.update(outcome.result_fields())
        self._refresh_receipt(existing, result)
        return result

    def _refresh_receipt(self, existing: dict[str, Any], result: dict[str, object]) -> None:
        """Refresh the replay receipt of a completed decision, without a new one."""
        try:
            self.storage.update_finding_decision_receipt(
                request_id=existing["request_id"],
                result_json=json.dumps(
                    result, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                ),
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
        except FindingDecisionStorageError:
            # The immutable decision and the durable publication ledger remain
            # the authority; only the replayable receipt could not be refreshed.
            pass

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
        origin: FindingActionOrigin | None = None,
    ) -> dict[str, object]:
        if candidate.id != finding_id:
            raise FindingDecisionError(404, "hallazgo_no_encontrado", "No se encontró el hallazgo solicitado.")
        if decision not in {"approve", "reject", "request_changes"}:
            raise FindingDecisionError(422, "decision_invalida", "La decisión enviada no es válida.")
        if suggested_code_override is not None and not suggested_code_override.strip():
            raise FindingDecisionError(422, "codigo_sugerido_invalido", "El código alternativo no puede estar vacío.")
        effective_code = suggested_code_override if suggested_code_override is not None else candidate.suggested_code
        eligibility: ApprovalEligibility | None = None
        if decision == "approve":
            # The approval requires the whole chain: a confirmed defect, an
            # exact patch and an independent favorable review bound to that
            # execution, repository, file, origin commit and original source.
            # A confirmed defect, a valid syntax or approved tests are not a
            # review of the correction, and the review of another execution
            # never applies.
            eligibility = self._approval_eligibility(
                origin=origin, project_id=project_id, run_id=run_id, finding_id=finding_id,
                candidate=candidate, original_excerpt=original_excerpt, source=source,
                repository=repository, base_branch=base_branch,
                suggested_code_override=suggested_code_override,
            )
            if not eligibility.authorized:
                raise FindingDecisionError(
                    409, APPROVAL_REVIEW_REQUIRED_CODE, APPROVAL_REVIEW_REQUIRED_MESSAGE,
                )
        self._validate_origin(
            origin=origin, project_id=project_id, run_id=run_id, finding_id=finding_id,
            candidate=candidate, original_excerpt=original_excerpt, source=source,
            repository=repository, base_branch=base_branch,
        )
        if decision == "approve" and (
            origin is None or not origin.configured_for_code_action
        ):
            raise FindingDecisionError(
                409, "fuente_no_verificable",
                "Se requiere un origen de repositorio configurado y fijado para aprobar código.",
            )
        selected_branch = self._target_branch(finding_id, idempotency_key, target_branch, origin)
        if decision == "approve" and selected_branch == base_branch:
            raise FindingDecisionError(422, "rama_destino_invalida", "La rama destino debe ser distinta de la rama base.")
        legacy_request = {
            "decision": decision,
            "suggested_code_override": suggested_code_override,
            "target_branch": target_branch,
            "reason": reason.strip() if reason else None,
            "actor_id": actor_id,
        }
        normalized_request = {
            **legacy_request,
            "origin": origin.model_dump(mode="json") if origin is not None else None,
        }
        request_json = json.dumps(normalized_request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        request_sha256 = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        legacy_request_json = json.dumps(
            legacy_request, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        legacy_request_sha256 = hashlib.sha256(legacy_request_json.encode("utf-8")).hexdigest()
        request_id, event_id = self._ids(project_id, run_id, finding_id, idempotency_key)
        legacy_request_id = self._legacy_request_id(project_id, run_id, finding_id, idempotency_key)
        now = datetime.now(timezone.utc).isoformat()

        if decision == "approve" and not effective_code.strip():
            raise FindingDecisionError(409, "codigo_sugerido_requerido", "El hallazgo no contiene código sugerido para aprobar.")
        if decision == "approve":
            if (source is None or not source.is_complete or origin is None
                    or not origin.configured_for_code_action
                    or source.repository is None or repository is None
                    or source.repository.casefold() != repository.casefold()
                    or source.file_path != candidate.file_path
                    or source.commit_sha is None or source.commit_sha.lower() != candidate.commit_sha.lower()
                    or not source.source_sha256
                    or origin.source_sha256 is None or source.source_sha256 != origin.source_sha256
                    or origin.excerpt_sha256 is None
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
            if exc.code != "idempotency_conflict":
                raise self._storage_error(exc) from None
            try:
                reservation, existing = self.storage.reserve_finding_decision(
                    project_id=project_id, run_id=run_id, finding_id=finding_id,
                    idempotency_key=idempotency_key, request_id=legacy_request_id, event_id=event_id,
                    request_sha256=legacy_request_sha256, request_json=legacy_request_json,
                    decision=decision, actor_id=actor_id, recorded_at=now,
                )
            except FindingDecisionStorageError as legacy_exc:
                raise self._storage_error(legacy_exc) from None
            if reservation != "replay":
                raise FindingDecisionError(
                    409, "idempotencia_conflictiva",
                    "La clave de idempotencia ya se usó con una solicitud distinta.",
                )

        if reservation == "replay":
            try:
                saved_request = json.loads(existing.get("request_json", "{}"))
            except (ValueError, TypeError):
                raise FindingDecisionError(503, "recibo_invalido", "El recibo durable de la decisión no es válido.") from None
            if existing["state"] == "failed":
                raise FindingDecisionError(
                    int(existing["error_status"] or 503),
                    str(existing["error_code"] or "decision_no_disponible"),
                    str(existing["error_message"] or "La decisión anterior no pudo completarse."),
                )
            if "origin" not in saved_request and existing.get("decision") == "approve":
                raise FindingDecisionError(
                    409, "fuente_no_verificable",
                    "El recibo de aprobación heredado no permite verificar su repositorio y SHA de origen.",
                )
            try:
                result = json.loads(existing["result_json"])
            except (ValueError, TypeError):
                raise FindingDecisionError(503, "recibo_invalido", "El recibo durable de la decisión no es válido.") from None
            if (existing.get("decision") == "approve"
                    and result.get("publication_status") == PUBLICATION_PARTIAL):
                # The publication was interrupted. The authorization is
                # revalidated above and the persisted branch checkpoint is
                # reused: only the pull request is recovered, never the
                # branch, the commit or the human decision.
                result = self._recover_publication(
                    result=result, existing=existing, project_id=project_id, run_id=run_id,
                    finding_id=finding_id, origin=origin, eligibility=eligibility,
                    actor_id=actor_id, session_cookie=session_cookie,
                )
            result["reused"] = True
            return result

        branch_result: BranchExecutionResult | None = None
        publication: PublicationOutcome | None = None
        publication_audit: SecurityAuditEntry | None = None
        try:
            if decision == "approve":
                if origin is None or not origin.configured_for_code_action or not origin.repository:
                    raise FindingDecisionError(
                        409, "fuente_no_verificable",
                        "No se conoce un repositorio configurado y fijado para el hallazgo.",
                    )
                branch_result = self.code_action.create_verification_branch(
                    repo=origin.repository,
                    base_branch=origin.base_branch,
                    target_branch=selected_branch,
                    file_path=candidate.file_path,
                    original_excerpt=original_excerpt,
                    suggested_code=effective_code,
                    finding_id=finding_id,
                    actor=actor_id,
                )
                if branch_result.execution_mode not in {"live", "sandbox"}:
                    raise FindingDecisionError(503, "resultado_de_rama_invalido", "El adaptador no confirmó el modo de ejecución.")
                publication, publication_audit = self._publish_approved_branch(
                    project_id=project_id, run_id=run_id, finding_id=finding_id,
                    request_id=request_id, event_id=event_id, actor_id=actor_id,
                    idempotency_key=idempotency_key, origin=origin,
                    branch_result=branch_result, eligibility=eligibility,
                    session_cookie=session_cookie,
                )
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
                "publication_status": PUBLICATION_NOT_REQUESTED,
                "publication_error_code": None,
                "publication_error_message": None,
                "pull_request_number": None,
                "pull_request_url": None,
                "pull_request_state": None,
                "pull_request_simulated": None,
                "patch_review_id": None,
                "patch_sha256": None,
            }
            if publication is not None:
                result.update(publication.result_fields())
            if decision == "approve" and eligibility is not None:
                result["patch_review_id"] = eligibility.review_id
                result["patch_sha256"] = eligibility.patch_sha256
            if origin is not None and origin.repository is not None and origin.commit_sha is not None:
                owner, repository_name = origin.repository.split("/", 1)
                if origin.repository_identity is not None:
                    result["repository_id"] = origin.repository_identity.id
                result["repository_owner"] = owner
                result["repository_name"] = repository_name
                result["source_commit_sha"] = origin.commit_sha
            result_json = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            recorded_at = datetime.now(timezone.utc).isoformat()
            origin_note = (
                f" for {origin.repository}@{origin.commit_sha}"
                if origin is not None and origin.repository is not None and origin.commit_sha is not None
                else ""
            )
            audit = self._audit_entry(
                event_id=f"audit-{event_id}", project_id=project_id, finding_id=finding_id,
                actor_id=actor_id, session_cookie=session_cookie,
                reason=(
                    f"Finding decision {decision_status} recorded in {result['execution_mode']} mode"
                    f"{origin_note}{self._review_note(eligibility)}."
                ),
            )
            self.storage.complete_finding_decision(
                request_id=request_id, result_json=result_json, decision_status=decision_status,
                suggested_code=effective_code, improvement_summary=candidate.improvement_summary,
                branch_name=branch_result.branch_name if branch_result else None,
                branch_url=branch_result.branch_url if branch_result else None,
                commit_sha_applied=branch_result.commit_sha_applied if branch_result else None,
                audit_entry=audit, recorded_at=recorded_at,
                publication_audit_entry=publication_audit,
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
            reason=(
                f"Finding decision failed with code {failure.code}."
                f"{self._review_note(eligibility)}"
            ),
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
