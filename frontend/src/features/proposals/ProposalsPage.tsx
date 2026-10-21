import { useEffect, useRef, useState } from "react";
import { ArrowRight, ChevronLeft, ChevronRight, LockKeyhole } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { useExecuteProposalMutation, useProject, useProjects, useProposal, useProposals } from "../../api/queries";
import {
  comparisonValue, fieldLabel, formatDate, proposalAuthorizationStatusLabel, proposalFreshnessLabel,
  proposalReviewStatusLabel, proposalStatusTone, proposalTechnicalStatusLabel, riskKindLabel, riskKindTone,
} from "../../api/formatters";
import type { ExecuteProposalRequest, ExecuteProposalResponse, ProposalDetail, ProposalField, ProposalRiskRef } from "../../api/types";
import { ConfirmActionDialog } from "../../components/ConfirmActionDialog";
import { ProposalDecisionTrail } from "../../components/ProposalDecisionTrail";
import { EmptyState, ErrorState, LoadingState, Pill } from "../../components/States";
import { useToast } from "../../components/ToastProvider";
import { useSession } from "../auth/SessionProvider";
import { useWorkspace } from "../../app/WorkspaceContext";
import { proposalCanExecute, proposalCanModify, proposalCanReject, proposalExecutionBlockReason, proposalScope } from "./proposalDecisionHelper";
import "./proposals-phase1.css";

const pageSize = 25;
type DecisionAction = "approve" | "modify" | "reject";
type ModifiableField = "due_date" | "priority" | "assignee_ids" | "comment";

function technicalEligibilityLabel(status: ProposalDetail["technical_eligibility"] | null | undefined) {
  if (status === "eligible") return "Elegible";
  if (status === "ineligible") return "No elegible";
  return "Desconocida";
}

function technicalReviewLabel(review: ProposalDetail["technical_review"]) {
  return proposalTechnicalStatusLabel(review?.technical_status);
}

function executionProblem(result: ExecuteProposalResponse, expectedDecision: ExecuteProposalRequest["decision"]): string | null {
  if (result.decision !== expectedDecision) return "La respuesta del backend no coincide con la decisión enviada; el resultado permanece desconocido.";
  const status = typeof result.execution.status === "string" ? result.execution.status.trim() : "";
  const verification = typeof result.execution.verification_status === "string" ? result.execution.verification_status.trim() : "";
  const modeKnown = result.execution_mode === "live" || result.execution_mode === "sandbox";
  const reported = [status, verification].map((value) => value.toUpperCase());
  if (result.decision === "reject" && ["DENIED", "REJECTED"].includes(status.toUpperCase()) && modeKnown) return null;
  const confirmed = modeKnown && ["SIMULATED", "EXECUTED", "ALREADY_APPLIED"].includes(status.toUpperCase())
    && verification.toLowerCase() === "verified";
  const denied = reported.some((value) => ["DENIED", "FORBIDDEN", "NOT_AUTHORIZED"].includes(value));
  const failed = reported.some((value) => ["FAILED", "FAILURE", "ERROR", "CONFLICT"].includes(value));
  if (confirmed && !denied && !failed) return null;
  const outcome = denied ? "denegó" : "no confirmó";
  const reason = result.execution.reason;
  const explanation = typeof reason === "string" && reason.trim() ? ` ${reason.trim()}` : "";
  const mode = modeKnown ? ` · modo ${result.execution_mode}` : " · modo desconocido";
  const verificationState = !verification || verification.toLowerCase() === "unknown" ? "desconocida" : verification;
  return `El backend ${outcome} la ejecución (${status || "estado desconocido"} · verificación ${verificationState}${mode}).${explanation}`;
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
    description: "El backend volverá a validar el cambio de un único campo e informará si el resultado es sandbox o live.",
  };
  return {
    title: "Confirmar aprobación y ejecución",
    confirm: "Aprobar y ejecutar",
    description: "El backend volverá a validar la propuesta e informará el modo sandbox o live y el resultado; esta pantalla no anticipa una escritura en Trello.",
  };
}

function modifiableField(field: ProposalField): field is ModifiableField {
  return field === "due_date" || field === "priority" || field === "assignee_ids" || field === "comment";
}

function openProposalEvidence() {
  const details = document.getElementById("proposal-technical-details");
  if (details instanceof HTMLDetailsElement) details.open = true;
}

