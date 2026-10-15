"""Versioned REST resources for local sessions and immutable tracking queries."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Path, Query, Request, Response

from agente_gestion.api.control_plane import ControlPlaneRepository
from agente_gestion.api.control_schemas import (
    AuditCollection,
    AuditEventDetail,
    OutboxCollection,
    OutboxDetail,
    ProposalCollection,
    ProposalDetail,
    ProjectConfiguration,
    ReportCollection,
    ReportDecisionRequest,
    ReportDecisionResponse,
    ReportDetail,
    ScheduleCycleConfirmRequest,
    ScheduleCyclePreparation,
    ScheduleCyclePrepareRequest,
    ScheduleView,
    WeeklyCycleConfirmResponse,
    WeeklyCycleJob,
    WeeklyCycleJobCollection,
)
from agente_gestion.api.errors import ApiProblem
from agente_gestion.api.repository import SQLiteQueryRepository
from agente_gestion.api.schemas import (
    AnalysisJobView,
    AnalysisPreparation,
    AuthSessionView,
    CaptureHistoryView,
    ConfirmAnalysisRequest,
    ConfirmAnalysisResponse,
    ErrorEnvelope,
    ExecutiveSummary,
    FindingDetail,
    FindingSummary,
    LoginRequest,
    Page,
    PoliciesSummary,
    PrepareAnalysisRequest,
    ProjectView,
    QualitySummary,
    RunDetail,
    RunStages,
    RunSummary,
    SystemHealth,
    TaskDetailResponse,
    EvidenceSummary,
)
from agente_gestion.api.security import OperatorSession, OperatorSessionStore, sanitize_text
from agente_gestion.api.settings import ApiSettings, IDENTIFIER_PATTERN
from agente_gestion.application.tasks_query import TaskDetailView, TaskPlanSummary
from agente_gestion.domain.task_comparison import TaskSnapshotComparison
from agente_gestion.domain.tasks import TaskState


router = APIRouter(
    prefix="/api/v1",
    responses={status: {"model": ErrorEnvelope} for status in (401, 403, 404, 409, 422, 500, 503)},
)
_ID = IDENTIFIER_PATTERN.pattern


def _session_view(
    store: OperatorSessionStore, session: OperatorSession | None, settings: ApiSettings,
) -> AuthSessionView:
    if session is None:
        return AuthSessionView(
            authenticated=False,
            actor=None,
            csrf_token=None,
            capabilities=[],
            login_available=store.login_available,
        )
    capabilities = ["operator_session"]
    configured_project_ids = {project.id for project in settings.projects}
    executable_project_ids = {
        project.id for project in settings.projects if project.source_mode in {"fixtures", "real"}
    }
    scoped_projects = configured_project_ids.intersection(session.project_ids)
    executable_scoped_projects = executable_project_ids.intersection(session.project_ids)
    if scoped_projects:
        capabilities.extend(["audit:read", "configuration:read"])
    if settings.analysis_enabled and executable_scoped_projects:
        capabilities.extend(["analysis:prepare", "analysis:confirm"])
    if settings.governance_enabled and executable_scoped_projects:
        capabilities.extend([
            "governance:proposal_execute", "quality:decide", "notifications:email_send",
        ])
    if settings.report_decisions_enabled and executable_scoped_projects:
        capabilities.append("reports:decide")
    if (settings.weekly_cycle_enabled and settings.weekly_schedule is not None
            and settings.weekly_schedule.project_id in executable_scoped_projects):
        capabilities.extend(["weekly_cycle:prepare", "weekly_cycle:confirm"])
    return AuthSessionView(
        authenticated=True,
        actor=sanitize_text(
            session.actor,
            secrets_to_redact=(settings.operator_token.get_secret_value(),) if settings.operator_token else (),
            limit=128,
        ),
        csrf_token=session.csrf_token,
        capabilities=capabilities,
        login_available=store.login_available,
    )


def _require_exact_origin(request: Request, settings: ApiSettings) -> None:
    origin = request.headers.get("origin")
    if origin is None or origin not in settings.allowed_origins:
        raise ApiProblem(403, "origen_no_permitido", "El origen de la solicitud no está permitido.")


def _require_session(request: Request) -> OperatorSession:
    store: OperatorSessionStore = request.app.state.sessions
    session = store.get(request.cookies.get(request.app.state.settings.session_cookie_name))
    if session is None:
        raise ApiProblem(401, "sesion_requerida", "Inicie una sesión local de operador para continuar.")
    return session


def _require_project_capability(
    request: Request, project_id: str, capability: str, *, mutation: bool,
) -> OperatorSession:
    settings: ApiSettings = request.app.state.settings
    session = _require_session(request)
    project = next((item for item in settings.projects if item.id == project_id), None)
    if project is None:
        raise ApiProblem(404, "proyecto_no_configurado", "El proyecto no está configurado para esta operación.")
    if capability != "audit:read" and project.source_mode not in {"fixtures", "real"}:
        raise ApiProblem(404, "proyecto_no_configurado", "El proyecto no está configurado para esta operación.")
    if project_id not in session.project_ids:
        raise ApiProblem(403, "proyecto_fuera_de_sesion", "La sesión no tiene alcance para este proyecto.")
    governed_capability = (
        capability.startswith("governance:")
        or capability in {"quality:decide", "notifications:email_send"}
    )
    if governed_capability and not settings.governance_enabled:
        raise ApiProblem(503, "gobernanza_no_habilitada", "El servidor no habilitó las operaciones de gobernanza.")
    if capability == "reports:decide" and not settings.report_decisions_enabled:
        raise ApiProblem(503, "decision_informe_no_habilitada", "El servidor no habilitó decisiones humanas de informes.")
    if capability.startswith("weekly_cycle:") and not settings.weekly_cycle_enabled:
        raise ApiProblem(503, "ciclo_semanal_no_habilitado", "El servidor no habilitó el ciclo semanal manual.")
    view = _session_view(request.app.state.sessions, session, settings)
    if capability not in view.capabilities:
        raise ApiProblem(403, "capacidad_no_autorizada", "La sesión no cuenta con la capacidad requerida.")
    if mutation:
        _require_exact_origin(request, settings)
        if not request.app.state.sessions.csrf_is_valid(session, request.headers.get("x-csrf-token")):
            raise ApiProblem(403, "csrf_invalido", "La validación de seguridad de la solicitud no es válida.")
    return session


def _require_analysis_access(
    request: Request, project_id: str, *, mutation: bool,
) -> OperatorSession:
    settings: ApiSettings = request.app.state.settings
    if not settings.analysis_enabled:
        raise ApiProblem(503, "analisis_no_habilitado", "El servidor no habilitó la ejecución de análisis.")
    session = _require_session(request)
    project = next((item for item in settings.projects if item.id == project_id), None)
    if project is None or project.source_mode not in {"fixtures", "real"}:
        raise ApiProblem(404, "proyecto_no_ejecutable", "El proyecto no está configurado para ejecutar análisis.")
    if project_id not in session.project_ids:
        raise ApiProblem(403, "proyecto_fuera_de_sesion", "La sesión no tiene alcance para este proyecto.")
    if mutation:
        _require_exact_origin(request, settings)
        store: OperatorSessionStore = request.app.state.sessions
        if not store.csrf_is_valid(session, request.headers.get("x-csrf-token")):
            raise ApiProblem(403, "csrf_invalido", "La validación de seguridad de la solicitud no es válida.")
    return session


@router.get("/system/health", response_model=SystemHealth, tags=["Sistema"])
def system_health() -> SystemHealth:
    return SystemHealth(status="ok", version="0.1.0", external_writes_enabled=False)


@router.get("/auth/session", response_model=AuthSessionView, tags=["Sesión"])
def auth_session(request: Request) -> AuthSessionView:
    store: OperatorSessionStore = request.app.state.sessions
    cookie = request.cookies.get(request.app.state.settings.session_cookie_name)
    return _session_view(store, store.get(cookie), request.app.state.settings)


@router.post("/auth/login", response_model=AuthSessionView, tags=["Sesión"])
def auth_login(body: LoginRequest, request: Request, response: Response) -> AuthSessionView:
    settings: ApiSettings = request.app.state.settings
    _require_exact_origin(request, settings)
    store: OperatorSessionStore = request.app.state.sessions
    if not store.login_available:
        raise ApiProblem(503, "inicio_no_configurado", "El acceso local de operador no está configurado.")
    if not store.verify_operator_token(body.token):
        raise ApiProblem(401, "credenciales_invalidas", "No se pudo verificar la credencial de operador.")
    cookie_value, session = store.issue()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=cookie_value,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/api/v1",
    )
    return _session_view(store, session, settings)


@router.post("/auth/logout", status_code=204, response_class=Response, tags=["Sesión"])
def auth_logout(request: Request, response: Response) -> Response:
    settings: ApiSettings = request.app.state.settings
    _require_exact_origin(request, settings)
    session = _require_session(request)
    store: OperatorSessionStore = request.app.state.sessions
    if not store.csrf_is_valid(session, request.headers.get("x-csrf-token")):
        raise ApiProblem(403, "csrf_invalido", "La validación de seguridad de la solicitud no es válida.")
    store.revoke(request.cookies.get(settings.session_cookie_name))
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/api/v1",
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
    )
    response.status_code = 204
    return response


@router.get("/projects", response_model=Page[ProjectView], tags=["Proyectos"])
def list_projects(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[ProjectView]:
    items, total = _repository(request).project_views(page, page_size)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/projects/{project_id}", response_model=ProjectView, tags=["Proyectos"])
def get_project(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
) -> ProjectView:
    try:
        return _repository(request).require_project(project_id)
    except LookupError as exc:
        _raise_lookup(exc)


@router.get("/projects/{project_id}/summary", response_model=TaskPlanSummary, tags=["Proyectos"])
def project_summary(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    capture_id: Annotated[str | None, Query(pattern=_ID, max_length=128)] = None,
) -> TaskPlanSummary:
    repository = _repository(request)
    try:
        capture = repository.selected_capture(project_id, capture_id)
        return repository.summary(capture)
    except LookupError as exc:
        _raise_lookup(exc)


@router.get("/projects/{project_id}/tasks", response_model=Page[TaskDetailView], tags=["Tareas"])
def list_tasks(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    capture_id: Annotated[str | None, Query(pattern=_ID, max_length=128)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    state: TaskState | None = None,
    assignee_id: Annotated[str | None, Query(max_length=128)] = None,
    list_name: Annotated[str | None, Query(max_length=160)] = None,
    critical_only: bool = False,
    overdue_only: bool = False,
    due_soon_only: bool = False,
    sort: Literal["due_date", "title"] = "due_date",
    direction: Literal["asc", "desc"] = "asc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[TaskDetailView]:
    try:
        items, total = _repository(request).tasks(
            project_id,
            capture_id=capture_id,
            q=q,
            state=state,
            assignee_id=assignee_id,
            list_name=list_name,
            critical_only=critical_only,
            overdue_only=overdue_only,
            due_soon_only=due_soon_only,
            sort=sort,
            direction=direction,
            page=page,
            page_size=page_size,
        )
    except LookupError as exc:
        _raise_lookup(exc)
    except ValueError as exc:
        if str(exc) == "incompatible_due_filters":
            raise ApiProblem(422, "filtros_incompatibles", "Seleccione solo un filtro de vencimiento.") from None
        raise
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/projects/{project_id}/tasks/{task_id}", response_model=TaskDetailResponse, tags=["Tareas"])
def get_task(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    task_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    capture_id: Annotated[str | None, Query(pattern=_ID, max_length=128)] = None,
) -> TaskDetailResponse:
    try:
        return _repository(request).task_detail(project_id, task_id, capture_id)
    except LookupError as exc:
        _raise_lookup(exc)


@router.get("/projects/{project_id}/history", response_model=Page[CaptureHistoryView], tags=["Historial"])
def project_history(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[CaptureHistoryView]:
    repository = _repository(request)
    try:
        captures = repository.captures_for_project(project_id)
    except LookupError as exc:
        _raise_lookup(exc)
    items_all = [repository.history_item(capture) for capture in captures]
    total = len(items_all)
    selected = items_all[(page - 1) * page_size:page * page_size]
    return Page(items=selected, total=total, page=page, page_size=page_size)


@router.get("/projects/{project_id}/compare", response_model=TaskSnapshotComparison, tags=["Historial"])
def compare_project_captures(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    before: Annotated[str, Query(pattern=_ID, max_length=128)],
    after: Annotated[str, Query(pattern=_ID, max_length=128)],
) -> TaskSnapshotComparison:
    try:
        return _repository(request).compare(project_id, before, after)
    except LookupError as exc:
        _raise_lookup(exc)


@router.get("/projects/{project_id}/executive-summary", response_model=ExecutiveSummary, tags=["Ejecutivo"])
def project_executive_summary(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
) -> ExecutiveSummary:
    try:
        return _repository(request).executive_summary(project_id)
    except LookupError as exc:
        _raise_lookup(exc)


@router.get("/projects/{project_id}/quality", response_model=QualitySummary, tags=["Calidad"])
def project_quality(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
) -> QualitySummary:
    try:
        return _repository(request).quality_summary(project_id)
    except LookupError as exc:
        _raise_lookup(exc)


@router.get("/projects/{project_id}/policies", response_model=PoliciesSummary, tags=["Calidad"])
def project_policies(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
) -> PoliciesSummary:
    try:
        return _repository(request).policies_summary(project_id)
    except LookupError as exc:
        _raise_lookup(exc)


@router.get("/runs", response_model=Page[RunSummary], tags=["Ejecuciones"])
def list_runs(
    request: Request,
    project_id: Annotated[str, Query(pattern=_ID, max_length=128)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[RunSummary]:
    try:
        items, total = _repository(request).runs(project_id, page, page_size)
    except LookupError as exc:
        _raise_lookup(exc)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/runs/{run_id}", response_model=RunDetail, tags=["Ejecuciones"])
def get_run(
    request: Request,
    run_id: Annotated[str, Path(pattern=_ID, max_length=128)],
) -> RunDetail:
    try:
        return _repository(request).run_detail(run_id)
    except LookupError as exc:
        _raise_lookup(exc)


@router.get("/runs/{run_id}/stages", response_model=RunStages, tags=["Ejecuciones"])
def get_run_stages(
    request: Request,
    run_id: Annotated[str, Path(pattern=_ID, max_length=128)],
) -> RunStages:
    try:
        return _repository(request).run_stages(run_id)
    except LookupError as exc:
        _raise_lookup(exc)


@router.get("/runs/{run_id}/findings", response_model=Page[FindingSummary], tags=["Calidad"])
def list_run_findings(
    request: Request,
    run_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[FindingSummary]:
    try:
        items, total = _repository(request).findings(run_id, page, page_size)
    except LookupError as exc:
        _raise_lookup(exc)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/runs/{run_id}/findings/{finding_id}", response_model=FindingDetail, tags=["Calidad"])
def get_run_finding(
    request: Request,
    run_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    finding_id: Annotated[str, Path(pattern=_ID, max_length=128)],
) -> FindingDetail:
    try:
        return _repository(request).finding_detail(run_id, finding_id)
    except LookupError as exc:
        _raise_lookup(exc)


@router.get("/runs/{run_id}/evidence", response_model=Page[EvidenceSummary], tags=["Ejecuciones"])
def list_run_evidence(
    request: Request,
    run_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[EvidenceSummary]:
    try:
        items, total = _repository(request).evidence_page(run_id, page, page_size)
    except LookupError as exc:
        _raise_lookup(exc)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post(
    "/projects/{project_id}/analysis/prepare",
    response_model=AnalysisPreparation,
    tags=["Análisis"],
)
def prepare_project_analysis(
    request: Request,
    body: PrepareAnalysisRequest,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
) -> AnalysisPreparation:
    session = _require_analysis_access(request, project_id, mutation=True)
    service = request.app.state.analysis_jobs
    return service.prepare(
        project_id,
        request.cookies.get(request.app.state.settings.session_cookie_name),
    )


@router.post(
    "/projects/{project_id}/analysis/confirm",
    response_model=ConfirmAnalysisResponse,
    status_code=202,
    tags=["Análisis"],
)
def confirm_project_analysis(
    request: Request,
    body: ConfirmAnalysisRequest,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    idempotency_key: Annotated[str, Header(
        alias="Idempotency-Key", min_length=8, max_length=128,
        pattern=r"^[A-Za-z0-9._:-]{8,128}$",
    )],
) -> ConfirmAnalysisResponse:
    _require_analysis_access(request, project_id, mutation=True)
    service = request.app.state.analysis_jobs
    job, reused = service.confirm(
        project_id=project_id,
        preparation_id=body.preparation_id,
        configuration_fingerprint=body.configuration_fingerprint,
        remote_consent=body.remote_consent,
        session_cookie=request.cookies.get(request.app.state.settings.session_cookie_name),
        idempotency_key=idempotency_key,
    )
    return ConfirmAnalysisResponse(
        job=job,
        reused=reused,
        poll_url=f"/api/v1/analysis/jobs/{job.job_id}",
        poll_after_ms=1000,
    )


@router.get("/analysis/jobs", response_model=Page[AnalysisJobView], tags=["Análisis"])
def list_analysis_jobs(
    request: Request,
    project_id: Annotated[str, Query(pattern=_ID, max_length=128)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[AnalysisJobView]:
    _require_analysis_access(request, project_id, mutation=False)
    items, total = request.app.state.analysis_jobs.list_jobs(project_id, page, page_size)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/analysis/jobs/{job_id}", response_model=AnalysisJobView, tags=["Análisis"])
def get_analysis_job(
    request: Request,
    job_id: Annotated[str, Path(pattern=r"^webjob-[a-f0-9]{24}$", max_length=40)],
) -> AnalysisJobView:
    _require_session(request)
    try:
        job = request.app.state.analysis_jobs.get_job(job_id)
    except LookupError:
        raise ApiProblem(404, "trabajo_no_encontrado", "No se encontró el trabajo solicitado.") from None
    try:
        _require_analysis_access(request, job.project_id, mutation=False)
    except ApiProblem as exc:
        if exc.status_code in {403, 404}:
            raise ApiProblem(404, "trabajo_no_encontrado", "No se encontró el trabajo solicitado.") from None
        raise
    return job


@router.get("/projects/{project_id}/proposals", response_model=ProposalCollection, tags=["Gobernanza"])
def list_project_proposals(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    review_status: Literal["pending", "approved_offline", "rejected"] | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> ProposalCollection:
    try:
        _repository(request).require_project(project_id)
        items, total, availability = request.app.state.control_plane.proposals(
            project_id, page, page_size, review_status,
        )
    except LookupError as exc:
        _raise_lookup(exc)
    return ProposalCollection(
        availability=availability, items=items, total=total, page=page, page_size=page_size,
    )


@router.get("/projects/{project_id}/proposals/{proposal_id}", response_model=ProposalDetail, tags=["Gobernanza"])
def get_project_proposal(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    proposal_id: Annotated[str, Path(pattern=_ID, max_length=128)],
) -> ProposalDetail:
    try:
        _repository(request).require_project(project_id)
        return request.app.state.control_plane.proposal_detail(project_id, proposal_id)
    except LookupError as exc:
        _raise_lookup(exc)


@router.get("/projects/{project_id}/reports", response_model=ReportCollection, tags=["Informes"])
def list_project_reports(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    decision: Literal["pending", "approved", "rejected", "changes_requested", "postponed"] | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> ReportCollection:
    try:
        _repository(request).require_project(project_id)
        items, total, availability = request.app.state.control_plane.reports(
            project_id, page, page_size, decision,
        )
    except LookupError as exc:
        _raise_lookup(exc)
    return ReportCollection(
        availability=availability, items=items, total=total, page=page, page_size=page_size,
    )


@router.get("/projects/{project_id}/reports/{report_id}", response_model=ReportDetail, tags=["Informes"])
def get_project_report(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    report_id: Annotated[str, Path(pattern=_ID, max_length=128)],
) -> ReportDetail:
    try:
        _repository(request).require_project(project_id)
        return request.app.state.control_plane.report_detail(project_id, report_id)
    except LookupError as exc:
        _raise_lookup(exc)


@router.get("/projects/{project_id}/reports/{report_id}/download", tags=["Informes"])
def download_project_report(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    report_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    format: Literal["markdown", "json"] = "markdown",
) -> Response:
    try:
        _repository(request).require_project(project_id)
        media_type, filename, content = request.app.state.control_plane.report_download(
            project_id, report_id, format,
        )
    except LookupError as exc:
        _raise_lookup(exc)
    return Response(content=content, media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
    })


@router.post(
    "/projects/{project_id}/reports/{report_id}/decision",
    response_model=ReportDecisionResponse,
    tags=["Informes"],
)
def decide_project_report(
    request: Request,
    body: ReportDecisionRequest,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    report_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    idempotency_key: Annotated[str, Header(
        alias="Idempotency-Key", min_length=8, max_length=128,
        pattern=r"^[A-Za-z0-9._:-]{8,128}$",
    )],
) -> ReportDecisionResponse:
    session = _require_project_capability(request, project_id, "reports:decide", mutation=True)
    cookie = request.cookies.get(request.app.state.settings.session_cookie_name)
    try:
        report, decision_event, reused, audit_event_id = request.app.state.control_plane.decide_report(
            project_id, report_id, expected_version=body.expected_report_version,
            expected_human_decision=body.expected_human_decision, decision=body.decision,
            reason=body.reason, actor_id=session.actor, session_cookie=cookie,
            idempotency_key=idempotency_key,
        )
    except LookupError as exc:
        _raise_lookup(exc)
    return ReportDecisionResponse(
        report=report, decision_event=decision_event, reused=reused, audit_event_id=audit_event_id,
    )


@router.get("/projects/{project_id}/audit", response_model=AuditCollection, tags=["Auditoría"])
def list_project_audit(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    event_type: Annotated[str | None, Query(max_length=80)] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> AuditCollection:
    _require_project_capability(request, project_id, "audit:read", mutation=False)
    try:
        _repository(request).require_project(project_id)
    except LookupError as exc:
        _raise_lookup(exc)
    if event_type is not None and event_type not in {
        "analysis_run", "report_decision", "proposal_decision",
        "proposal_execution", "outbox_delivery", "security",
    }:
        raise ApiProblem(422, "tipo_de_evento_invalido", "El filtro de evento de auditoría no es válido.")
    if since is not None and until is not None and since > until:
        raise ApiProblem(422, "rango_temporal_invalido", "El intervalo de auditoría no es válido.")
    items, total, availability = request.app.state.control_plane.audit_page(
        project_id, page=page, page_size=page_size, event_type=event_type, since=since, until=until,
    )
    return AuditCollection(availability=availability, items=items, total=total, page=page, page_size=page_size)


@router.get("/projects/{project_id}/audit/{event_id}", response_model=AuditEventDetail, tags=["Auditoría"])
def get_project_audit_event(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    event_id: Annotated[str, Path(pattern=_ID, max_length=128)],
) -> AuditEventDetail:
    _require_project_capability(request, project_id, "audit:read", mutation=False)
    try:
        _repository(request).require_project(project_id)
        return request.app.state.control_plane.audit_detail(project_id, event_id)
    except LookupError as exc:
        _raise_lookup(exc)


@router.get("/projects/{project_id}/schedule", response_model=ScheduleView, tags=["Programación"])
def get_project_schedule(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
) -> ScheduleView:
    try:
        _repository(request).require_project(project_id)
    except LookupError as exc:
        _raise_lookup(exc)
    return request.app.state.weekly_cycles.schedule_view(project_id)


@router.get("/projects/{project_id}/schedule/outbox", response_model=OutboxCollection, tags=["Programación"])
def list_project_outbox(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> OutboxCollection:
    try:
        _repository(request).require_project(project_id)
        items, total, availability = request.app.state.control_plane.outbox(project_id, page, page_size)
    except LookupError as exc:
        _raise_lookup(exc)
    return OutboxCollection(
        availability=availability, items=items, total=total, page=page, page_size=page_size,
    )


@router.get("/projects/{project_id}/schedule/outbox/{delivery_id}", response_model=OutboxDetail, tags=["Programación"])
def get_project_outbox_receipt(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    delivery_id: Annotated[str, Path(pattern=_ID, max_length=128)],
) -> OutboxDetail:
    try:
        _repository(request).require_project(project_id)
        return request.app.state.control_plane.outbox_detail(project_id, delivery_id)
    except LookupError as exc:
        _raise_lookup(exc)


@router.get("/projects/{project_id}/schedule/outbox/{delivery_id}/download", tags=["Programación"])
def download_project_outbox(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    delivery_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    format: Literal["markdown", "json"] = "markdown",
) -> Response:
    try:
        _repository(request).require_project(project_id)
        media_type, filename, content = request.app.state.control_plane.outbox_download(
            project_id, delivery_id, format,
        )
    except LookupError as exc:
        _raise_lookup(exc)
    return Response(content=content, media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
    })


@router.post(
    "/projects/{project_id}/schedule/manual-cycle/prepare",
    response_model=ScheduleCyclePreparation,
    tags=["Programación"],
)
def prepare_project_weekly_cycle(
    request: Request,
    body: ScheduleCyclePrepareRequest,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
) -> ScheduleCyclePreparation:
    _require_project_capability(request, project_id, "weekly_cycle:prepare", mutation=True)
    return request.app.state.weekly_cycles.prepare(
        project_id, request.cookies.get(request.app.state.settings.session_cookie_name),
    )


@router.post(
    "/projects/{project_id}/schedule/manual-cycle/confirm",
    response_model=WeeklyCycleConfirmResponse,
    status_code=202,
    tags=["Programación"],
)
def confirm_project_weekly_cycle(
    request: Request,
    body: ScheduleCycleConfirmRequest,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    idempotency_key: Annotated[str, Header(
        alias="Idempotency-Key", min_length=8, max_length=128,
        pattern=r"^[A-Za-z0-9._:-]{8,128}$",
    )],
) -> WeeklyCycleConfirmResponse:
    session = _require_project_capability(request, project_id, "weekly_cycle:confirm", mutation=True)
    job, reused = request.app.state.weekly_cycles.confirm(
        project_id, body.preparation_id, body.configuration_fingerprint,
        request.cookies.get(request.app.state.settings.session_cookie_name), session.actor,
        idempotency_key,
    )
    return WeeklyCycleConfirmResponse(
        job=job, reused=reused,
        poll_url=f"/api/v1/projects/{project_id}/schedule/manual-cycle/jobs/{job.job_id}",
        poll_after_ms=1000,
    )


@router.get(
    "/projects/{project_id}/schedule/manual-cycle/jobs",
    response_model=WeeklyCycleJobCollection,
    tags=["Programación"],
)
def list_project_weekly_cycle_jobs(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> WeeklyCycleJobCollection:
    _require_project_capability(request, project_id, "weekly_cycle:confirm", mutation=False)
    items, total, availability = request.app.state.weekly_cycles.list_jobs(project_id, page, page_size)
    return WeeklyCycleJobCollection(
        availability=availability, items=items, total=total, page=page, page_size=page_size,
    )


@router.get(
    "/projects/{project_id}/schedule/manual-cycle/jobs/{job_id}",
    response_model=WeeklyCycleJob,
    tags=["Programación"],
)
def get_project_weekly_cycle_job(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
    job_id: Annotated[str, Path(pattern=r"^webcycle-[a-f0-9]{24}$", max_length=40)],
) -> WeeklyCycleJob:
    _require_project_capability(request, project_id, "weekly_cycle:confirm", mutation=False)
    try:
        job = request.app.state.weekly_cycles.get_job(job_id)
    except LookupError:
        raise ApiProblem(404, "ciclo_semanal_no_encontrado", "No se encontró el ciclo solicitado.") from None
    if job.project_id != project_id:
        raise ApiProblem(404, "ciclo_semanal_no_encontrado", "No se encontró el ciclo solicitado.")
    return job


@router.get("/projects/{project_id}/configuration", response_model=ProjectConfiguration, tags=["Configuración"])
def get_project_configuration(
    request: Request,
    project_id: Annotated[str, Path(pattern=_ID, max_length=128)],
) -> ProjectConfiguration:
    session = request.app.state.sessions.get(
        request.cookies.get(request.app.state.settings.session_cookie_name),
    )
    capabilities = []
    if session is not None and project_id in session.project_ids:
        capabilities = _session_view(request.app.state.sessions, session, request.app.state.settings).capabilities
    try:
        return request.app.state.control_plane.configuration(project_id, capabilities)
    except LookupError as exc:
        _raise_lookup(exc)


def _repository(request: Request) -> SQLiteQueryRepository:
    return request.app.state.repository


def _raise_lookup(exc: LookupError) -> None:
    reason = exc.args[0] if exc.args else "not_found"
    if reason == "project_not_found":
        raise ApiProblem(404, "proyecto_no_encontrado", "No se encontró el proyecto solicitado.") from None
    if reason == "task_not_found":
        raise ApiProblem(404, "tarea_no_encontrada", "No se encontró la tarea en la captura solicitada.") from None
    if reason == "comparison_unavailable":
        raise ApiProblem(409, "comparacion_no_disponible", "Las capturas no se pueden comparar con la evidencia disponible.") from None
    if reason == "run_not_found":
        raise ApiProblem(404, "ejecucion_no_encontrada", "No se encontró la ejecución solicitada.") from None
    if reason == "finding_not_found":
        raise ApiProblem(404, "hallazgo_no_encontrado", "No se encontró el hallazgo solicitado en esa ejecución.") from None
    if reason == "proposal_not_found":
        raise ApiProblem(404, "propuesta_no_encontrada", "No se encontró la propuesta solicitada en este proyecto.") from None
    if reason == "report_not_found":
        raise ApiProblem(404, "informe_no_encontrado", "No se encontró el informe solicitado en este proyecto.") from None
    if reason == "report_markdown_unavailable":
        raise ApiProblem(404, "informe_markdown_no_disponible", "El informe no contiene un Markdown almacenado.") from None
    if reason == "audit_event_not_found":
        raise ApiProblem(404, "evento_auditoria_no_encontrado", "No se encontró el evento de auditoría solicitado.") from None
    if reason == "outbox_not_found":
        raise ApiProblem(404, "recibo_outbox_no_encontrado", "No se encontró el recibo de outbox solicitado.") from None
    if reason == "outbox_artifact_unavailable":
        raise ApiProblem(404, "artefacto_outbox_no_disponible", "El artefacto no está disponible o no coincide con el recibo almacenado.") from None
    raise ApiProblem(404, "captura_no_encontrada", "No se encontró la captura solicitada.") from None
