"""Protected API endpoint for governed Trello proposal execution."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Header, Path, Request

from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.api.errors import ApiProblem
from agente_gestion.api.routes import _require_project_capability
from agente_gestion.api.settings import IDENTIFIER_PATTERN
from agente_gestion.application.proposal_execution import (
    ProposalExecutionError,
    ProposalExecutionInput,
    ProposalExecutionReceipt,
    ProposalExecutionService,
)


router = APIRouter(prefix="/api/v1", tags=["Gobernanza"])
_IDENTIFIER = IDENTIFIER_PATTERN.pattern


@router.post(
    "/projects/{project_id}/proposals/{proposal_id}/execute",
    response_model=ProposalExecutionReceipt,
    responses={409: {"description": "Conflicto o resultado previo incierto"},
               422: {"description": "Solicitud inválida"},
               503: {"description": "La ejecución segura no está disponible"}},
)
def execute_project_proposal(
    body: ProposalExecutionInput,
    request: Request,
    project_id: Annotated[str, Path(pattern=_IDENTIFIER, max_length=128)],
    proposal_id: Annotated[str, Path(pattern=_IDENTIFIER, max_length=128)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> ProposalExecutionReceipt:
    session = _require_project_capability(
        request, project_id, "governance:proposal_execute", mutation=True,
    )
    settings = request.app.state.settings
    project = next((item for item in settings.projects if item.id == project_id), None)
    if project is None:
        raise ApiProblem(404, "proyecto_no_configurado", "El proyecto no está configurado para esta operación.")
    cookie = request.cookies.get(settings.session_cookie_name)
    try:
        storage = SQLiteStorageAdapter(settings.db_path)
        return ProposalExecutionService(storage).execute(
            project_id=project_id,
            proposal_id=proposal_id,
            body=body,
            actor_id=session.actor,
            session_cookie=cookie,
            idempotency_key=idempotency_key,
            source_mode=project.source_mode,
            external_writes_enabled=settings.external_writes_enabled,
            trello_api_key=(settings.trello_api_key.get_secret_value() if settings.trello_api_key else None),
            trello_token=(settings.trello_token.get_secret_value() if settings.trello_token else None),
            trello_priority_labels=settings.trello_priority_labels.get(project_id, {}),
            trello_board_id=settings.trello_board_ids.get(project_id),
            timezone_name=settings.timezone,
        )
    except ProposalExecutionError as exc:
        raise ApiProblem(exc.status_code, exc.code, exc.message) from None
    except sqlite3.Error:
        raise ApiProblem(503, "base_no_disponible", "No se pudo registrar el intento de ejecución de forma segura.") from None