/** Compact risk marker shown in the inbox for proposals that carry a deterministic risk reference. */
function ProposalRiskBadge({ riskRefs }: { riskRefs: ProposalRiskRef[] }) {
  const first = riskRefs[0];
  return (
    <span className="proposal-list-risk">
      <Pill tone={first.resolved ? riskKindTone(first.kind) : "unknown"}>{riskKindLabel(first.kind)}</Pill>
      {first.resolved ? null : <span className="proposal-list-risk-state">no verificable</span>}
      {riskRefs.length > 1 && <span className="proposal-list-risk-extra">+{riskRefs.length - 1}</span>}
    </span>
  );
}

/**
 * Deterministic risk that motivated the proposal. A proposal never proves that its risk
 * disappeared, and a reference that cannot be resolved in the current capture is declared
 * unverifiable instead of valid.
 */
function ProposalRiskOrigin({ proposal }: { proposal: ProposalDetail }) {
  const riskRefs = proposal.risk_refs;
  return (
    <section className="proposal-risk-origin" aria-labelledby="proposal-risk-origin-title">
      <div className="proposal-trail-heading">
        <div><p className="eyebrow">RIESGO DETERMINISTA</p><h3 id="proposal-risk-origin-title">Riesgo que motivó la acción</h3></div>
      </div>
      {riskRefs === undefined ? (
        <p className="unknown-note">La respuesta del backend no incluye el vínculo de esta propuesta con riesgos deterministas.</p>
      ) : riskRefs.length === 0 ? (
        <p className="unknown-note">Esta propuesta no está asociada a un riesgo determinista.</p>
      ) : (
        <ul className="proposal-risk-list">
          {riskRefs.map((riskRef) => (
            <li key={`${riskRef.risk_id}:${riskRef.task_id}`} className="proposal-risk-item">
              <div className="proposal-risk-head">
                <Pill tone={riskRef.resolved ? riskKindTone(riskRef.kind) : "unknown"}>{riskKindLabel(riskRef.kind)}</Pill>
                <code>{riskRef.risk_id}</code>
                {riskRef.resolved ? null : <Pill tone="unknown">No verificable en la captura vigente</Pill>}
              </div>
              <p className="proposal-risk-meta">Tarea de origen: <code>{riskRef.task_id}</code> · captura {riskRef.capture_id}</p>
              <div className="proposal-risk-links">
                <Link className="text-link" to={`/tasks?task=${encodeURIComponent(riskRef.task_id)}&capture_id=${encodeURIComponent(riskRef.capture_id)}`}>Abrir tarea de origen <ArrowRight aria-hidden="true" size={14} /></Link>
                <a className="text-link" href="#proposal-evidence" onClick={openProposalEvidence}>Ver la evidencia de la propuesta <ArrowRight aria-hidden="true" size={14} /></a>
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="proposal-risk-note">
        Una propuesta aprobada o ejecutada no demuestra que el riesgo haya desaparecido: hace falta evidencia posterior de la captura.
      </p>
    </section>
  );
}

export function ProposalsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { selectedProjectId } = useWorkspace();
  const projectsQuery = useProjects();
  const listedProject = projectsQuery.data?.items.find((item) => item.id === selectedProjectId);
  const selectedProjectQuery = useProject(selectedProjectId, Boolean(selectedProjectId && !listedProject));
  const project = listedProject ?? selectedProjectQuery.data ?? (!selectedProjectId ? projectsQuery.data?.items[0] ?? null : null);
  const proposalId = searchParams.get("proposal_id") ?? searchParams.get("proposal");
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
  const [comment, setComment] = useState("");
  const [progress, setProgress] = useState("");
  const [failure, setFailure] = useState("");
  const inFlight = useRef(false);
  const executionKeys = useRef(new Map<string, string>());
  const proposal = detailQuery.data;
  const technicalReview = proposal?.technical_review;
  const technicalErrors = Array.isArray(technicalReview?.technical_errors) ? technicalReview.technical_errors : null;
  const dryRun = technicalReview?.dry_run;
  const canDecide = Boolean(session?.authenticated && session.csrf_token
    && session.capabilities.includes("governance:proposal_execute"));
  const proposalMatchesProject = Boolean(project && proposal && proposal.project_id === project.id);
  const canExecute = Boolean(proposalMatchesProject && proposal && canDecide && proposalCanExecute(proposal));
  const canReject = Boolean(proposalMatchesProject && proposal && canDecide && proposalCanReject(proposal));
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
    setComment("");
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
    setComment(proposal.field === "comment" && typeof proposal.proposed_value === "string" ? proposal.proposed_value : "");
  }

  function changeDraft(field: ModifiableField, value: string) {
    setModifiedField(field);
    if (field === "due_date") setDueDate(value);
    else if (field === "comment") setComment(value);
    else if (field === "priority") setPriority(value);
    else setAssignee(value);
  }

  async function submitExecution(action: DecisionAction) {
    const actionAllowed = action === "reject" ? canReject : canExecute;
    if (!proposal || !project || proposal.project_id !== project.id || !session?.csrf_token || !canDecide || !actionAllowed
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
        || (modifiedField === "assignee_ids" && !modifiedAssigneeIds?.length)
        || (modifiedField === "comment" && !comment.trim())) {
        setFailure("El campo modificado debe tener un valor válido antes de enviar la decisión.");
        return;
      }
    }
    const payload: ExecuteProposalRequest = {
      decision,
      ...(decision === "modify_and_execute" && modifiedDueDate ? { modified_due_date: modifiedDueDate } : {}),
      ...(decision === "modify_and_execute" && modifiedPriority ? { modified_priority: modifiedPriority } : {}),
      ...(decision === "modify_and_execute" && modifiedAssigneeIds ? { modified_assignee_ids: modifiedAssigneeIds } : {}),
      ...(decision === "modify_and_execute" && modifiedField === "comment" ? { modified_comment: comment } : {}),
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
      const problem = executionProblem(result, payload.decision);
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
  const executionBlockReason = proposal ? proposalExecutionBlockReason(proposal) : null;

  return (
    <section className="governance-page proposals-page">
      <header className="page-heading-row">
        <div>
          <p className="eyebrow">GOBERNANZA DEL PROYECTO · PROPUESTAS</p>
          <h1>Propuestas</h1>
          <p className="page-subtitle">El sistema comprueba de forma determinista los datos y la vigencia de cada propuesta. No revisa código correctivo. La gestora decide; el backend valida y reporta el resultado.</p>
        </div>
        <Pill tone="neutral">Capturas verificables</Pill>
      </header>

      <section className="module-toolbar panel" aria-label="Estado de las propuestas">
        <span>Las propuestas y sus estados provienen del backend.</span>
        <span className="caption">Una revisión técnica lista o una propuesta elegible no equivalen a aprobación humana ni autorización para ejecutar.</span>
      </section>

      {proposalsQuery.isPending && <LoadingState label="Consultando propuestas del backend…" />}
      {proposalsQuery.isError && <ErrorState title="No se pudieron cargar las propuestas" message={proposalsQuery.error.message} onRetry={() => void proposalsQuery.refetch()} />}
      {proposalsQuery.data?.availability === "unknown" && <EmptyState title="Disponibilidad de propuestas desconocida" message="La API no pudo confirmar la disponibilidad de propuestas; no se interpreta como una bandeja vacía." />}
      {proposalsQuery.data?.availability === "available" && proposalsQuery.data.items.length === 0 && (
        <div className="proposal-empty-state">
          <EmptyState
            title="No hay propuestas registradas"
            message="El backend confirmó la consulta y no devolvió propuestas para este proyecto. El motivo está en el diagnóstico de la etapa de propuestas de la última ejecución."
          />
          {project?.latest_run_id && (
            <Link className="button button-secondary button-small" to={`/analysis/${project.latest_run_id}`}>
              Ver el diagnóstico de la última ejecución
            </Link>
          )}
        </div>
      )}
      {proposalsQuery.data?.availability === "available" && proposalsQuery.data.items.length > 0 && proposalsQuery.data.pending_actionable_count === 0 && (
        <p className="unknown-note" role="status">
          No hay propuestas pendientes de decisión: las {proposalsQuery.data.total} propuestas registradas se encuentran en otros estados.
        </p>
      )}

      {proposalsQuery.data?.availability === "available" && proposalsQuery.data.items.length > 0 && (
        <div className="proposal-workspace">
          <section className="panel proposal-inbox" aria-labelledby="proposal-inbox-title">
            <div className="section-heading">
              <div><p className="eyebrow">ACCIONES RECOMENDADAS</p><h2 id="proposal-inbox-title">Bandeja de revisión</h2></div>
              <span className="count-label">{proposalsQuery.data.total} propuestas</span>
            </div>
            <div className="proposal-list">
              {proposalsQuery.data.items.map((item) => (
                <Link to={proposalHref(item.proposal_id)} key={item.proposal_id} className={`proposal-list-item${proposalId === item.proposal_id ? " proposal-list-item-active" : ""}`} aria-label={`Abrir propuesta para ${fieldLabel(item.field)}`}>
                  <span className="proposal-list-top">
                    <strong>{fieldLabel(item.field)}</strong>
                    <Pill tone={proposalStatusTone(item.technical_review?.technical_status ?? item.technical_eligibility)}>
                      {item.technical_review ? technicalReviewLabel(item.technical_review) : technicalEligibilityLabel(item.technical_eligibility)}
                    </Pill>
                  </span>
                  {item.risk_refs && item.risk_refs.length > 0 && <ProposalRiskBadge riskRefs={item.risk_refs} />}
                  <span className="proposal-list-meta">Propuesta sobre una tarea · vigencia {item.freshness === "current" ? "actual" : item.freshness === "stale" ? "obsoleta" : "desconocida"}</span>
                </Link>
              ))}
            </div>
            <div className="table-footer">
              <span>Página {page} de {pageCount}</span>
              <div className="pagination-controls">
                <button type="button" className="icon-button" aria-label="Página anterior de propuestas" disabled={page <= 1} onClick={() => changePage(page - 1)}><ChevronLeft aria-hidden="true" size={16} /></button>
                <button type="button" className="icon-button" aria-label="Página siguiente de propuestas" disabled={page >= pageCount} onClick={() => changePage(page + 1)}><ChevronRight aria-hidden="true" size={16} /></button>
              </div>
            </div>
          </section>

          <section className="panel proposal-detail" aria-labelledby="proposal-detail-title">
            {!proposalId && <EmptyState title="Selecciona una propuesta" message="El detalle se consulta solo después de elegir una propuesta." />}
            {proposalId && detailQuery.isPending && <LoadingState label="Consultando detalle y revisión técnica…" />}
            {proposalId && detailQuery.isError && <ErrorState title="No se pudo cargar la propuesta" message={detailQuery.error.message} onRetry={() => void detailQuery.refetch()} />}
            {proposal && (
              <>
                <div className="proposal-detail-heading">
                  <div><p className="eyebrow">PROPUESTA DEL SISTEMA</p><h2 id="proposal-detail-title">{fieldLabel(proposal.field)}</h2></div>
                  <Link className="text-link" to={`/tasks?q=${encodeURIComponent(proposal.task_id)}&capture_id=${encodeURIComponent(proposal.capture_id)}`}>Abrir tarea <ArrowRight aria-hidden="true" size={14} /></Link>
                </div>

                <ProposalRiskOrigin proposal={proposal} />

                <div className="proposal-manager-card">
                  <section className="proposal-business-step" aria-labelledby="proposal-problem-title">
                    <p className="eyebrow">1 · PROBLEMA</p>
                    <h3 id="proposal-problem-title">Qué se detectó</h3>
                    <p>{proposal.rationale || "El backend no informó una explicación del problema."}</p>
                  </section>

                  <section className="proposal-business-step" aria-labelledby="proposal-change-title">
                    <p className="eyebrow">2 · CAMBIO RECOMENDADO</p>
                    <h3 id="proposal-change-title">{fieldLabel(proposal.field)}</h3>
                    <div className="proposal-single-comparison" aria-label={`Comparación del campo ${fieldLabel(proposal.field)}`}>
                      <div><span>{proposal.field === "comment" ? "Operación" : "Valor actual"}</span><strong>{proposal.field === "comment" ? "Agregar comentario; no sustituye datos de la tarjeta" : comparisonValue(proposal.current_value, proposal.field)}</strong></div>
                      <ArrowRight aria-hidden="true" size={18} />
                      <div><span>{modifiedField ? "Cambio de la gestora" : "Cambio recomendado"}</span><strong>{modifiedField === "comment" ? comment : modifiedField === "due_date" ? dueDate || comparisonValue(proposal.proposed_value, proposal.field) : modifiedField === "priority" ? priority || comparisonValue(proposal.proposed_value, proposal.field) : modifiedField === "assignee_ids" ? assignee || comparisonValue(proposal.proposed_value, proposal.field) : comparisonValue(proposal.proposed_value, proposal.field)}</strong></div>
                    </div>
                    <p>Impacto esperado: {proposal.expected_impact || "No informado."}</p>
                    {modifiedField && <p className="caption">La modificación sustituye únicamente el valor recomendado para este mismo campo.</p>}
                  </section>

                  <section className="proposal-business-step" aria-labelledby="proposal-review-title">
                    <p className="eyebrow">3 · REVISIÓN TÉCNICA</p>
                    <h3 id="proposal-review-title">{technicalReviewLabel(proposal.technical_review)}</h3>
                    <p>Comprobación determinista de los datos de la propuesta y una simulación técnica. No es una revisión independiente de código ni una evaluación de un parche.</p>
                    {!proposal.technical_review && <p className="unknown-note">El resultado de la revisión técnica es desconocido.</p>}
                  </section>

                  <section className="proposal-business-step" aria-labelledby="proposal-conditions-title">
                    <p className="eyebrow">4 · CONDICIONES TÉCNICAS</p>
                    <h3 id="proposal-conditions-title">Elegibilidad y vigencia</h3>
                    <div className="proposal-condition-strip">
                      <div><span>Elegibilidad</span><Pill tone={proposalStatusTone(proposal.technical_eligibility)}>{technicalEligibilityLabel(proposal.technical_eligibility)}</Pill></div>
                      <div><span>Vigencia</span><Pill tone={proposalStatusTone(proposal.freshness)}>{proposalFreshnessLabel(proposal.freshness)}</Pill></div>
                    </div>
                    <p>Ser elegible solo indica que supera las comprobaciones técnicas informadas; no significa que esté aprobada o autorizada.</p>
                  </section>

                  <section className="proposal-business-step" aria-labelledby="proposal-manager-decision-title">
                    <p className="eyebrow">5 · DECISIÓN DE LA GESTORA</p>
                    <h3 id="proposal-manager-decision-title">Decisión y autorización informadas</h3>
                    <div className="proposal-condition-strip">
                      <div><span>Decisión humana</span><Pill tone={proposalStatusTone(proposal.review_status)}>{proposalReviewStatusLabel(proposal.review_status)}</Pill></div>
                      <div><span>Autorización</span><Pill tone={proposalStatusTone(proposal.authorization_status)}>{proposalAuthorizationStatusLabel(proposal.authorization_status)}</Pill></div>
                    </div>
                    <p>La decisión requiere sesión y permiso del servidor. El estado de autorización se muestra tal como lo informa el backend.</p>
                    {canDecide && executionBlockReason && <p className="unknown-note" role="status">{executionBlockReason}</p>}
                    {!session?.authenticated && <p className="analysis-security-note"><LockKeyhole aria-hidden="true" size={16} /> Vista de consulta. Inicia sesión para enviar una decisión protegida.</p>}
                    {session?.authenticated && !session.capabilities.includes("governance:proposal_execute") && <p className="analysis-security-note"><LockKeyhole aria-hidden="true" size={16} /> La sesión no tiene governance:proposal_execute; las decisiones protegidas están ocultas.</p>}

                    {canDecide && (canExecute || canReject) && !executionResult && (
                      <div className="proposal-action-area">
                        <label className="filter-control"><span>Motivo de la decisión</span><textarea aria-label="Motivo de la decisión" maxLength={1000} rows={3} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
                        {editing && proposal.field === "comment" && <fieldset className="proposal-draft-editor" disabled={working || Boolean(executionResult)}><legend>Revisar el comentario efectivo</legend><label>Comentario de seguimiento<textarea aria-label="Comentario de seguimiento" maxLength={2000} rows={7} value={comment} onChange={(event) => changeDraft("comment", event.target.value)} /></label><p>El backend revisará el texto modificado antes de ejecutar. La simulación no publica en Trello.</p></fieldset>}
                        <p>El motivo se envía con la decisión protegida. Rechazar no requiere que la propuesta sea técnicamente ejecutable.</p>
                        {editing && proposal.field === "due_date" && <fieldset className="proposal-draft-editor" disabled={working || Boolean(executionResult)}><legend>Modificar la fecha recomendada</legend><label>Nueva fecha límite<input type="date" value={dueDate} onChange={(event) => changeDraft("due_date", event.target.value)} /></label></fieldset>}
                        {editing && proposal.field === "priority" && <fieldset className="proposal-draft-editor" disabled={working || Boolean(executionResult)}><legend>Modificar la prioridad recomendada</legend><label>Nueva prioridad<select value={priority} onChange={(event) => changeDraft("priority", event.target.value)}><option value="">Selecciona prioridad</option><option value="low">Baja</option><option value="medium">Media</option><option value="high">Alta</option></select></label></fieldset>}
                        {editing && proposal.field === "assignee_ids" && <fieldset className="proposal-draft-editor" disabled={working || Boolean(executionResult)}><legend>Modificar los responsables recomendados</legend><label>Nuevo responsable<input maxLength={200} value={assignee} placeholder="Identificadores separados por coma" onChange={(event) => changeDraft("assignee_ids", event.target.value)} /></label></fieldset>}
                        {editing && <p className="inline-warning">Solo se enviará al backend el campo de esta propuesta; el servidor vuelve a validarlo antes de ejecutar.</p>}
                        <div className="proposal-manager-actions">
                          {canExecute && <>
                            <button type="button" className="button button-primary" disabled={working || !reason.trim()} onClick={() => setActiveAction("approve")}>{working ? "Enviando decisión…" : "Aprobar y ejecutar"}</button>
                            {canModify && <button type="button" className="button button-secondary" disabled={working} onClick={startEditing}>{editing ? "Cambiar campo de decisión" : "Modificar el campo recomendado"}</button>}
                          </>}
                          {canReject && <button type="button" className="button button-secondary" disabled={working || !reason.trim()} onClick={() => setActiveAction("reject")}>Rechazar propuesta</button>}
                        </div>
                      </div>
                    )}
                    {canDecide && !canExecute && !canReject && !executionBlockReason && <p className="unknown-note">No se ofrece otra decisión porque el estado actual no está pendiente.</p>}
                    {progress && <p role="status">{progress}</p>}
                    {failure && <p role="alert" className="inline-warning">{failure}</p>}
                    {executionResult && <div className="proposal-demo-result" role="status">
                      <strong>{executionResult.decision === "reject" ? "Rechazo registrado por el backend" : executionResult.execution_mode === "sandbox" ? "Resultado sandbox registrado · no acredita cambios live" : "Resultado live informado por el backend"}</strong>
                      <p>Decisión: {executionResult.decision} · estado: {executionResult.execution.status} · verificación: {executionResult.execution.verification_status}.</p>
                    </div>}
                  </section>

                  <ProposalDecisionTrail proposal={proposal} proposalHref={proposalHref} />

                  <details className="proposal-technical-details" id="proposal-technical-details">
                    <summary>Ver evidencia y datos técnicos de la propuesta</summary>
                    <section className="detail-section">
                      <h3>Revisión técnica determinista</h3>
                      {proposal.technical_review ? <>
                        <Pill tone={proposalStatusTone(proposal.technical_review.technical_status)}>{technicalReviewLabel(proposal.technical_review)}</Pill>
                        {technicalErrors === null && <p className="unknown-note">La lista de condiciones técnicas es desconocida.</p>}
                        {technicalErrors && technicalErrors.length > 0 && <div className="inline-warning"><strong>Condiciones informadas:</strong><ul>{technicalErrors.map((error) => <li key={error}>{error}</li>)}</ul></div>}
                        <p>Simulación técnica: {dryRun?.status ?? "desconocida"}{Array.isArray(dryRun?.reasons) && dryRun.reasons.length ? ` · ${dryRun.reasons.join(" · ")}` : ""}.</p>
                        <p>Registrada: {formatDate(proposal.technical_review.recorded_at, true)}</p>
                      </> : <p className="unknown-note">El detalle de la revisión no está disponible.</p>}
                    </section>
                    <section className="detail-section" id="proposal-evidence">
                      <h3>Evidencia asociada</h3>
                      <p>Se muestra solo bajo demanda; la interfaz no lee artefactos fuera de la respuesta del backend.</p>
                      {proposal.evidence.length ? <ul className="evidence-list">{proposal.evidence.map((evidence) => <li key={evidence.evidence_id}><span className="evidence-source">{evidence.category}</span><strong>{evidence.summary}</strong><code>{evidence.evidence_id}</code></li>)}</ul> : <p className="unknown-note">No hay evidencia asociada en la respuesta.</p>}
                    </section>
                  </details>
                </div>
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
