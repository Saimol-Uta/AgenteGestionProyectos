import { useRef, useState } from "react";
import { CalendarClock, ChevronLeft, ChevronRight, Download, FileJson, FileText, LockKeyhole, ShieldCheck } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useWorkspace } from "../../app/WorkspaceContext";
import { api } from "../../api/client";
import { queryKeys, useProject, useProjects, useOutbox, useOutboxDetail, useSchedule, useWeeklyCycleJob, useWeeklyCycleJobs } from "../../api/queries";
import type { ScheduleCyclePreparation, WeeklyCycleJob, WeeklyCycleOutcome } from "../../api/types";
import { formatDate, formatPercentage } from "../../api/formatters";
import { ConfirmActionDialog } from "../../components/ConfirmActionDialog";
import { EmptyState, ErrorState, LoadingState, Pill } from "../../components/States";
import { SafeMarkdown } from "../../components/SafeMarkdown";
import { saveDownloadedFile } from "../../components/download";
import { useToast } from "../../components/ToastProvider";
import { useSession } from "../auth/SessionProvider";

const pageSize = 25;
type DownloadFormat = "markdown" | "json";
const terminalStatuses: WeeklyCycleJob["status"][] = ["completed", "failed", "interrupted"];

function jobStatusLabel(status: WeeklyCycleJob["status"]) {
  const labels = { queued: "Trabajo en cola", running: "Trabajo en ejecución local", completed: "Trabajo completado", failed: "Trabajo fallido", interrupted: "Trabajo interrumpido" };
  return labels[status];
}

function outcomeLabel(outcome: WeeklyCycleOutcome) {
  const labels: Record<WeeklyCycleOutcome, string> = {
    EXECUTED: "Ciclo registrado como ejecutado", SKIPPED_NOT_DUE: "Omitido: la ventana aún no está vencida",
    SKIPPED_ALREADY_DISPATCHED: "Omitido: la ventana ya fue despachada", FAILED: "Fallido", unknown: "Desconocido",
  };
  return labels[outcome];
}

function outcomeTone(outcome: WeeklyCycleOutcome): "neutral" | "positive" | "warning" | "danger" | "unknown" {
  if (outcome === "EXECUTED") return "positive";
  if (outcome.startsWith("SKIPPED")) return "warning";
  if (outcome === "FAILED") return "danger";
  if (outcome === "unknown") return "unknown";
  return "neutral";
}

function scheduleValue(value: boolean | null): string {
  return value === null ? "Desconocido" : value ? "Sí" : "No";
}

function scheduleStatusLabel(value: ScheduleCyclePreparation["preflight"]["schedule_status"]) {
  const labels = { configured: "Configurada", incomplete: "Incompleta", invalid: "Inválida", disabled: "Deshabilitada" };
  return labels[value];
}

