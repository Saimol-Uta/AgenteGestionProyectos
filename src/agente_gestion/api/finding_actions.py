"""Protected HTTP endpoint for human-reviewed code finding decisions."""

from typing import Annotated

from fastapi import APIRouter, Header, Path, Request

from agente_gestion.adapters.github_code_action_adapter import CodeActionError, GitHubCodeActionAdapter
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.api.errors import ApiProblem
from agente_gestion.api.repository import DataIntegrityError, SQLiteQueryRepository
from agente_gestion.api.routes import _require_project_capability, _require_session
from agente_gestion.api.schemas import FindingDecisionRequest, FindingDecisionResponse
from agente_gestion.application.finding_decisions import FindingDecisionError, FindingDecisionService
from agente_gestion.api.settings import IDENTIFIER_PATTERN
from agente_gestion.domain.code_action import FindingActionOrigin


router = APIRouter(prefix="/api/v1")
_ID = IDENTIFIER_PATTERN.pattern
_FINDING_ID = r"^[A-Za-z0-9][A-Za-z0-9._-]*(?::[A-Za-z0-9][A-Za-z0-9._-]*)*$"


def _same_repository_name(left: str | None, right: str | None) -> bool:
    return bool(left and right and left.casefold() == right.casefold())


def _configured_origin(origin: FindingActionOrigin, settings, project) -> FindingActionOrigin:
    """Bind stored origin facts to exactly one current project repository selector."""
    if origin.repository is None:
        return origin
    repositories = tuple(getattr(project, "repositories", ()) or ())
    identity = origin.repository_identity
    if repositories:
        if identity is not None:
            same_id = [entry for entry in repositories if entry.id == identity.id]
            if any(not _same_repository_name(
                f"{entry.owner}/{entry.repo}", origin.repository,
            ) for entry in same_id):
                raise CodeActionError(
                    "fuente_no_verificable",
                    "La configuración actual contradice el repositorio de origen almacenado.", 409,
                )
            matches = [entry for entry in repositories
                       if entry.id == identity.id
                       and _same_repository_name(f"{entry.owner}/{entry.repo}", origin.repository)]
        else:
            matches = [entry for entry in repositories
                       if _same_repository_name(f"{entry.owner}/{entry.repo}", origin.repository)]
        if len(matches) != 1:
            return origin
        selected = matches[0]
        if origin.base_branch is not None and origin.base_branch != selected.branch:
            return origin
        return FindingActionOrigin.model_validate({
            **origin.model_dump(mode="python"),
            "repository": f"{selected.owner}/{selected.repo}",
            "base_branch": selected.branch,
            "configured_for_code_action": True,
        })

    # The pre-MultiRepo singleton remains usable only when stored source facts
    # explicitly match its one configured repository. Never use this for a run
    # whose persisted report/snapshot shows multiple repositories.
    configured_repository = getattr(settings, "github_repository", None)
    if (origin.multi_repository or not _same_repository_name(configured_repository, origin.repository)
            or origin.repository_identity is not None and not _same_repository_name(
                f"{origin.repository_identity.owner}/{origin.repository_identity.repo}", origin.repository,
            )):
        return origin
    base_branch = getattr(settings, "github_base_branch", None)
    if origin.base_branch is not None and origin.base_branch != base_branch:
        return origin
    return FindingActionOrigin.model_validate({
        **origin.model_dump(mode="python"),
        "repository": configured_repository,
        "base_branch": base_branch,
        "configured_for_code_action": True,
    })


def _code_action_for_request(request, settings, project, candidate, source, origin):
    code_action = getattr(request.app.state, "code_action_port", None)
    if code_action is None:
        github_token = getattr(settings, "github_token", None)
        github_token = github_token.get_secret_value() if github_token is not None else None
        if origin.configured_for_code_action:
            def source_lookup(repo: str, path: str):
                if (source is None or not _same_repository_name(repo, origin.repository)
                        or path != origin.file_path
                        or source.repository is None
                        or not _same_repository_name(source.repository, origin.repository)
                        or source.commit_sha != origin.commit_sha
                        or source.file_path != origin.file_path
                        or source.source_sha256 != origin.source_sha256):
                    return None
                return source

            code_action = GitHubCodeActionAdapter(
                branches_dir=getattr(settings, "branches_dir", ".runtime/branches"),
                token=github_token,
                repository=origin.repository,
                base_branch=origin.base_branch,
                external_writes_enabled=getattr(settings, "external_writes_enabled", False),
                project_mode=project.source_mode,
                source_lookup=source_lookup,
                origin=origin,
            )
        else:
            code_action = GitHubCodeActionAdapter(
                branches_dir=getattr(settings, "branches_dir", ".runtime/branches"),
                token=github_token,
                repository=None,
                base_branch="main",
                external_writes_enabled=False,
                project_mode=project.source_mode,
            )
    return code_action, origin.repository, origin.base_branch or ""


