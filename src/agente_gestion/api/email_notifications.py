"""Protected manager email delivery and same-project HTML preview endpoints."""

from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Path as ApiPath, Request, Response
from pydantic import Field, StrictStr

from agente_gestion.adapters.outbox.hybrid_outbox import (
    HybridNotificationOutboxAdapter,
    OutboxIdempotencyConflict,
)
from agente_gestion.adapters.outbox.smtp import SMTPConfig, validate_email_address
from agente_gestion.adapters.storage.sqlite import SQLiteStorageAdapter
from agente_gestion.api.errors import ApiProblem
from agente_gestion.api.routes import _require_project_capability
from agente_gestion.api.schemas import ApiModel
from agente_gestion.application.manager_notifications import ManagerNotificationService


_ID = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
router = APIRouter(
    prefix="/api/v1",
    responses={status: {"description": "Solicitud rechazada"} for status in (401, 403, 404, 409, 422, 503)},
)


class ManagerEmailRequest(ApiModel):
    recipient_email: StrictStr | None = Field(default=None, max_length=254)
    outbox_delivery_id: StrictStr | None = Field(default=None, pattern=_ID, max_length=128)


class ManagerEmailResponse(ApiModel):
    delivery_id: str
    delivery_channel: Literal[
        "local_outbox_simulated_email", "smtp_email_sent", "smtp_email_failed",
    ]
    recipient_email: str
    html_preview_url: str
    status: Literal["DELIVERED", "FAILED"]


def _components(request: Request):
    settings = request.app.state.settings
    storage = SQLiteStorageAdapter(settings.db_path)
    smtp_config = SMTPConfig.from_settings(settings)
    smtp_factory = getattr(request.app.state, "email_smtp_factory", None)
    outbox = HybridNotificationOutboxAdapter(
        storage,
        settings.outbox_dir,
        smtp_config=smtp_config,
        smtp_factory=smtp_factory,
    )
    service = ManagerNotificationService(
        storage, outbox, settings=settings, smtp_config=smtp_config,
    )
    return settings, storage, outbox, service


def _api_key_material(request: Request, project_id: str, idempotency_key: str) -> str:
    cookie = request.cookies.get(request.app.state.settings.session_cookie_name, "")
    session_fingerprint = sha256(cookie.encode("utf-8")).hexdigest()
    return sha256(
        f"manager-email\0{project_id}\0{session_fingerprint}\0{idempotency_key}".encode("utf-8")
    ).hexdigest()