export function CalendarPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { selectedProjectId } = useWorkspace();
  const projectsQuery = useProjects();
  const listedProject = projectsQuery.data?.items.find((item) => item.id === selectedProjectId);
  const selectedProjectQuery = useProject(selectedProjectId, Boolean(selectedProjectId && !listedProject));
  const project = listedProject ?? selectedProjectQuery.data ?? (!selectedProjectId ? projectsQuery.data?.items[0] ?? null : null);
  const { session } = useSession();
  const { notify } = useToast();
  const queryClient = useQueryClient();
  const authenticated = Boolean(session?.authenticated && session.csrf_token);
  const canPrepare = Boolean(authenticated && session?.capabilities.includes("weekly_cycle:prepare"));
  const canConfirm = Boolean(authenticated && session?.capabilities.includes("weekly_cycle:confirm"));
  const canReadCycleJobs = Boolean(session?.authenticated && session.capabilities.includes("weekly_cycle:confirm"));
  const scheduleQuery = useSchedule(project?.id ?? null, Boolean(session?.authenticated));
  const page = Math.max(1, Number(searchParams.get("page") ?? 1) || 1);
  const deliveryId = searchParams.get("delivery_id");
  const outboxQuery = useOutbox(project?.id ?? null, page, pageSize, Boolean(session?.authenticated));
  const detailQuery = useOutboxDetail(project?.id ?? null, deliveryId, Boolean(session?.authenticated));
  const jobsQuery = useWeeklyCycleJobs(project?.id ?? null, 1, 25, canReadCycleJobs);
  const [preparation, setPreparation] = useState<ScheduleCyclePreparation | null>(null);
  const [prepareDialogOpen, setPrepareDialogOpen] = useState(false);
  const [cycleConfirmationOpen, setCycleConfirmationOpen] = useState(false);
  const [localCycleConsent, setLocalCycleConsent] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [downloadBusy, setDownloadBusy] = useState<DownloadFormat | null>(null);
  const retryKeys = useRef(new Map<string, string>());
  const schedule = scheduleQuery.data;
  const canStartPreparation = Boolean(canPrepare && schedule?.availability === "configured" && schedule.enabled && schedule.can_run_manually);
  const canConfirmPrepared = Boolean(canConfirm && preparation?.eligible && preparation.preflight.can_run && preparation.capabilities.includes("weekly_cycle:confirm"));
  const activeJobQuery = useWeeklyCycleJob(project?.id ?? null, activeJobId, canReadCycleJobs);
  const activeJob = activeJobQuery.data;

  function selectDelivery(id: string | null) {
    const next = new URLSearchParams(searchParams);
    if (id) next.set("delivery_id", id);
    else next.delete("delivery_id");
    setSearchParams(next);
  }

  function deliveryHref(id: string) {
    const next = new URLSearchParams(searchParams);
    next.set("delivery_id", id);
    return { pathname: "/calendar", search: `?${next.toString()}` };
  }

  function changePage(nextPage: number) {
    const next = new URLSearchParams(searchParams);
    if (nextPage > 1) next.set("page", String(nextPage));
    else next.delete("page");
    setSearchParams(next);
  }

  async function prepareCycle() {
    if (!project || !session?.csrf_token || !canStartPreparation) return;
    setPreparing(true);
    try {
      const response = await api.prepareWeeklyCycle(project.id, session.csrf_token);
      setPreparation(response);
      setLocalCycleConsent(false);
      setPrepareDialogOpen(false);
      setCycleConfirmationOpen(false);
      notify(response.eligible ? "Preparación completada; revisa la preflight antes de confirmar." : "La preflight no habilita un ciclo manual.", response.eligible ? "success" : "info");
    } catch (error) {
      notify(error instanceof Error ? error.message : "No se pudo preparar el ciclo.", "error");
    } finally {
      setPreparing(false);
    }
  }

  async function confirmCycle() {
    if (!project || !session?.csrf_token || !preparation || !canConfirmPrepared) return;
    const retryIdentity = `${preparation.preparation_id}:${preparation.configuration_fingerprint}`;
    const idempotencyKey = retryKeys.current.get(retryIdentity) ?? crypto.randomUUID();
    retryKeys.current.set(retryIdentity, idempotencyKey);
    setConfirming(true);
    try {
      const response = await api.confirmWeeklyCycle(project.id, {
        preparation_id: preparation.preparation_id,
        configuration_fingerprint: preparation.configuration_fingerprint,
        confirm_cycle: true,
        remote_consent: false,
      }, session.csrf_token, idempotencyKey);
      retryKeys.current.delete(retryIdentity);
      setActiveJobId(response.job.job_id);
      setPreparation(null);
      setCycleConfirmationOpen(false);
      queryClient.setQueryData(queryKeys.weeklyCycleJob(project.id, response.job.job_id), response.job);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.weeklyCycleJobs(project.id, 1, 25) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.schedule(project.id) }),
        queryClient.invalidateQueries({ queryKey: ["schedule-outbox", project.id] }),
        queryClient.invalidateQueries({ queryKey: ["audit", project.id] }),
      ]);
      notify("Ciclo manual local aceptado; sigue el estado persistido por la API.", "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "No se pudo confirmar el ciclo.", "error");
    } finally {
      setConfirming(false);
    }
  }

  async function downloadOutbox(format: DownloadFormat) {
    if (!project || !detailQuery.data?.receipt.artifact_available) return;
    setDownloadBusy(format);
    try {
      saveDownloadedFile(await api.downloadOutbox(project.id, detailQuery.data.receipt.delivery_id, format));
      notify(`Descarga ${format === "markdown" ? "Markdown" : "JSON"} solicitada para el recibo almacenado.`, "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "El artefacto no pudo descargarse.", "error");
    } finally {
      setDownloadBusy(null);
    }
  }

  const pages = Math.max(1, Math.ceil((outboxQuery.data?.total ?? 0) / pageSize));
  const canPollJob = Boolean(canReadCycleJobs && activeJob && !terminalStatuses.includes(activeJob.status));

  return (
    <section className="governance-page calendar-page">
      <header className="page-heading-row"><div><p className="eyebrow">PROGRAMACIÓN Y RECIBOS PERSISTIDOS</p><h1>Calendario y reportes semanales</h1><p className="page-subtitle">Consulta la ventana semanal y la outbox. La programación configurada no demuestra que exista un trigger automático; el ciclo manual requiere preparación, confirmación explícita e idempotencia.</p></div><Pill tone="neutral">Trigger automático no verificado</Pill></header>
      {!session?.authenticated && <div className="inline-warning"><LockKeyhole aria-hidden="true" size={16} /> Inicia una sesión local para consultar la programación y la outbox protegidas.</div>}
      {session?.authenticated && scheduleQuery.isPending && <LoadingState label="Consultando programación del backend…" />}
      {session?.authenticated && scheduleQuery.isError && <ErrorState title="No se pudo consultar la programación" message={scheduleQuery.error.message} onRetry={() => void scheduleQuery.refetch()} />}
      {schedule && <section className="schedule-overview panel" aria-labelledby="schedule-overview-title">
        <div className="section-heading"><div><p className="eyebrow">CONFIGURACIÓN SERVIDOR</p><h2 id="schedule-overview-title">Ventana semanal</h2></div><Pill tone={schedule.availability === "configured" ? "positive" : "unknown"}>{schedule.availability === "configured" ? "Configuración presente" : "Disponibilidad desconocida"}</Pill></div>
        {schedule.schedule ? <>
          <div className="schedule-meta-grid"><div><span>Proyecto programado</span><strong>{schedule.project_id}</strong></div><div><span>Día semanal (valor del backend)</span><strong>{schedule.schedule.weekday}</strong></div><div><span>Hora local</span><strong>{schedule.schedule.local_time}</strong></div><div><span>Zona horaria</span><strong>{schedule.schedule.timezone}</strong></div><div><span>Regla de ventana</span><strong>{schedule.schedule.window_rule}</strong></div><div><span>Habilitado por servidor</span><strong>{schedule.enabled ? "Sí" : "No"}</strong></div><div><span>¿Vence ahora?</span><strong>{scheduleValue(schedule.is_due)}</strong></div><div><span>¿Ya despachado?</span><strong>{scheduleValue(schedule.is_dispatched)}</strong></div><div><span>Historial outbox</span><strong>{schedule.outbox_history === "available" ? "Disponible" : "Desconocido"}</strong></div><div><span>Disparo automático</span><strong>No verificado</strong></div></div><p className="schedule-trigger-note">Disparador automático: no verificado</p>
          {schedule.planned_window && <div className="schedule-window-card"><h3>Ventana planificada del servidor</h3><div><span>Identificador</span><code>{schedule.planned_window.window_id}</code></div><div><span>Inicio local / UTC</span><strong>{formatDate(schedule.planned_window.window_start_local, true)} · {formatDate(schedule.planned_window.window_start_utc, true)}</strong></div><div><span>Fin local / UTC</span><strong>{formatDate(schedule.planned_window.window_end_local, true)} · {formatDate(schedule.planned_window.window_end_utc, true)}</strong></div><div><span>Corte local / UTC</span><strong>{formatDate(schedule.planned_window.cutoff_local, true)} · {formatDate(schedule.planned_window.cutoff_utc, true)}</strong></div><div><span>Próximo límite local / UTC</span><strong>{formatDate(schedule.planned_window.next_boundary_local, true)} · {formatDate(schedule.planned_window.next_boundary_utc, true)}</strong></div></div>}
          {!schedule.planned_window && <p className="unknown-note">La ventana planificada no está disponible en la respuesta.</p>}
        </> : <p className="unknown-note">La configuración semanal está ausente o no confirmada; no se inventa una ventana.</p>}
        {schedule.last_receipt && <p className="last-receipt-note">Último recibo: <button type="button" className="text-link" onClick={() => selectDelivery(schedule.last_receipt!.delivery_id)}>{schedule.last_receipt.delivery_id}</button> · {schedule.last_receipt.status} · {formatDate(schedule.last_receipt.dispatched_at, true)}</p>}
      </section>}

      <section className="manual-cycle-panel panel" aria-labelledby="manual-cycle-title"><div className="section-heading"><div><p className="eyebrow">ACCIÓN EXPLÍCITA · DOS FASES</p><h2 id="manual-cycle-title">Ciclo manual</h2></div><CalendarClock aria-hidden="true" size={18} /></div>
        <p>La preparación consulta requisitos; por sí sola no inicia trabajos. La confirmación solicita el ciclo local persistido en SQLite. No fuerza ventanas, no toma capturas, no genera informe final y no usa conectores o proveedores remotos.</p>
        {!canPrepare && <p className="analysis-security-note"><LockKeyhole aria-hidden="true" size={16} /> El servidor no concedió <code>weekly_cycle:prepare</code> para esta sesión.</p>}
        {canPrepare && <button type="button" className="button button-secondary" onClick={() => setPrepareDialogOpen(true)} disabled={!canStartPreparation || preparing || confirming}><ShieldCheck aria-hidden="true" size={15} /> {preparing ? "Preparando…" : "Preparar ciclo manual"}</button>}
        {canPrepare && schedule && !canStartPreparation && <p className="unknown-note">La configuración del servidor no declara un ciclo manual elegible; no se envía la preparación.</p>}
        {preparation && <div className="preparation-card" aria-label="Preflight del ciclo manual"><div className="detail-section-heading"><div><h3>Preparación del ciclo</h3><p>Preparación vence {formatDate(preparation.expires_at, true)} · fingerprint {preparation.configuration_fingerprint}</p></div><Pill tone={preparation.eligible && preparation.preflight.can_run ? "positive" : "warning"}>{preparation.eligible && preparation.preflight.can_run ? "Elegible para confirmar" : "No elegible"}</Pill></div>
          <div className="preflight-grid"><div><span>Estado de programación</span><strong>{scheduleStatusLabel(preparation.preflight.schedule_status)}</strong></div><div><span>Ventana</span><strong>{preparation.preflight.window_id ?? "Desconocida"}</strong></div><div><span>Hay capturas disponibles</span><strong>{scheduleValue(preparation.preflight.has_captures)}</strong></div><div><span>Historial de outbox</span><strong>{preparation.preflight.outbox_history === "available" ? "Disponible" : "Desconocido"}</strong></div><div><span>Vinculación del informe</span><strong>{preparation.preflight.report_binding}</strong></div><div><span>¿Puede ejecutarse?</span><strong>{scheduleValue(preparation.preflight.can_run)}</strong></div><div><span>Remoto requerido / permitido</span><strong>No / No</strong></div></div>
          <label className="cycle-local-consent"><input type="checkbox" checked={localCycleConsent} onChange={(event) => setLocalCycleConsent(event.target.checked)} /> <span>Confirmo ejecutar el ciclo local mediante el canal SQLite, sin llamadas remotas ni entregas externas</span></label>
          {!preparation.eligible && <p className="unknown-note">El servidor no autorizó la preparación del ciclo.</p>}
          <div className="preparation-actions">{!canConfirm && <p className="unknown-note">La sesión no tiene la capability separada <code>weekly_cycle:confirm</code>.</p>}{canConfirm && !canConfirmPrepared && preparation.eligible && <p className="unknown-note">La preparación no concede confirmación; se requiere elegibilidad y capability actual del servidor.</p>}<button type="button" className="button button-primary" onClick={() => setCycleConfirmationOpen(true)} disabled={!canConfirmPrepared || !localCycleConsent}>Confirmar ciclo semanal</button></div>
        </div>}
      </section>

      {activeJob && <section className="cycle-job-panel panel" aria-live="polite" aria-labelledby="cycle-job-title"><div className="section-heading"><div><p className="eyebrow">TRABAJO MANUAL PERSISTIDO</p><h2 id="cycle-job-title">Estado del ciclo</h2></div><Pill tone={activeJob.status === "completed" && activeJob.outcome === "EXECUTED" ? "positive" : activeJob.status === "failed" ? "danger" : "neutral"}>{jobStatusLabel(activeJob.status)}</Pill></div><dl className="audit-detail-grid"><div><dt>Trabajo</dt><dd><code>{activeJob.job_id}</code></dd></div><div><dt>Resultado</dt><dd><Pill tone={outcomeTone(activeJob.outcome)}>{outcomeLabel(activeJob.outcome)}</Pill></dd></div><div><dt>Ventana</dt><dd>{activeJob.window_id ?? "Desconocida"}</dd></div><div><dt>Recibo</dt><dd>{activeJob.delivery_id ? <button type="button" className="text-link" onClick={() => selectDelivery(activeJob.delivery_id)}>{activeJob.delivery_id}</button> : "No informado"}</dd></div><div><dt>Consentimiento remoto</dt><dd>No</dd></div><div><dt>Última actualización</dt><dd>{formatDate(activeJob.updated_at, true)}</dd></div>{activeJob.error_code && <div><dt>Código de error</dt><dd><code>{activeJob.error_code}</code></dd></div>}</dl>{canPollJob && <p className="caption">El sondeo GET se detiene en estados terminales; HTTP GET no inicia ni cambia el ciclo.</p>}{activeJobQuery.isError && <p role="alert" className="inline-warning">No se pudo actualizar el estado: {activeJobQuery.error.message}</p>}</section>}

      {canReadCycleJobs && jobsQuery.data?.availability === "available" && jobsQuery.data.items.length > 0 && <section className="cycle-history-panel panel"><div className="section-heading"><div><p className="eyebrow">HISTORIAL SERVIDOR</p><h2>Ciclos manuales anteriores</h2></div><span className="count-label">{jobsQuery.data.total} trabajos</span></div><ul className="cycle-history-list">{jobsQuery.data.items.map((job) => <li key={job.job_id}><span><strong>{jobStatusLabel(job.status)}</strong><code>{job.job_id}</code></span><Pill tone={outcomeTone(job.outcome)}>{outcomeLabel(job.outcome)}</Pill><span>{formatDate(job.updated_at, true)}</span><button type="button" className="text-link" onClick={() => setActiveJobId(job.job_id)}>Consultar estado</button></li>)}</ul></section>}

      <section className="outbox-section"><div className="section-heading"><div><p className="eyebrow">MENSAJES Y RECIBOS</p><h2>Outbox</h2></div><span className="caption">Artefactos solo disponibles si el servidor verifica el recibo almacenado.</span></div>
        {session?.authenticated && outboxQuery.isPending && <LoadingState label="Consultando recibos de la outbox…" />}
        {session?.authenticated && outboxQuery.isError && <ErrorState title="No se pudo cargar la outbox" message={outboxQuery.error.message} onRetry={() => void outboxQuery.refetch()} />}
        {session?.authenticated && outboxQuery.data?.availability === "unknown" && <EmptyState title="Disponibilidad de outbox desconocida" message="El backend no confirma que el historial esté disponible." />}
        {session?.authenticated && outboxQuery.data?.availability === "available" && outboxQuery.data.items.length === 0 && <EmptyState title="No hay recibos almacenados" message="No se sintetizan mensajes, ciclos o informes ausentes." />}
        {session?.authenticated && outboxQuery.data?.availability === "available" && outboxQuery.data.items.length > 0 && <div className="outbox-workspace"><section className="panel outbox-list-panel" aria-label="Recibos de outbox"><div className="outbox-list">{outboxQuery.data.items.map((receipt) => <Link to={deliveryHref(receipt.delivery_id)} className={`outbox-list-item${receipt.delivery_id === deliveryId ? " outbox-list-item-active" : ""}`} key={receipt.delivery_id} aria-label={`Abrir entrega ${receipt.delivery_id}`}><span className="outbox-list-top"><strong>{receipt.status}</strong><Pill tone={receipt.status === "DELIVERED" ? "positive" : receipt.status === "FAILED" ? "danger" : "warning"}>{receipt.channel}</Pill></span><span>{receipt.delivery_id}</span><span>Ventana {receipt.window_id}</span><span>{formatDate(receipt.dispatched_at, true)}</span><span>Artefacto {receipt.artifact_available ? "disponible según el recibo" : "no disponible"}</span></Link>)}</div><div className="table-footer"><span>Página {page} de {pages}</span><div className="pagination-controls"><button type="button" className="icon-button" aria-label="Página anterior de recibos" disabled={page <= 1} onClick={() => changePage(page - 1)}><ChevronLeft aria-hidden="true" size={16} /></button><button type="button" className="icon-button" aria-label="Página siguiente de recibos" disabled={page >= pages} onClick={() => changePage(page + 1)}><ChevronRight aria-hidden="true" size={16} /></button></div></div></section>
          <section className="panel outbox-detail-panel" aria-label="Detalle del recibo">{!deliveryId && <EmptyState title="Selecciona un recibo" message="Se consultará su mensaje persistido y la vinculación de artefacto." />}{deliveryId && detailQuery.isPending && <LoadingState label="Consultando detalle de outbox…" />}{deliveryId && detailQuery.isError && <ErrorState title="No se pudo cargar el recibo" message={detailQuery.error.message} onRetry={() => void detailQuery.refetch()} />}{detailQuery.data && <><div className="outbox-detail-heading"><div><p className="eyebrow">{detailQuery.data.receipt.channel.toUpperCase()} · {detailQuery.data.receipt.status}</p><h3>Entrega {detailQuery.data.receipt.delivery_id}</h3><p className="outbox-subject">{detailQuery.data.message.subject}</p><code>{detailQuery.data.receipt.delivery_id} · mensaje {detailQuery.data.message.message_id}</code></div><Pill tone="neutral">{detailQuery.data.receipt.window_id}</Pill></div><p className="outbox-summary">{detailQuery.data.message.summary}</p><div className="outbox-meta-grid"><div><span>Creado</span><strong>{formatDate(detailQuery.data.message.created_at, true)}</strong></div><div><span>Recibido</span><strong>{formatDate(detailQuery.data.receipt.dispatched_at, true)}</strong></div><div><span>Informe vinculado</span><strong>{detailQuery.data.message.report_id ?? "No vinculado"}</strong></div><div><span>Canal</span><strong>{detailQuery.data.message.channel}</strong></div>{detailQuery.data.message.recipient && <div><span>Destinatario configurado</span><strong>{detailQuery.data.message.recipient}</strong></div>}</div>
              <section className="outbox-metrics"><div><span>Total</span><strong>{detailQuery.data.message.metrics.total_tasks ?? "Desconocido"}</strong></div><div><span>Incluidos</span><strong>{detailQuery.data.message.metrics.included_tasks ?? "Desconocido"}</strong></div><div><span>Completados</span><strong>{detailQuery.data.message.metrics.completed_tasks ?? "Desconocido"}</strong></div><div><span>Progreso</span><strong>{detailQuery.data.message.metrics.progress_percentage === null ? "Desconocido" : formatPercentage(detailQuery.data.message.metrics.progress_percentage)}</strong></div><div><span>Vencidos</span><strong>{detailQuery.data.message.metrics.overdue_count ?? "Desconocido"}</strong></div><div><span>Bloqueados</span><strong>{detailQuery.data.message.metrics.blocked_count ?? "Desconocido"}</strong></div><div><span>Desconocidos</span><strong>{detailQuery.data.message.metrics.unknown_count ?? "Desconocido"}</strong></div><div><span>Contradictorios</span><strong>{detailQuery.data.message.metrics.contradictory_count ?? "Desconocido"}</strong></div></section>
              <div className="report-download-actions"><span>Descargar contenido vinculado</span><button type="button" aria-label="Descargar recibo Markdown" className="button button-secondary" onClick={() => void downloadOutbox("markdown")} disabled={!detailQuery.data.receipt.artifact_available || downloadBusy !== null}><FileText aria-hidden="true" size={15} /> Markdown{downloadBusy === "markdown" ? "…" : ""}</button><button type="button" aria-label="Descargar recibo JSON" className="button button-secondary" onClick={() => void downloadOutbox("json")} disabled={!detailQuery.data.receipt.artifact_available || downloadBusy !== null}><FileJson aria-hidden="true" size={15} /> JSON{downloadBusy === "json" ? "…" : ""}</button><Download aria-hidden="true" size={15} /></div>
              <SafeMarkdown source={detailQuery.data.message.body_markdown} className="safe-markdown outbox-markdown" />{detailQuery.data.message.risks.length > 0 && <section className="detail-section"><h4>Riesgos persistidos</h4><ul>{detailQuery.data.message.risks.map((risk, index) => <li key={`${index}-${risk}`}>{risk}</li>)}</ul></section>}{detailQuery.data.message.report_id && <Link className="text-link" to={`/reports?report_id=${encodeURIComponent(detailQuery.data.message.report_id)}`}>Abrir informe vinculado</Link>}</>}</section></div>}
      </section>
      <ConfirmActionDialog open={prepareDialogOpen} onOpenChange={(open) => { if (!open && !preparing) setPrepareDialogOpen(false); }} title="Preparar ciclo semanal local" description="La preparación valida de forma protegida el calendario y los recibos. No inicia el ciclo ni modifica estado externo." confirmLabel="Comprobar elegibilidad" onConfirm={() => void prepareCycle()} working={preparing} />
      <ConfirmActionDialog open={cycleConfirmationOpen} onOpenChange={(open) => { if (!open && !confirming) setCycleConfirmationOpen(false); }} title="Confirmar ciclo semanal" description={`Proyecto ${project?.id ?? "desconocido"}. Preparación ${preparation?.preparation_id ?? "desconocida"}; fingerprint ${preparation?.configuration_fingerprint ?? "desconocido"}. La casilla confirma un ciclo local en SQLite con remote_consent=false; no se fuerzan ventanas, no se llama a Trello, a proveedores remotos ni a entregas externas.`} confirmLabel="Ejecutar ciclo local" onConfirm={() => void confirmCycle()} working={confirming} />
    </section>
  );
}
