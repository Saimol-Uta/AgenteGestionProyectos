import { useMemo, useRef, useState } from "react";
import { ArrowRight, ChevronLeft, ChevronRight, Fingerprint, LockKeyhole, ShieldCheck } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { fieldLabel, comparisonValue, formatDate } from "../../api/formatters";
import { queryKeys, useProject, useProjects, useProposal, useProposals } from "../../api/queries";
import type { ProposalDetail, ProposalReviewFilter } from "../../api/types";
import { ConfirmActionDialog } from "../../components/ConfirmActionDialog";
import { EmptyState, ErrorState, LoadingState, Pill } from "../../components/States";
import { useToast } from "../../components/ToastProvider";
import { useSession } from "../auth/SessionProvider";
import { useWorkspace } from "../../app/WorkspaceContext";

const pageSize = 25;
type ReviewAction = "approve" | "reject" | "authorize" | "simulate";

function reviewLabel(status: ProposalDetail["review_status"]) {
  const labels = { pending: "Pendiente de revisión", approved_offline: "Aprobada offline", rejected: "Rechazada localmente", unknown: "Desconocida" };
  return labels[status];
}

function authorizationLabel(status: ProposalDetail["authorization_status"]) {
  const labels = { not_authorized: "No autorizada", authorized_dry_run: "Autorizada para simulación", denied: "Denegada", revoked: "Revocada", unknown: "Desconocida" };
  return labels[status];
}

function executionLabel(status: ProposalDetail["execution_status"]) {
  const labels: Record<ProposalDetail["execution_status"], string> = {
    not_executed: "No ejecutada", EXECUTED: "Ejecución registrada por el backend", ALREADY_APPLIED: "Ya aplicado según el backend",
    DENIED: "Denegada por el backend", CONFLICT: "Conflicto registrado", FAILED: "Fallida", unknown: "Desconocida",
  };
  return labels[status];
}

function proposalTone(value: string): "neutral" | "positive" | "warning" | "danger" | "unknown" {
  if (["approved_offline", "authorized_dry_run", "eligible", "current"].includes(value)) return "positive";
  if (["pending", "not_authorized"].includes(value)) return "warning";
  if (["rejected", "revoked", "denied", "ineligible", "stale"].includes(value)) return "danger";
  if (value === "unknown") return "unknown";
  return "neutral";
}

function actionCopy(action: ReviewAction) {
  switch (action) {
    case "approve": return { title: "Confirmar aprobación de revisión local", confirm: "Registrar aprobación offline", description: "Esta decisión registra únicamente una revisión local: no autoriza una simulación ni ejecuta cambios. Tampoco aprueba informes ni modifica Trello." };
    case "reject": return { title: "Confirmar rechazo de revisión local", confirm: "Registrar rechazo local", description: "El rechazo se registra en el historial inmutable de revisión. No cambia la captura ni ejecuta una acción externa." };
    case "authorize": return { title: "Autorizar simulación", confirm: "Autorizar simulación", description: "Esta autorización versionada permite únicamente una simulación local. No concede permiso de ejecución real ni escribe en servicios externos." };
    case "simulate": return { title: "Confirmar simulación local", confirm: "Simular sin escrituras", description: "El motor volverá a validar la propuesta almacenada y su autorización. La simulación no lee proveedores, no modifica estado en vivo y no habilita ejecución real." };
  }
}