@router.post(
    "/runs/{run_id}/findings/{finding_id}/decision",
    response_model=FindingDecisionResponse,
    tags=["Calidad"],
)
def decide_run_finding(
    request: Request,
    body: FindingDecisionRequest,
    run_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    finding_id: Annotated[str, Path(pattern=_FINDING_ID, max_length=128)],
    idempotency_key: Annotated[str, Header(
        alias="Idempotency-Key", min_length=8, max_length=128,
        pattern=r"^[A-Za-z0-9._:-]{8,128}$",
    )],
) -> FindingDecisionResponse:
    session = _require_session(request)
    settings = request.app.state.settings
    repository: SQLiteQueryRepository = request.app.state.repository
    try:
        project_id = repository.run_project_id(run_id)
    except LookupError as exc:
        raise ApiProblem(404, "corrida_no_encontrada", "No se encontró la corrida solicitada.") from exc
    except DataIntegrityError:
        raise ApiProblem(500, "corrida_no_valida", "No se pudo validar la corrida almacenada.") from None

    session = _require_project_capability(
        request, project_id, "quality:decide", mutation=True,
    )
    try:
        candidate, original_excerpt, source, source_repository, origin = repository.finding_action_context(
            run_id, finding_id,
        )
    except LookupError as exc:
        code = str(exc)
        if code == "finding_not_actionable":
            raise ApiProblem(409, "hallazgo_no_accionable", "El hallazgo no tiene un artefacto de informe utilizable.") from None
        if code in {"finding_source_ambiguous", "finding_source_unavailable"}:
            raise ApiProblem(409, "fuente_no_verificable", "No hay una fuente única y verificable para este hallazgo.") from None
        raise ApiProblem(404, "hallazgo_no_encontrado", "No se encontró el hallazgo solicitado.") from None
    except DataIntegrityError:
        raise ApiProblem(500, "hallazgo_no_valido", "No se pudo validar el hallazgo almacenado.") from None

    project = next((item for item in settings.projects if item.id == project_id), None)
    if project is None:
        raise ApiProblem(404, "proyecto_no_configurado", "El proyecto no está configurado para esta operación.")
    try:
        origin = _configured_origin(origin, settings, project)
    except CodeActionError as exc:
        raise ApiProblem(exc.status_code, exc.code, str(exc)) from None
    if body.decision == "approve" and not origin.configured_for_code_action:
        raise ApiProblem(
            409, "fuente_no_verificable",
            "No hay un repositorio y una rama base configurados que coincidan con el origen almacenado.",
        )
    try:
        code_action, branch_repository, base_branch = _code_action_for_request(
            request, settings, project, candidate, source, origin,
        )
    except CodeActionError as exc:
        raise ApiProblem(exc.status_code, exc.code, str(exc)) from None

    try:
        service = FindingDecisionService(SQLiteStorageAdapter(settings.db_path), code_action)
        result = service.decide(
            project_id=project_id,
            run_id=run_id,
            finding_id=finding_id,
            candidate=candidate,
            decision=body.decision,
            idempotency_key=idempotency_key,
            actor_id=session.actor,
            session_cookie=request.cookies.get(settings.session_cookie_name),
            original_excerpt=original_excerpt,
            source=source,
            repository=branch_repository,
            base_branch=base_branch,
            origin=origin,
            suggested_code_override=body.suggested_code_override,
            target_branch=body.target_branch,
            reason=body.reason,
        )
    except FindingDecisionError as exc:
        raise ApiProblem(exc.status_code, exc.code, exc.message) from None
    except CodeActionError as exc:
        raise ApiProblem(exc.status_code, exc.code, str(exc)) from None
    return FindingDecisionResponse.model_validate(result)
