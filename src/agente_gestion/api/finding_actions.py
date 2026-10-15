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


router = APIRouter(prefix="/api/v1")
_ID = IDENTIFIER_PATTERN.pattern


def _code_action_for_request(request, settings, project, candidate, source, source_repository):
    configured_repository = getattr(settings, "github_repository", None)
    code_action = getattr(request.app.state, "code_action_port", None)
    if code_action is None:
        github_token = getattr(settings, "github_token", None)
        github_token = github_token.get_secret_value() if github_token is not None else None
        code_action = GitHubCodeActionAdapter(
            branches_dir=getattr(settings, "branches_dir", ".runtime/branches"),
            token=github_token,
            repository=configured_repository,
            base_branch=getattr(settings, "github_base_branch", "main"),
            external_writes_enabled=(
                getattr(settings, "external_writes_enabled", False)
                and source_repository is not None and configured_repository == source_repository
            ),
            project_mode=project.source_mode,
            source_lookup=(
                lambda repo, path: source
                if source is not None and path == candidate.file_path
                and (repo == source_repository or repo == configured_repository)
                else None
            ),
        )
    branch_repository = source_repository or configured_repository or "local/project"
    return code_action, branch_repository


@router.post(
    "/runs/{run_id}/findings/{finding_id}/decision",
    response_model=FindingDecisionResponse,
    tags=["Calidad"],
)
def decide_run_finding(
    request: Request,
    body: FindingDecisionRequest,
    run_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    finding_id: Annotated[str, Path(pattern=_ID, max_length=128)],
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
        candidate, original_excerpt, source, source_repository = repository.finding_action_context(
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
        code_action, branch_repository = _code_action_for_request(
            request, settings, project, candidate, source, source_repository,
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
            base_branch=getattr(settings, "github_base_branch", "main"),
            suggested_code_override=body.suggested_code_override,
            target_branch=body.target_branch,
            reason=body.reason,
        )
    except FindingDecisionError as exc:
        raise ApiProblem(exc.status_code, exc.code, exc.message) from None
    except CodeActionError as exc:
        raise ApiProblem(exc.status_code, exc.code, str(exc)) from None
    return FindingDecisionResponse.model_validate(result)