export function ProposalsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { selectedProjectId } = useWorkspace();
  const projectsQuery = useProjects();
  const listedProject = projectsQuery.data?.items.find((item) => item.id === selectedProjectId);
  const selectedProjectQuery = useProject(selectedProjectId, Boolean(selectedProjectId && !listedProject));
  const project = listedProject ?? selectedProjectQuery.data ?? (!selectedProjectId ? projectsQuery.data?.items[0] ?? null : null);
  const proposalId = searchParams.get("proposal_id");
  const reviewStatusParam = searchParams.get("review_status") ?? "";
  const reviewStatus = (["pending", "approved_offline", "rejected"] as const).includes(reviewStatusParam as ProposalReviewFilter) ? reviewStatusParam as ProposalReviewFilter : "";
  const page = Math.max(1, Number(searchParams.get("page") ?? 1) || 1);
  const proposalsQuery = useProposals(project?.id ?? null, page, pageSize, reviewStatus);
  const detailQuery = useProposal(project?.id ?? null, proposalId);
  const { session } = useSession();
  const queryClient = useQueryClient();
  const { notify } = useToast();
  const [activeAction, setActiveAction] = useState<ReviewAction | null>(null);
  const [working, setWorking] = useState(false);
  const [auditEventId, setAuditEventId] = useState<string | null>(null);
  const retryKeys = useRef(new Map<string, string>());
  const proposal = detailQuery.data;
  const canReview = Boolean(session?.authenticated && session.csrf_token && session.capabilities.includes("governance:proposal_review"));
  const canAuthorizeDryRun = Boolean(session?.authenticated && session.csrf_token && session.capabilities.includes("governance:dry_run_authorization"));
  const canRunDryRun = Boolean(session?.authenticated && session.csrf_token && session.capabilities.includes("governance:dry_run"));
  const canAuthorizeCurrent = Boolean(proposal?.review_status === "approved_offline" && proposal.freshness === "current" && proposal.technical_eligibility === "eligible");
  const authorizationEvent = useMemo(
    () => proposal?.decision_history.slice().reverse().find((event) => event.decision === "authorize_dry_run") ?? null,
    [proposal?.decision_history],
  );
  const canSimulate = Boolean(proposal?.authorization_status === "authorized_dry_run" && proposal.freshness === "current" && proposal.technical_eligibility === "eligible" && authorizationEvent);

  function proposalHref(id: string) {
    const next = new URLSearchParams(searchParams);
    next.set("proposal_id", id);
    return { pathname: "/proposals", search: `?${next.toString()}` };
  }

  function changeFilter(value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set("review_status", value);
    else next.delete("review_status");
    next.delete("page");
    setSearchParams(next);
  }

  function changePage(nextPage: number) {
    const next = new URLSearchParams(searchParams);
    if (nextPage > 1) next.set("page", String(nextPage));
    else next.delete("page");
    setSearchParams(next);
  }

  async function performAction(action: ReviewAction) {
    if (!proposal || !project || !session?.csrf_token || proposal.version === null) return;
    const capability = action === "approve" || action === "reject" ? canReview : action === "authorize" ? canAuthorizeDryRun : canRunDryRun;
    if (!capability) return;
    const key = `${proposal.proposal_id}:${action}:${proposal.version}:${proposal.proposal_body_sha256}`;
    const idempotencyKey = retryKeys.current.get(key) ?? crypto.randomUUID();
    retryKeys.current.set(key, idempotencyKey);
    setWorking(true);
    try {
      if (action === "approve" || action === "reject") {
        const response = await api.reviewProposal(project.id, proposal.proposal_id, {
          decision: action,
          expected_version: proposal.version,
          proposal_body_sha256: proposal.proposal_body_sha256,
          capture_id: proposal.capture_id,
        }, session.csrf_token, idempotencyKey);
        queryClient.setQueryData(queryKeys.proposal(project.id, proposal.proposal_id), response.proposal);
        setAuditEventId(response.audit_event_id);
      } else if (action === "authorize") {
        const response = await api.authorizeProposalDryRun(project.id, proposal.proposal_id, {
          authorize: true,
          expected_version: proposal.version,
          proposal_body_sha256: proposal.proposal_body_sha256,
          capture_id: proposal.capture_id,
        }, session.csrf_token, idempotencyKey);
        queryClient.setQueryData(queryKeys.proposal(project.id, proposal.proposal_id), response.proposal);
        setAuditEventId(response.audit_event_id);
      } else {
        if (!authorizationEvent) return;
        const response = await api.runProposalDryRun(project.id, proposal.proposal_id, {
          expected_version: proposal.version,
          proposal_body_sha256: proposal.proposal_body_sha256,
          capture_id: proposal.capture_id,
          permission_event_id: authorizationEvent.event_id,
        }, session.csrf_token, idempotencyKey);
        setAuditEventId(response.audit_event_id);
        await queryClient.invalidateQueries({ queryKey: queryKeys.proposal(project.id, proposal.proposal_id) });
      }
      retryKeys.current.delete(key);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["proposals", project.id] }),
        queryClient.invalidateQueries({ queryKey: ["audit", project.id] }),
      ]);
      setActiveAction(null);
      notify(action === "simulate" ? "Simulación local registrada; no se ejecutaron cambios." : "Decisión registrada por el backend.", "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "La operación no pudo completarse.", "error");
    } finally {
      setWorking(false);
    }
  }

  const dialog = activeAction ? actionCopy(activeAction) : null;
  const pageCount = Math.max(1, Math.ceil((proposalsQuery.data?.total ?? 0) / pageSize));

  return (
    <section className="governance-page proposals-page">
      <header className="page-heading-row">
        <div><p className="eyebrow">GOBERNANZA LOCAL · SIN ESCRITURAS EXTERNAS</p><h1>Propuestas</h1><p className="page-subtitle">Revisa los cambios observados, su evidencia y las decisiones persistidas. Revisar, autorizar una simulación y ejecutar la simulación son pasos distintos.</p></div>
        <Pill tone="neutral">Captura inmutable</Pill>
      </header>

      <section className="module-toolbar panel" aria-label="Filtros de propuestas">
        <label className="filter-control"><span>Estado de revisión</span><select value={reviewStatus} onChange={(event) => changeFilter(event.target.value)}>
          <option value="">Todos los estados</option><option value="pending">Pendiente</option><option value="approved_offline">Aprobada offline</option><option value="rejected">Rechazada</option>
        </select></label>
        <span className="caption">El historial proviene de decisiones append-only; la identidad de una afirmación offline no se considera verificada.</span>
      </section>

      {proposalsQuery.isPending && <LoadingState label="Consultando propuestas del backend…" />}
      {proposalsQuery.isError && <ErrorState title="No se pudieron cargar las propuestas" message={proposalsQuery.error.message} onRetry={() => void proposalsQuery.refetch()} />}
      {proposalsQuery.data?.availability === "unknown" && <EmptyState title="Disponibilidad de propuestas desconocida" message="La API no pudo confirmar la disponibilidad del ledger; no se interpreta como una bandeja vacía." />}
      {proposalsQuery.data?.availability === "available" && proposalsQuery.data.items.length === 0 && <EmptyState title="No hay propuestas en este filtro" message="El backend confirmó la consulta y no devolvió propuestas para el estado seleccionado." />}

      {proposalsQuery.data?.availability === "available" && proposalsQuery.data.items.length > 0 && (
        <div className="proposal-workspace">
          <section className="panel proposal-inbox" aria-labelledby="proposal-inbox-title">
            <div className="section-heading"><div><p className="eyebrow">CAPTURAS OBSERVADAS</p><h2 id="proposal-inbox-title">Bandeja de revisión</h2></div><span className="count-label">{proposalsQuery.data.total} propuestas</span></div>
            <div className="proposal-list">
              {proposalsQuery.data.items.map((item) => (
                <Link to={proposalHref(item.proposal_id)} key={item.proposal_id} className={`proposal-list-item${proposalId === item.proposal_id ? " proposal-list-item-active" : ""}`} aria-label={`Abrir propuesta ${item.proposal_id}`}>
                  <span className="proposal-list-top"><strong>{fieldLabel(item.field)}</strong><Pill tone={proposalTone(item.review_status)}>{reviewLabel(item.review_status)}</Pill></span>
                  <span className="proposal-list-id">{item.proposal_id} · Tarea {item.task_id}</span>
                  <span className="proposal-list-diff">{comparisonValue(item.current_value, item.field)} <ArrowRight aria-hidden="true" size={13} /> {comparisonValue(item.proposed_value, item.field)}</span>
                  <span className="proposal-list-meta">Captura {item.capture_id} · versión {item.version ?? "desconocida"}</span>
                </Link>
              ))}
            </div>
            <div className="table-footer"><span>Página {page} de {pageCount}</span><div className="pagination-controls"><button type="button" className="icon-button" aria-label="Página anterior de propuestas" disabled={page <= 1} onClick={() => changePage(page - 1)}><ChevronLeft aria-hidden="true" size={16} /></button><button type="button" className="icon-button" aria-label="Página siguiente de propuestas" disabled={page >= pageCount} onClick={() => changePage(page + 1)}><ChevronRight aria-hidden="true" size={16} /></button></div></div>
          </section>

          <section className="panel proposal-detail" aria-labelledby="proposal-detail-title">
            {!proposalId && <EmptyState title="Selecciona una propuesta" message="El detalle se consulta solo después de elegir una fila de la bandeja." />}
            {proposalId && detailQuery.isPending && <LoadingState label="Consultando detalle y decisiones persistidas…" />}
            {proposalId && detailQuery.isError && <ErrorState title="No se pudo cargar la propuesta" message={detailQuery.error.message} onRetry={() => void detailQuery.refetch()} />}
            {proposal && (
              <>
                <div className="proposal-detail-heading"><div><p className="eyebrow">DETALLE DE PROPUESTA</p><h2 id="proposal-detail-title">{fieldLabel(proposal.field)} · {proposal.task_id}</h2><code>{proposal.proposal_id}</code></div><Link className="text-link" to={`/tasks?q=${encodeURIComponent(proposal.task_id)}&capture_id=${encodeURIComponent(proposal.capture_id)}`}>Abrir tarea <ArrowRight aria-hidden="true" size={14} /></Link></div>
                <div className="proposal-value-diff" aria-label="Comparación entre valor observado y propuesto">
                  <div><span>Valor observado</span><strong>{comparisonValue(proposal.current_value, proposal.field)}</strong></div>
                  <ArrowRight aria-hidden="true" size={18} />
                  <div><span>Valor propuesto</span><strong>{comparisonValue(proposal.proposed_value, proposal.field)}</strong></div>
                </div>
                <div className="proposal-detail-grid">
                  <div><span>Motivo</span><p>{proposal.rationale}</p></div>
                  <div><span>Impacto esperado</span><p>{proposal.expected_impact}</p></div>
                  <div><span>Captura</span><code>{proposal.capture_id}</code></div>
                  <div><span>Versión</span><code>{proposal.version ?? "Desconocida"}</code></div>
                  <div className="proposal-hash"><span>Hash SHA-256 del cuerpo</span><code>{proposal.proposal_body_sha256}</code></div>
                </div>
                <div className="proposal-status-strip" aria-label="Estados independientes">
                  <div><span>Elegibilidad técnica</span><Pill tone={proposalTone(proposal.technical_eligibility)}>{proposal.technical_eligibility}</Pill></div>
                  <div><span>Vigencia de captura</span><Pill tone={proposalTone(proposal.freshness)}>{proposal.freshness}</Pill></div>
                  <div><span>Revisión local</span><Pill tone={proposalTone(proposal.review_status)}>{reviewLabel(proposal.review_status)}</Pill></div>
                  <div><span>Autorización de simulación</span><Pill tone={proposalTone(proposal.authorization_status)}>{authorizationLabel(proposal.authorization_status)}</Pill></div>
                  <div><span>Ejecución real</span><Pill tone="neutral">{executionLabel(proposal.execution_status)}</Pill></div>
                </div>
                {proposal.technical_errors.length > 0 && <div className="inline-warning"><strong>La validación técnica reportó:</strong><ul>{proposal.technical_errors.map((error) => <li key={error}>{error}</li>)}</ul></div>}
                <section className="detail-section"><div className="detail-section-heading"><div><h3>Evidencia asociada</h3><p>Proyección saneada entregada por el backend; no se leen artefactos en el navegador.</p></div><Fingerprint aria-hidden="true" size={16} /></div>
                  {proposal.evidence.length ? <ul className="evidence-list">{proposal.evidence.map((evidence) => <li key={evidence.evidence_id}><span className="evidence-source">{evidence.category}</span><strong>{evidence.summary}</strong><code>{evidence.evidence_id}</code></li>)}</ul> : <p className="unknown-note">No hay evidencia asociada en la respuesta.</p>}
                </section>
                <section className="detail-section"><div className="detail-section-heading"><div><h3>Historial de decisiones</h3><p>Las afirmaciones del core offline no verifican la identidad de una persona.</p></div><ShieldCheck aria-hidden="true" size={16} /></div>
                  {proposal.decision_history_availability === "unknown" ? <p className="unknown-note">Disponibilidad del historial desconocida.</p> : proposal.decision_history.length ? <ol className="decision-history">{proposal.decision_history.map((event) => <li key={event.event_id}><div><strong>{event.decision}</strong><Pill tone={event.actor_provenance === "unknown" ? "unknown" : "warning"}>{event.actor_provenance === "unknown" ? "Autor no verificado" : "Afirmación offline no verificada"}</Pill></div><span>{event.recorded_at ? formatDate(event.recorded_at, true) : "Fecha desconocida"} · versión {event.version}</span><code>{event.event_id}</code></li>)}</ol> : <p className="unknown-note">No hay decisiones registradas en el historial disponible.</p>}
                </section>
                <section className="detail-section"><div className="detail-section-heading"><div><h3>Simulaciones persistidas</h3><p>SIMULATED no equivale a ejecución real ni a cambio de estado en vivo.</p></div></div>
                  {proposal.dry_run_availability === "unknown" ? <p className="unknown-note">Disponibilidad del historial de simulaciones desconocida.</p> : proposal.dry_runs.length ? <ol className="decision-history">{proposal.dry_runs.map((run) => <li key={run.request_id}><div><strong>{run.status}</strong><Pill tone="neutral">Estado de ejecución real: No ejecutada</Pill></div><span>{comparisonValue(run.before, proposal.field)} → {comparisonValue(run.after, proposal.field)} · {formatDate(run.recorded_at, true)}</span><span>Estado en vivo: {run.live_state_changed ? "Cambio reportado" : "Sin cambios"}</span><span>Ejecución real permitida: {run.real_execution_allowed ? "Sí" : "No"}</span><code>{run.request_id}</code>{run.reasons.map((reason) => <small key={reason}>{reason}</small>)}</li>)}</ol> : <p className="unknown-note">No hay simulaciones registradas.</p>}
                </section>
                <div className="proposal-action-area">
                  {!session?.authenticated && <p className="analysis-security-note"><LockKeyhole aria-hidden="true" size={16} /> Inicia una sesión local verificada para solicitar decisiones; la sesión no es identidad externa.</p>}
                  {session?.authenticated && !canReview && !canAuthorizeDryRun && !canRunDryRun && <p className="analysis-security-note"><LockKeyhole aria-hidden="true" size={16} /> El servidor no concedió capabilities de gobernanza para este proyecto.</p>}
                  {canReview && proposal.review_status === "pending" && proposal.version !== null && <div className="proposal-action-group"><span>1 · Revisión offline</span><p>Decide si el cambio puede continuar al siguiente paso, sin autorizarlo ni ejecutarlo.</p><div className="proposal-action-buttons"><button type="button" className="button button-primary" onClick={() => setActiveAction("approve")}>Aprobar para revisión local</button><button type="button" className="button button-secondary" onClick={() => setActiveAction("reject")}>Rechazar revisión local</button></div></div>}
                  {canAuthorizeDryRun && canAuthorizeCurrent && proposal.authorization_status !== "authorized_dry_run" && <div className="proposal-action-group"><span>2 · Autorización de simulación</span><p>Permiso separado, versionado y limitado a esta simulación local.</p><button type="button" className="button button-secondary" onClick={() => setActiveAction("authorize")}>Autorizar simulación segura</button></div>}
                  {canRunDryRun && canSimulate && <div className="proposal-action-group"><span>3 · Simulación de solo lectura</span><p>Revalida captura, hash, versión y concesión; no invoca conectores externos.</p><button type="button" className="button button-primary" onClick={() => setActiveAction("simulate")}>Simular propuesta autorizada</button></div>}
                  {auditEventId && <p className="audit-receipt-note">Evento de auditoría enlazado: <code>{auditEventId}</code></p>}
                </div>
              </>
            )}
          </section>
        </div>
      )}
      <ConfirmActionDialog open={Boolean(activeAction)} onOpenChange={(open) => { if (!open && !working) setActiveAction(null); }} title={dialog?.title ?? "Confirmar operación"} description={dialog?.description ?? ""} confirmLabel={dialog?.confirm ?? "Confirmar"} onConfirm={() => { if (activeAction) void performAction(activeAction); }} working={working} danger={activeAction === "reject"} />
    </section>
  );
}