def _lookup_outbox_message(request: Request, project_id: str, delivery_id: str):
    control_plane = request.app.state.control_plane
    page_size = 10_000
    rows, total, availability = control_plane._outbox_rows(project_id, page=1, page_size=page_size)
    for receipt, message in rows:
        if receipt.delivery_id == delivery_id and receipt.project_id == project_id:
            return receipt, message
    for page in range(2, (total + page_size - 1) // page_size + 1):
        rows, _total, _availability = control_plane._outbox_rows(
            project_id, page=page, page_size=page_size,
        )
        for receipt, message in rows:
            if receipt.delivery_id == delivery_id and receipt.project_id == project_id:
                return receipt, message
    return None


def _response(
    project_id: str, receipt, recipient_email: str,
) -> ManagerEmailResponse:
    if receipt.delivery_channel not in {
        "local_outbox_simulated_email", "smtp_email_sent", "smtp_email_failed",
    }:
        raise ApiProblem(500, "canal_de_correo_invalido", "No se pudo validar el recibo de correo.")
    return ManagerEmailResponse(
        delivery_id=receipt.delivery_id,
        delivery_channel=receipt.delivery_channel,
        recipient_email=recipient_email,
        html_preview_url=(
            f"/api/v1/projects/{project_id}/notifications/email/"
            f"{receipt.delivery_id}/preview"
        ),
        status=receipt.status,
    )


@router.post(
    "/projects/{project_id}/notifications/email",
    response_model=ManagerEmailResponse,
    tags=["Notificaciones"],
)
def send_manager_email(
    body: ManagerEmailRequest,
    request: Request,
    project_id: Annotated[str, ApiPath(pattern=_ID, max_length=128)],
    idempotency_key: Annotated[str, Header(
        alias="Idempotency-Key", min_length=8, max_length=128,
        pattern=r"^[A-Za-z0-9._:-]{8,128}$",
    )],
) -> ManagerEmailResponse:
    session = _require_project_capability(
        request, project_id, "notifications:email_send", mutation=True,
    )
    settings, _storage, outbox, service = _components(request)
    delivery_key = _api_key_material(request, project_id, idempotency_key)
    fingerprint = service.request_fingerprint(
        project_id, body.recipient_email, body.outbox_delivery_id,
    )
    try:
        replay = outbox.lookup_email_attempt(project_id, delivery_key, fingerprint)
    except OutboxIdempotencyConflict:
        raise ApiProblem(409, "idempotencia_conflictiva", "La clave de idempotencia ya se usó con otra solicitud.") from None
    if replay is not None:
        stored = _lookup_outbox_message(request, project_id, replay.delivery_id)
        stored_recipient = stored[1].recipient if stored is not None else None
        replay_recipient = stored_recipient or body.recipient_email or settings.manager_email
        if replay_recipient is None:
            raise ApiProblem(503, "destinatario_recibo_no_disponible", "No se pudo recuperar el destinatario del recibo.")
        try:
            replay_recipient = validate_email_address(replay_recipient)
        except ValueError:
            raise ApiProblem(503, "destinatario_recibo_no_disponible", "No se pudo validar el destinatario del recibo.") from None
        _audit_delivery(request, _storage, session.actor, project_id, replay.delivery_id, idempotency_key, fingerprint)
        return _response(project_id, replay, replay_recipient)

    window_id = "manual-" + delivery_key[:32]
    if body.outbox_delivery_id is not None:
        selected = _lookup_outbox_message(request, project_id, body.outbox_delivery_id)
        if selected is None:
            raise ApiProblem(404, "recibo_outbox_no_encontrado", "No se encontró el recibo solicitado en este proyecto.")
        _source_receipt, source_message = selected
        raw_recipient = body.recipient_email or settings.manager_email or source_message.recipient
        if raw_recipient is None:
            raise ApiProblem(422, "destinatario_requerido", "Indique un correo destinatario configurado para el gestor.")
        try:
            recipient = validate_email_address(raw_recipient)
        except ValueError:
            raise ApiProblem(422, "destinatario_invalido", "La dirección de correo no es válida.") from None
        message = service.message_from_receipt(
            source_message,
            recipient_email=recipient,
            window_id=window_id,
            delivery_key=delivery_key,
            delivery_fingerprint=fingerprint,
        )
    else:
        raw_recipient = body.recipient_email or settings.manager_email
        if raw_recipient is None:
            raise ApiProblem(422, "destinatario_requerido", "Indique un correo destinatario configurado para el gestor.")
        try:
            recipient = validate_email_address(raw_recipient)
        except ValueError:
            raise ApiProblem(422, "destinatario_invalido", "La dirección de correo no es válida.") from None
        try:
            message = service.compose_message(
                project_id,
                recipient_email=recipient,
                window_id=window_id,
                delivery_key=delivery_key,
                delivery_fingerprint=fingerprint,
            )
        except LookupError:
            raise ApiProblem(404, "captura_no_encontrada", "No hay datos de proyecto disponibles para crear el reporte.") from None

    try:
        receipt = outbox.deliver(message)
    except OutboxIdempotencyConflict:
        raise ApiProblem(409, "idempotencia_conflictiva", "La clave de idempotencia ya se usó con otra solicitud.") from None
    _audit_delivery(request, _storage, session.actor, project_id, receipt.delivery_id, idempotency_key, fingerprint)
    return _response(project_id, receipt, recipient)


def _audit_delivery(request, storage, actor_id, project_id, delivery_id, key, fingerprint):
    request.app.state.control_plane._ensure_web_audit(
        storage, project_id=project_id, actor_id=actor_id,
        session_cookie=request.cookies.get(request.app.state.settings.session_cookie_name),
        permission="web:manager_email", resource_id=delivery_id,
        idempotency_key=key, request_fingerprint=fingerprint,
    )


@router.get(
    "/projects/{project_id}/notifications/email/{delivery_id}/preview",
    response_class=Response,
    tags=["Notificaciones"],
)
def preview_manager_email(
    request: Request,
    project_id: Annotated[str, ApiPath(pattern=_ID, max_length=128)],
    delivery_id: Annotated[str, ApiPath(pattern=r"^dlv-[a-f0-9]{12}$", max_length=16)],
) -> Response:
    _require_project_capability(request, project_id, "audit:read", mutation=False)
    selected = _lookup_outbox_message(request, project_id, delivery_id)
    if selected is None:
        raise ApiProblem(404, "vista_previa_no_encontrada", "No se encontró la vista previa solicitada en este proyecto.")
    receipt, message = selected
    if (message.delivery_key is None or receipt.delivery_channel is None
            or receipt.project_id != project_id):
        raise ApiProblem(404, "vista_previa_no_encontrada", "No se encontró la vista previa solicitada en este proyecto.")
    root = Path(request.app.state.settings.outbox_dir).absolute()
    content = request.app.state.control_plane._read_regular_file_beneath(
        root, Path(f"{delivery_id}.html"),
    )
    expected = message.body_html.encode("utf-8") if message.body_html is not None else None
    if content is None or expected is None or content != expected:
        raise ApiProblem(404, "vista_previa_no_disponible", "La vista previa no está disponible o no coincide con el recibo.")
    return Response(
        content=content,
        media_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )
