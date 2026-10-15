import { useEffect, useRef, useState } from "react";
import { ArrowRight, ChevronLeft, ChevronRight, Fingerprint, LockKeyhole, ShieldCheck } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { useExecuteProposalMutation, useProject, useProjects, useProposal, useProposals } from "../../api/queries";
import { analysisExecutionModeLabel, comparisonValue, fieldLabel, formatDate } from "../../api/formatters";
import type { ExecuteProposalRequest, ExecuteProposalResponse, ProposalDetail, ProposalField } from "../../api/types";
import { ConfirmActionDialog } from "../../components/ConfirmActionDialog";
import { EmptyState, ErrorState, LoadingState, Pill } from "../../components/States";
import { useToast } from "../../components/ToastProvider";
import { useSession } from "../auth/SessionProvider";
import { useWorkspace } from "../../app/WorkspaceContext";
import { proposalCanExecute, proposalCanModify, proposalScope } from "./proposalDecisionHelper";
import "./proposals-phase1.css";

const pageSize = 25;
type DecisionAction = "approve" | "modify" | "reject";
type ModifiableField = "due_date" | "priority" | "assignee_ids";

function proposalTone(value: string): "neutral" | "positive" | "warning" | "danger" | "unknown" {
  if (["ready", "eligible", "current", "EXECUTED", "ALREADY_APPLIED"].includes(value)) return "positive";
  if (["pending", "not_executed"].includes(value)) return "warning";
  if (["blocked", "rejected", "DENIED", "CONFLICT", "FAILED", "ineligible", "stale"].includes(value)) return "danger";
  if (value === "unknown") return "unknown";
  return "neutral";
}

function executionLabel(status: ProposalDetail["execution_status"]) {
  const labels: Record<ProposalDetail["execution_status"], string> = {
    not_executed: "Sin ejecución registrada", EXECUTED: "Ejecución registrada por el backend",
    ALREADY_APPLIED: "Ya aplicado según el backend", DENIED: "Denegado por el backend",
    CONFLICT: "Conflicto registrado", FAILED: "Fallido", unknown: "Estado desconocido",
  };
  return labels[status];
}

function executionProblem(result: ExecuteProposalResponse): string | null {
  const status = result.execution.status.trim();
  const verification = result.execution.verification_status.trim();
  const reported = [status, verification].map((value) => value.toUpperCase());
  if (result.decision === "reject" && ["DENIED", "REJECTED"].includes(status.toUpperCase())) return null;
  const confirmed = ["SIMULATED", "EXECUTED", "ALREADY_APPLIED"].includes(status.toUpperCase())
    && verification.toLowerCase() === "verified";
  const denied = reported.some((value) => ["DENIED", "FORBIDDEN", "NOT_AUTHORIZED"].includes(value));
  const failed = reported.some((value) => ["FAILED", "FAILURE", "ERROR", "CONFLICT"].includes(value));
  if (confirmed && !denied && !failed) return null;
  const outcome = denied ? "denegó" : "no completó";
  const reason = result.execution.reason;
  const explanation = typeof reason === "string" && reason.trim() ? ` ${reason.trim()}` : "";
  return `El backend ${outcome} la ejecución (${status || verification || "estado desconocido"}).${explanation}`;
}

function actionCopy(action: DecisionAction, modified: boolean) {
  if (action === "reject") return {
    title: "Confirmar rechazo de propuesta",
    confirm: "Rechazar propuesta",
    description: "El motivo se registrará con la decisión enviada al backend; no se guarda una decisión local.",
  };
  if (action === "modify" || modified) return {
    title: "Confirmar modificación y ejecución",
    confirm: "Modificar y ejecutar",
    description: "El backend volverá a validar el cambio de un único campo antes de ejecutar la propuesta.",
  };
  return {
    title: "Confirmar aprobación y ejecución",
    confirm: "Aprobar y ejecutar",
    description: "El backend volverá a validar la propuesta y determinará el resultado y el modo de ejecución.",
  };
}

function modifiableField(field: ProposalField): field is ModifiableField {
  return field === "due_date" || field === "priority" || field === "assignee_ids";
}

export function ProposalsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { selectedProjectId } = useWorkspace();
  const projectsQuery = useProjects();
  const listedProject = projectsQuery.data?.items.find((item) => item.id === selectedProjectId);
  const selectedProjectQuery = useProject(selectedProjectId, Boolean(selectedProjectId && !listedProject));
  const project = listedProject ?? selectedProjectQuery.data ?? (!selectedProjectId ? projectsQuery.data?.items[0] ?? null : null);
  const proposalId = searchParams.get("proposal_id");
  const page = Math.max(1, Number(searchParams.get("page") ?? 1) || 1);
  const proposalsQuery = useProposals(project?.id ?? null, page, pageSize);
  const detailQuery = useProposal(project?.id ?? null, proposalId);
  const { session } = useSession();
  const { notify } = useToast();
  const executionMutation = useExecuteProposalMutation();
  const [activeAction, setActiveAction] = useState<DecisionAction | null>(null);
  const [working, setWorking] = useState(false);
  const [executionResult, setExecutionResult] = useState<ExecuteProposalResponse | null>(null);
  const [reason, setReason] = useState("");
  const [editing, setEditing] = useState(false);
  const [modifiedField, setModifiedField] = useState<ModifiableField | null>(null);
  const [dueDate, setDueDate] = useState("");
  const [priority, setPriority] = useState("");
  const [assignee, setAssignee] = useState("");
  const [progress, setProgress] = useState("");
  const [failure, setFailure] = useState("");
  const inFlight = useRef(false);
  const executionKeys = useRef(new Map<string, string>());
  const proposal = detailQuery.data;
  const canExecute = Boolean(
    project && proposal && proposal.project_id === project.id && proposalCanExecute(proposal)
      && session?.authenticated && session.csrf_token
      && session.capabilities.includes("governance:proposal_execute"),
  );
  const canModify = Boolean(canExecute && proposal && proposalCanModify(proposal));
  const hasModifiedValue = Boolean(modifiedField);

  useEffect(() => {
    setExecutionResult(null);
    setActiveAction(null);
    setReason("");
    setEditing(false);
    setModifiedField(null);
    setDueDate("");
    setPriority("");
    setAssignee("");
    setFailure("");
    setProgress("");
  }, [proposal?.project_id, proposal?.proposal_id, proposal?.capture_id, proposal?.proposal_body_sha256]);

  function proposalHref(id: string) {
    const next = new URLSearchParams(searchParams);
    next.set("proposal_id", id);
    return { pathname: "/proposals", search: `?${next.toString()}` };
  }

  function changePage(nextPage: number) {
    const next = new URLSearchParams(searchParams);
    if (nextPage > 1) next.set("page", String(nextPage));
    else next.delete("page");
    setSearchParams(next);
  }

  function startEditing() {
    if (!canModify || !proposal || !modifiableField(proposal.field)) return;
    setEditing(true);
    setModifiedField(null);
    setDueDate(proposal.field === "due_date" && typeof proposal.proposed_value === "string" ? proposal.proposed_value.slice(0, 10) : "");
    setPriority(proposal.field === "priority" && typeof proposal.proposed_value === "string" && ["low", "medium", "high"].includes(proposal.proposed_value) ? proposal.proposed_value : "");
    setAssignee(proposal.field === "assignee_ids" && Array.isArray(proposal.proposed_value) ? proposal.proposed_value.join(", ") : "");
  }

  function changeDraft(field: ModifiableField, value: string) {
    setModifiedField(field);
    if (field === "due_date") setDueDate(value);
    else if (field === "priority") setPriority(value);
    else setAssignee(value);
  }

  async function submitExecution(action: DecisionAction) {
    if (!proposal || !project || proposal.project_id !== project.id || !session?.csrf_token || !canExecute
      || inFlight.current || executionMutation.isPending || !reason.trim()) return;
    let decision: ExecuteProposalRequest["decision"] = action === "reject" ? "reject" : "approve_and_execute";
    let modifiedDueDate = "";
    let modifiedPriority = "";
    let modifiedAssigneeIds: string[] | null = null;
    if (action !== "reject" && modifiedField) {
      decision = "modify_and_execute";
      if (modifiedField === "due_date") modifiedDueDate = dueDate.trim();
      if (modifiedField === "priority") modifiedPriority = priority.trim();
      if (modifiedField === "assignee_ids") {
        modifiedAssigneeIds = assignee.split(/[\n,]+/).map((value) => value.trim()).filter(Boolean)
          .filter((value, index, values) => values.indexOf(value) === index);
      }
      if ((modifiedField === "due_date" && !modifiedDueDate)
        || (modifiedField === "priority" && !modifiedPriority)
        || (modifiedField === "assignee_ids" && !modifiedAssigneeIds?.length)) {
        setFailure("El campo modificado debe tener un valor válido antes de enviar la decisión.");
        return;
      }
    }
    const payload: ExecuteProposalRequest = {
      decision,
      ...(decision === "modify_and_execute" && modifiedDueDate ? { modified_due_date: modifiedDueDate } : {}),
      ...(decision === "modify_and_execute" && modifiedPriority ? { modified_priority: modifiedPriority } : {}),
      ...(decision === "modify_and_execute" && modifiedAssigneeIds ? { modified_assignee_ids: modifiedAssigneeIds } : {}),
      reason: reason.trim(),
    };
    const keyIdentity = `${proposalScope(proposal)}:${JSON.stringify(payload)}`;
    const idempotencyKey = executionKeys.current.get(keyIdentity) ?? crypto.randomUUID();
    executionKeys.current.set(keyIdentity, idempotencyKey);
    inFlight.current = true;
    setWorking(true);
    setFailure("");
    setProgress("Enviando decisión protegida al backend…");
    try {
      const result = await executionMutation.mutateAsync({
        projectId: project.id,
        proposalId: proposal.proposal_id,
        payload,
        csrfToken: session.csrf_token,
        idempotencyKey,
      });
      const problem = executionProblem(result);
      if (problem) {
        setFailure(problem);
        setProgress("La respuesta del backend no confirma una ejecución aceptada.");
        notify(problem, "error");
        return;
      }
      executionKeys.current.delete(keyIdentity);
      setExecutionResult(result);
      setActiveAction(null);
      setEditing(false);
      setProgress(`Backend: ${result.execution_mode} · ${result.execution.status} · verificación ${result.execution.verification_status}.`);
      notify(result.decision === "reject"
        ? "Rechazo registrado por el backend."
        : result.execution_mode === "sandbox"
          ? `Resultado local: ${result.execution.status}; Trello no recibió cambios.`
          : `Trello reportó ${result.execution.status}; verificación ${result.execution.verification_status}.`, "success");
    } catch (error) {
      const message = error instanceof Error ? error.message : "La ejecución de la propuesta no pudo completarse.";
      setFailure(message);
      setProgress("La operación no fue confirmada por el backend; no se registra un resultado local.");
      notify(message, "error");
    } finally {
      inFlight.current = false;
      setWorking(false);
    }
  }

  const dialog = activeAction ? actionCopy(activeAction, hasModifiedValue && activeAction !== "reject") : null;
  const pageCount = Math.max(1, Math.ceil((proposalsQuery.data?.total ?? 0) / pageSize));
  const proposalActionStatus = proposal?.technical_review?.technical_status ?? proposal?.technical_eligibility ?? "unknown";

  return (
    <section className="governance-page proposals-page">
      <header className="page-heading-row">
        <div><p className="eyebrow">GOBERNANZA DEL PROYECTO · EJECUCIÓN PROTEGIDA</p><h1>Propuestas</h1><p className="page-subtitle">El Revisor técnico actúa automáticamente. El gestor decide aprobar, modificar o rechazar; el backend valida y reporta el resultado.</p></div>
        <Pill tone="neutral">Captura inmutable</Pill>
      </header>

      <section className="module-toolbar panel" aria-label="Estado de las propuestas">
        <span>El backend informa la disponibilidad y los resultados de revisión técnica.</span>
        <span className="caption">Las decisiones y sus motivos se envían al endpoint protegido; no se guardan decisiones de demostración en este navegador.</span>
      </section>

      {proposalsQuery.isPending && <LoadingState label="Consultando propuestas del backend…" />}
      {proposalsQuery.isError && <ErrorState title="No se pudieron cargar las propuestas" message={proposalsQuery.error.message} onRetry={() => void proposalsQuery.refetch()} />}
      {proposalsQuery.data?.availability === "unknown" && <EmptyState title="Disponibilidad de propuestas desconocida" message="La API no pudo confirmar la disponibilidad de propuestas; no se interpreta como una bandeja vacía." />}
      {proposalsQuery.data?.availability === "available" && proposalsQuery.data.items.length === 0 && <EmptyState title="No hay propuestas disponibles" message="El backend confirmó la consulta y no devolvió propuestas." />}

      {proposalsQuery.data?.availability === "available" && proposalsQuery.data.items.length > 0 && (
        <div className="proposal-workspace">
          <section className="panel proposal-inbox" aria-labelledby="proposal-inbox-title">
            <div className="section-heading"><div><p className="eyebrow">PROPUESTAS DEL EJECUTOR</p><h2 id="proposal-inbox-title">Bandeja de revisión</h2></div><span className="count-label">{proposalsQuery.data.total} propuestas</span></div>
            <div className="proposal-list">
              {proposalsQuery.data.items.map((item) => (
                <Link to={proposalHref(item.proposal_id)} key={item.proposal_id} className={`proposal-list-item${proposalId === item.proposal_id ? " proposal-list-item-active" : ""}`} aria-label={`Abrir propuesta ${item.proposal_id}`}>
                  <span className="proposal-list-top"><strong>{fieldLabel(item.field)}</strong><Pill tone={proposalTone(item.technical_review?.technical_status ?? item.technical_eligibility)}>{item.technical_review?.technical_status === "ready" ? "Revisión técnica lista" : item.technical_review?.technical_status === "blocked" ? "Revisión técnica bloqueada" : item.technical_eligibility}</Pill></span>
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
            {proposalId && detailQuery.isPending && <LoadingState label="Consultando detalle y revisión técnica…" />}
            {proposalId && detailQuery.isError && <ErrorState title="No se pudo cargar la propuesta" message={detailQuery.error.message} onRetry={() => void detailQuery.refetch()} />}
            {proposal && (
              <>
                <div className="proposal-detail-heading"><div><p className="eyebrow">PROPUESTA DE ACCIÓN CORRECTIVA · AGENTE EJECUTOR</p><h2 id="proposal-detail-title">{fieldLabel(proposal.field)} · {proposal.task_id}</h2><code>{proposal.proposal_id}</code></div><Link className="text-link" to={`/tasks?q=${encodeURIComponent(proposal.task_id)}&capture_id=${encodeURIComponent(proposal.capture_id)}`}>Abrir tarea <ArrowRight aria-hidden="true" size={14} /></Link></div>
                <div className="proposal-manager-card">
                  <div className="proposal-manager-seal"><ShieldCheck aria-hidden="true" size={18} /><strong>{executionResult ? "Resultado reportado por el backend" : proposal.technical_review ? (proposal.technical_review.technical_status === "ready" ? "Agente Revisor · Revisión técnica lista" : "Agente Revisor · Revisión técnica bloqueada") : "Agente Revisor · Resultado no disponible"}</strong></div>
                  {proposal.technical_review && <p>Revisión automática: {proposal.technical_review.technical_status} · aprobación humana requerida · autorización de ejecución aún pendiente. Validación: {proposal.technical_review.dry_run.status}.</p>}
                  <p><strong>Problema detectado:</strong> {proposal.evidence[0]?.summary ?? "La captura no incluye un diagnóstico adicional."}</p>
                  <div className="proposal-manager-context"><div><span>Causa · Agente Ejecutor</span><p>{proposal.rationale}</p></div><div><span>Impacto sobre el proyecto</span><p>{proposal.expected_impact}</p></div></div>
                  <div className="proposal-manager-comparison" role="table" aria-label="Antes y después de la acción correctiva">
                    <div role="row"><strong role="columnheader">Campo</strong><strong role="columnheader">Antes</strong><strong role="columnheader">Después · {modifiedField ? "Modificación propuesta" : "Propuesta del backend"}</strong></div>
                    {(["due_date", "priority", "assignee_ids"] as const).map((field) => {
                      const original = proposal.field === field ? comparisonValue(proposal.proposed_value, field) : "Sin dato / sin cambio";
                      const draft = field === "due_date" ? dueDate : field === "priority" ? priority : assignee;
                      return <div role="row" key={field}><span role="cell">{fieldLabel(field)}</span><span role="cell">{proposal.field === field ? comparisonValue(proposal.current_value, field) : "Sin dato"}</span><strong role="cell">{modifiedField === field && draft ? field === "priority" ? comparisonValue(draft, field) : draft : original}</strong></div>;
                    })}
                  </div>
                  <p className="proposal-demo-banner"><strong>{executionResult ? "DECISIÓN · RESULTADO REGISTRADO" : canExecute ? "DECISIÓN PROTEGIDA · A LA ESPERA DEL GESTOR" : "VISTA DE CONSULTA · SIN DECISIONES LOCALES"}</strong><br />{executionResult ? "El resultado indica si la acción se aplicó en la nube o se registró localmente." : "El navegador no registra aprobaciones, rechazos ni simulaciones locales."}</p>
                  {editing && proposal.field === "due_date" && <fieldset className="proposal-draft-editor" disabled={working || Boolean(executionResult)}><legend>Modificar la fecha propuesta</legend><label>Nueva fecha límite<input type="date" value={dueDate} onChange={(event) => changeDraft("due_date", event.target.value)} /></label></fieldset>}
                  {editing && proposal.field === "priority" && <fieldset className="proposal-draft-editor" disabled={working || Boolean(executionResult)}><legend>Modificar la prioridad propuesta</legend><label>Nueva prioridad<select value={priority} onChange={(event) => changeDraft("priority", event.target.value)}><option value="">Selecciona prioridad</option><option value="low">Baja</option><option value="medium">Media</option><option value="high">Alta</option></select></label></fieldset>}
                  {editing && proposal.field === "assignee_ids" && <fieldset className="proposal-draft-editor" disabled={working || Boolean(executionResult)}><legend>Modificar responsables propuestos</legend><label>Nuevo responsable<input maxLength={200} value={assignee} placeholder="Identificadores separados por coma" onChange={(event) => changeDraft("assignee_ids", event.target.value)} /></label></fieldset>}
                  {editing && <p className="inline-warning">Solo se enviará al backend el campo de esta propuesta; el servidor vuelve a validarlo antes de ejecutar.</p>}
                  {canExecute && !executionResult && <div className="proposal-action-area">
                    <label className="filter-control"><span>Motivo de la decisión</span><textarea aria-label="Motivo de la decisión" maxLength={1000} rows={3} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
                    <p>El motivo se registra con la decisión protegida. Se requiere para aprobar, modificar o rechazar.</p>
                    <div className="proposal-manager-actions">
                      <button type="button" className="button button-primary" disabled={working || !reason.trim()} onClick={() => setActiveAction("approve")}>{working ? "Enviando decisión…" : "Aprobar y Aplicar en Trello"}</button>
                      <button type="button" className="button button-secondary" disabled={!canModify || working} onClick={startEditing}>Modificar antes de Aprobar</button>
                      <button type="button" className="button button-secondary" disabled={working || !reason.trim()} onClick={() => setActiveAction("reject")}>Rechazar Propuesta</button>
                    </div>
                  </div>}
                  {!session?.authenticated && <p className="analysis-security-note"><LockKeyhole aria-hidden="true" size={16} /> Vista de consulta. Inicia sesión para solicitar una decisión al backend.</p>}
                  {session?.authenticated && !session.capabilities.includes("governance:proposal_execute") && <p className="analysis-security-note"><LockKeyhole aria-hidden="true" size={16} /> La sesión no tiene governance:proposal_execute; las decisiones están ocultas.</p>}
                  {session?.authenticated && session.capabilities.includes("governance:proposal_execute") && !canExecute && <p className="unknown-note">El backend no informa una propuesta vigente, elegible y lista para ejecución.</p>}
                  {progress && <p role="status">{progress}</p>}
                  {failure && <p role="alert" className="inline-warning">{failure}</p>}
                  {executionResult && <div className="proposal-demo-result"><strong>{analysisExecutionModeLabel(executionResult.execution_mode) ?? "Modo no informado"} · {executionResult.execution_mode === "sandbox" ? "Trello sin cambios" : "Resultado informado por Trello"}</strong><p>Decisión: {executionResult.decision} · estado: {executionResult.execution.status} · Verificación: {executionResult.execution.verification_status}</p><p>El resultado no implica que un servicio externo haya aplicado el cambio.</p></div>}
                </div>
                <div className="proposal-value-diff" aria-label="Comparación entre valor observado y propuesto">
                  <div><small>Antes</small><span>Valor observado</span><strong>{comparisonValue(proposal.current_value, proposal.field)}</strong></div>
                  <ArrowRight aria-hidden="true" size={18} />
                  <div><small>Después</small><span>Valor propuesto</span><strong>{comparisonValue(proposal.proposed_value, proposal.field)}</strong></div>
                </div>
                <p className="caption">Los valores de otros campos no se infieren si el backend no los informó.</p>
                <div className="proposal-detail-grid">
                  <div><span>Captura</span><code>{proposal.capture_id}</code></div>
                  <div><span>Versión</span><code>{proposal.version ?? "Desconocida"}</code></div>
                  <div className="proposal-hash"><span>Hash SHA-256 del cuerpo</span><code>{proposal.proposal_body_sha256}</code></div>
                </div>
                <div className="proposal-status-strip" aria-label="Estados informados por el backend">
                  <div><span>Elegibilidad técnica</span><Pill tone={proposalTone(proposal.technical_eligibility)}>{proposal.technical_eligibility}</Pill></div>
                  <div><span>Vigencia de captura</span><Pill tone={proposalTone(proposal.freshness)}>{proposal.freshness}</Pill></div>
                  <div><span>Agente Revisor automático</span><Pill tone={proposalTone(proposalActionStatus)}>{proposalActionStatus}</Pill></div>
                  <div><span>Resultado de ejecución</span><Pill tone={proposalTone(proposal.execution_status)}>{executionLabel(proposal.execution_status)}</Pill></div>
                </div>
                <details className="proposal-technical-details"><summary>Ver evidencia y resultado del Revisor automático</summary>
                  {proposal.technical_review && <section className="detail-section"><div className="detail-section-heading"><div><h3>Revisión técnica automática</h3><p>El resultado técnico no sustituye la decisión humana ni autoriza la ejecución.</p></div><Fingerprint aria-hidden="true" size={16} /></div>
                    <Pill tone={proposalTone(proposal.technical_review.technical_status)}>{proposal.technical_review.technical_status}</Pill>
                    {proposal.technical_review.technical_errors.length > 0 && <div className="inline-warning"><strong>El Revisor informó:</strong><ul>{proposal.technical_review.technical_errors.map((error) => <li key={error}>{error}</li>)}</ul></div>}
                    <p>Validación técnica: {proposal.technical_review.dry_run.status}{proposal.technical_review.dry_run.reasons.length ? ` · ${proposal.technical_review.dry_run.reasons.join(" · ")}` : ""}</p>
                    <p>Registrada: {formatDate(proposal.technical_review.recorded_at, true)}</p>
                  </section>}
                  <section className="detail-section"><div className="detail-section-heading"><div><h3>Evidencia asociada</h3><p>Proyección entregada por el backend; no se leen artefactos en el navegador.</p></div></div>
                    {proposal.evidence.length ? <ul className="evidence-list">{proposal.evidence.map((evidence) => <li key={evidence.evidence_id}><span className="evidence-source">{evidence.category}</span><strong>{evidence.summary}</strong><code>{evidence.evidence_id}</code></li>)}</ul> : <p className="unknown-note">No hay evidencia asociada en la respuesta.</p>}
                  </section>
                </details>
              </>
            )}
          </section>
        </div>
      )}
      <ConfirmActionDialog
        open={Boolean(activeAction)}
        onOpenChange={(open) => { if (!open && !working) setActiveAction(null); }}
        title={dialog?.title ?? "Confirmar decisión"}
        description={dialog?.description ?? ""}
        error={failure}
        confirmLabel={dialog?.confirm ?? "Confirmar"}
        onConfirm={() => { if (activeAction) void submitExecution(activeAction); }}
        working={working}
        danger={activeAction === "reject"}
      />
    </section>
  );
}
