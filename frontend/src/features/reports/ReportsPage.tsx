import { useRef, useState } from "react";
import { ArrowDownToLine, ChevronLeft, ChevronRight, Download, FileJson, FileText, LockKeyhole } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { formatDate, formatPercentage } from "../../api/formatters";
import { queryKeys, useProject, useProjects, useReport, useReports } from "../../api/queries";
import type { ReportHumanDecision } from "../../api/types";
import { ConfirmActionDialog } from "../../components/ConfirmActionDialog";
import { EmptyState, ErrorState, LoadingState, Pill } from "../../components/States";
import { SafeMarkdown } from "../../components/SafeMarkdown";
import { saveDownloadedFile } from "../../components/download";
import { useToast } from "../../components/ToastProvider";
import { useSession } from "../auth/SessionProvider";
import { useWorkspace } from "../../app/WorkspaceContext";

const pageSize = 25;
type DownloadFormat = "markdown" | "json";
type DecisionChoice = Exclude<ReportHumanDecision, "pending">;

const decisionLabels: Record<ReportHumanDecision, string> = {
  pending: "Pendiente de decisión", approved: "Aprobado por decisión humana", rejected: "Rechazado por decisión humana",
  changes_requested: "Cambios solicitados", postponed: "Pospuesto",
};

function decisionTone(decision: ReportHumanDecision): "neutral" | "positive" | "warning" | "danger" {
  if (decision === "approved") return "positive";
  if (decision === "rejected") return "danger";
  if (decision === "pending" || decision === "postponed") return "warning";
  return "neutral";
}

function choiceDescription(choice: DecisionChoice) {
  const labels: Record<DecisionChoice, string> = {
    approved: "Aprobar este informe como decisión humana persistida.",
    rejected: "Rechazar este informe como decisión humana persistida.",
    changes_requested: "Solicitar cambios como decisión humana persistida.",
    postponed: "Posponer la revisión de este informe.",
  };
  return labels[choice];
}

