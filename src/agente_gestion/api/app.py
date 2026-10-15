"""FastAPI application factory for the local, read-only H5 web platform."""

from contextlib import asynccontextmanager
import sqlite3
import time
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from agente_gestion.api.analysis_jobs import AnalysisJobService, AnalysisRuntimePlan
from agente_gestion.api.control_plane import ControlPlaneRepository
from agente_gestion.api.errors import ApiProblem
from agente_gestion.api.repository import DataIntegrityError, SQLiteQueryRepository
from agente_gestion.api.routes import router
from agente_gestion.api.email_notifications import router as email_notifications_router
from agente_gestion.api.finding_actions import router as finding_actions_router
from agente_gestion.api.proposal_execution import router as proposal_execution_router
from agente_gestion.api.schemas import ErrorEnvelope
from agente_gestion.api.security import OperatorSessionStore
from agente_gestion.api.settings import ApiSettings, ProjectConfig, load_api_settings
from agente_gestion.api.weekly_cycles import WeeklyCycleService


_VERSION = "0.1.0"


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorEnvelope(error={"code": code, "message": message})
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def create_app(
    settings: ApiSettings | None = None,
    *,
    clock: Callable[[], float] | None = None,
    analysis_executor: Callable[[str, AnalysisRuntimePlan, bool], object] | None = None,
    analysis_plan_loader: Callable[[ApiSettings, str], AnalysisRuntimePlan] | None = None,
    schedule_clock: Callable[[], object] | None = None,
) -> FastAPI:
    """Build an isolated app instance without opening/creating a database or starting jobs."""
    selected = settings or load_api_settings()
    if analysis_plan_loader is None:
        analysis_service = AnalysisJobService(
            selected, executor=analysis_executor, clock=clock or time.monotonic,
        )
    else:
        analysis_service = AnalysisJobService(
            selected,
            executor=analysis_executor,
            plan_loader=analysis_plan_loader,
            clock=clock or time.monotonic,
        )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            yield
        finally:
            application.state.analysis_jobs.shutdown()
            application.state.weekly_cycles.shutdown()

    app = FastAPI(
        title="API web local — AgenteGestionProyectos",
        summary="Consultas seguras de proyectos, tareas y capturas históricas.",
        description=(
            "La consulta local es pública y de solo lectura; no autentica a un usuario privilegiado. "
            "Las sesiones de operador se verifican en el servidor y no habilitan escrituras externas."
        ),
        version=_VERSION,
        lifespan=lifespan,
    )
    app.state.settings = selected
    app.state.repository = SQLiteQueryRepository(selected)
    app.state.control_plane = ControlPlaneRepository(selected)
    app.state.sessions = OperatorSessionStore(selected, clock=clock) if clock else OperatorSessionStore(selected)
    app.state.analysis_jobs = analysis_service
    app.state.weekly_cycles = WeeklyCycleService(
        selected,
        app.state.control_plane,
        clock=clock or time.monotonic,
        schedule_clock=schedule_clock,
    )

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(selected.allowed_hosts),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(selected.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key"],
    )

    @app.middleware("http")
    async def require_origin_for_mutations(request: Request, call_next):
        if (request.url.path.startswith("/api/v1/")
                and request.method not in {"GET", "HEAD", "OPTIONS"}
                and request.headers.get("origin") not in selected.allowed_origins):
            return _error(403, "origen_no_permitido", "El origen de la solicitud no está permitido.")
        return await call_next(request)

    @app.exception_handler(ApiProblem)
    async def handle_api_problem(_request: Request, exc: ApiProblem) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        return _error(422, "validacion_invalida", "Los parámetros o datos enviados no son válidos.")

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return _error(404, "recurso_no_encontrado", "No se encontró el recurso solicitado.")
        if exc.status_code == 405:
            return _error(405, "metodo_no_permitido", "El método solicitado no está disponible.")
        return _error(exc.status_code, "solicitud_rechazada", "La solicitud no pudo completarse.")

    @app.exception_handler(DataIntegrityError)
    async def handle_integrity_error(_request: Request, _exc: DataIntegrityError) -> JSONResponse:
        return _error(500, "datos_no_validos", "No se pudo validar la integridad de los datos almacenados.")

    @app.exception_handler(sqlite3.Error)
    async def handle_database_error(_request: Request, _exc: sqlite3.Error) -> JSONResponse:
        return _error(503, "base_no_disponible", "La consulta local no está disponible en este momento.")

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, _exc: Exception) -> JSONResponse:
        return _error(500, "error_interno", "Ocurrió un error interno al procesar la solicitud.")

    app.include_router(router)
    app.include_router(finding_actions_router)
    app.include_router(proposal_execution_router)
    app.include_router(email_notifications_router)
    return app


# Import is intentionally inert: no DB connection, migrations, jobs, or external adapters.
app = create_app()