export function ReportsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { selectedProjectId } = useWorkspace();
  const projectsQuery = useProjects();
  const listedProject = projectsQuery.data?.items.find((item) => item.id === selectedProjectId);
  const selectedProjectQuery = useProject(selectedProjectId, Boolean(selectedProjectId && !listedProject));
  const project = listedProject ?? selectedProjectQuery.data ?? (!selectedProjectId ? projectsQuery.data?.items[0] ?? null : null);
  const reportId = searchParams.get("report_id");
  const decisionParam = searchParams.get("decision") ?? "";
  const decisionFilter = (["pending", "approved", "rejected", "changes_requested", "postponed"] as const).includes(decisionParam as ReportHumanDecision) ? decisionParam as ReportHumanDecision : "";
  const page = Math.max(1, Number(searchParams.get("page") ?? 1) || 1);
  const reportsQuery = useReports(project?.id ?? null, page, pageSize, decisionFilter);
  const detailQuery = useReport(project?.id ?? null, reportId);
  const { session } = useSession();
  const queryClient = useQueryClient();
  const { notify } = useToast();
  const [choice, setChoice] = useState<DecisionChoice>("approved");
  const [reason, setReason] = useState("");
  const [confirmDecision, setConfirmDecision] = useState(false);
  const [working, setWorking] = useState(false);
  const [downloadBusy, setDownloadBusy] = useState<DownloadFormat | null>(null);
  const [auditEventId, setAuditEventId] = useState<string | null>(null);
  const retryKeys = useRef(new Map<string, string>());
  const report = detailQuery.data;
  const canDecideReports = Boolean(session?.authenticated && session.csrf_token && session.capabilities.includes("reports:decide"));

  function reportHref(id: string) {
    const next = new URLSearchParams(searchParams);
    next.set("report_id", id);
    return { pathname: "/reports", search: `?${next.toString()}` };
  }

  function changeFilter(value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set("decision", value);
    else next.delete("decision");
    next.delete("page");
    setSearchParams(next);
  }

  function changePage(nextPage: number) {
    const next = new URLSearchParams(searchParams);
    if (nextPage > 1) next.set("page", String(nextPage));
    else next.delete("page");
    setSearchParams(next);
  }

  async function downloadReport(format: DownloadFormat) {
    if (!project || !report) return;
    setDownloadBusy(format);
    try {
      saveDownloadedFile(await api.downloadReport(project.id, report.report_id, format));
      notify(`Descarga ${format === "markdown" ? "Markdown" : "JSON"} solicitada al backend.`, "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "La descarga no pudo completarse.", "error");
    } finally {
      setDownloadBusy(null);
    }
  }

  async function submitDecision() {
    if (!project || !report || !session?.csrf_token || !canDecideReports || report.human_decision !== "pending") return;
    const key = `${report.report_id}:${report.report_version}:${choice}`;
    const idempotencyKey = retryKeys.current.get(key) ?? crypto.randomUUID();
    retryKeys.current.set(key, idempotencyKey);
    setWorking(true);
    try {
      const response = await api.decideReport(project.id, report.report_id, {
        expected_report_version: report.report_version,
        expected_human_decision: "pending",
        decision: choice,
        reason: reason.trim() || null,
      }, session.csrf_token, idempotencyKey);
      retryKeys.current.delete(key);
      queryClient.setQueryData(queryKeys.report(project.id, report.report_id), response.report);
      setAuditEventId(response.audit_event_id);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["reports", project.id] }),
        queryClient.invalidateQueries({ queryKey: ["audit", project.id] }),
      ]);
      setConfirmDecision(false);
      notify("Decisión humana del informe registrada por el backend; no modifica una propuesta ni un servicio externo.", "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "La decisión no pudo registrarse.", "error");
    } finally {
      setWorking(false);
    }
  }

  const pages = Math.max(1, Math.ceil((reportsQuery.data?.total ?? 0) / pageSize));

  return (
    <section className="governance-page reports-page">
      <header className="page-heading-row"><div><p className="eyebrow">EVIDENCIA Y DECISIÓN HUMANA</p><h1>Informes</h1><p className="page-subtitle">Consulta informes persistidos, descarga artefactos desde la API y registra una decisión humana versionada. La decisión del informe es independiente de la revisión de propuestas.</p></div><Pill tone="neutral">Solo lectura externa</Pill></header>
      <section className="module-toolbar panel" aria-label="Filtros de informes">
        <label className="filter-control"><span>Decisión humana</span><select value={decisionFilter} onChange={(event) => changeFilter(event.target.value)}><option value="">Todas las decisiones</option><option value="pending">Pendiente</option><option value="approved">Aprobado</option><option value="rejected">Rechazado</option><option value="changes_requested">Cambios solicitados</option><option value="postponed">Pospuesto</option></select></label>
        <span className="caption">Los artefactos se solicitan bajo demanda; el navegador no procesa HTML crudo ni carga imágenes remotas.</span>
      </section>
      {reportsQuery.isPending && <LoadingState label="Consultando informes persistidos…" />}
      {reportsQuery.isError && <ErrorState title="No se pudieron cargar los informes" message={reportsQuery.error.message} onRetry={() => void reportsQuery.refetch()} />}
      {reportsQuery.data?.availability === "unknown" && <EmptyState title="Disponibilidad de informes desconocida" message="La respuesta no confirma que el historial esté disponible." />}
      {reportsQuery.data?.availability === "available" && reportsQuery.data.items.length === 0 && <EmptyState title="No hay informes en este filtro" message="La API confirmó la consulta sin resultados para la decisión seleccionada." />}

      {reportsQuery.data?.availability === "available" && reportsQuery.data.items.length > 0 && <div className="report-workspace">
        <section className="panel report-list-panel" aria-labelledby="report-list-title"><div className="section-heading"><div><p className="eyebrow">ARCHIVO DEL PROYECTO</p><h2 id="report-list-title">Informes recientes</h2></div><span className="count-label">{reportsQuery.data.total} informes</span></div>
          <div className="report-list">{reportsQuery.data.items.map((item) => <Link to={reportHref(item.report_id)} className={`report-list-item${item.report_id === reportId ? " report-list-item-active" : ""}`} key={item.report_id} aria-label={`Abrir informe ${item.report_id}`}>
            <span className="report-list-top"><strong>Informe {item.report_id}</strong><Pill tone={decisionTone(item.human_decision)}>{decisionLabels[item.human_decision]}</Pill></span><span>{item.report_id} · versión {item.report_version}</span><span>{formatDate(item.created_at, true)} · corte {formatDate(item.cutoff_datetime, true)}</span><span>{item.metrics.included_cards} de {item.metrics.total_cards} elementos incluidos{item.metrics.progress_percentage !== null ? ` · ${formatPercentage(item.metrics.progress_percentage)}` : " · progreso desconocido"}</span>
          </Link>)}</div>
          <div className="table-footer"><span>Página {page} de {pages}</span><div className="pagination-controls"><button type="button" className="icon-button" aria-label="Página anterior de informes" disabled={page <= 1} onClick={() => changePage(page - 1)}><ChevronLeft aria-hidden="true" size={16} /></button><button type="button" className="icon-button" aria-label="Página siguiente de informes" disabled={page >= pages} onClick={() => changePage(page + 1)}><ChevronRight aria-hidden="true" size={16} /></button></div></div>
        </section>

        <section className="panel report-detail-panel" aria-labelledby="report-detail-title">
          {!reportId && <EmptyState title="Selecciona un informe" message="El cuerpo del informe se consulta por separado al abrirlo." />}
          {reportId && detailQuery.isPending && <LoadingState label="Consultando detalle del informe…" />}
          {reportId && detailQuery.isError && <ErrorState title="No se pudo cargar el informe" message={detailQuery.error.message} onRetry={() => void detailQuery.refetch()} />}
          {report && <>
            <div className="report-detail-heading"><div><p className="eyebrow">INFORME · RUN {report.run_id ?? "DESCONOCIDO"}</p><h2 id="report-detail-title">Informe {report.report_id}</h2><code>versión {report.report_version}</code></div><Pill tone={decisionTone(report.human_decision)}>{decisionLabels[report.human_decision]}</Pill></div>
            <p className="report-summary">{report.summary}</p>
            <div className="report-meta-grid"><div><span>Fecha de creación</span><strong>{formatDate(report.created_at, true)}</strong></div><div><span>Fecha de corte</span><strong>{formatDate(report.cutoff_datetime, true)}</strong></div><div><span>Recomendación del motor</span><strong>{report.recommendation}</strong></div><div><span>Decisión humana</span><strong>{decisionLabels[report.human_decision]}</strong></div><div><span>Motivo de decisión</span><strong>{report.decision_reason ?? "No informado"}</strong></div><div><span>Fecha de decisión</span><strong>{report.decision_timestamp ? formatDate(report.decision_timestamp, true) : "No informada"}</strong></div></div>
            <section className="report-metrics" aria-label="Métricas del informe"><div><span>Elementos totales</span><strong>{report.metrics.total_cards}</strong></div><div><span>Incluidos</span><strong>{report.metrics.included_cards}</strong></div><div><span>Completados</span><strong>{report.metrics.completed_cards}</strong></div><div><span>Progreso</span><strong>{report.metrics.progress_percentage === null ? "Desconocido" : formatPercentage(report.metrics.progress_percentage)}</strong></div><div><span>Vencidos</span><strong>{report.metrics.overdue_card_ids.length}</strong></div><div><span>Bloqueados</span><strong>{report.metrics.blocked_card_ids.length}</strong></div><div><span>Desconocidos</span><strong>{report.metrics.unknown_card_ids.length}</strong></div><div><span>Contradictorios</span><strong>{report.metrics.contradictory_card_ids.length}</strong></div></section>
            <div className="report-download-actions"><span>Descargar artefacto</span><button type="button" aria-label="Descargar Markdown" className="button button-secondary" onClick={() => void downloadReport("markdown")} disabled={downloadBusy !== null}><FileText aria-hidden="true" size={15} /> Markdown{downloadBusy === "markdown" ? "…" : ""}</button><button type="button" aria-label="Descargar JSON" className="button button-secondary" onClick={() => void downloadReport("json")} disabled={downloadBusy !== null}><FileJson aria-hidden="true" size={15} /> JSON{downloadBusy === "json" ? "…" : ""}</button></div>
            <section className="report-markdown-section"><div className="detail-section-heading"><div><h3>Contenido Markdown</h3><p>HTML crudo omitido; enlaces seguros bajo demanda y sin imágenes externas.</p></div><Download aria-hidden="true" size={16} /></div>{report.markdown_text === null ? <p className="unknown-note">El cuerpo Markdown no está disponible en esta respuesta.</p> : <SafeMarkdown source={report.markdown_text} className="safe-markdown report-markdown" />}</section>
            {report.risk_task_ids.length > 0 && <section className="detail-section"><h3>Tareas marcadas como riesgo por el informe</h3><ul className="task-id-list">{report.risk_task_ids.map((taskId) => <li key={taskId}><Link to={`/tasks?q=${encodeURIComponent(taskId)}`}>{taskId} <ArrowDownToLine aria-hidden="true" size={12} /></Link></li>)}</ul></section>}
            {report.recommendations.length > 0 && <section className="detail-section"><h3>Recomendaciones reportadas</h3><ul className="report-recommendations">{report.recommendations.map((recommendation, index) => <li key={`${index}-${recommendation}`}>{recommendation}</li>)}</ul></section>}
            {report.policy_results.length > 0 && <section className="detail-section"><h3>Resultados de políticas</h3><ul className="report-policy-results">{report.policy_results.map((policy) => <li key={policy.rule_id}><span>{policy.description}</span><Pill tone={policy.status === "pass" ? "positive" : policy.status === "fail" ? "danger" : "unknown"}>{policy.status}</Pill><small>{policy.reason ?? "Sin motivo informado"}</small></li>)}</ul></section>}
            <section className="detail-section"><div className="detail-section-heading"><div><h3>Historial de decisiones humanas</h3><p>Decidir un informe no aprueba ni ejecuta propuestas.</p></div></div>{report.decision_history.length ? <ol className="decision-history">{report.decision_history.map((event) => <li key={event.decision_id}><div><strong>{decisionLabels[event.decision]}</strong><Pill tone="unknown">Autor no verificado</Pill></div><span>{formatDate(event.recorded_at, true)} · {event.reason ?? "Sin motivo informado"}</span><code>{event.decision_id}</code></li>)}</ol> : <p className="unknown-note">No hay decisiones registradas en el historial del informe.</p>}</section>
            {report.human_decision === "pending" && <section className="report-decision-box"><div><h3>Registrar decisión humana</h3><p>Esta decisión queda versionada en el informe. No cambia el estado de propuestas ni produce escrituras externas.</p></div>{!canDecideReports ? <p className="unknown-note"><LockKeyhole aria-hidden="true" size={15} /> El servidor no concedió <code>reports:decide</code>.</p> : <>
              <label className="filter-control"><span>Decisión humana del informe</span><select aria-label="Decisión humana del informe" value={choice} onChange={(event) => setChoice(event.target.value as DecisionChoice)}><option value="approved">Aprobar informe</option><option value="rejected">Rechazar informe</option><option value="changes_requested">Solicitar cambios</option><option value="postponed">Posponer</option></select></label>
              <label className="filter-control report-reason-control"><span>Motivo de la decisión</span><textarea aria-label="Motivo de la decisión" value={reason} onChange={(event) => setReason(event.target.value)} rows={3} placeholder="Añade contexto para el historial de decisiones" />{choice !== "approved" && !reason.trim() && <small className="form-error">El motivo es obligatorio para esta decisión.</small>}</label>
              <button type="button" className="button button-primary" onClick={() => setConfirmDecision(true)} disabled={choice !== "approved" && !reason.trim()}>Registrar decisión humana</button>
            </>}</section>}
            {auditEventId && <p className="audit-receipt-note">Evento de auditoría enlazado: <code>{auditEventId}</code></p>}
          </>}
        </section>
      </div>}
      <ConfirmActionDialog open={confirmDecision} onOpenChange={(open) => { if (!open && !working) setConfirmDecision(false); }} title="Confirmar decisión humana" description={`${choiceDescription(choice)} Informe ${report?.report_id ?? "desconocido"}, versión ${report?.report_version ?? "desconocida"}. La decisión esperada aún es pendiente y se enviará con CSRF e idempotencia.`} confirmLabel="Confirmar decisión humana" onConfirm={() => void submitDecision()} working={working} danger={choice === "rejected"} />
    </section>
  );
}
